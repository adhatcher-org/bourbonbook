import argparse
import json
from pathlib import Path

import pytest

import bourbonbook.model_evaluation as evaluation


def summary() -> dict:
    fields = {
        field: {"scored": 3, "matched": 3, "accuracy": 1.0}
        for field in ("name", "brand", "proof", "abv", "size", "status", "fill_level")
    }
    return {"requests": 3, "successes": 3, "p50_ms": 100, "p95_ms": 120, "fields": fields}


def report(model: str, role: str, **overrides) -> dict:
    value = {
        "schema_version": 2,
        "report_contract": "benchmark-v2-local-only",
        "fixture_manifest_sha256": "frozen-fixture",
        "runs_per_case": 3,
        "cold_start_state": "unloaded",
        "cold_start": {"status": "complete", "duration_ms": 50},
        "provider": "ollama",
        "model": {role: model},
        "runtime_evidence": {
            "gpu": [{"name": "NVIDIA RTX 3090", "memory_total_mib": "24576"}],
            "ollama": {
                "version": "0.12.0",
                "resident_models": [{"name": model, "digest": "sha256:trial"}],
            },
            "configured_models": {role: model},
        },
        "operations": {role: {"summary": summary()}},
    }
    value.update(overrides)
    return value


def config(*candidates: dict) -> dict:
    return {
        "schema_version": evaluation.EVALUATION_SCHEMA_VERSION,
        "baselines": {
            "photo": report("baseline-vision", "photo"),
            "name": report("baseline-text", "name"),
        },
        "candidates": list(candidates),
    }


def candidate(model: str, role: str) -> dict:
    return {"model": model, "role": role, "report": report(model, role)}


def test_evaluate_role_selection_accepts_all_expected_role_trials() -> None:
    result = evaluation.evaluate_role_selection(
        config(
            candidate("gemma4:26b", "photo"),
            candidate("qwen3:30b-a3b", "name"),
            candidate("qwen3.6:35b", "name"),
        )
    )

    assert result["outcome"] == "complete"
    assert result["source"] == "captured-p2-00-v2-reports"
    assert {record["outcome"] for record in result["records"]} == {"accepted"}
    assert result["roles"] == evaluation.ROLE_CANDIDATES


def test_evaluate_role_selection_rejects_wrong_role_missing_evidence_and_regression() -> None:
    bad = candidate("gemma4:26b", "name")
    bad["report"]["runtime_evidence"] = {"gpu": []}
    bad["report"]["operations"]["name"]["summary"]["p95_ms"] = 121
    result = evaluation.evaluate_role_selection(config(bad))
    record = next(record for record in result["records"] if record["model"] == "gemma4:26b")

    assert result["outcome"] == "incomplete"
    assert record["outcome"] == "rejected"
    assert "not eligible" in " ".join(record["reasons"])
    assert "RTX 3090" in " ".join(record["reasons"])
    assert any(record["outcome"] == "incomplete" for record in result["records"])


@pytest.mark.parametrize(
    ("defect", "expected_reason"),
    [
        ("schema", "schema v2"),
        ("contract", "local-only report contract"),
        ("gpu", "RTX 3090"),
        ("ollama", "Ollama version"),
        ("digest", "evaluated model digest"),
        ("configured", "configured model does not match"),
    ],
)
def test_evaluate_role_selection_rejects_each_required_candidate_evidence(
    defect: str, expected_reason: str
) -> None:
    trial = candidate("gemma4:26b", "photo")
    captured = trial["report"]
    if defect == "schema":
        captured["schema_version"] = 1
    elif defect == "contract":
        captured["report_contract"] = "legacy-v1"
    elif defect == "gpu":
        captured["runtime_evidence"]["gpu"] = []
    elif defect == "ollama":
        captured["runtime_evidence"]["ollama"] = {}
    elif defect == "digest":
        captured["runtime_evidence"]["ollama"]["resident_models"] = []
    else:
        captured["runtime_evidence"]["configured_models"] = {"photo": "wrong-model"}

    result = evaluation.evaluate_role_selection(config(trial))
    record = next(record for record in result["records"] if record["model"] == "gemma4:26b")

    assert record["outcome"] == "rejected"
    assert expected_reason in " ".join(record["reasons"])


