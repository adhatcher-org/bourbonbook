from __future__ import annotations

import contextlib
import contextvars
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import delete

from bourbonbook.logging_config import log_event
from bourbonbook.models import ApiUsage

logger = logging.getLogger(__name__)

_usage_recorder: contextvars.ContextVar[AIUsageRecorder | None] = contextvars.ContextVar(
    "usage_recorder", default=None
)
_usage_user_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "usage_user_id", default=None
)

HTTP_REQUESTS = Counter(
    "bourbonbook_http_requests_total",
    "HTTP requests by method, route template, and status class.",
    ("method", "route", "status_class"),
)
HTTP_DURATION = Histogram(
    "bourbonbook_http_request_duration_seconds",
    "HTTP request duration by method and route template.",
    ("method", "route"),
)
HTTP_IN_PROGRESS = Gauge(
    "bourbonbook_http_requests_in_progress",
    "HTTP requests currently in progress.",
    ("method", "route"),
)
AUTH_EVENTS = Counter(
    "bourbonbook_auth_events_total",
    "Authentication and account events.",
    ("event", "result"),
)
AI_REQUESTS = Counter(
    "bourbonbook_ai_requests_total",
    "AI provider requests.",
    ("provider", "operation", "model", "result"),
)
AI_DURATION = Histogram(
    "bourbonbook_ai_request_duration_seconds",
    "AI provider request duration.",
    ("provider", "operation", "model"),
)
AI_TOKENS = Counter(
    "bourbonbook_ai_tokens_total",
    "AI provider token or token-like counts.",
    ("provider", "operation", "model", "direction"),
)
OPENAI_WEB_SEARCH = Counter(
    "bourbonbook_openai_web_search_calls_total",
    "OpenAI web search calls used by operation and model.",
    ("operation", "model"),
)
EMAIL_DELIVERIES = Counter(
    "bourbonbook_email_deliveries_total",
    "Email delivery attempts.",
    ("kind", "result"),
)
EMAIL_DURATION = Histogram(
    "bourbonbook_email_delivery_duration_seconds",
    "Email delivery duration.",
    ("kind",),
)
PRICE_JOBS = Counter(
    "bourbonbook_price_jobs_total",
    "Price job outcomes.",
    ("result",),
)
PRICE_JOB_DURATION = Histogram(
    "bourbonbook_price_job_duration_seconds",
    "Price job duration by result.",
    ("result",),
)
PRICE_JOB_STATE = Gauge(
    "bourbonbook_price_jobs_current",
    "Current price jobs by state.",
    ("state",),
)


@dataclass(frozen=True)
class UsageMetadata:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    web_search_calls: int | None = None


def bounded_error_type(exc: BaseException) -> str:
    name = exc.__class__.__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "rate" in name:
        return "rate_limit"
    if "http" in name or "api" in name:
        return "provider_error"
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "parse_error"
    if isinstance(exc, OSError):
        return "network_error"
    return "unexpected"


def count_openai_web_search_calls(response: Any) -> int:
    return sum(
        1
        for item in getattr(response, "output", [])
        if getattr(item, "type", None) == "web_search_call"
    )


def openai_usage_metadata(response: Any) -> UsageMetadata:
    usage = getattr(response, "usage", None)
    if usage is None:
        return UsageMetadata(web_search_calls=count_openai_web_search_calls(response))
    data = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
    input_details = data.get("input_tokens_details") or {}
    output_details = data.get("output_tokens_details") or {}
    return UsageMetadata(
        input_tokens=data.get("input_tokens"),
        output_tokens=data.get("output_tokens"),
        total_tokens=data.get("total_tokens"),
        cached_input_tokens=input_details.get("cached_tokens"),
        reasoning_tokens=output_details.get("reasoning_tokens"),
        web_search_calls=count_openai_web_search_calls(response),
    )


def ollama_usage_metadata(body: dict[str, Any]) -> UsageMetadata:
    input_tokens = _optional_int(body.get("prompt_eval_count"))
    output_tokens = _optional_int(body.get("eval_count"))
    total = None
    if input_tokens is not None or output_tokens is not None:
        total = (input_tokens or 0) + (output_tokens or 0)
    return UsageMetadata(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total)


def ollama_duration_ms(body: dict[str, Any], fallback_ms: int) -> int:
    total_duration = _optional_int(body.get("total_duration"))
    if total_duration is None:
        return fallback_ms
    return max(0, round(total_duration / 1_000_000))


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def route_template(request: Any) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return str(path)
    return "unmatched"


def observe_http(method: str, route: str, status_code: int, duration_seconds: float) -> None:
    status_class = f"{status_code // 100}xx"
    HTTP_REQUESTS.labels(method, route, status_class).inc()
    HTTP_DURATION.labels(method, route).observe(duration_seconds)


def observe_auth_event(event: str, result: str) -> None:
    AUTH_EVENTS.labels(event, result).inc()


def metrics_response() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


