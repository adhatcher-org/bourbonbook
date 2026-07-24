"""Deterministically select local model roles from P2-00 benchmark reports.

This command intentionally reads report JSON only.  It never initializes a provider client,
contacts Ollama, or changes application model defaults.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from bourbonbook.benchmark_cli import (
    REPORT_CONTRACT_VERSION,
    REPORT_SCHEMA_VERSION,
    compare_reports,
    write_json,
)

EVALUATION_SCHEMA_VERSION = 1
ROLE_CANDIDATES = {
    "photo": ("gemma4:26b",),
    "name": ("qwen3:30b-a3b", "qwen3.6:35b"),
}
EXPECTED_MODELS = frozenset(model for models in ROLE_CANDIDATES.values() for model in models)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path}")
    return value


def runtime_evidence_errors(evidence: Any, *, model: str | None = None) -> list[str]:
    """Check the bounded P2-00 runtime evidence recorded with a report."""
    errors: list[str] = []
    if not isinstance(evidence, dict):
        return ["runtime_evidence is required"]
    gpu = evidence.get("gpu")
    if not isinstance(gpu, list) or not any(
        isinstance(item, dict) and "rtx 3090" in str(item.get("name", "")).lower() for item in gpu
    ):
        errors.append("runtime_evidence must identify an RTX 3090")
    ollama = evidence.get("ollama")
    if not isinstance(ollama, dict) or not ollama.get("version"):
        errors.append("runtime_evidence must record an Ollama version")
    elif model is not None:
        residents = ollama.get("resident_models")
        if not isinstance(residents, list) or not any(
            isinstance(item, dict) and item.get("name") == model and item.get("digest")
            for item in residents
        ):
            errors.append("runtime_evidence must record the evaluated model digest")
    return errors


def report_errors(report: dict[str, Any], *, model: str, role: str) -> list[str]:
    """Return report-contract errors without inspecting a live runtime."""
    errors: list[str] = []
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        errors.append("report must use P2-00 schema v2")
    if report.get("report_contract") != REPORT_CONTRACT_VERSION:
        errors.append("report must use the P2-00 local-only report contract")
    if report.get("provider") != "ollama":
        errors.append("report provider must be ollama")
    operations = report.get("operations")
    if not isinstance(operations, dict) or set(operations) != {role}:
        errors.append(f"{model} is restricted to the {role} operation")
    if report.get("model") != {role: model}:
        errors.append(f"report model must be exactly {{'{role}': '{model}'}}")
    errors.extend(runtime_evidence_errors(report.get("runtime_evidence"), model=model))
    evidence = report.get("runtime_evidence")
    configured = evidence.get("configured_models") if isinstance(evidence, dict) else None
    if not isinstance(configured, dict) or configured.get(role) != model:
        errors.append("runtime_evidence configured model does not match the trial")
    return errors


def baseline_errors(report: Any, role: str) -> list[str]:
    if not isinstance(report, dict):
        return [f"missing {role} baseline report"]
    errors: list[str] = []
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        errors.append("baseline must use P2-00 schema v2")
    if report.get("report_contract") != REPORT_CONTRACT_VERSION:
        errors.append("baseline must use the P2-00 local-only report contract")
    if report.get("provider") != "ollama":
        errors.append("baseline provider must be ollama")
    if not isinstance(report.get("operations"), dict) or role not in report["operations"]:
        errors.append(f"baseline is missing the {role} operation")
    models = report.get("model")
    baseline_model = models.get(role) if isinstance(models, dict) else None
    if not isinstance(baseline_model, str) or not baseline_model:
        errors.append(f"baseline is missing the configured {role} model")
    errors.extend(runtime_evidence_errors(report.get("runtime_evidence"), model=baseline_model))
    evidence = report.get("runtime_evidence")
    configured = evidence.get("configured_models") if isinstance(evidence, dict) else None
    if not isinstance(configured, dict) or configured.get(role) != baseline_model:
        errors.append("baseline runtime_evidence configured model does not match the baseline")
    return errors


def operation_report(report: dict[str, Any], role: str) -> dict[str, Any]:
    """Project a baseline to the evaluated operation before applying P2-00's gate."""
    projected = dict(report)
    operations = report.get("operations", {})
    projected["operations"] = {role: operations.get(role)}
    model = report.get("model", {})
    projected["model"] = {role: model.get(role)} if isinstance(model, dict) else {}
    return projected