def test_evaluate_role_selection_records_excluded_coder_and_missing_candidates() -> None:
    result = evaluation.evaluate_role_selection(config(candidate("qwen3-coder:30b", "name")))
    coder = next(record for record in result["records"] if record["model"] == "qwen3-coder:30b")

    assert coder == {
        "model": "qwen3-coder:30b",
        "role": "name",
        "outcome": "rejected",
        "reasons": ["qwen3-coder:30b is excluded from P2-01 application roles"],
    }
    assert len([record for record in result["records"] if record["outcome"] == "incomplete"]) == 3


def test_evaluate_role_selection_rejects_invalid_config_and_baseline() -> None:
    with pytest.raises(ValueError, match="schema"):
        evaluation.evaluate_role_selection({})
    with pytest.raises(ValueError, match="Duplicate"):
        evaluation.evaluate_role_selection(
            config(candidate("gemma4:26b", "photo"), candidate("gemma4:26b", "photo"))
        )

    invalid = config(candidate("gemma4:26b", "photo"))
    invalid["baselines"]["photo"] = {"schema_version": 1}
    result = evaluation.evaluate_role_selection(invalid)
    record = next(record for record in result["records"] if record["model"] == "gemma4:26b")
    assert record["outcome"] == "rejected"
    assert "baseline must use P2-00 schema v2" in record["reasons"]


def test_evaluate_role_selection_rejects_missing_baseline_model_runtime_evidence() -> None:
    invalid = config(candidate("gemma4:26b", "photo"))
    baseline = invalid["baselines"]["photo"]
    baseline["runtime_evidence"]["ollama"]["resident_models"] = []
    baseline["runtime_evidence"]["configured_models"] = {"photo": "wrong-model"}

    result = evaluation.evaluate_role_selection(invalid)
    record = next(record for record in result["records"] if record["model"] == "gemma4:26b")

    assert record["outcome"] == "rejected"
    assert "evaluated model digest" in " ".join(record["reasons"])
    assert "configured model does not match the baseline" in " ".join(record["reasons"])


def test_evaluate_role_selection_rejects_incomplete_v2_report_without_crashing() -> None:
    incomplete = candidate("gemma4:26b", "photo")
    incomplete["report"].pop("fixture_manifest_sha256")

    result = evaluation.evaluate_role_selection(config(incomplete))
    record = next(record for record in result["records"] if record["model"] == "gemma4:26b")

    assert record["outcome"] == "rejected"
    assert "acceptance gate could not evaluate captured report" in " ".join(record["reasons"])


def test_evaluate_role_selection_applies_the_p2_00_frozen_comparison_gate() -> None:
    slower = candidate("gemma4:26b", "photo")
    slower["report"]["operations"]["photo"]["summary"]["p95_ms"] = 121

    result = evaluation.evaluate_role_selection(config(slower))
    record = next(record for record in result["records"] if record["model"] == "gemma4:26b")

    assert record["outcome"] == "rejected"
    assert "acceptance gate: photo p95_ms regressed" in record["reasons"]


def test_load_config_and_main_write_offline_evaluation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "evaluation.json"
    photo_baseline = tmp_path / "photo-baseline.json"
    name_baseline = tmp_path / "name-baseline.json"
    photo_trial = tmp_path / "photo-trial.json"
    for path, value in (
        (photo_baseline, report("baseline-vision", "photo")),
        (name_baseline, report("baseline-text", "name")),
        (photo_trial, report("gemma4:26b", "photo")),
    ):
        path.write_text(json.dumps(value))
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "baselines": {"photo": photo_baseline.name, "name": name_baseline.name},
                "candidates": [
                    {"model": "gemma4:26b", "role": "photo", "report": photo_trial.name}
                ],
            }
        )
    )
    loaded = evaluation.load_config(config_path)
    assert loaded["candidates"][0]["report"]["model"] == {"photo": "gemma4:26b"}

    output = tmp_path / "result.json"
    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda self: argparse.Namespace(config=config_path, output=output),
    )
    evaluation.main()
    assert json.loads(output.read_text())["outcome"] == "incomplete"
    assert json.loads(capsys.readouterr().out)["schema_version"] == 1


def test_main_rejects_malformed_captured_json_without_writing_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "evaluation.json"
    output = tmp_path / "result.json"
    config_path.write_text("{not-json")
    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda self: argparse.Namespace(config=config_path, output=output),
    )

    with pytest.raises(SystemExit, match="2"):
        evaluation.main()

    assert not output.exists()
    assert "Expecting property name" in capsys.readouterr().err
