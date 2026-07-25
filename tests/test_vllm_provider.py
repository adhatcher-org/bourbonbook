from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bourbonbook.config import Settings
from bourbonbook.openai_provider import BottleAnalysis
from bourbonbook.provider_clients import reset_shared_vllm_client, set_shared_vllm_client
from bourbonbook.vllm_provider import request_analysis


def settings_for(
    tmp_path,
    *,
    base_url: str | None = "http://vllm.test/v1",
    model: str | None = "Qwen/Qwen2.5-VL-7B-Instruct",
    min_pixels: int | None = None,
    max_pixels: int | None = None,
) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        session_secret="test-secret",
        secure_cookies=False,
        ollama_url="http://ollama.test",
        ollama_model="test-ollama",
        max_users=10,
        max_upload_mb=2,
        analysis_provider="vllm",
        vllm_base_url=base_url,
        vllm_model=model,
        vllm_min_pixels=min_pixels,
        vllm_max_pixels=max_pixels,
    )


def analysis(**overrides) -> BottleAnalysis:
    values = dict(
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
    )
    values.update(overrides)
    return BottleAnalysis(**values)


class FakeMessage:
    def __init__(self, parsed) -> None:
        self.parsed = parsed


class FakeCompletion:
    def __init__(self, parsed, *, prompt_tokens=10, completion_tokens=5) -> None:
        self.choices = [SimpleNamespace(message=FakeMessage(parsed))]
        self.usage = SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )


class FakeCompletions:
    def __init__(self, parsed) -> None:
        self.parsed = parsed
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return FakeCompletion(self.parsed)


class FakeChat:
    def __init__(self, completions: FakeCompletions) -> None:
        self.completions = completions


class FakeClient:
    def __init__(self, parsed, **kwargs) -> None:
        self.init_kwargs = kwargs
        self.chat = FakeChat(FakeCompletions(parsed))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:
        pass


_UNSET = object()


def install_fake_client(monkeypatch, parsed=_UNSET) -> FakeClient:
    client = FakeClient(analysis() if parsed is _UNSET else parsed)
    monkeypatch.setattr("bourbonbook.provider_clients.AsyncOpenAI", lambda **kwargs: client)
    return client


def test_vllm_photo_analysis_uses_structured_output_and_resolution_overrides(
    tmp_path, monkeypatch
) -> None:
    photo = tmp_path / "bottle.jpg"
    photo.write_bytes(b"photo-bytes")
    client = install_fake_client(monkeypatch)

    result, status = asyncio.run(
        request_analysis(
            "Analyze",
            settings_for(tmp_path, min_pixels=200, max_pixels=4000),
            photo,
        )
    )

    assert status == "complete"
    assert result["name"] == "Example Bourbon"
    assert result["fill_level"] == 45
    assert result["status"] == "Opened"
    assert "msrp" not in result

    call = client.chat.completions.calls[0]
    assert call["model"] == "Qwen/Qwen2.5-VL-7B-Instruct"
    assert call["response_format"] is BottleAnalysis
    content = call["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Analyze"}
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert call["extra_body"] == {"mm_processor_kwargs": {"min_pixels": 200, "max_pixels": 4000}}


def test_vllm_name_analysis_omits_extra_body_and_image(tmp_path, monkeypatch) -> None:
    client = install_fake_client(monkeypatch)

    result, status = asyncio.run(
        request_analysis("Identify", settings_for(tmp_path, min_pixels=200, max_pixels=4000))
    )

    assert status == "complete"
    assert result["name"] == "Example Bourbon"
    call = client.chat.completions.calls[0]
    assert call["extra_body"] is None
    assert call["messages"][0]["content"] == [{"type": "text", "text": "Identify"}]


def test_missing_vllm_config_is_unavailable(tmp_path, monkeypatch) -> None:
    def fail_if_called(**kwargs):
        raise AssertionError("vLLM client should not be created without base URL/model")

    monkeypatch.setattr("bourbonbook.provider_clients.AsyncOpenAI", fail_if_called)

    no_url, status = asyncio.run(request_analysis("Analyze", settings_for(tmp_path, base_url=None)))
    no_model, status_two = asyncio.run(
        request_analysis("Analyze", settings_for(tmp_path, model=None))
    )

    assert (no_url, status) == ({}, "unavailable")
    assert (no_model, status_two) == ({}, "unavailable")


def test_missing_parsed_vllm_output_is_unavailable(tmp_path, monkeypatch) -> None:
    install_fake_client(monkeypatch, parsed=None)

    result, status = asyncio.run(request_analysis("Analyze", settings_for(tmp_path)))

    assert (result, status) == ({}, "unavailable")


def test_vllm_request_failure_is_unavailable(tmp_path, monkeypatch) -> None:
    class FailingCompletions:
        async def parse(self, **kwargs):
            raise ValueError("malformed guided-json response")

    class FailingChat:
        completions = FailingCompletions()

    class FailingClient:
        def __init__(self, **kwargs) -> None:
            self.chat = FailingChat()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

    monkeypatch.setattr(
        "bourbonbook.provider_clients.AsyncOpenAI", lambda **kwargs: FailingClient()
    )

    result, status = asyncio.run(request_analysis("Analyze", settings_for(tmp_path)))

    assert (result, status) == ({}, "unavailable")


def test_shared_vllm_client_is_reused(tmp_path, monkeypatch) -> None:
    shared = FakeClient(analysis(name="Shared Bourbon"))

    class TempClient:
        def __init__(self, **kwargs) -> None:
            raise AssertionError("A shared vLLM client should have been reused")

    token = set_shared_vllm_client(shared)
    monkeypatch.setattr("bourbonbook.provider_clients.AsyncOpenAI", TempClient)
    try:
        result, status = asyncio.run(request_analysis("Analyze", settings_for(tmp_path)))
    finally:
        reset_shared_vllm_client(token)

    assert status == "complete"
    assert result["name"] == "Shared Bourbon"
