from __future__ import annotations

import asyncio

from bourbonbook.analysis import (
    analyze_bottle,
    analyze_bottle_name,
    enrich_from_verified_catalog,
    merge_analysis,
    normalize_analysis,
    search_bottle_prices,
    warm_analysis_model,
)
from bourbonbook.config import Settings


def settings_for(tmp_path, provider: str) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        session_secret="test-secret",
        secure_cookies=False,
        ollama_url="http://ollama.test",
        ollama_model="test-ollama",
        max_users=10,
        max_upload_mb=2,
        analysis_provider=provider,
        openai_api_key="test-key",
        openai_model="test-openai",
    )


def test_openai_provider_is_selected(tmp_path, monkeypatch) -> None:
    async def fake_request(prompt, settings, photo=None):
        assert "Example Bourbon" in prompt
        assert settings.openai_model == "test-openai"
        assert photo is None
        return {"proof": 114}, "complete"

    monkeypatch.setattr("bourbonbook.openai_provider.request_analysis", fake_request)

    result, status = asyncio.run(
        analyze_bottle_name("Example Bourbon", settings_for(tmp_path, "openai"))
    )

    assert status == "complete"
    assert result == {"name": "Example Bourbon", "proof": 114}


def test_unknown_provider_is_unavailable(tmp_path) -> None:
    result, status = asyncio.run(analyze_bottle_name("Example", settings_for(tmp_path, "other")))

    assert status == "unavailable"
    assert result == {}


def test_vllm_provider_is_selected(tmp_path, monkeypatch) -> None:
    async def fake_request(prompt, settings, photo=None):
        assert "Example Bourbon" in prompt
        assert photo is None
        return {"proof": 114}, "complete"

    monkeypatch.setattr("bourbonbook.vllm_provider.request_analysis", fake_request)

    result, status = asyncio.run(
        analyze_bottle_name("Example Bourbon", settings_for(tmp_path, "vllm"))
    )

    assert status == "complete"
    assert result == {"name": "Example Bourbon", "proof": 114}


def test_ollama_provider_and_price_provider_boundaries(tmp_path, monkeypatch) -> None:
    settings = settings_for(tmp_path, "ollama")

    async def fake_request(prompt, settings, photo=None):
        return {"name": "From Ollama", "photo": str(photo) if photo else None}, "complete"

    monkeypatch.setattr("bourbonbook.ollama.request_analysis", fake_request)
    assert asyncio.run(analyze_bottle(tmp_path / "photo.jpg", settings))[0]["name"] == "From Ollama"
    assert asyncio.run(analyze_bottle_name("Bottle", settings))[1] == "complete"

    async def fake_prices(name, settings, *, size=None):
        assert size == "750ml"
        return {"msrp": 50.0}, [], "complete"

    monkeypatch.setattr("bourbonbook.ollama_search.search_prices", fake_prices)
    assert asyncio.run(search_bottle_prices("Bottle", settings, size="750ml"))[0] == {"msrp": 50.0}


def test_openai_price_provider_is_selected(tmp_path, monkeypatch) -> None:
    settings = settings_for(tmp_path, "openai")

    async def fake_prices(name, settings, *, size=None):
        assert size == "750ml"
        return {"msrp": 42.0}, [], "complete"

    monkeypatch.setattr("bourbonbook.openai_provider.search_prices", fake_prices)
    assert asyncio.run(search_bottle_prices("Bottle", settings, size="750ml"))[0] == {"msrp": 42.0}


def test_unknown_price_provider_is_unavailable(tmp_path) -> None:
    settings = settings_for(tmp_path, "other")

    assert asyncio.run(search_bottle_prices("Bottle", settings)) == ({}, [], "unavailable")


def test_warm_analysis_model_only_dispatches_for_ollama(tmp_path, monkeypatch) -> None:
    calls: list[object] = []

    async def fake_warm(settings):
        calls.append(settings)

    monkeypatch.setattr("bourbonbook.ollama.warm_vision_model", fake_warm)

    ollama_settings = settings_for(tmp_path, "ollama")
    asyncio.run(warm_analysis_model(ollama_settings))
    assert calls == [ollama_settings]

    asyncio.run(warm_analysis_model(settings_for(tmp_path, "openai")))
    asyncio.run(warm_analysis_model(settings_for(tmp_path, "other")))
    assert calls == [ollama_settings]


def test_partial_ollama_photo_analysis_refines_with_text_model_only(tmp_path, monkeypatch) -> None:
    settings = settings_for(tmp_path, "ollama")
    calls: list[str] = []

    async def fake_ollama(prompt, configured_settings, photo=None):
        calls.append("vision" if photo else "text")
        return {
            "name": "Uncatalogued Bottle",
            "ocr_text": "UNCATALOGUED BOTTLE 100 PROOF",
            "proof": 100,
        }, "complete"

    monkeypatch.setattr("bourbonbook.ollama.request_analysis", fake_ollama)

    values, status = asyncio.run(analyze_bottle(tmp_path / "photo.jpg", settings))

    assert status == "complete"
    assert values["ocr_text"] == "UNCATALOGUED BOTTLE 100 PROOF"
    assert calls == ["vision", "text"]


def test_merge_analysis_drops_msrp_by_default() -> None:
    merged = merge_analysis({"name": "Example"}, {"msrp": 69.99, "brand": "Example Brand"})

    assert "msrp" not in merged
    assert merged["brand"] == "Example Brand"


def test_verified_catalog_match_carries_its_curated_msrp() -> None:
    values, matched = enrich_from_verified_catalog({"name": "New Riff 8 Years"})

    assert matched is True
    assert values["msrp"] == 69.99


def test_analyze_bottle_name_returns_verified_catalog_msrp(tmp_path) -> None:
    values, status = asyncio.run(
        analyze_bottle_name("New Riff 8 Years", settings_for(tmp_path, "ollama"))
    )

    assert status == "verified"
    assert values["msrp"] == 69.99


def test_normalize_analysis_derives_missing_abv_from_proof() -> None:
    values = normalize_analysis({"proof": 90})

    assert values["abv"] == 45.0


def test_normalize_analysis_derives_missing_proof_from_abv() -> None:
    values = normalize_analysis({"abv": 45.0})

    assert values["proof"] == 90.0


def test_normalize_analysis_resolves_a_disagreeing_proof_and_abv_to_the_higher_reading() -> None:
    values = normalize_analysis({"proof": 90, "abv": 43.0})

    assert values["proof"] == 90.0
    assert values["abv"] == 45.0


def test_normalize_analysis_leaves_a_consistent_proof_and_abv_untouched() -> None:
    values = normalize_analysis({"proof": 107.0, "abv": 53.5})

    assert values["proof"] == 107.0
    assert values["abv"] == 53.5


def test_normalize_analysis_reconciles_proof_and_abv_even_without_a_fill_level() -> None:
    """Proof/ABV reconciliation must not be skipped just because fill_level is absent,
    which is the normal case for a typed-name lookup rather than a photo."""
    values = normalize_analysis({"proof": 90})

    assert values["abv"] == 45.0
    assert "fill_level" not in values


def test_normalize_analysis_snaps_size_to_the_nearest_standard_bottle() -> None:
    values = normalize_analysis({"size": "751ml"})

    assert values["size"] == "750ml"


def test_normalize_analysis_converts_size_units_before_snapping() -> None:
    values = normalize_analysis({"size": "1L"})

    assert values["size"] == "1000ml"


def test_normalize_analysis_leaves_a_non_standard_size_untouched() -> None:
    values = normalize_analysis({"size": "620ml"})

    assert values["size"] == "620ml"
