from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit

import httpx

from bourbonbook.config import Settings
from bourbonbook.ollama import failure_context as real_failure_context
from bourbonbook.ollama_search import MAX_TOOL_ROUNDS, search_prices
from bourbonbook.provider_clients import reset_shared_ollama_client, set_shared_ollama_client


def settings_for(tmp_path, ollama_api_key: str | None = "test-ollama-cloud-key") -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        session_secret="test-secret",
        secure_cookies=False,
        ollama_url="http://ollama.test",
        ollama_model="test-ollama",
        max_users=10,
        max_upload_mb=2,
        analysis_provider="ollama",
        ollama_api_key=ollama_api_key,
    )


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict, dict | None]] = []

    async def post(self, url: str, json: dict, headers: dict | None = None) -> FakeResponse:
        self.calls.append((url, json, headers))
        return self._responses.pop(0)


def test_missing_ollama_api_key_is_unavailable(tmp_path, monkeypatch) -> None:
    def fail_if_called(**kwargs):
        raise AssertionError("Ollama client should not be created without OLLAMA_API_KEY")

    monkeypatch.setattr("bourbonbook.provider_clients.httpx.AsyncClient", fail_if_called)

    result = asyncio.run(
        search_prices("Weller Antique 107", settings_for(tmp_path, ollama_api_key=None))
    )

    assert result == ({}, [], "unavailable")


def test_ollama_web_search_round_trip_returns_a_grounded_price(tmp_path) -> None:
    settings = settings_for(tmp_path)
    client = FakeClient(
        [
            FakeResponse(
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "web_search",
                                    "arguments": {"query": "Weller Antique 107 OHLQ price"},
                                }
                            }
                        ],
                    }
                }
            ),
            FakeResponse(
                {
                    "results": [
                        {
                            "title": "OHLQ",
                            "url": "https://www.ohlq.com/product/weller-antique-107",
                            "content": "Sizes & Pricing: 750ml $34.99",
                        }
                    ]
                }
            ),
            FakeResponse(
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "web_fetch",
                                    "arguments": {
                                        "url": "https://www.ohlq.com/product/weller-antique-107"
                                    },
                                }
                            }
                        ],
                    }
                }
            ),
            FakeResponse(
                {
                    "title": "Weller Antique 107 | OHLQ",
                    "content": "Sizes & Pricing: 750ml $34.99",
                    "links": [],
                }
            ),
            FakeResponse(
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "msrp": 34.99,
                                "msrp_source_title": "OHLQ",
                                "msrp_source_url": "https://www.ohlq.com/product/weller-antique-107",
                                "msrp_basis": "Exact OHLQ Sizes & Pricing match.",
                            }
                        ),
                    }
                }
            ),
        ]
    )
    token = set_shared_ollama_client(client)
    try:
        prices, sources, status = asyncio.run(
            search_prices("Weller Antique 107", settings, size="750ml")
        )
    finally:
        reset_shared_ollama_client(token)

    assert status == "complete"
    assert prices == {"msrp": 34.99}
    assert sources == [
        {
            "kind": "msrp",
            "title": "OHLQ",
            "url": "https://www.ohlq.com/product/weller-antique-107",
            "basis": "Exact OHLQ Sizes & Pricing match.",
        }
    ]
    assert client.calls[0][0] == f"{settings.ollama_url}/api/chat"
    assert client.calls[0][1]["options"] == {"num_ctx": 4096}
    assert client.calls[1][0] == "https://ollama.com/api/web_search"
    assert client.calls[1][2]["Authorization"] == "Bearer test-ollama-cloud-key"
    assert client.calls[2][0] == f"{settings.ollama_url}/api/chat"
    assert client.calls[3][0] == "https://ollama.com/api/web_fetch"
    assert client.calls[3][1] == {"url": "https://www.ohlq.com/product/weller-antique-107"}
    assert client.calls[4][0] == f"{settings.ollama_url}/api/chat"


