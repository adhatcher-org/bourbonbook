import argparse
import asyncio
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import bourbonbook.benchmark_cli as benchmark_cli
from bourbonbook.benchmark_cli import (
    as_number,
    compare_reports,
    export_fixture,
    json_digest,
    load_fixture,
    normalize,
    percentile,
    private_benchmark_path,
    private_directory,
    run_fixture,
    safe_photo,
    sha256_path,
    summarize,
    utc_now,
    write_json,
)


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


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class FakeSession:
    def __init__(self, bottles=None, owner=None) -> None:
        self.bottles = bottles or []
        self.owner = owner
        self.get_calls = []
        self.scalar_calls = []

    def scalars(self, query):
        self.scalar_calls.append(query)
        return self.bottles

    def get(self, model, pk):
        self.get_calls.append((model, pk))
        return self.owner if pk == self.owner.id else None

    def scalar(self, query):
        self.scalar_calls.append(query)
        return self.owner


class FakeDatabase:
    def __init__(self, settings, session: FakeSession) -> None:
        self.settings = settings
        self.engine = FakeEngine()
        self._session = session

    @contextmanager
    def session_factory(self):
        yield self._session


def make_bottle(**overrides):
    data = {
        "name": "W.L. Weller Full Proof",
        "brand": "Weller",
        "release": "Full Proof",
        "edition": None,
        "spirit_type": "bourbon",
        "distilled_by": "Sazerac",
        "mash_bill": "wheated bourbon",
        "proof": 114,
        "abv": 57,
        "size": "750ml",
        "age_statement": None,
        "barrel_number": None,
        "bottle_number": None,
        "warehouse": None,
        "floor": None,
        "status": "Opened",
        "fill_level": 50,
        "msrp": 99.99,
        "photo_name": "sample.jpg",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_settings(tmp_path: Path, *, provider: str = "openai") -> SimpleNamespace:
    return SimpleNamespace(
        data_dir=tmp_path,
        analysis_provider=provider,
        openai_model="gpt-4.1",
        ollama_model="qwen2.5vl:3b",
    )


def make_manifest(case, *, manifest_sha="manifest-sha") -> dict:
    return {
        "schema_version": benchmark_cli.FIXTURE_SCHEMA_VERSION,
        "created_at": "2026-07-13T12:00:00+00:00",
        "case_count": 1,
        "scored_fields": list(benchmark_cli.PHOTO_FIELDS),
        "critical_fields": list(benchmark_cli.CRITICAL_FIELDS),
        "cases": [case],
        "manifest_sha256": manifest_sha,
    }


def make_case(expected=None, *, case_id="case-001", photo_file="photos/case-001.jpg"):
    return {
        "case_id": case_id,
        "photo_file": photo_file,
        "photo_sha256": "photo-sha",
        "expected": expected
        or {
            "name": "W.L. Weller Full Proof",
            "brand": "Weller",
            "release": "Full Proof",
            "edition": None,
            "spirit_type": "bourbon",
            "distilled_by": "Sazerac",
            "mash_bill": "wheated bourbon",
            "proof": 114,
            "abv": 57,
            "size": "750ml",
            "age_statement": None,
            "barrel_number": None,
            "bottle_number": None,
            "warehouse": None,
            "floor": None,
            "status": "Opened",
            "fill_level": 50,
        },
        "price_reference": {"msrp": 99.99},
    }


@pytest.mark.parametrize(
    ("percent", "expected"),
    [(0.5, 2.0), (0.95, 2.9), (0.0, 1.0)],
)
def test_helpers_cover_basic_math_and_formatting(percent: float, expected: float) -> None:
    assert percentile([1.0, 2.0, 3.0], percent) == expected


def test_helpers_cover_datetime_digest_normalize_and_numbers(tmp_path: Path) -> None:
    now = utc_now()
    datetime.fromisoformat(now)

    assert json_digest({"b": 2, "a": 1}) == json_digest({"a": 1, "b": 2})
    assert normalize("W.L. Weller’s 114 proof!") == "w l wellers 114 proof"
    assert as_number("114 proof") == 114.0
    assert percentile([], 0.5) is None

    path = tmp_path / "payload.json"
    private_directory(path.parent)
    write_json(path, {"hello": "world"})
    assert json.loads(path.read_text()) == {"hello": "world"}


def test_safe_photo_and_owner_lookup(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    photo = uploads / "sample.jpg"
    photo.write_bytes(b"photo-bytes")

    assert safe_photo(uploads, "sample.jpg") == photo

    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    symlink = uploads / "linked.jpg"
    symlink.symlink_to(outside)
    with pytest.raises(ValueError, match="unsafe"):
        safe_photo(uploads, "linked.jpg")

    with pytest.raises(ValueError, match="unsafe"):
        safe_photo(uploads, "../escape.jpg")

    owner = SimpleNamespace(id=7, username="aaron")
    session = FakeSession(owner=owner)
    assert benchmark_cli.owner_for(session, "7") == owner
    assert benchmark_cli.owner_for(session, "aaron") == owner


def test_export_fixture_creates_private_manifest_and_copies_photos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    source = uploads / "sample.jpg"
    source.write_bytes(b"photo-bytes")
    bottle = make_bottle()
    owner = SimpleNamespace(id=1, username="aaron")
    session = FakeSession(bottles=[bottle], owner=owner)
    database = FakeDatabase(settings, session)

    monkeypatch.setattr(benchmark_cli, "Database", lambda settings: database)

    manifest = export_fixture(settings, "1", tmp_path / "benchmarks" / "fixture")

    copied = tmp_path / "benchmarks" / "fixture" / "photos" / "case-001.jpg"
    assert copied.exists()
    assert copied.read_bytes() == b"photo-bytes"
    assert copied.stat().st_mode & 0o777 == 0o600
    assert manifest["case_count"] == 1
    assert manifest["critical_fields"] == list(benchmark_cli.CRITICAL_FIELDS)
    assert manifest["scored_fields"] == list(benchmark_cli.PHOTO_FIELDS)
    assert manifest["cases"][0]["expected"]["name"] == bottle.name
    assert manifest["cases"][0]["price_reference"] == {"msrp": bottle.msrp}
    assert database.engine.disposed

    loaded = load_fixture(tmp_path / "benchmarks" / "fixture")
    assert loaded["manifest_sha256"] == manifest["manifest_sha256"]


def test_export_fixture_rejects_non_empty_destination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = make_settings(tmp_path)
    destination = tmp_path / "benchmarks" / "fixture"
    destination.mkdir(parents=True)
    (destination / "existing.txt").write_text("data")

    monkeypatch.setattr(
        benchmark_cli, "Database", lambda settings: FakeDatabase(settings, FakeSession())
    )

    with pytest.raises(ValueError, match="new or empty"):
        export_fixture(settings, "1", destination)


def test_load_fixture_rejects_changed_and_corrupted_assets(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    photos = fixture / "photos"
    photos.mkdir(parents=True)
    photo = photos / "case-001.jpg"
    photo.write_bytes(b"photo-bytes")
    manifest = make_manifest(make_case())
    manifest["cases"][0]["photo_sha256"] = sha256_path(photo)
    payload = dict(manifest)
    payload.pop("manifest_sha256")
    manifest["manifest_sha256"] = json_digest(payload)
    (fixture / "manifest.json").write_text(json.dumps(manifest))

    loaded = load_fixture(fixture)
    assert loaded["manifest_sha256"] == manifest["manifest_sha256"]

    broken_manifest = make_manifest(make_case(), manifest_sha="wrong")
    (fixture / "manifest.json").write_text(json.dumps(broken_manifest))
    with pytest.raises(ValueError, match="digest"):
        load_fixture(fixture)

    broken_manifest = make_manifest(make_case())
    broken_manifest["cases"][0]["photo_sha256"] = sha256_path(photo)
    payload = dict(broken_manifest)
    payload.pop("manifest_sha256")
    broken_manifest["manifest_sha256"] = json_digest(payload)
    (fixture / "manifest.json").write_text(json.dumps(broken_manifest))
    photo.write_bytes(b"different")
    with pytest.raises(ValueError, match="photo digest"):
        load_fixture(fixture)


def test_run_fixture_builds_photo_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path, provider="openai")
    fixture = tmp_path / "fixture"
    photos = fixture / "photos"
    photos.mkdir(parents=True)
    photo = photos / "case-001.jpg"
    photo.write_bytes(b"photo-bytes")
    manifest = make_manifest(make_case())
    manifest["cases"][0]["photo_sha256"] = sha256_path(photo)
    payload = dict(manifest)
    payload.pop("manifest_sha256")
    manifest["manifest_sha256"] = json_digest(payload)
    (fixture / "manifest.json").write_text(json.dumps(manifest))

    async def fake_analyze_bottle(path, settings):
        return {**make_case()["expected"], "status": "Opened"}, "complete"

    monkeypatch.setattr(benchmark_cli, "analyze_bottle", fake_analyze_bottle)
    monkeypatch.setattr(benchmark_cli.time, "perf_counter", iter([1.0, 1.4, 2.0, 2.2]).__next__)

    report = asyncio.run(run_fixture(fixture, settings, ("photo",), 1, "unloaded"))

    assert report["provider"] == "openai"
    assert report["model"] == settings.openai_model
    assert report["cold_start"]["operation"] == "photo"
    assert report["cold_start"]["status"] == "complete"
    assert report["operations"]["photo"]["summary"]["requests"] == 1
    assert report["operations"]["photo"]["summary"]["successes"] == 1
    assert report["operations"]["photo"]["summary"]["fields"]["name"]["scored"] == 1


def test_run_fixture_builds_name_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = make_settings(tmp_path, provider="ollama")
    fixture = tmp_path / "fixture"
    photos = fixture / "photos"
    photos.mkdir(parents=True)
    photo = photos / "case-001.jpg"
    photo.write_bytes(b"photo-bytes")
    manifest = make_manifest(make_case())
    manifest["cases"][0]["photo_sha256"] = sha256_path(photo)
    payload = dict(manifest)
    payload.pop("manifest_sha256")
    manifest["manifest_sha256"] = json_digest(payload)
    (fixture / "manifest.json").write_text(json.dumps(manifest))

    async def fake_analyze_name(name, settings):
        return {**make_case()["expected"], "status": "Opened"}, "complete"

    monkeypatch.setattr(benchmark_cli, "analyze_bottle_name", fake_analyze_name)
    monkeypatch.setattr(benchmark_cli.time, "perf_counter", iter([1.0, 1.1, 2.0, 2.3]).__next__)

    report = asyncio.run(run_fixture(fixture, settings, ("name",), 1, "uncontrolled"))

    assert report["provider"] == "ollama"
    assert report["model"] == settings.ollama_model
    assert report["cold_start"]["operation"] == "name"
    assert report["operations"]["name"]["summary"]["requests"] == 1


def test_summarize_and_compare_reports_cover_regressions() -> None:
    samples = [
        {
            "duration_ms": 100.0,
            "status": "complete",
            "comparisons": {
                "name": {"match": True},
                "brand": {"match": True},
                "proof": {"match": True},
                "abv": {"match": True},
                "size": {"match": True},
                "status": {"match": True},
                "fill_level": {"match": True},
            },
        },
        {
            "duration_ms": 140.0,
            "status": "failed",
            "comparisons": {
                "name": {"match": False},
                "brand": {"match": False},
                "proof": {"match": False},
                "abv": {"match": False},
                "size": {"match": False},
                "status": {"match": False},
                "fill_level": {"match": False},
            },
        },
    ]
    summary = summarize(samples, list(benchmark_cli.CRITICAL_FIELDS))
    assert summary["requests"] == 2
    assert summary["successes"] == 1
    assert summary["p50_ms"] == 120.0
    assert summary["p95_ms"] == 138.0
    assert summary["overall_accuracy"] == 0.5
    assert summary["fields"]["name"]["scored"] == 2

    photo_summary = report(p50=100, p95=120, accuracy=1.0)["operations"]["photo"]["summary"]
    baseline = {
        "fixture_manifest_sha256": "fixture",
        "runs_per_case": 3,
        "cold_start_state": "unloaded",
        "cold_start": {"status": "complete", "duration_ms": 100},
        "operations": {
            "photo": {"summary": photo_summary},
            "name": {"summary": photo_summary},
        },
    }
    candidate = {
        "fixture_manifest_sha256": "different",
        "runs_per_case": 2,
        "cold_start_state": "unloaded",
        "cold_start": {"status": "failed", "duration_ms": 101},
        "operations": {
            "photo": {
                "summary": {
                    "requests": 2,
                    "successes": 1,
                    "p50_ms": 101,
                    "p95_ms": 121,
                    "fields": {
                        field: {"scored": 2, "accuracy": 0.5}
                        for field in benchmark_cli.CRITICAL_FIELDS
                    },
                }
            }
        },
    }

    failures = compare_reports(baseline, candidate)["failures"]
    assert "fixture manifest differs" in failures
    assert "runs per case differ" in failures
    assert "cold-start request failed" in failures
    assert "photo p50_ms regressed" in failures
    assert "photo p95_ms regressed" in failures
    assert "photo request count differs" in failures
    assert "photo success count regressed" in failures
    assert "missing operation: name" in failures


def test_private_benchmark_path_and_main_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = make_settings(tmp_path)
    allowed = tmp_path / "benchmarks" / "fixtures" / "fixture.json"
    disallowed = tmp_path / "elsewhere.json"
    allowed.parent.mkdir(parents=True)

    assert private_benchmark_path(settings, allowed) == allowed.resolve()
    with pytest.raises(ValueError, match="beneath"):
        private_benchmark_path(settings, disallowed)

    export_manifest = {"case_count": 1, "manifest_sha256": "export-sha"}
    run_report = {
        "operations": {"photo": {"summary": {"requests": 1}}, "name": {"summary": {"requests": 1}}}
    }
    compare_result = {"accepted": True, "failures": []}
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    baseline.write_text(json.dumps({"hello": "baseline"}))
    candidate.write_text(json.dumps({"hello": "candidate"}))

    def export_args():
        return SimpleNamespace(command="export", owner="1", output=allowed)

    def run_args():
        return SimpleNamespace(
            command="run",
            fixture=allowed,
            output=tmp_path / "reports" / "candidate.json",
            provider="ollama",
            operations=("photo", "name"),
            runs=3,
            cold_start_state="unloaded",
        )

    def compare_args():
        return SimpleNamespace(command="compare", baseline=baseline, candidate=candidate)

    monkeypatch.setattr(benchmark_cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(
        benchmark_cli,
        "export_fixture",
        lambda settings, owner, output: export_manifest,
    )

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: export_args())
    monkeypatch.setattr(benchmark_cli, "private_benchmark_path", lambda settings, path: path)
    benchmark_cli.main()
    assert json.loads(capsys.readouterr().out) == export_manifest

    written = {}

    def fake_write_json(path, value):
        written["path"] = path
        written["value"] = value

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: run_args())
    monkeypatch.setattr(
        benchmark_cli.asyncio,
        "run",
        lambda coro: (coro.close(), run_report)[1],
    )
    monkeypatch.setattr(benchmark_cli, "write_json", fake_write_json)
    benchmark_cli.main()
    assert written["path"] == tmp_path / "reports" / "candidate.json"
    assert written["value"] == run_report
    assert json.loads(capsys.readouterr().out) == {
        "photo": {"requests": 1},
        "name": {"requests": 1},
    }

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: compare_args())
    monkeypatch.setattr(
        benchmark_cli,
        "compare_reports",
        lambda baseline_data, candidate_data: compare_result,
    )
    benchmark_cli.main()
    assert json.loads(capsys.readouterr().out) == compare_result

    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(
            command="run",
            fixture=allowed,
            output=tmp_path / "reports" / "candidate.json",
            provider="ollama",
            operations=("photo",),
            runs=0,
            cold_start_state="unloaded",
        ),
    )
    monkeypatch.setattr(
        argparse.ArgumentParser,
        "error",
        lambda self, message: (_ for _ in ()).throw(SystemExit(message)),
    )
    with pytest.raises(SystemExit, match="positive"):
        benchmark_cli.main()
