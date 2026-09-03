from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from bourbonbook.analysis import (
    OUTPUT_FIELDS,
    PHOTO_OUTPUT_FIELDS,
    PHOTO_PROMPT,
    SchemaConformanceError,
    analysis_schema,
    name_prompt,
    normalize_analysis,
    validate_against_schema,
)
from bourbonbook.config import Settings
from bourbonbook.logging_config import log_event
from bourbonbook.observability import (
    UsageMetadata,
    bounded_error_type,
    current_usage_recorder,
    current_usage_user_id,
    ollama_duration_ms,
    ollama_usage_metadata,
)
from bourbonbook.provider_clients import ollama_client_session

logger = logging.getLogger(__name__)


def analysis_model(settings: Settings, photo: Path | None) -> str:
    if photo:
        return settings.ollama_vision_model or settings.ollama_model
    return settings.ollama_text_model or settings.ollama_model


def analysis_context_window(settings: Settings, photo: Path | None) -> int:
    """Resolve the fixed model role's context window in one place."""
    return settings.ollama_context_window(vision=photo is not None)


def endpoint_port(endpoint) -> int | str:
    try:
        if endpoint.port:
            return endpoint.port
    except ValueError:
        return "invalid"
    return 443 if endpoint.scheme == "https" else 80 if endpoint.scheme == "http" else "unknown"


def connection_reason(exc: httpx.ConnectError) -> str:
    detail = str(exc).lower()
    if "ssl" in detail or "certificate" in detail:
        return "tls_handshake"
    if "connection refused" in detail:
        return "connection_refused"
    if "no route" in detail:
        return "no_route"
    if "network is unreachable" in detail:
        return "network_unreachable"
    if any(
        marker in detail
        for marker in ("name or service not known", "nodename nor servname", "name resolution")
    ):
        return "dns_failure"
    return "connection_failed"


def failure_context(
    exc: BaseException,
    settings: Settings,
    operation: str,
    model: str,
    duration_ms: int,
    *,
    endpoint_url: str | None = None,
) -> dict[str, str | int]:
    """Return safe, operational details for an Ollama request failure."""
    endpoint = urlsplit(endpoint_url or settings.ollama_url)
    context: dict[str, str | int] = {
        "provider": "ollama",
        "operation": operation,
        "model": model,
        "endpoint_scheme": endpoint.scheme or "unknown",
        "endpoint_host": endpoint.hostname or "unknown",
        "endpoint_port": endpoint_port(endpoint),
        "failure_kind": "unexpected",
        "exception_type": exc.__class__.__name__,
        "duration_ms": duration_ms,
    }
    if isinstance(exc, httpx.HTTPStatusError):
        context["failure_kind"] = "http_status"
        context["http_status"] = exc.response.status_code
    elif isinstance(exc, httpx.TimeoutException):
        context["failure_kind"] = "timeout"
    elif isinstance(exc, httpx.ConnectError):
        reason = connection_reason(exc)
        context["failure_kind"] = "tls_error" if reason == "tls_handshake" else "connect_error"
        context["connection_reason"] = reason
    elif isinstance(exc, httpx.RequestError):
        context["failure_kind"] = "request_error"
    elif isinstance(exc, json.JSONDecodeError):
        context["failure_kind"] = "invalid_json"
    elif isinstance(exc, (KeyError, TypeError)):
        context["failure_kind"] = "invalid_response"
    elif isinstance(exc, OSError):
        context["failure_kind"] = "photo_read_error"
    return context