def test_ollama_price_search_drops_a_url_that_was_never_consulted(tmp_path) -> None:
    settings = settings_for(tmp_path)
    client = FakeClient(
        [
            FakeResponse(
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "web_search",
                                    "arguments": {"query": "Bottle price"},
                                }
                            }
                        ],
                    }
                }
            ),
            FakeResponse(
                {
                    "results": [
                        {
                            "title": "OHLQ",
                            "url": "https://www.ohlq.com/product/example",
                            "content": "Sizes & Pricing: 750ml $29.99",
                        }
                    ]
                }
            ),
            FakeResponse(
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "msrp": 29.99,
                                "msrp_source_title": "Some retailer",
                                "msrp_source_url": "https://never-consulted.example/price",
                                "msrp_basis": "Asserted without a matching tool result.",
                            }
                        ),
                    }
                }
            ),
        ]
    )
    token = set_shared_ollama_client(client)
    try:
        prices, sources, status = asyncio.run(search_prices("Bottle", settings))
    finally:
        reset_shared_ollama_client(token)

    assert prices == {}
    assert sources == []
    assert status == "unavailable"


def test_ollama_price_search_exceeds_max_tool_rounds(tmp_path) -> None:
    settings = settings_for(tmp_path)
    tool_call_round = [
        FakeResponse(
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "web_search", "arguments": {"query": "Bottle"}}}
                    ],
                }
            }
        ),
        FakeResponse(
            {
                "results": [
                    {
                        "title": "OHLQ",
                        "url": "https://www.ohlq.com/product/example",
                        "content": "Sizes & Pricing: 750ml $29.99",
                    }
                ]
            }
        ),
    ]
    client = FakeClient(tool_call_round * MAX_TOOL_ROUNDS)
    token = set_shared_ollama_client(client)
    try:
        result = asyncio.run(search_prices("Bottle", settings))
    finally:
        reset_shared_ollama_client(token)

    assert result == ({}, [], "unavailable")
    assert len(client.calls) == MAX_TOOL_ROUNDS * 2
    assert all(call[0] == f"{settings.ollama_url}/api/chat" for call in client.calls[0::2])
    assert all(call[0] == "https://ollama.com/api/web_search" for call in client.calls[1::2])


def test_ollama_price_search_failure_attributes_local_chat_endpoint(tmp_path, monkeypatch) -> None:
    settings = settings_for(tmp_path)

    class FailingClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, dict | None]] = []

        async def post(self, url: str, json: dict, headers: dict | None = None):
            self.calls.append((url, json, headers))
            raise httpx.ConnectError("connection refused")

    captured: dict[str, str | None] = {}

    def spy_failure_context(exc, settings, operation, model, duration_ms, *, endpoint_url=None):
        captured["endpoint_url"] = endpoint_url
        return real_failure_context(
            exc, settings, operation, model, duration_ms, endpoint_url=endpoint_url
        )

    monkeypatch.setattr("bourbonbook.ollama_search.failure_context", spy_failure_context)

    client = FailingClient()
    token = set_shared_ollama_client(client)
    try:
        result = asyncio.run(search_prices("Bottle", settings))
    finally:
        reset_shared_ollama_client(token)

    assert result == ({}, [], "unavailable")
    assert captured["endpoint_url"] == settings.ollama_url


def test_ollama_price_search_failure_attributes_cloud_endpoint(tmp_path, monkeypatch) -> None:
    settings = settings_for(tmp_path)

    class FailingClient(FakeClient):
        async def post(self, url: str, json: dict, headers: dict | None = None):
            self.calls.append((url, json, headers))
            if urlsplit(url).hostname == "ollama.com":
                raise httpx.ConnectError("connection refused")
            return self._responses.pop(0)

    client = FailingClient(
        [
            FakeResponse(
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "web_search", "arguments": {"query": "Bottle"}}}
                        ],
                    }
                }
            )
        ]
    )

    captured: dict[str, str | None] = {}

    def spy_failure_context(exc, settings, operation, model, duration_ms, *, endpoint_url=None):
        captured["endpoint_url"] = endpoint_url
        return real_failure_context(
            exc, settings, operation, model, duration_ms, endpoint_url=endpoint_url
        )

    monkeypatch.setattr("bourbonbook.ollama_search.failure_context", spy_failure_context)

    token = set_shared_ollama_client(client)
    try:
        result = asyncio.run(search_prices("Bottle", settings))
    finally:
        reset_shared_ollama_client(token)

    assert result == ({}, [], "unavailable")
    assert captured["endpoint_url"] == "https://ollama.com"
