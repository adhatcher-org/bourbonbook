from __future__ import annotations

import asyncio

from bourbonbook.config import Settings
from bourbonbook.ollama import normalize_analysis, request_analysis


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
    monkeypatch.setattr("bourbonbook.ollama.httpx.AsyncClient", FakeClient)

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
