from __future__ import annotations

import asyncio

from bourbonbook.config import Settings
from bourbonbook.ollama import analyze_bottle_name, normalize_analysis, request_analysis
from bourbonbook.provider_clients import reset_shared_ollama_client, set_shared_ollama_client


class FakeResponse:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, str]:
        return {
            "response": "",
            "thinking": '{"name":"Example Bourbon","proof":100,"abv":50}',
        }


class FakeClient:
    def __init__(self, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:
        pass

    async def post(self, url: str, json: dict) -> FakeResponse:
        assert json["think"] is False
        return FakeResponse()


def test_qwen_thinking_field_is_accepted(tmp_path, monkeypatch) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        session_secret="test-secret",
        secure_cookies=False,
        ollama_url="http://ollama.test",
        ollama_model="qwen3-vl:8b",
        max_users=10,
        max_upload_mb=2,
    )
    monkeypatch.setattr("bourbonbook.provider_clients.httpx.AsyncClient", FakeClient)

    result, status = asyncio.run(request_analysis("Analyze this bottle", settings))

    assert status == "complete"
    assert result == {"name": "Example Bourbon", "proof": 100, "abv": 50}


def test_status_is_derived_from_fill_level() -> None:
    assert normalize_analysis({"fill_level": 95, "status": "Opened"}) == {
        "fill_level": 100,
        "status": "Unopened",
    }
    assert normalize_analysis({"fill_level": "40%", "status": "Unopened"}) == {
        "fill_level": 40,
        "status": "Opened",
    }
    assert normalize_analysis({"fill_level": 0}) == {"fill_level": 0, "status": "Empty"}


def test_invalid_ollama_response_is_unavailable(tmp_path, monkeypatch) -> None:
    class InvalidResponse(FakeResponse):
        def json(self) -> dict[str, object]:
            return {"response": None}

    class InvalidClient(FakeClient):
        async def post(self, url: str, json: dict) -> InvalidResponse:
            return InvalidResponse()

    monkeypatch.setattr("bourbonbook.provider_clients.httpx.AsyncClient", InvalidClient)
    settings = Settings(
        data_dir=tmp_path,
        database_url="sqlite://",
        session_secret="secret",
        secure_cookies=False,
        ollama_url="http://ollama.invalid",
        ollama_model="test",
        max_users=1,
        max_upload_mb=1,
    )

    assert asyncio.run(request_analysis("prompt", settings)) == ({}, "unavailable")
    assert asyncio.run(analyze_bottle_name("Bottle", settings)) == ({}, "unavailable")


def test_shared_ollama_client_is_reused(tmp_path, monkeypatch) -> None:
    class SharedClient:
        async def post(self, url: str, json: dict) -> FakeResponse:
            assert url.endswith("/api/generate")
            return FakeResponse()

    class TempClient:
        def __init__(self, **kwargs) -> None:
            raise AssertionError("A shared Ollama client should have been reused")

    token = set_shared_ollama_client(SharedClient())
    monkeypatch.setattr("bourbonbook.provider_clients.httpx.AsyncClient", TempClient)
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        session_secret="secret",
        secure_cookies=False,
        ollama_url="http://ollama.invalid",
        ollama_model="test",
        max_users=1,
        max_upload_mb=1,
    )
    try:
        result, status = asyncio.run(request_analysis("prompt", settings))
    finally:
        reset_shared_ollama_client(token)

    assert status == "complete"
    assert result["name"] == "Example Bourbon"
