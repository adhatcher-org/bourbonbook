from __future__ import annotations

import asyncio

import httpx

from bourbonbook import ollama
from bourbonbook.analysis import analysis_schema
from bourbonbook.config import Settings
from bourbonbook.ollama import (
    analyze_bottle_name,
    normalize_analysis,
    request_analysis,
    warm_vision_model,
)
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


def test_photo_and_name_analysis_select_their_configured_models(tmp_path, monkeypatch) -> None:
    requests: list[dict] = []

    class RecordingClient(FakeClient):
        async def post(self, url: str, json: dict) -> FakeResponse:
            requests.append(json)
            return FakeResponse()

    photo = tmp_path / "bottle.jpg"
    photo.write_bytes(b"photo-bytes")
    monkeypatch.setattr("bourbonbook.provider_clients.httpx.AsyncClient", RecordingClient)
    settings = Settings(
        data_dir=tmp_path,
        database_url="sqlite://",
        session_secret="secret",
        secure_cookies=False,
        ollama_url="http://ollama.test",
        ollama_model="legacy-model",
        ollama_vision_model="vision-model",
        ollama_text_model="text-model",
        max_users=1,
        max_upload_mb=1,
    )

    assert asyncio.run(request_analysis("photo prompt", settings, photo))[1] == "complete"
    assert asyncio.run(request_analysis("name prompt", settings))[1] == "complete"
    assert [request["model"] for request in requests] == ["vision-model", "text-model"]
    assert [request["options"]["num_ctx"] for request in requests] == [32768, 4096]
    assert "date_bottled" in requests[0]["prompt"]
    assert "date_bottled" not in requests[1]["prompt"]


def test_ollama_model_remains_the_default_for_both_analysis_paths(tmp_path, monkeypatch) -> None:
    selected_models: list[str] = []

    class RecordingClient(FakeClient):
        async def post(self, url: str, json: dict) -> FakeResponse:
            selected_models.append(json["model"])
            return FakeResponse()

    photo = tmp_path / "bottle.jpg"
    photo.write_bytes(b"photo-bytes")
    monkeypatch.setattr("bourbonbook.provider_clients.httpx.AsyncClient", RecordingClient)
    settings = Settings(
        data_dir=tmp_path,
        database_url="sqlite://",
        session_secret="secret",
        secure_cookies=False,
        ollama_url="http://ollama.test",
        ollama_model="legacy-model",
        max_users=1,
        max_upload_mb=1,
    )

    assert asyncio.run(request_analysis("photo prompt", settings, photo))[1] == "complete"
    assert asyncio.run(request_analysis("name prompt", settings))[1] == "complete"
    assert selected_models == ["legacy-model", "legacy-model"]


def test_ollama_context_windows_follow_the_model_role(tmp_path, monkeypatch) -> None:
    requests: list[dict] = []

    class RecordingClient(FakeClient):
        async def post(self, url: str, json: dict) -> FakeResponse:
            requests.append(json)
            return FakeResponse()

    photo = tmp_path / "bottle.jpg"
    photo.write_bytes(b"photo-bytes")
    monkeypatch.setattr("bourbonbook.provider_clients.httpx.AsyncClient", RecordingClient)
    settings = Settings(
        data_dir=tmp_path,
        database_url="sqlite://",
        session_secret="secret",
        secure_cookies=False,
        ollama_url="http://ollama.test",
        ollama_model="legacy-model",
        ollama_num_ctx=8192,
        ollama_vision_num_ctx=24576,
        ollama_text_num_ctx=12288,
        max_users=1,
        max_upload_mb=1,
    )

    assert asyncio.run(request_analysis("photo prompt", settings, photo))[1] == "complete"
    assert asyncio.run(request_analysis("name prompt", settings))[1] == "complete"
    assert [request["options"]["num_ctx"] for request in requests] == [24576, 12288]


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


