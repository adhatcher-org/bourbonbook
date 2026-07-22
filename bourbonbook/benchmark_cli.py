"""Private, opt-in AI benchmark tooling for a Bourbon Book data volume.

This module deliberately has no web route.  It is intended to run in the application
container against a user-approved collection, and stores its fixtures beneath DATA_DIR.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from bourbonbook.analysis import FIELDS, analyze_bottle, analyze_bottle_name
from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.models import Bottle, User

FIXTURE_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 2
REPORT_CONTRACT_VERSION = "benchmark-v2-local-only"
PHOTO_FIELDS = tuple(field for field in FIELDS if field != "msrp")
NAME_FIELDS = tuple(
    field
    for field in PHOTO_FIELDS
    if field not in {"status", "fill_level", "barrel_number", "bottle_number", "warehouse", "floor"}
)
OPERATION_FIELDS = {"photo": PHOTO_FIELDS, "name": NAME_FIELDS}
CRITICAL_FIELDS = {
    "photo": ("name", "brand", "proof", "abv", "size", "status", "fill_level"),
    "name": ("name", "brand", "proof", "abv", "size"),
}
NUMERIC_FIELDS = {"proof", "abv", "fill_level"}
SUCCESS_STATUSES = {"complete", "verified"}
PHOTO_PREPROCESS_REVISION = "application-default"

CommandRunner = Callable[[list[str]], str]
OllamaGetter = Callable[[str], Awaitable[dict[str, Any]]]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_digest(value: Any) -> str:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(rendered).hexdigest()


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def safe_photo(upload_root: Path, photo_name: str) -> Path:
    candidate = upload_root / photo_name
    resolved_root = upload_root.resolve()
    resolved = candidate.resolve()
    if (
        candidate.is_symlink()
        or not resolved.is_relative_to(resolved_root)
        or not resolved.is_file()
    ):
        raise ValueError(f"Benchmark source photo is missing or unsafe: {photo_name!r}")
    return resolved


def owner_for(session, selector: str) -> User:
    if selector.isdigit():
        owner = session.get(User, int(selector))
    else:
        owner = session.scalar(select(User).where(User.username == selector))
    if owner is None:
        raise ValueError("No user matches --owner; use that user's exact ID or username")
    return owner


def expected_values(bottle: Bottle) -> dict[str, Any]:
    return {field: getattr(bottle, field) for field in PHOTO_FIELDS}


def export_fixture(settings: Settings, owner_selector: str, destination: Path) -> dict[str, Any]:
    destination = destination.resolve()
    photos_dir = destination / "photos"
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Fixture destination must be new or empty")
    private_directory(destination)
    private_directory(photos_dir)
    database = Database(settings)
    upload_root = settings.data_dir / "uploads"
    try:
        with database.session_factory() as session:
            owner = owner_for(session, owner_selector)
            bottles = list(
                session.scalars(
                    select(Bottle)
                    .where(Bottle.owner_id == owner.id, Bottle.on_shopping_list.is_(False))
                    .order_by(Bottle.id)
                )
            )
            if not bottles:
                raise ValueError("Selected user has no non-shopping-list bottles")
            cases: list[dict[str, Any]] = []
            for index, bottle in enumerate(bottles, start=1):
                if not bottle.photo_name:
                    raise ValueError(
                        "Every benchmark bottle must have a photo; found one without one"
                    )
                source = safe_photo(upload_root, bottle.photo_name)
                suffix = source.suffix.lower() or ".jpg"
                photo_file = f"case-{index:03d}{suffix}"
                target = photos_dir / photo_file
                target.write_bytes(source.read_bytes())
                target.chmod(0o600)
                cases.append(
                    {
                        "case_id": f"case-{index:03d}",
                        "photo_file": f"photos/{photo_file}",
                        "photo_sha256": sha256_path(target),
                        "expected": expected_values(bottle),
                        # This is reference-only.  Photograph analysis never scores MSRP.
                        "price_reference": {"msrp": bottle.msrp},
                    }
                )
    finally:
        database.engine.dispose()

    manifest = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "created_at": utc_now(),
        "case_count": len(cases),
        "scored_fields": list(PHOTO_FIELDS),
        "critical_fields": list(CRITICAL_FIELDS["photo"]),
        "cases": cases,
    }
    manifest["manifest_sha256"] = json_digest(manifest)
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    manifest_path.chmod(0o600)
    return manifest


def load_fixture(path: Path) -> dict[str, Any]:
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    expected_digest = manifest.pop("manifest_sha256", None)
    if manifest.get("schema_version") != FIXTURE_SCHEMA_VERSION or not expected_digest:
        raise ValueError("Unsupported or incomplete benchmark fixture")
    if json_digest(manifest) != expected_digest:
        raise ValueError("Benchmark fixture manifest digest does not match")
    manifest["manifest_sha256"] = expected_digest
    for case in manifest["cases"]:
        photo = safe_photo(path, case["photo_file"])
        if sha256_path(photo) != case["photo_sha256"]:
            raise ValueError(f"Benchmark photo digest does not match for {case['case_id']}")
    return manifest


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower().replace("’", "").replace("'", "")).strip()


def as_number(value: Any) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group()) if match else None


def canonical_size(value: Any) -> int | None:
    """Return a bottle size in millilitres only when its unit is unambiguous."""
    if value in (None, ""):
        return None
    rendered = str(value).strip().lower().replace(" ", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ml|millilit(?:er|re)s?|cl|l|lit(?:er|re)s?)", rendered)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2)
    if unit.startswith(("ml", "millil")):
        multiplier = 1
    elif unit == "cl":
        multiplier = 10
    else:
        multiplier = 1000
    millilitres = amount * multiplier
    return int(millilitres) if millilitres.is_integer() else None


def matches(field: str, expected: Any, actual: Any) -> bool:
    if expected in (None, ""):
        return False
    if field in NUMERIC_FIELDS:
        expected_number, actual_number = as_number(expected), as_number(actual)
        tolerance = 10 if field == "fill_level" else 0.5
        return (
            expected_number is not None
            and actual_number is not None
            and abs(expected_number - actual_number) <= tolerance
        )
    if field == "size":
        expected_size, actual_size = canonical_size(expected), canonical_size(actual)
        return expected_size is not None and expected_size == actual_size
    expected_text, actual_text = normalize(expected), normalize(actual)
    return bool(expected_text) and expected_text == actual_text


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 2)


def summarize(samples: list[dict[str, Any]], fields: list[str]) -> dict[str, Any]:
    timings = [sample["duration_ms"] for sample in samples]
    field_summary: dict[str, dict[str, int | float]] = {}
    for field in fields:
        comparisons = [
            sample["comparisons"][field] for sample in samples if field in sample["comparisons"]
        ]
        scored = len(comparisons)
        matched = sum(item["match"] for item in comparisons)
        field_summary[field] = {
            "scored": scored,
            "matched": matched,
            "accuracy": round(matched / scored, 4) if scored else 0.0,
        }
    scored = sum(item["scored"] for item in field_summary.values())
    matched = sum(item["matched"] for item in field_summary.values())
    return {
        "requests": len(samples),
        "successes": sum(sample["status"] in SUCCESS_STATUSES for sample in samples),
        "p50_ms": percentile(timings, 0.5),
        "p95_ms": percentile(timings, 0.95),
        "max_ms": max(timings) if timings else None,
        "overall_accuracy": round(matched / scored, 4) if scored else 0.0,
        "fields": field_summary,
    }


def configured_model(settings: Settings, operation: str) -> str:
    if operation == "photo":
        return getattr(settings, "ollama_vision_model", None) or settings.ollama_model
    return getattr(settings, "ollama_text_model", None) or settings.ollama_model


def ensure_local_benchmark_settings(settings: Settings) -> None:
    if settings.analysis_provider != "ollama" or settings.openai_api_key is not None:
        raise ValueError(
            "Benchmarks are local-only; the provider must be Ollama and the OpenAI key "
            "must be cleared"
        )


def local_benchmark_settings(settings: Settings) -> Settings:
    """Strip OpenAI configuration before benchmark settings reach provider dispatch."""
    return replace(settings, analysis_provider="ollama", openai_api_key=None)


def default_command_runner(command: list[str]) -> str:
    return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=5)


async def default_ollama_getter(settings: Settings, path: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=settings.ollama_url, timeout=5.0) as client:
        response = await client.get(path)
        response.raise_for_status()
        payload = response.json()
    return payload if isinstance(payload, dict) else {}


def gpu_snapshot(command_runner: CommandRunner = default_command_runner) -> list[dict[str, str]]:
    """Capture public GPU inventory; return no data when the host has no NVIDIA tooling."""
    try:
        output = command_runner(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ]
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if not isinstance(output, str):
        return []
    keys = ("name", "driver_version", "memory_total_mib", "memory_used_mib")
    return [
        dict(zip(keys, (part.strip() for part in line.split(",")), strict=True))
        for line in output.splitlines()
        if line.strip() and len(line.split(",")) == len(keys)
    ]


async def collect_runtime_evidence(
    settings: Settings,
    *,
    preprocess_revision: str = PHOTO_PREPROCESS_REVISION,
    command_runner: CommandRunner = default_command_runner,
    ollama_getter: OllamaGetter | None = None,
) -> dict[str, Any]:
    """Collect bounded non-secret evidence without making a benchmark result depend on it."""
    ensure_local_benchmark_settings(settings)
    getter = ollama_getter or (lambda path: default_ollama_getter(settings, path))
    version: str | None = None
    resident_models: list[dict[str, Any]] = []
    try:
        version_payload = await getter("/api/version")
        version_value = version_payload.get("version")
        version = str(version_value) if version_value not in (None, "") else None
        ps_payload = await getter("/api/ps")
        models = ps_payload.get("models", [])
        if not isinstance(models, list):
            raise ValueError("Ollama /api/ps models must be a list")
        for model in models:
            if not isinstance(model, dict):
                continue
            resident_models.append(
                {
                    key: model[key]
                    for key in ("name", "digest", "size_vram", "expires_at")
                    if model.get(key) not in (None, "")
                }
            )
    except (httpx.HTTPError, OSError, TypeError, ValueError, AttributeError):
        # A benchmark still records application timings if runtime inspection is unavailable.
        pass
    return {
        "collected_at": utc_now(),
        "ollama": {
            "endpoint_host": urlsplit(settings.ollama_url).hostname,
            "version": version,
            "resident_models": resident_models,
        },
        "gpu": gpu_snapshot(command_runner),
        "configured_models": {
            "photo": configured_model(settings, "photo"),
            "name": configured_model(settings, "name"),
        },
        "preprocess_revision": preprocess_revision,
        "timing_instrumentation": {
            "queue_wait_ms": None,
            "model_load_ms": None,
            "model_eviction_ms": None,
            "instrumented": False,
        },
    }


def upgrade_report(report: dict[str, Any]) -> dict[str, Any]:
    """Read v1 reports for inspection, but prevent comparisons across metric contracts."""
    schema_version = report.get("schema_version", 1)
    if schema_version == REPORT_SCHEMA_VERSION:
        return dict(report)
    if schema_version != 1:
        raise ValueError(f"Unsupported benchmark report schema: {schema_version!r}")
    upgraded = dict(report)
    upgraded["schema_version"] = REPORT_SCHEMA_VERSION
    upgraded["report_contract"] = "legacy-v1"
    upgraded["runtime_evidence"] = {"available": False, "reason": "not recorded by report v1"}
    upgraded["migration"] = {"from_schema_version": 1, "comparison_compatible": False}
    return upgraded


async def run_fixture(
    fixture: Path,
    settings: Settings,
    operations: tuple[str, ...],
    runs: int,
    cold_start_state: str,
    *,
    runtime_evidence: dict[str, Any] | None = None,
    preprocess_revision: str = PHOTO_PREPROCESS_REVISION,
) -> dict[str, Any]:
    ensure_local_benchmark_settings(settings)
    manifest = load_fixture(fixture)
    cases = manifest["cases"]
    first_operation = operations[0]
    first_case = cases[0]
    started = time.perf_counter()
    if first_operation == "photo":
        _, cold_status = await analyze_bottle(
            safe_photo(fixture, first_case["photo_file"]), settings
        )
    else:
        _, cold_status = await analyze_bottle_name(first_case["expected"]["name"], settings)
    cold_start = {
        "operation": first_operation,
        "case_id": first_case["case_id"],
        "status": cold_status,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    samples: dict[str, list[dict[str, Any]]] = {operation: [] for operation in operations}
    for operation in operations:
        for iteration in range(runs):
            for case in cases:
                started = time.perf_counter()
                if operation == "photo":
                    actual, status = await analyze_bottle(
                        safe_photo(fixture, case["photo_file"]), settings
                    )
                else:
                    actual, status = await analyze_bottle_name(case["expected"]["name"], settings)
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                comparisons = {
                    field: {
                        "expected": expected,
                        "actual": actual.get(field),
                        "match": matches(field, expected, actual.get(field)),
                    }
                    for field, expected in case["expected"].items()
                    if field in OPERATION_FIELDS[operation] and expected not in (None, "")
                }
                samples[operation].append(
                    {
                        "case_id": case["case_id"],
                        "iteration": iteration + 1,
                        "status": status,
                        "duration_ms": duration_ms,
                        "comparisons": comparisons,
                    }
                )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_contract": REPORT_CONTRACT_VERSION,
        "created_at": utc_now(),
        "fixture_manifest_sha256": manifest["manifest_sha256"],
        "case_count": manifest["case_count"],
        "runs_per_case": runs,
        "cold_start_state": cold_start_state,
        "cold_start": cold_start,
        "provider": settings.analysis_provider,
        "model": {operation: configured_model(settings, operation) for operation in operations},
        "runtime_evidence": runtime_evidence
        if runtime_evidence is not None
        else await collect_runtime_evidence(settings, preprocess_revision=preprocess_revision),
        "operations": {
            operation: {
                "scoring_fields": list(OPERATION_FIELDS[operation]),
                "critical_fields": list(CRITICAL_FIELDS[operation]),
                "summary": summarize(values, list(OPERATION_FIELDS[operation])),
                "samples": values,
            }
            for operation, values in samples.items()
        },
    }


def compare_reports(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    baseline, candidate = upgrade_report(baseline), upgrade_report(candidate)
    failures: list[str] = []
    baseline_contract = baseline.get("report_contract")
    candidate_contract = candidate.get("report_contract")
    if (
        baseline_contract != REPORT_CONTRACT_VERSION
        or candidate_contract != REPORT_CONTRACT_VERSION
    ):
        failures.append("reports must use the current benchmark v2 contract")
    if baseline_contract != candidate_contract:
        failures.append(
            "report contract differs; recapture the baseline with the current benchmark"
        )
    if baseline["fixture_manifest_sha256"] != candidate["fixture_manifest_sha256"]:
        failures.append("fixture manifest differs")
    if baseline["runs_per_case"] != candidate["runs_per_case"]:
        failures.append("runs per case differ")
    if baseline["cold_start_state"] != candidate["cold_start_state"]:
        failures.append("cold-start state differs")
    if baseline["cold_start_state"] == "unloaded":
        if candidate["cold_start"]["status"] not in SUCCESS_STATUSES:
            failures.append("cold-start request failed")
        if candidate["cold_start"]["duration_ms"] > baseline["cold_start"]["duration_ms"]:
            failures.append("cold-start latency regressed")
    for operation, baseline_operation in baseline["operations"].items():
        candidate_operation = candidate["operations"].get(operation)
        if candidate_operation is None:
            failures.append(f"missing operation: {operation}")
            continue
        baseline_summary, candidate_summary = (
            baseline_operation["summary"],
            candidate_operation["summary"],
        )
        for metric in ("p50_ms", "p95_ms"):
            if (
                candidate_summary[metric] is None
                or candidate_summary[metric] > baseline_summary[metric]
            ):
                failures.append(f"{operation} {metric} regressed")
        if candidate_summary["requests"] != baseline_summary["requests"]:
            failures.append(f"{operation} request count differs")
        if candidate_summary["successes"] < baseline_summary["successes"]:
            failures.append(f"{operation} success count regressed")
        critical_fields = baseline_operation.get("critical_fields", CRITICAL_FIELDS[operation])
        for field in critical_fields:
            baseline_field = baseline_summary["fields"].get(field, {})
            candidate_field = candidate_summary["fields"].get(field, {})
            if candidate_field.get("scored", 0) < baseline_field.get("scored", 0):
                failures.append(f"{operation} {field} coverage regressed")
            if candidate_field.get("accuracy", 0) < baseline_field.get("accuracy", 0):
                failures.append(f"{operation} {field} accuracy regressed")
    return {"accepted": not failures, "failures": failures}


def write_json(path: Path, value: dict[str, Any]) -> None:
    private_directory(path.parent)
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


def private_benchmark_path(settings: Settings, path: Path) -> Path:
    root = (settings.data_dir / "benchmarks").resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(
            "Benchmark fixtures and reports must be stored beneath DATA_DIR/benchmarks"
        )
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser(description="Private Bourbon Book AI benchmark tooling")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Snapshot one owner's bottles and photos")
    export.add_argument("--owner", required=True, help="Exact owner ID or username")
    export.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run", help="Run a live provider against a private fixture")
    run.add_argument("--fixture", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument(
        "--live",
        action="store_true",
        help="Acknowledge that this command calls the configured local Ollama service.",
    )
    run.add_argument(
        "--operations", choices=("photo", "name"), nargs="+", default=("photo", "name")
    )
    run.add_argument("--runs", type=int, default=3)
    run.add_argument(
        "--preprocess-revision",
        default=PHOTO_PREPROCESS_REVISION,
        help="A non-secret identifier for the image preprocessing configuration used by this run.",
    )
    run.add_argument(
        "--cold-start-state",
        choices=("unloaded", "uncontrolled"),
        default="uncontrolled",
        help="Use unloaded only after deliberately unloading/restarting the selected local model.",
    )
    compare = commands.add_parser("compare", help="Require a candidate to meet a baseline")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    upgrade = commands.add_parser(
        "upgrade-report", help="Upgrade a legacy report for inspection; it remains incomparable"
    )
    upgrade.add_argument("--input", type=Path, required=True)
    upgrade.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "export":
            settings = Settings.from_env()
            manifest = export_fixture(
                settings, args.owner, private_benchmark_path(settings, args.output)
            )
            print(
                json.dumps(
                    {
                        "case_count": manifest["case_count"],
                        "manifest_sha256": manifest["manifest_sha256"],
                    }
                )
            )
        elif args.command == "run":
            if args.runs < 1:
                raise ValueError("--runs must be positive")
            if not args.live:
                raise ValueError("Benchmark runs call Ollama; pass --live to proceed")
            settings = local_benchmark_settings(Settings.from_env())
            ensure_local_benchmark_settings(settings)
            report = asyncio.run(
                run_fixture(
                    private_benchmark_path(settings, args.fixture),
                    settings,
                    tuple(args.operations),
                    args.runs,
                    args.cold_start_state,
                    preprocess_revision=args.preprocess_revision,
                )
            )
            write_json(private_benchmark_path(settings, args.output), report)
            print(
                json.dumps(
                    {
                        operation: value["summary"]
                        for operation, value in report["operations"].items()
                    },
                    indent=2,
                )
            )
        elif args.command == "upgrade-report":
            settings = Settings.from_env()
            source = private_benchmark_path(settings, args.input)
            destination = private_benchmark_path(settings, args.output)
            upgraded = upgrade_report(json.loads(source.read_text()))
            write_json(destination, upgraded)
            print(
                json.dumps(
                    {
                        "schema_version": upgraded["schema_version"],
                        "report_contract": upgraded["report_contract"],
                        "migration": upgraded.get("migration"),
                    }
                )
            )
        else:
            result = compare_reports(
                json.loads(args.baseline.read_text()), json.loads(args.candidate.read_text())
            )
            print(json.dumps(result, indent=2))
            if not result["accepted"]:
                raise SystemExit(1)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
