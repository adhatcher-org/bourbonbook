from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bourbonbook.config import Settings
from bourbonbook.openai_provider import (
    BottleAnalysis,
    PriceAnalysis,
    request_analysis,
    search_prices,
)


def settings_for(tmp_path, api_key: str | None = "test-key") -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        session_secret="test-secret",
        secure_cookies=False,
        ollama_url="http://ollama.test",
        ollama_model="test-ollama",
        max_users=10,
        max_upload_mb=2,
        analysis_provider="openai",
        openai_api_key=api_key,
        openai_model="test-openai",
    )


def test_openai_image_analysis_uses_structured_output(tmp_path, monkeypatch) -> None:
    photo = tmp_path / "bottle.jpg"
    photo.write_bytes(b"photo-bytes")
    captured = {}

    class FakeResponses:
        async def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                output_parsed=BottleAnalysis(
                    name="Example Bourbon",
                    brand="Example",
                    release=None,
                    edition=None,
                    spirit_type="Bourbon",
                    distilled_by=None,
                    mash_bill=None,
                    proof=100,
                    abv=50,
                    size="750ml",
                    age_statement=None,
                    barrel_number=None,
                    bottle_number=None,
                    warehouse=None,
                    floor=None,
                    status="Unopened",
                    fill_level=45,
                    msrp=None,
                    secondary_price=None,
                )
            )

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            assert kwargs["api_key"] == "test-key"
            self.responses = FakeResponses()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

    monkeypatch.setattr("bourbonbook.openai_provider.AsyncOpenAI", FakeClient)

    result, status = asyncio.run(request_analysis("Analyze", settings_for(tmp_path), photo))

    assert status == "complete"
    assert result["fill_level"] == 45
    assert result["status"] == "Opened"
    assert "msrp" not in result
    assert captured["model"] == "test-openai"
    assert captured["text_format"] is BottleAnalysis
    image = captured["input"][0]["content"][1]
    assert image["image_url"].startswith("data:image/jpeg;base64,")
    assert image["detail"] == "high"


def test_missing_openai_key_is_unavailable(tmp_path, monkeypatch) -> None:
    def fail_if_called(**kwargs):
        raise AssertionError("OpenAI client should not be created without a key")

    monkeypatch.setattr("bourbonbook.openai_provider.AsyncOpenAI", fail_if_called)

    result, status = asyncio.run(request_analysis("Analyze", settings_for(tmp_path, None)))

    assert status == "unavailable"
    assert result == {}


def test_grounded_price_search_requires_consulted_source(tmp_path, monkeypatch) -> None:
    captured = {}

    class FakeWebCall:
        type = "web_search_call"

        def model_dump(self):
            return {
                "type": "web_search_call",
                "action": {
                    "sources": [
                        {"url": "https://example.com/msrp?utm_source=openai"},
                        {"url": "https://example.com/auction/123"},
                    ]
                },
            }

    class FakeResponses:
        async def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                output_parsed=PriceAnalysis(
                    msrp=59.99,
                    secondary_price=125,
                    msrp_source_title="Official price book",
                    msrp_source_url="https://example.com/msrp",
                    msrp_basis="Current official listing.",
                    secondary_source_title="Completed auction",
                    secondary_source_url="https://example.com/not-consulted",
                    secondary_basis="Recent completed sale.",
                ),
                output=[FakeWebCall()],
            )

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.responses = FakeResponses()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

    monkeypatch.setattr("bourbonbook.openai_provider.AsyncOpenAI", FakeClient)

    prices, sources, status = asyncio.run(
        search_prices("Weller Antique 107", settings_for(tmp_path))
    )

    assert status == "complete"
    assert prices == {"msrp": 59.99}
    assert sources == [
        {
            "kind": "msrp",
            "title": "Official price book",
            "url": "https://example.com/msrp",
            "basis": "Current official listing.",
        }
    ]
    assert captured["tools"] == [{"type": "web_search", "search_context_size": "medium"}]
    assert captured["include"] == ["web_search_call.action.sources"]
