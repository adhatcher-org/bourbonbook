import json
from pathlib import Path

import pytest

from bourbonbook.benchmark_cli import compare_reports, load_fixture, matches


def report(*, p50: float, p95: float, accuracy: float, fixture: str = "fixture") -> dict:
    fields = {
        field: {"scored": 3, "matched": round(3 * accuracy), "accuracy": accuracy}
        for field in ("name", "brand", "proof", "abv", "size", "status", "fill_level")
    }
    summary = {
        "requests": 3,
        "successes": 3,
        "p50_ms": p50,
        "p95_ms": p95,
        "fields": fields,
    }
    return {
        "fixture_manifest_sha256": fixture,
        "runs_per_case": 3,
        "cold_start_state": "unloaded",
        "cold_start": {"status": "complete", "duration_ms": 150},
        "operations": {"photo": {"summary": summary}},
    }


def test_compare_requires_speed_and_critical_accuracy() -> None:
    baseline = report(p50=100, p95=120, accuracy=1.0)

    assert compare_reports(baseline, report(p50=99, p95=119, accuracy=1.0))["accepted"]
    failures = compare_reports(baseline, report(p50=101, p95=119, accuracy=0.99))["failures"]

    assert "photo p50_ms regressed" in failures
    assert "photo name accuracy regressed" in failures


def test_compare_requires_identical_benchmark_configuration() -> None:
    baseline = report(p50=100, p95=120, accuracy=1.0)
    candidate = report(p50=99, p95=119, accuracy=1.0)
    candidate["cold_start_state"] = "uncontrolled"

    assert "cold-start state differs" in compare_reports(baseline, candidate)["failures"]


def test_compare_rejects_lower_success_or_cold_speed() -> None:
    baseline = report(p50=100, p95=120, accuracy=1.0)
    candidate = report(p50=99, p95=119, accuracy=1.0)
    candidate["operations"]["photo"]["summary"]["successes"] = 2
    candidate["cold_start"]["duration_ms"] = 151

    failures = compare_reports(baseline, candidate)["failures"]

    assert "cold-start latency regressed" in failures
    assert "photo success count regressed" in failures


def test_field_matching_handles_name_and_numeric_tolerance() -> None:
    assert matches("name", "W.L. Weller Full Proof", "W.L. Weller Full Proof Kentucky")
    assert matches("proof", 114.0, "114 proof")
    assert matches("fill_level", 40, 50)
    assert not matches("fill_level", 40, 55)


def test_load_fixture_rejects_changed_manifest(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    manifest = {
        "schema_version": 1,
        "created_at": "now",
        "case_count": 0,
        "scored_fields": [],
        "critical_fields": [],
        "cases": [],
        "manifest_sha256": "wrong",
    }
    (fixture / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="digest"):
        load_fixture(fixture)