def test_ollama_connection_failures_log_safe_actionable_context(tmp_path, monkeypatch) -> None:
    class FailingClient(FakeClient):
        async def post(self, url: str, json: dict) -> FakeResponse:
            raise httpx.ConnectError(
                "[Errno 111] Connection refused", request=httpx.Request("POST", url)
            )

    monkeypatch.setattr("bourbonbook.provider_clients.httpx.AsyncClient", FailingClient)
    logged: list[tuple[str, dict, dict]] = []

    def capture_warning(message: str, values: dict, *, extra: dict) -> None:
        logged.append((message, values, extra))

    monkeypatch.setattr(ollama.logger, "warning", capture_warning)
    settings = Settings(
        data_dir=tmp_path,
        database_url="sqlite://",
        session_secret="secret",
        secure_cookies=False,
        ollama_url="https://ollama.internal:11434",
        ollama_model="test-model",
        max_users=1,
        max_upload_mb=1,
    )
    assert asyncio.run(request_analysis("prompt", settings)) == ({}, "unavailable")

    message, values, extra = logged[-1]
    assert "failure_kind=connect_error" in message % values
    assert "connection_reason=connection_refused" in message % values
    assert "exception_type=ConnectError" in message % values
    assert "endpoint=https://ollama.internal:11434" in message % values
    assert extra["failure_kind"] == "connect_error"
    assert extra["connection_reason"] == "connection_refused"
    assert extra["endpoint_host"] == "ollama.internal"


def test_warm_vision_model_loads_the_configured_vision_model_without_a_prompt(
    tmp_path, monkeypatch
) -> None:
    requests: list[dict] = []

    class RecordingClient(FakeClient):
        async def post(self, url: str, json: dict) -> FakeResponse:
            requests.append({"url": url, "json": json})
            return FakeResponse()

    monkeypatch.setattr("bourbonbook.provider_clients.httpx.AsyncClient", RecordingClient)
    settings = Settings(
        data_dir=tmp_path,
        database_url="sqlite://",
        session_secret="secret",
        secure_cookies=False,
        ollama_url="http://ollama.test",
        ollama_model="legacy-model",
        ollama_vision_model="vision-model",
        max_users=1,
        max_upload_mb=1,
    )

    asyncio.run(warm_vision_model(settings))

    assert requests == [
        {
            "url": "http://ollama.test/api/generate",
            "json": {"model": "vision-model", "options": {"num_ctx": 32768}},
        }
    ]


def test_warm_vision_model_falls_back_to_the_legacy_model(tmp_path, monkeypatch) -> None:
    requests: list[dict] = []

    class RecordingClient(FakeClient):
        async def post(self, url: str, json: dict) -> FakeResponse:
            requests.append(json)
            return FakeResponse()

    monkeypatch.setattr("bourbonbook.provider_clients.httpx.AsyncClient", RecordingClient)
    settings = Settings(
        data_dir=tmp_path,
        database_url="sqlite://",
        session_secret="secret",
        secure_cookies=False,
        ollama_url="http://ollama.test",
        ollama_model="legacy-model",
        max_users=1,
        max_upload_mb=1,
    )

    asyncio.run(warm_vision_model(settings))

    assert requests == [{"model": "legacy-model", "options": {"num_ctx": 32768}}]


def test_warm_vision_model_failure_is_non_fatal(tmp_path, monkeypatch) -> None:
    class FailingClient(FakeClient):
        async def post(self, url: str, json: dict) -> FakeResponse:
            raise httpx.ConnectError(
                "[Errno 111] Connection refused", request=httpx.Request("POST", url)
            )

    monkeypatch.setattr("bourbonbook.provider_clients.httpx.AsyncClient", FailingClient)
    settings = Settings(
        data_dir=tmp_path,
        database_url="sqlite://",
        session_secret="secret",
        secure_cookies=False,
        ollama_url="http://ollama.test",
        ollama_model="legacy-model",
        max_users=1,
        max_upload_mb=1,
    )

    asyncio.run(warm_vision_model(settings))


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


# --- structured output (A15) --------------------------------------------------------