def comparison_errors(baseline: dict[str, Any], report: dict[str, Any], role: str) -> list[str]:
    """Apply P2-00's frozen gate without letting malformed captures abort the record."""
    try:
        comparison = compare_reports(operation_report(baseline, role), report)
    except (KeyError, TypeError, ValueError) as exc:
        return [f"acceptance gate could not evaluate captured report: {exc}"]
    return [f"acceptance gate: {failure}" for failure in comparison["failures"]]


def evaluate_role_selection(config: dict[str, Any]) -> dict[str, Any]:
    """Evaluate captured P2-00 reports and return accepted/rejected/incomplete records."""
    if config.get("schema_version") != EVALUATION_SCHEMA_VERSION:
        raise ValueError("Unsupported model-evaluation configuration schema")
    baselines = config.get("baselines")
    candidates = config.get("candidates")
    if not isinstance(baselines, dict) or not isinstance(candidates, list):
        raise ValueError("Configuration requires baselines and candidates")

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("Each candidate must be an object")
        model, role, report = candidate.get("model"), candidate.get("role"), candidate.get("report")
        if not isinstance(model, str) or not isinstance(role, str) or not isinstance(report, dict):
            raise ValueError("Each candidate requires model, role, and report")
        if model in seen:
            raise ValueError(f"Duplicate model candidate: {model}")
        seen.add(model)
        expected_roles = [
            expected_role for expected_role, models in ROLE_CANDIDATES.items() if model in models
        ]
        if not expected_roles:
            records.append(
                {
                    "model": model,
                    "role": role,
                    "outcome": "rejected",
                    "reasons": [f"{model} is excluded from P2-01 application roles"],
                }
            )
            continue
        errors = [] if role in expected_roles else [f"{model} is not eligible for {role}"]
        errors.extend(report_errors(report, model=model, role=role))
        baseline = baselines.get(role)
        errors.extend(baseline_errors(baseline, role))
        if not errors:
            errors.extend(comparison_errors(baseline, report, role))
        records.append(
            {
                "model": model,
                "role": role,
                "outcome": "accepted" if not errors else "rejected",
                "reasons": errors,
            }
        )

    missing = sorted(EXPECTED_MODELS - seen)
    for model in missing:
        role = next(role for role, models in ROLE_CANDIDATES.items() if model in models)
        records.append(
            {
                "model": model,
                "role": role,
                "outcome": "incomplete",
                "reasons": ["required candidate report was not provided"],
            }
        )
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "source": "captured-p2-00-v2-reports",
        "roles": ROLE_CANDIDATES,
        "outcome": "complete" if not missing else "incomplete",
        "records": sorted(records, key=lambda record: (record["role"], record["model"])),
    }


def load_config(path: Path) -> dict[str, Any]:
    """Resolve report paths in the private, versioned evaluation configuration."""
    config = read_json(path)
    baselines = config.get("baselines", {})
    candidates = config.get("candidates", [])
    if not isinstance(baselines, dict) or not isinstance(candidates, list):
        return config
    resolved = dict(config)
    resolved["baselines"] = {
        role: read_json((path.parent / report_path).resolve())
        if isinstance(report_path, str)
        else report_path
        for role, report_path in baselines.items()
    }
    resolved["candidates"] = [
        {
            **candidate,
            "report": read_json((path.parent / candidate["report"]).resolve())
            if isinstance(candidate, dict) and isinstance(candidate.get("report"), str)
            else candidate.get("report")
            if isinstance(candidate, dict)
            else None,
        }
        for candidate in candidates
    ]
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate captured P2-00 local model trial reports"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = evaluate_role_selection(load_config(args.config))
        write_json(args.output, result)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
