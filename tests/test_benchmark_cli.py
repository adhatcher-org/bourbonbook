import argparse
import asyncio
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import bourbonbook.benchmark_cli as benchmark_cli
from bourbonbook.benchmark_cli import (
    as_number,
    canonical_size,
    collect_runtime_evidence,
    compare_reports,
    ensure_local_benchmark_settings,
    export_fixture,
    json_digest,
    load_fixture,
    local_benchmark_settings,
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
from bourbonbook.config import Settings


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


def make_settings(tmp_path: Path, *, provider: str = "ollama") -> SimpleNamespace:
    return SimpleNamespace(
        data_dir=tmp_path,
        analysis_provider=provider,
        openai_api_key=None,
        openai_model="gpt-4.1",
        ollama_model="qwen2.5vl:3b",
        ollama_url="http://ollama.test",
        ollama_vision_model="vision-test",
        ollama_text_model="text-test",
    )


def make_manifest(case, *, manifest_sha="manifest-sha") -> dict:
    return {
        "schema_version": benchmark_cli.FIXTURE_SCHEMA_VERSION,
        "created_at": "2026-07-13T12:00:00+00:00",
        "case_count": 1,
        "scored_fields": list(benchmark_cli.PHOTO_FIELDS),
        "critical_fields": list(benchmark_cli.CRITICAL_FIELDS["photo"]),
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
    assert canonical_size("0.75 L") == 750
    assert canonical_size("75cl") == 750
    assert canonical_size("750") is None
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
    assert manifest["critical_fields"] == list(benchmark_cli.CRITICAL_FIELDS["photo"])
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

    async def fake_analyze_bottle(path, settings):
        return {**make_case()["expected"], "status": "Opened"}, "complete"

    monkeypatch.setattr(benchmark_cli, "analyze_bottle", fake_analyze_bottle)
    monkeypatch.setattr(benchmark_cli.time, "perf_counter", iter([1.0, 1.4, 2.0, 2.2]).__next__)

    report = asyncio.run(
        run_fixture(
            fixture,
            settings,
            ("photo",),
            1,
            "unloaded",
            runtime_evidence={"fake": True},
        )
    )

    assert report["provider"] == "ollama"
    assert report["model"] == {"photo": "vision-test"}
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

    report = asyncio.run(
        run_fixture(
            fixture,
            settings,
            ("name",),
            1,
            "uncontrolled",
            runtime_evidence={"fake": True},
        )
    )

    assert report["provider"] == "ollama"
    assert report["model"] == {"name": "text-test"}
    assert report["cold_start"]["operation"] == "name"
    assert report["operations"]["name"]["summary"]["requests"] == 1
    assert "status" not in report["operations"]["name"]["summary"]["fields"]
    assert "fill_level" not in report["operations"]["name"]["summary"]["fields"]


def test_local_only_contract_and_runtime_evidence_are_deterministic(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="local-only"):
        ensure_local_benchmark_settings(make_settings(tmp_path, provider="openai"))
    openai_enabled = make_settings(tmp_path, provider="ollama")
    openai_enabled.openai_api_key = "not-for-benchmarks"
    with pytest.raises(ValueError, match="OpenAI key"):
        ensure_local_benchmark_settings(openai_enabled)

    forced = local_benchmark_settings(
        Settings(
            data_dir=tmp_path,
            database_url="sqlite://",
            session_secret="test-secret",
            secure_cookies=False,
            ollama_url="http://ollama.test",
            ollama_model="test-model",
            max_users=1,
            max_upload_mb=1,
            analysis_provider="openai",
            openai_api_key="configured-key",
        )
    )
    assert forced.analysis_provider == "ollama"
    assert forced.openai_api_key is None

    async def fake_get(path: str) -> dict:
        if path == "/api/version":
            return {"version": "0.12.0"}
        assert path == "/api/ps"
        return {
            "models": [
                {
                    "name": "gemma4:26b",
                    "digest": "sha256:expected",
                    "size_vram": 123,
                    "expires_at": "2026-07-21T12:00:00Z",
                    "ignored": "not exported",
                }
            ]
        }

    evidence = asyncio.run(
        collect_runtime_evidence(
            make_settings(tmp_path, provider="ollama"),
            preprocess_revision="image-v3",
            command_runner=lambda command: "NVIDIA RTX 3090, 580.1, 24576, 99\n",
            ollama_getter=fake_get,
        )
    )
    assert evidence["ollama"] == {
        "endpoint_host": "ollama.test",
        "version": "0.12.0",
        "resident_models": [
            {
                "name": "gemma4:26b",
                "digest": "sha256:expected",
                "size_vram": 123,
                "expires_at": "2026-07-21T12:00:00Z",
            }
        ],
    }
    assert evidence["gpu"] == [
        {
            "name": "NVIDIA RTX 3090",
            "driver_version": "580.1",
            "memory_total_mib": "24576",
            "memory_used_mib": "99",
        }
    ]
    assert evidence["preprocess_revision"] == "image-v3"
    assert evidence["timing_instrumentation"]["instrumented"] is False

    async def malformed_get(path: str) -> dict:
        return {"models": "not-a-list"} if path == "/api/ps" else {"version": "0.12.0"}

    incomplete_evidence = asyncio.run(
        collect_runtime_evidence(
            make_settings(tmp_path, provider="ollama"),
            command_runner=lambda command: "",
            ollama_getter=malformed_get,
        )
    )
    assert incomplete_evidence["ollama"]["version"] == "0.12.0"
    assert incomplete_evidence["ollama"]["resident_models"] == []

    async def non_object_get(path: str):
        return []

    malformed_evidence = asyncio.run(
        collect_runtime_evidence(
            make_settings(tmp_path, provider="ollama"),
            command_runner=lambda command: None,
            ollama_getter=non_object_get,
        )
    )
    assert malformed_evidence["ollama"]["version"] is None
    assert malformed_evidence["ollama"]["resident_models"] == []
    assert malformed_evidence["gpu"] == []


def test_local_only_settings_reach_ollama_dispatch_without_openai_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The benchmark's forced settings must select only the Ollama client boundary."""
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

    forced = local_benchmark_settings(
        Settings(
            data_dir=tmp_path,
            database_url="sqlite://",
            session_secret="test-secret",
            secure_cookies=False,
            ollama_url="http://ollama.test",
            ollama_model="test-model",
            max_users=1,
            max_upload_mb=1,
            analysis_provider="openai",
            openai_api_key="configured-key",
        )
    )
    ollama_calls: list[tuple[str, str | None]] = []

    async def fake_ollama_request(prompt, settings, photo=None):
        assert settings.analysis_provider == "ollama"
        assert settings.openai_api_key is None
        ollama_calls.append((prompt, photo.name if photo else None))
        return make_case()["expected"], "complete"

    async def forbidden_openai_request(*args, **kwargs):
        raise AssertionError("benchmark attempted an OpenAI provider call")

    import bourbonbook.ollama as ollama
    import bourbonbook.openai_provider as openai_provider

    monkeypatch.setattr(ollama, "request_analysis", fake_ollama_request)
    monkeypatch.setattr(openai_provider, "request_analysis", forbidden_openai_request)

    report = asyncio.run(
        run_fixture(
            fixture,
            forced,
            ("photo",),
            1,
            "uncontrolled",
            runtime_evidence={"fake": True},
        )
    )

    assert report["provider"] == "ollama"
    assert len(ollama_calls) == 2


def test_runtime_evidence_degrades_on_timeout_or_failure_and_preserves_cancellation(
    tmp_path: Path,
) -> None:
    async def timeout_get(path: str) -> dict:
        raise httpx.ReadTimeout("synthetic timeout")

    timeout_evidence = asyncio.run(
        collect_runtime_evidence(
            make_settings(tmp_path, provider="ollama"),
            command_runner=lambda command: "",
            ollama_getter=timeout_get,
        )
    )
    assert timeout_evidence["ollama"]["version"] is None
    assert timeout_evidence["ollama"]["resident_models"] == []

    async def failed_get(path: str) -> dict:
        raise OSError("synthetic runtime failure")

    failed_evidence = asyncio.run(
        collect_runtime_evidence(
            make_settings(tmp_path, provider="ollama"),
            command_runner=lambda command: "",
            ollama_getter=failed_get,
        )
    )
    assert failed_evidence["ollama"]["version"] is None

    async def cancelled_get(path: str) -> dict:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            collect_runtime_evidence(
                make_settings(tmp_path, provider="ollama"),
                command_runner=lambda command: "",
                ollama_getter=cancelled_get,
            )
        )


def test_main_forces_local_settings_before_invoking_the_benchmark(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configured = Settings(
        data_dir=tmp_path,
        database_url="sqlite://",
        session_secret="test-secret",
        secure_cookies=False,
        ollama_url="http://ollama.test",
        ollama_model="test-model",
        max_users=1,
        max_upload_mb=1,
        analysis_provider="openai",
        openai_api_key="configured-key",
    )
    fixture = tmp_path / "benchmarks" / "fixtures" / "fixture"
    output = tmp_path / "benchmarks" / "reports" / "candidate.json"
    observed = {}
    report = {"operations": {"photo": {"summary": {"requests": 1}}}}

    async def fake_run_fixture(
        fixture_path, settings, operations, runs, cold_start_state, **kwargs
    ):
        observed.update(
            fixture=fixture_path,
            provider=settings.analysis_provider,
            openai_api_key=settings.openai_api_key,
            operations=operations,
            runs=runs,
            cold_start_state=cold_start_state,
        )
        return report

    monkeypatch.setattr(benchmark_cli.Settings, "from_env", lambda: configured)
    monkeypatch.setattr(benchmark_cli, "run_fixture", fake_run_fixture)
    monkeypatch.setattr(benchmark_cli, "private_benchmark_path", lambda settings, path: path)
    monkeypatch.setattr(benchmark_cli, "write_json", lambda path, value: None)
    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(
            command="run",
            fixture=fixture,
            output=output,
            live=True,
            operations=("photo",),
            runs=1,
            cold_start_state="uncontrolled",
            preprocess_revision="application-default",
        ),
    )

    benchmark_cli.main()

    assert observed == {
        "fixture": fixture,
        "provider": "ollama",
        "openai_api_key": None,
        "operations": ("photo",),
        "runs": 1,
        "cold_start_state": "uncontrolled",
    }
    assert json.loads(capsys.readouterr().out) == {"photo": {"requests": 1}}


def test_strict_identity_size_and_legacy_report_policy() -> None:
    assert benchmark_cli.matches("size", "750 ml", "0.75L")
    assert not benchmark_cli.matches("size", "750 ml", "1 L")
    assert not benchmark_cli.matches("name", "Weller Full Proof", "Weller Proof")

    legacy = report(p50=100, p95=120, accuracy=1.0)
    current = report(p50=100, p95=120, accuracy=1.0)
    current.update(
        {
            "schema_version": benchmark_cli.REPORT_SCHEMA_VERSION,
            "report_contract": benchmark_cli.REPORT_CONTRACT_VERSION,
            "runtime_evidence": {"fake": True},
        }
    )
    assert "report contract differs" in " ".join(compare_reports(legacy, current)["failures"])
    assert "current benchmark v2" in " ".join(compare_reports(legacy, legacy)["failures"])


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
            live=True,
            operations=("photo", "name"),
            runs=3,
            cold_start_state="unloaded",
            preprocess_revision="application-default",
        )

    def compare_args():
        return SimpleNamespace(command="compare", baseline=baseline, candidate=candidate)

    def upgrade_args():
        return SimpleNamespace(
            command="upgrade-report",
            input=baseline,
            output=tmp_path / "reports" / "upgraded.json",
        )

    monkeypatch.setattr(benchmark_cli.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(benchmark_cli, "local_benchmark_settings", lambda value: value)
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

    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(
            command="run",
            fixture=allowed,
            output=tmp_path / "reports" / "candidate.json",
            live=False,
            operations=("photo",),
            runs=1,
            cold_start_state="unloaded",
            preprocess_revision="application-default",
        ),
    )
    monkeypatch.setattr(
        argparse.ArgumentParser,
        "error",
        lambda self, message: (_ for _ in ()).throw(SystemExit(message)),
    )
    with pytest.raises(SystemExit, match="pass --live"):
        benchmark_cli.main()

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: compare_args())
    monkeypatch.setattr(
        benchmark_cli,
        "compare_reports",
        lambda baseline_data, candidate_data: compare_result,
    )
    benchmark_cli.main()
    assert json.loads(capsys.readouterr().out) == compare_result

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", lambda self: upgrade_args())
    baseline.write_text(json.dumps(report(p50=100, p95=120, accuracy=1.0)))
    benchmark_cli.main()
    assert written["path"] == tmp_path / "reports" / "upgraded.json"
    assert written["value"]["schema_version"] == benchmark_cli.REPORT_SCHEMA_VERSION
    assert written["value"]["report_contract"] == "legacy-v1"
    assert json.loads(capsys.readouterr().out)["migration"] == {
        "from_schema_version": 1,
        "comparison_compatible": False,
    }

    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda self: SimpleNamespace(
            command="run",
            fixture=allowed,
            output=tmp_path / "reports" / "candidate.json",
            live=True,
            operations=("photo",),
            runs=0,
            cold_start_state="unloaded",
            preprocess_revision="application-default",
        ),
    )
    monkeypatch.setattr(
        argparse.ArgumentParser,
        "error",
        lambda self, message: (_ for _ in ()).throw(SystemExit(message)),
    )
    with pytest.raises(SystemExit, match="positive"):
        benchmark_cli.main()