def conforming_payload(schema: dict, **overrides) -> str:
    """Build a body that actually conforms to ``schema``.

    The validator checks the whole object, so a fake that returns a partial one is testing the
    validator rather than the path under test. Every property gets a value its own spec allows.
    """
    import json as json_module

    values: dict = {}
    for field, spec in schema["properties"].items():
        allowed = spec.get("type")
        allowed = [allowed] if isinstance(allowed, str) else list(allowed or ())
        if "enum" in spec:
            values[field] = next(c for c in spec["enum"] if c is not None)
        elif "string" in allowed:
            values[field] = f"{field}-value"
        elif "integer" in allowed:
            values[field] = 50
        elif "number" in allowed:
            values[field] = 50.0
        else:
            values[field] = None
    values.update(overrides)
    return json_module.dumps(values)


class SchemaResponse:
    """A complete, schema-conforming body that stopped cleanly.

    Real Ollama reports ``done``/``done_reason`` and, on a thinking-capable model, writes the
    constrained object into the ``thinking`` channel rather than ``response`` -- measured on
    0.32.13, 2026-08-23. Both channels are exercised; the default mirrors the non-thinking shape.
    """

    def __init__(
        self,
        payload: str | None = None,
        *,
        channel: str = "response",
        done: bool = True,
        done_reason: str | None = "stop",
        photo: bool = True,
    ) -> None:
        self.payload = (
            payload if payload is not None else conforming_payload(analysis_schema(photo=photo))
        )
        self.channel = channel
        self.done = done
        self.done_reason = done_reason

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        body = {"response": "", "thinking": "", "done": self.done}
        body[self.channel] = self.payload
        if self.done_reason is not None:
            body["done_reason"] = self.done_reason
        return body


class FailingResponse:
    def __init__(self, status: int) -> None:
        self.status_code = status

    def raise_for_status(self) -> None:
        request = httpx.Request("POST", "http://ollama.test/api/generate")
        raise httpx.HTTPStatusError(
            "error", request=request, response=httpx.Response(self.status_code, request=request)
        )

    def json(self) -> dict[str, str]:
        raise AssertionError("A failed response body must never be read")


def structured_settings(tmp_path, *, structured: bool) -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url="sqlite://",
        session_secret="secret",
        secure_cookies=False,
        ollama_url="http://ollama.test",
        ollama_model="legacy-model",
        max_users=1,
        max_upload_mb=1,
        ollama_structured_output=structured,
    )


def recording_client(requests: list[dict], response):
    """``response`` may be a fixed object, or a callable taking the request and returning one."""

    class RecordingClient(FakeClient):
        async def post(self, url: str, json: dict):
            requests.append(json)
            return response(json) if callable(response) else response

    return RecordingClient


def capture_warnings(monkeypatch) -> list[tuple[str, dict, dict]]:
    logged: list[tuple[str, dict, dict]] = []

    def capture_warning(message: str, values: dict, *, extra: dict) -> None:
        logged.append((message, values, extra))

    monkeypatch.setattr(ollama.logger, "warning", capture_warning)
    return logged


def test_structured_output_sends_the_schema(tmp_path, monkeypatch) -> None:
    requests: list[dict] = []
    photo = tmp_path / "bottle.jpg"
    photo.write_bytes(b"photo-bytes")
    monkeypatch.setattr(
        "bourbonbook.provider_clients.httpx.AsyncClient",
        recording_client(
            requests,
            lambda request: SchemaResponse(conforming_payload(request["format"])),
        ),
    )
    settings = structured_settings(tmp_path, structured=True)

    assert asyncio.run(request_analysis("photo prompt", settings, photo))[1] == "complete"
    assert asyncio.run(request_analysis("name prompt", settings))[1] == "complete"

    assert requests[0]["format"] == analysis_schema(photo=True)
    assert requests[1]["format"] == analysis_schema(photo=False)
    assert [request["options"]["temperature"] for request in requests] == [0.1, 0.1]
    assert [request["options"]["num_ctx"] for request in requests] == [32768, 4096]


