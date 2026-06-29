from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from bourbonbook.email import MemoryEmailSender, OutgoingEmail
from bourbonbook.models import ApiUsage
from bourbonbook.observability import (
    AIUsageRecorder,
    ObservedEmailSender,
    UsageMetadata,
    bounded_error_type,
    current_usage_recorder,
    current_usage_user_id,
    ollama_duration_ms,
    ollama_usage_metadata,
    openai_usage_metadata,
    route_template,
    usage_context,
)
from tests.test_app import make_client


class FakeUsage:
    def model_dump(self):
        return {
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
            "input_tokens_details": {"cached_tokens": 3},
            "output_tokens_details": {"reasoning_tokens": 2},
        }


class FakeOutput:
    type = "web_search_call"


class FakeResponse:
    usage = FakeUsage()
    output = [FakeOutput()]


def test_provider_usage_metadata_helpers() -> None:
    openai = openai_usage_metadata(FakeResponse())
    assert openai.input_tokens == 11
    assert openai.cached_input_tokens == 3
    assert openai.reasoning_tokens == 2
    assert openai.web_search_calls == 1

    ollama = ollama_usage_metadata({"prompt_eval_count": "4", "eval_count": 6})
    assert ollama.total_tokens == 10
    assert ollama_duration_ms({"total_duration": 2_500_000}, fallback_ms=99) == 2
    assert ollama_duration_ms({}, fallback_ms=99) == 99

    class RateLimitError(Exception):
        pass

    class HTTPError(Exception):
        pass

    assert bounded_error_type(TimeoutError()) == "timeout"
    assert bounded_error_type(RateLimitError()) == "rate_limit"
    assert bounded_error_type(HTTPError()) == "provider_error"
    assert bounded_error_type(ValueError()) == "parse_error"
    assert bounded_error_type(OSError()) == "network_error"
    assert bounded_error_type(RuntimeError()) == "unexpected"
    assert ollama_usage_metadata({"prompt_eval_count": "bad"}).input_tokens is None
    assert route_template(type("Request", (), {"scope": {}})()) == "unmatched"


def test_usage_context_and_recorder_persist_and_cleanup(tmp_path: Path) -> None:
    _, app = make_client(tmp_path)
    recorder = app.state.usage_recorder
    with usage_context(recorder, 42):
        assert current_usage_recorder() is recorder
        assert current_usage_user_id() == 42

    recorder.record(
        provider="openai",
        operation="price_search",
        model="gpt-test",
        success=False,
        duration_ms=123,
        metadata=UsageMetadata(input_tokens=2, output_tokens=3, total_tokens=5),
        error_type="timeout",
        user_id=None,
    )
    with app.state.database.session_factory() as session:
        usage = session.scalar(select(ApiUsage).where(ApiUsage.model == "gpt-test"))
        assert usage is not None
        assert usage.success is False
        assert usage.error_type == "timeout"

        session.add(
            ApiUsage(
                provider="ollama",
                operation="name_analysis",
                model="old",
                success=True,
                duration_ms=1,
                created_at=datetime.now(UTC) - timedelta(days=365),
            )
        )
        session.commit()

    cleanup = AIUsageRecorder(
        app.state.database.session_factory, retention_days=30, metrics_enabled=False
    )
    assert cleanup.cleanup_old_records() == 1
    assert (
        AIUsageRecorder(
            app.state.database.session_factory, retention_days=0, metrics_enabled=False
        ).cleanup_old_records()
        == 0
    )

    app.state.usage_recorder.record(
        provider="openai",
        operation="price_search",
        model="gpt-search",
        success=True,
        duration_ms=50,
        metadata=UsageMetadata(web_search_calls=1),
    )


def test_usage_recorder_handles_storage_failure() -> None:
    class BrokenContext:
        def __enter__(self):
            raise RuntimeError("database unavailable")

        def __exit__(self, exc_type, exc, tb):
            return False

    recorder = AIUsageRecorder(lambda: BrokenContext(), retention_days=1, metrics_enabled=False)
    recorder.record(
        provider="openai",
        operation="name_analysis",
        model="gpt-test",
        success=True,
        duration_ms=1,
    )
    assert recorder.cleanup_old_records() == 0


@pytest.mark.anyio
async def test_observed_email_sender_records_success_and_failure() -> None:
    message = OutgoingEmail("person@example.com", "Verify your email", "text", "<p>html</p>")
    memory = MemoryEmailSender()
    observed = ObservedEmailSender(memory, metrics_enabled=True)
    await observed.send(message)
    assert observed.messages == [message]

    class FailingSender:
        async def send(self, message):
            raise TimeoutError("smtp timed out")

    with pytest.raises(TimeoutError):
        await ObservedEmailSender(FailingSender(), metrics_enabled=True).send(message)
