from __future__ import annotations

import asyncio

from bourbonbook.analysis import analyze_bottle, analyze_bottle_name, search_bottle_prices
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

    monkeypatch.setattr("bourbonbook.openai_provider.search_prices", fake_prices)
    assert asyncio.run(search_bottle_prices("Bottle", settings, size="750ml"))[0] == {"msrp": 50.0}


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
