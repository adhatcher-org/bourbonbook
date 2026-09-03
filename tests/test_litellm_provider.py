"""The LiteLLM gateway provider.

LiteLLM fronts the same local models the app has always used, so these tests pin the two
things that make it a distinct provider: its own model roles and budgets, and the OpenAI
chat-completions shape it speaks. Nothing here touches the network.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from bourbonbook import litellm_provider
from bourbonbook.catalog_extract import extract_catalog_chunk
from bourbonbook.config import Settings, openai_compatible_base
from bourbonbook.litellm_provider import (
    request_analysis,
    search_prices,
    warm_vision_model,
)
from bourbonbook.provider_clients import reset_shared_ollama_client, set_shared_ollama_client


def settings_for(tmp_path, **overrides) -> Settings:
    values = {
        "data_dir": tmp_path,
        "database_url": f"sqlite:///{tmp_path / 'test.db'}",
        "session_secret": "test-secret",
        "secure_cookies": False,
        "ollama_url": "http://ollama.test",
        "ollama_model": "unused-ollama",
        "max_users": 10,
        "max_upload_mb": 2,
        "analysis_provider": "litellm",
        "litellm_url": "http://litellm.test/v1",
        "litellm_api_key": "test-litellm-key",
        "litellm_model": "gateway-fallback",
    }
    values.update(overrides)
    return Settings(**values)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://litellm.test/v1/chat/completions")
            raise httpx.HTTPStatusError(
                "status", request=request, response=httpx.Response(self.status_code)
            )

    def json(self) -> dict:
        return self._payload


def completion(content: str) -> FakeResponse:
    return FakeResponse(
        {
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }
    )


class RecordingClient:
    """The one seam every provider test drives, standing in for the pooled HTTP client."""

    def __init__(self, responses: list[FakeResponse | BaseException]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def post(
        self, url: str, json: dict, headers: dict | None = None, **kwargs
    ) -> FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": headers, **kwargs})
        result = self._responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture
def client_factory():
    """Install a fake client for the duration of one test and always remove it."""
    tokens = []

    def install(responses):
        client = RecordingClient(responses)
        tokens.append(set_shared_ollama_client(client))
        return client

    yield install
    for token in reversed(tokens):
        reset_shared_ollama_client(token)


def test_text_analysis_uses_the_text_role_budget(tmp_path, client_factory) -> None:
    settings = settings_for(
        tmp_path,
        litellm_text_model="gateway-text",
        litellm_text_num_ctx=8192,
        litellm_text_max_tokens=512,
    )
    client = client_factory([completion(json.dumps({"name": "Example Bourbon", "proof": 100}))])

    values, status = asyncio.run(request_analysis("Analyze this bottle", settings))

    assert status == "complete"
    assert values["name"] == "Example Bourbon"
    request = client.calls[0]
    assert request["url"] == "http://litellm.test/v1/chat/completions"
    assert request["headers"] == {"Authorization": "Bearer test-litellm-key"}
    assert request["json"]["model"] == "gateway-text"
    assert request["json"]["num_ctx"] == 8192
    assert request["json"]["max_tokens"] == 512
    assert request["json"]["response_format"] == {"type": "json_object"}
    assert request["json"]["stream"] is False


def test_photo_analysis_sends_a_data_url_with_the_vision_budget(tmp_path, client_factory) -> None:
    photo = tmp_path / "bottle.jpg"
    photo.write_bytes(b"photo-bytes")
    settings = settings_for(
        tmp_path,
        litellm_vision_model="gateway-vision",
        litellm_vision_num_ctx=32768,
        litellm_text_model="gateway-text",
    )
    client = client_factory([completion(json.dumps({"name": "Photo Bourbon"}))])

    values, status = asyncio.run(request_analysis("Read the label", settings, photo))

    assert status == "complete"
    assert values["name"] == "Photo Bourbon"
    body = client.calls[0]["json"]
    assert body["model"] == "gateway-vision"
    assert body["num_ctx"] == 32768
    content = body["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_roles_fall_back_to_the_shared_model_and_context(tmp_path, client_factory) -> None:
    settings = settings_for(tmp_path, litellm_num_ctx=2048)
    client = client_factory([completion(json.dumps({"name": "Fallback Bourbon"}))])

    asyncio.run(request_analysis("Analyze this bottle", settings))

    body = client.calls[0]["json"]
    assert body["model"] == "gateway-fallback"
    assert body["num_ctx"] == 2048
    assert "max_tokens" not in body


def test_a_keyless_proxy_is_not_sent_an_empty_authorization(tmp_path, client_factory) -> None:
    settings = settings_for(tmp_path, litellm_api_key=None)
    client = client_factory([completion(json.dumps({"name": "Open Bourbon"}))])

    asyncio.run(request_analysis("Analyze this bottle", settings))

    assert client.calls[0]["headers"] == {}


def test_an_unconfigured_url_never_reaches_the_network(tmp_path, monkeypatch) -> None:
    def fail_if_called(**kwargs):
        raise AssertionError("no client should be created without LITELLM_URL")

    monkeypatch.setattr("bourbonbook.provider_clients.httpx.AsyncClient", fail_if_called)

    assert asyncio.run(request_analysis("Analyze", settings_for(tmp_path, litellm_url=None))) == (
        {},
        "unavailable",
    )


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse({}, status_code=502),
        FakeResponse({"choices": []}),
        FakeResponse({"choices": [{"message": {"role": "assistant", "content": "not json"}}]}),
    ],
    ids=["http_error", "no_choices", "unparsable_content"],
)
def test_a_broken_gateway_degrades_instead_of_raising(tmp_path, client_factory, response) -> None:
    client_factory([response])

    assert asyncio.run(request_analysis("Analyze", settings_for(tmp_path))) == ({}, "unavailable")


def test_a_transport_failure_degrades_instead_of_raising(tmp_path, client_factory) -> None:
    client_factory([httpx.ConnectError("connection refused")])

    assert asyncio.run(request_analysis("Analyze", settings_for(tmp_path))) == ({}, "unavailable")


def test_warm_up_asks_for_the_vision_model_and_one_token(tmp_path, client_factory) -> None:
    settings = settings_for(tmp_path, litellm_vision_model="gateway-vision")
    client = client_factory([completion("ok")])

    asyncio.run(warm_vision_model(settings))

    body = client.calls[0]["json"]
    assert body["model"] == "gateway-vision"
    assert body["max_tokens"] == 1
    assert "response_format" not in body


def test_warm_up_failure_is_not_fatal(tmp_path, client_factory) -> None:
    client_factory([httpx.ConnectError("connection refused")])

    asyncio.run(warm_vision_model(settings_for(tmp_path)))


def test_price_search_without_a_cloud_key_is_unavailable(tmp_path, monkeypatch) -> None:
    def fail_if_called(**kwargs):
        raise AssertionError("no client should be created without OLLAMA_API_KEY")

    monkeypatch.setattr("bourbonbook.provider_clients.httpx.AsyncClient", fail_if_called)

    result = asyncio.run(search_prices("Weller Antique 107", settings_for(tmp_path)))

    assert result == ({}, [], "unavailable")


def tool_call(name: str, arguments: dict) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"call-{name}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
            }
        ]
    }


def test_price_search_returns_a_price_backed_by_a_consulted_source(
    tmp_path, client_factory
) -> None:
    url = "https://www.ohlq.com/product/weller-antique-107"
    settings = settings_for(tmp_path, ollama_api_key="test-cloud-key")
    client = client_factory(
        [
            FakeResponse(tool_call("web_search", {"query": "Weller Antique 107 OHLQ price"})),
            FakeResponse({"results": [{"title": "OHLQ", "url": url, "content": "750ml $34.99"}]}),
            completion(
                json.dumps(
                    {
                        "msrp": 34.99,
                        "msrp_source_title": "OHLQ",
                        "msrp_source_url": url,
                        "msrp_basis": "Exact OHLQ Sizes & Pricing match.",
                    }
                )
            ),
        ]
    )

    prices, sources, status = asyncio.run(
        search_prices("Weller Antique 107", settings, size="750ml")
    )

    assert status == "complete"
    assert prices == {"msrp": 34.99}
    assert sources[0]["url"] == url
    # The chat model is proxied; the research tools stay on Ollama Cloud.
    assert client.calls[0]["url"] == "http://litellm.test/v1/chat/completions"
    assert client.calls[1]["url"] == "https://ollama.com/api/web_search"
    assert client.calls[2]["json"]["messages"][-1]["tool_call_id"] == "call-web_search"


def test_price_search_rejects_a_price_with_no_consulted_source(tmp_path, client_factory) -> None:
    settings = settings_for(tmp_path, ollama_api_key="test-cloud-key")
    client_factory(
        [
            completion(
                json.dumps(
                    {
                        "msrp": 34.99,
                        "msrp_source_title": "Invented",
                        "msrp_source_url": "https://example.invalid/never-fetched",
                        "msrp_basis": "guess",
                    }
                )
            )
        ]
    )

    assert asyncio.run(search_prices("Weller Antique 107", settings)) == ({}, [], "unavailable")


def test_price_search_gives_up_after_the_tool_round_cap(tmp_path, client_factory) -> None:
    """A model that never stops calling tools must not loop against the gateway forever."""
    settings = settings_for(tmp_path, ollama_api_key="test-cloud-key")
    url = "https://www.ohlq.com/product/weller-antique-107"
    rounds = []
    for _ in range(litellm_provider.MAX_TOOL_ROUNDS):
        rounds.append(FakeResponse(tool_call("web_search", {"query": "price"})))
        rounds.append(FakeResponse({"results": [{"title": "OHLQ", "url": url, "content": "x"}]}))
    client = client_factory(rounds)

    assert asyncio.run(search_prices("Weller Antique 107", settings)) == ({}, [], "unavailable")
    assert len(client.calls) == litellm_provider.MAX_TOOL_ROUNDS * 2


def test_price_search_degrades_when_the_gateway_is_unreachable(tmp_path, client_factory) -> None:
    settings = settings_for(tmp_path, ollama_api_key="test-cloud-key")
    client_factory([httpx.ConnectError("connection refused")])

    assert asyncio.run(search_prices("Weller Antique 107", settings)) == ({}, [], "unavailable")


def test_price_search_without_a_gateway_url_is_unavailable(tmp_path, monkeypatch) -> None:
    def fail_if_called(**kwargs):
        raise AssertionError("no client should be created without LITELLM_URL")

    monkeypatch.setattr("bourbonbook.provider_clients.httpx.AsyncClient", fail_if_called)
    settings = settings_for(tmp_path, litellm_url=None, ollama_api_key="test-cloud-key")

    assert asyncio.run(search_prices("Weller Antique 107", settings)) == ({}, [], "unavailable")


def test_catalog_extraction_follows_the_configured_provider(tmp_path) -> None:
    settings = settings_for(tmp_path, litellm_vision_model="gateway-vision")
    client = RecordingClient(
        [completion(json.dumps([{"name": "Example Bourbon", "size": "750ml", "price": "$34.99"}]))]
    )

    items = asyncio.run(extract_catalog_chunk(client, settings, b"image-bytes"))

    assert items and items[0]["name"] == "Example Bourbon"
    request = client.calls[0]
    assert request["url"] == "http://litellm.test/v1/chat/completions"
    assert request["headers"] == {"Authorization": "Bearer test-litellm-key"}
    assert request["json"]["model"] == "gateway-vision"
    assert request["json"]["temperature"] == 0


def test_a_bare_origin_gains_the_openai_compatible_suffix() -> None:
    assert openai_compatible_base("http://litellm:4000") == "http://litellm:4000/v1"
    assert openai_compatible_base("http://litellm:4000/") == "http://litellm:4000/v1"
    assert openai_compatible_base("http://litellm:4000/v1") == "http://litellm:4000/v1"
    assert openai_compatible_base("http://litellm:4000/gateway") == "http://litellm:4000/gateway"
    assert openai_compatible_base("  ") is None


def test_the_provider_label_reaches_the_usage_ledger() -> None:
    assert litellm_provider.PROVIDER == "litellm"