def test_structured_output_disabled_preserves_the_legacy_payload(tmp_path, monkeypatch) -> None:
    requests: list[dict] = []
    photo = tmp_path / "bottle.jpg"
    photo.write_bytes(b"photo-bytes")
    monkeypatch.setattr(
        "bourbonbook.provider_clients.httpx.AsyncClient",
        recording_client(requests, FakeResponse()),
    )
    settings = structured_settings(tmp_path, structured=False)

    assert asyncio.run(request_analysis("photo prompt", settings, photo))[1] == "complete"
    assert asyncio.run(request_analysis("name prompt", settings))[1] == "complete"

    assert [request["format"] for request in requests] == ["json", "json"]
    assert requests[0]["options"] == {"temperature": 0.1, "num_ctx": 32768}
    assert requests[1]["options"] == {"temperature": 0.1, "num_ctx": 4096}
    assert requests[0]["prompt"].startswith("photo prompt\nReturn ONLY one JSON object")
    assert requests[1]["prompt"].startswith("name prompt\nReturn ONLY one JSON object")


def test_structured_output_leaves_the_prompt_and_field_list_unchanged(
    tmp_path, monkeypatch
) -> None:
    photo = tmp_path / "bottle.jpg"
    photo.write_bytes(b"photo-bytes")
    prompts: dict[bool, list[str]] = {}
    for structured in (True, False):
        requests: list[dict] = []
        response = SchemaResponse() if structured else FakeResponse()
        monkeypatch.setattr(
            "bourbonbook.provider_clients.httpx.AsyncClient",
            recording_client(requests, response),
        )
        settings = structured_settings(tmp_path, structured=structured)
        asyncio.run(request_analysis("photo prompt", settings, photo))
        asyncio.run(request_analysis("name prompt", settings))
        prompts[structured] = [request["prompt"] for request in requests]

    assert prompts[True] == prompts[False]
    photo_prompt, name_prompt_text = prompts[True]
    assert photo_prompt.endswith(", ocr_text, date_bottled.")
    assert name_prompt_text.endswith(", ocr_text.")
    assert "date_bottled" in photo_prompt
    assert "date_bottled" not in name_prompt_text


def test_schema_rejection_makes_one_request_and_falls_back_to_manual_review(
    tmp_path, monkeypatch
) -> None:
    requests: list[dict] = []
    monkeypatch.setattr(
        "bourbonbook.provider_clients.httpx.AsyncClient",
        recording_client(requests, FailingResponse(400)),
    )
    logged = capture_warnings(monkeypatch)
    settings = structured_settings(tmp_path, structured=True)

    assert asyncio.run(request_analysis("name prompt", settings)) == ({}, "unavailable")

    assert len(requests) == 1
    _message, _values, extra = logged[-1]
    assert extra["failure_kind"] == "schema_rejected_or_bad_request"
    assert extra["error_type"] == "provider_error"
    assert extra["http_status"] == 400


def test_no_raw_material_is_logged_on_the_rejection_path(tmp_path, monkeypatch) -> None:
    photo = tmp_path / "bottle.jpg"
    photo.write_bytes(b"photo-bytes")
    requests: list[dict] = []
    monkeypatch.setattr(
        "bourbonbook.provider_clients.httpx.AsyncClient",
        recording_client(requests, FailingResponse(400)),
    )
    logged = capture_warnings(monkeypatch)
    settings = structured_settings(tmp_path, structured=True)

    assert asyncio.run(request_analysis("secret photo prompt", settings, photo)) == (
        {},
        "unavailable",
    )

    forbidden = (
        "secret photo prompt",
        "additionalProperties",
        "ocr_text",
        "Example Bourbon",
        "photo-bytes",
        "cGhvdG8tYnl0ZXM=",
    )
    for message, values, extra in logged:
        rendered = f"{message % values} {extra}"
        for marker in forbidden:
            assert marker not in rendered, marker


def test_a_non_schema_http_error_keeps_its_existing_classification(tmp_path, monkeypatch) -> None:
    for structured, status in ((True, 500), (False, 400)):
        requests: list[dict] = []
        monkeypatch.setattr(
            "bourbonbook.provider_clients.httpx.AsyncClient",
            recording_client(requests, FailingResponse(status)),
        )
        logged = capture_warnings(monkeypatch)
        settings = structured_settings(tmp_path, structured=structured)

        assert asyncio.run(request_analysis("name prompt", settings)) == ({}, "unavailable")

        _message, _values, extra = logged[-1]
        assert extra["error_type"] == "provider_error"
        assert extra["failure_kind"] == "http_status"


