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
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from bourbonbook.analysis import FIELDS, analyze_bottle, analyze_bottle_name
from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.models import Bottle, User

FIXTURE_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
PHOTO_FIELDS = tuple(field for field in FIELDS if field != "msrp")
CRITICAL_FIELDS = ("name", "brand", "proof", "abv", "size", "status", "fill_level")
NUMERIC_FIELDS = {"proof", "abv", "fill_level"}


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
        "critical_fields": list(CRITICAL_FIELDS),
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
    expected_text, actual_text = normalize(expected), normalize(actual)
    if field == "name":
        return bool(expected_text) and (
            set(expected_text.split()) <= set(actual_text.split())
            or set(actual_text.split()) <= set(expected_text.split())
        )
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
        "successes": sum(sample["status"] == "complete" for sample in samples),
        "p50_ms": percentile(timings, 0.5),
        "p95_ms": percentile(timings, 0.95),
        "max_ms": max(timings) if timings else None,
        "overall_accuracy": round(matched / scored, 4) if scored else 0.0,
        "fields": field_summary,
    }


async def run_fixture(
    fixture: Path,
    settings: Settings,
    operations: tuple[str, ...],
    runs: int,
    cold_start_state: str,
) -> dict[str, Any]:
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
                    if expected not in (None, "")
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
        "created_at": utc_now(),
        "fixture_manifest_sha256": manifest["manifest_sha256"],
        "case_count": manifest["case_count"],
        "runs_per_case": runs,
        "cold_start_state": cold_start_state,
        "cold_start": cold_start,
        "provider": settings.analysis_provider,
        "model": settings.openai_model
        if settings.analysis_provider == "openai"
        else settings.ollama_model,
        "operations": {
            operation: {
                "summary": summarize(values, list(manifest["scored_fields"])),
                "samples": values,
            }
            for operation, values in samples.items()
        },
    }


def compare_reports(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    if baseline["fixture_manifest_sha256"] != candidate["fixture_manifest_sha256"]:
        failures.append("fixture manifest differs")
    if baseline["runs_per_case"] != candidate["runs_per_case"]:
        failures.append("runs per case differ")
    if baseline["cold_start_state"] != candidate["cold_start_state"]:
        failures.append("cold-start state differs")
    if baseline["cold_start_state"] == "unloaded":
        if candidate["cold_start"]["status"] != "complete":
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
        for field in CRITICAL_FIELDS:
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
        "--operations", choices=("photo", "name"), nargs="+", default=("photo", "name")
    )
    run.add_argument("--runs", type=int, default=3)
    run.add_argument(
        "--cold-start-state",
        choices=("unloaded", "uncontrolled"),
        default="uncontrolled",
        help="Use unloaded only after deliberately unloading/restarting the selected local model.",
    )
    compare = commands.add_parser("compare", help="Require a candidate to meet a baseline")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
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
            settings = Settings.from_env()
            report = asyncio.run(
                run_fixture(
                    private_benchmark_path(settings, args.fixture),
                    settings,
                    tuple(args.operations),
                    args.runs,
                    args.cold_start_state,
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