async def request_analysis(
    prompt: str, settings: Settings, photo: Path | None = None
) -> tuple[dict[str, Any], str]:
    model = analysis_model(settings, photo)
    has_photo = photo is not None
    output_fields = PHOTO_OUTPUT_FIELDS if has_photo else OUTPUT_FIELDS
    field_list = ", ".join(output_fields)
    structured = settings.ollama_structured_output
    schema = analysis_schema(photo=has_photo) if structured else None
    payload: dict[str, Any] = {
        "model": model,
        "prompt": f"{prompt}\nReturn ONLY one JSON object with these keys: {field_list}.",
        "stream": False,
        "think": False,
        "format": schema if structured else "json",
        "options": {
            "temperature": 0.1,
            "num_ctx": analysis_context_window(settings, photo),
        },
    }
    if photo:
        payload["images"] = [base64.b64encode(photo.read_bytes()).decode("ascii")]
    recorder = current_usage_recorder()
    operation = "photo_analysis" if photo else "name_analysis"
    start = time.perf_counter()
    metadata = UsageMetadata()
    incomplete_generation = False
    log_event(
        logger,
        logging.INFO,
        "ollama_analysis_started",
        "Ollama analysis started",
        operation=operation,
        model=model,
        endpoint_scheme=urlsplit(settings.ollama_url).scheme or "unknown",
        endpoint_host=urlsplit(settings.ollama_url).hostname or "unknown",
    )
    try:
        async with ollama_client_session() as client:
            response = await client.post(f"{settings.ollama_url}/api/generate", json=payload)
            response.raise_for_status()
        body = response.json()
        fallback_ms = round((time.perf_counter() - start) * 1000)
        metadata = ollama_usage_metadata(body)
        duration_ms = ollama_duration_ms(body, fallback_ms)
        # Which channel carries the output is a property of the model, not of the schema.
        # Measured on Ollama 0.32.13 (2026-08-23): thinking-capable models (qwen3-vl:8b,
        # qwen3.8:27b, qwen3.6:27b) emit a grammar-constrained object into `thinking` and leave
        # `response` empty, while a non-thinking model (qwen2.5-coder:7b) does the opposite. So
        # the fallback is load-bearing for model portability and is kept in both states; the
        # guarantee comes from validating the result below, not from trusting a channel.
        raw_output = body.get("response") or body.get("thinking")
        if structured and not (body.get("done") is True and body.get("done_reason") == "stop"):
            # A server abort (`done` false) or context exhaustion (`done_reason` "length")
            # returns a truncated string that may still parse. Refuse it before it can be
            # mistaken for a complete answer.
            incomplete_generation = True
            raw_output = None
        parsed = json.loads(raw_output)
        if structured and schema is not None:
            validate_against_schema(parsed, schema)
        values = {
            key: parsed.get(key) for key in output_fields if parsed.get(key) not in (None, "")
        }
        if recorder:
            recorder.record(
                provider="ollama",
                operation=operation,
                model=model,
                success=True,
                duration_ms=duration_ms,
                metadata=metadata,
                user_id=current_usage_user_id(),
            )
        log_event(
            logger,
            logging.INFO,
            "ollama_analysis_completed",
            "Ollama analysis completed",
            operation=operation,
            model=model,
            result="success",
            duration_ms=duration_ms,
            fields_returned=len(values),
        )
        return normalize_analysis(values), "complete"
    except (httpx.HTTPError, KeyError, TypeError, ValueError, OSError) as exc:
        error_type = bounded_error_type(exc)
        duration_ms = round((time.perf_counter() - start) * 1000)
        context = failure_context(exc, settings, operation, model, duration_ms)
        if incomplete_generation:
            context["failure_kind"] = "incomplete_generation"
        elif isinstance(exc, SchemaConformanceError):
            context["failure_kind"] = "schema_nonconforming"
        elif (
            structured
            and isinstance(exc, httpx.HTTPStatusError)
            and exc.response.status_code == 400
        ):
            # Ollama also returns 400 for an undecodable or oversized image, and no token
            # separating the two may be logged, so the name states the ambiguity.
            context["failure_kind"] = "schema_rejected_or_bad_request"
        if recorder:
            recorder.record(
                provider="ollama",
                operation=operation,
                model=model,
                success=False,
                duration_ms=duration_ms,
                metadata=metadata,
                error_type=error_type,
                user_id=current_usage_user_id(),
            )
        logger.warning(
            "Ollama analysis unavailable: operation=%(operation)s model=%(model)s "
            "endpoint=%(endpoint_scheme)s://%(endpoint_host)s:%(endpoint_port)s "
            "failure_kind=%(failure_kind)s connection_reason=%(connection_reason)s "
            "exception_type=%(exception_type)s http_status=%(http_status)s "
            "duration_ms=%(duration_ms)s",
            {
                **context,
                "connection_reason": context.get("connection_reason", "none"),
                "http_status": context.get("http_status", "none"),
            },
            extra={
                "event": "ollama_analysis_failed",
                "error_type": error_type,
                **context,
            },
        )
        return ({}, "unavailable")


async def analyze_bottle(photo: Path, settings: Settings) -> tuple[dict[str, Any], str]:
    return await request_analysis(PHOTO_PROMPT, settings, photo)


async def analyze_bottle_name(name: str, settings: Settings) -> tuple[dict[str, Any], str]:
    return await request_analysis(name_prompt(name), settings)


async def warm_vision_model(settings: Settings) -> None:
    """Best-effort pre-load of the vision model into Ollama's memory.

    Ollama evicts a model after its keep_alive window and pays a real reload cost (seconds
    to tens of seconds for a large model) on the next request. A bare {"model": ...} POST to
    /api/generate, with no prompt, loads the model without running inference. Calling this as
    soon as a user opens the add-bottle form hides that load time behind the time they spend
    filling out the rest of the form, instead of behind their photo upload. Failures here are
    silently non-fatal: this is a latency optimization, not a required step.
    """
    model = settings.ollama_vision_model or settings.ollama_model
    try:
        async with ollama_client_session() as client:
            response = await client.post(
                f"{settings.ollama_url}/api/generate",
                json={
                    "model": model,
                    "options": {"num_ctx": settings.ollama_context_window(vision=True)},
                },
            )
            response.raise_for_status()
        log_event(
            logger,
            logging.INFO,
            "ollama_model_warmed",
            "Ollama vision model warm-up requested",
            model=model,
        )
    except (httpx.HTTPError, OSError) as exc:
        logger.info(
            "Ollama model warm-up failed (non-fatal): model=%(model)s "
            "exception_type=%(exception_type)s",
            {"model": model, "exception_type": exc.__class__.__name__},
            extra={"event": "ollama_model_warm_failed", "model": model},
        )