def test_empty_strings_are_dropped_from_the_parsed_values(tmp_path, monkeypatch) -> None:
    blanks = {
        field: ""
        for field, spec in analysis_schema(photo=True)["properties"].items()
        if spec.get("type") == ["string", "null"] and "enum" not in spec
    }
    body = conforming_payload(analysis_schema(photo=True), **blanks)
    requests: list[dict] = []
    monkeypatch.setattr(
        "bourbonbook.provider_clients.httpx.AsyncClient",
        recording_client(requests, SchemaResponse(body)),
    )
    photo = tmp_path / "bottle.jpg"
    photo.write_bytes(b"photo-bytes")
    settings = structured_settings(tmp_path, structured=True)

    values, status = asyncio.run(request_analysis("photo prompt", settings, photo))

    assert status == "complete"
    assert all(field not in values for field in blanks)


def test_thinking_channel_is_used_when_structured_output_is_enabled(tmp_path, monkeypatch) -> None:
    """Which channel carries the object is a model property, not a schema property.

    Measured on Ollama 0.32.13 (2026-08-23): thinking-capable models put the grammar-constrained
    object in ``thinking`` and leave ``response`` empty; a non-thinking model does the reverse.
    Refusing ``thinking`` makes structured output unusable on every thinking-capable model.
    """
    requests: list[dict] = []
    monkeypatch.setattr(
        "bourbonbook.provider_clients.httpx.AsyncClient",
        recording_client(requests, SchemaResponse(channel="thinking", photo=False)),
    )
    settings = structured_settings(tmp_path, structured=True)

    values, status = asyncio.run(request_analysis("name prompt", settings))

    assert status == "complete"
    assert values["name"] == "name-value"


def test_incomplete_generation_is_refused_when_structured_output_is_enabled(
    tmp_path, monkeypatch
) -> None:
    """A truncated body can still parse; ``done``/``done_reason`` is what says it is whole."""
    requests: list[dict] = []
    monkeypatch.setattr(
        "bourbonbook.provider_clients.httpx.AsyncClient",
        recording_client(requests, SchemaResponse(photo=False, done=False, done_reason=None)),
    )
    logged = capture_warnings(monkeypatch)
    settings = structured_settings(tmp_path, structured=True)

    assert asyncio.run(request_analysis("name prompt", settings)) == ({}, "unavailable")

    _message, _values, extra = logged[-1]
    assert extra["failure_kind"] == "incomplete_generation"


def test_context_exhausted_generation_is_refused(tmp_path, monkeypatch) -> None:
    """``done`` true with ``done_reason`` "length" is the case a done-only guard would miss."""
    requests: list[dict] = []
    monkeypatch.setattr(
        "bourbonbook.provider_clients.httpx.AsyncClient",
        recording_client(requests, SchemaResponse(photo=False, done_reason="length")),
    )
    logged = capture_warnings(monkeypatch)
    settings = structured_settings(tmp_path, structured=True)

    assert asyncio.run(request_analysis("name prompt", settings)) == ({}, "unavailable")

    _message, _values, extra = logged[-1]
    assert extra["failure_kind"] == "incomplete_generation"


def test_nonconforming_response_is_refused_when_structured_output_is_enabled(
    tmp_path, monkeypatch
) -> None:
    """Sending a schema is not receiving one: Ollama silently drops keywords such as maxLength."""
    requests: list[dict] = []
    monkeypatch.setattr(
        "bourbonbook.provider_clients.httpx.AsyncClient",
        recording_client(requests, SchemaResponse('{"name": "Example Bourbon"}')),
    )
    logged = capture_warnings(monkeypatch)
    settings = structured_settings(tmp_path, structured=True)

    assert asyncio.run(request_analysis("name prompt", settings)) == ({}, "unavailable")

    _message, _values, extra = logged[-1]
    assert extra["failure_kind"] == "schema_nonconforming"