@contextlib.contextmanager
def usage_context(recorder: AIUsageRecorder | None, user_id: int | None) -> Iterator[None]:
    recorder_token = _usage_recorder.set(recorder)
    user_token = _usage_user_id.set(user_id)
    try:
        yield
    finally:
        _usage_user_id.reset(user_token)
        _usage_recorder.reset(recorder_token)


def current_usage_recorder() -> AIUsageRecorder | None:
    return _usage_recorder.get()


def current_usage_user_id() -> int | None:
    return _usage_user_id.get()


class AIUsageRecorder:
    def __init__(self, session_factory, retention_days: int, metrics_enabled: bool) -> None:
        self.session_factory = session_factory
        self.retention_days = retention_days
        self.metrics_enabled = metrics_enabled

    def record(
        self,
        *,
        provider: str,
        operation: str,
        model: str,
        success: bool,
        duration_ms: int,
        metadata: UsageMetadata | None = None,
        error_type: str | None = None,
        user_id: int | None = None,
    ) -> None:
        metadata = metadata or UsageMetadata()
        bounded_error = (error_type or "")[:40] or None
        try:
            with self.session_factory() as session:
                session.add(
                    ApiUsage(
                        provider=provider[:40],
                        operation=operation[:40],
                        model=model[:120],
                        success=success,
                        error_type=bounded_error,
                        duration_ms=max(0, duration_ms),
                        input_tokens=metadata.input_tokens,
                        output_tokens=metadata.output_tokens,
                        total_tokens=metadata.total_tokens,
                        cached_input_tokens=metadata.cached_input_tokens,
                        reasoning_tokens=metadata.reasoning_tokens,
                        web_search_calls=metadata.web_search_calls,
                        user_id=user_id,
                    )
                )
                session.commit()
        except Exception:
            logger.exception(
                "Failed to persist API usage",
                extra={
                    "event": "usage_record_failure",
                    "provider": provider,
                    "operation": operation,
                },
            )
        self._record_metrics(provider, operation, model, success, duration_ms, metadata)
        log_event(
            logger,
            logging.INFO if success else logging.WARNING,
            "ai_request_completed",
            "AI provider request completed",
            provider=provider,
            operation=operation,
            model=model,
            result="success" if success else "failure",
            duration_ms=duration_ms,
            error_type=bounded_error,
        )

    def _record_metrics(
        self,
        provider: str,
        operation: str,
        model: str,
        success: bool,
        duration_ms: int,
        metadata: UsageMetadata,
    ) -> None:
        if not self.metrics_enabled:
            return
        result = "success" if success else "failure"
        AI_REQUESTS.labels(provider, operation, model, result).inc()
        AI_DURATION.labels(provider, operation, model).observe(duration_ms / 1000)
        token_values = {
            "input": metadata.input_tokens,
            "output": metadata.output_tokens,
            "cached_input": metadata.cached_input_tokens,
            "reasoning": metadata.reasoning_tokens,
        }
        for direction, value in token_values.items():
            if value:
                AI_TOKENS.labels(provider, operation, model, direction).inc(value)
        if provider == "openai" and metadata.web_search_calls:
            OPENAI_WEB_SEARCH.labels(operation, model).inc(metadata.web_search_calls)

    def cleanup_old_records(self) -> int:
        if self.retention_days <= 0:
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        try:
            with self.session_factory() as session:
                result = session.execute(delete(ApiUsage).where(ApiUsage.created_at < cutoff))
                session.commit()
                return int(result.rowcount or 0)
        except Exception:
            logger.exception(
                "Failed to clean API usage records",
                extra={"event": "usage_cleanup_failure"},
            )
            return 0


class ObservedEmailSender:
    def __init__(self, wrapped, metrics_enabled: bool) -> None:
        self.wrapped = wrapped
        self.metrics_enabled = metrics_enabled
        if hasattr(wrapped, "messages"):
            self.messages = wrapped.messages

    async def send(self, message) -> None:
        if "Verify" in message.subject:
            kind = "verification"
        elif "Reset" in message.subject:
            kind = "reset"
        else:
            kind = "security"
        start = time.perf_counter()
        try:
            await self.wrapped.send(message)
        except Exception as exc:
            duration = time.perf_counter() - start
            if self.metrics_enabled:
                EMAIL_DELIVERIES.labels(kind, "failure").inc()
                EMAIL_DURATION.labels(kind).observe(duration)
            log_event(
                logger,
                logging.WARNING,
                "email_delivery_failed",
                "Email delivery failed",
                kind=kind,
                error_type=bounded_error_type(exc),
                duration_ms=round(duration * 1000),
            )
            raise
        duration = time.perf_counter() - start
        if self.metrics_enabled:
            EMAIL_DELIVERIES.labels(kind, "success").inc()
            EMAIL_DURATION.labels(kind).observe(duration)
        log_event(
            logger,
            logging.INFO,
            "email_delivery_succeeded",
            "Email delivery succeeded",
            kind=kind,
            duration_ms=round(duration * 1000),
        )
