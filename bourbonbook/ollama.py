from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from bourbonbook.analysis import OUTPUT_FIELDS, PHOTO_PROMPT, name_prompt, normalize_analysis
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
    exc: BaseException, settings: Settings, operation: str, model: str, duration_ms: int
) -> dict[str, str | int]:
    """Return safe, operational details for an Ollama request failure."""
    endpoint = urlsplit(settings.ollama_url)
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
    field_list = ", ".join(OUTPUT_FIELDS)
    payload: dict[str, Any] = {
        "model": model,
        "prompt": f"{prompt}\nReturn ONLY one JSON object with these keys: {field_list}.",
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_ctx": 4096},
    }
    if photo:
        payload["images"] = [base64.b64encode(photo.read_bytes()).decode("ascii")]
    recorder = current_usage_recorder()
    operation = "photo_analysis" if photo else "name_analysis"
    start = time.perf_counter()
    metadata = UsageMetadata()
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
        raw_output = body.get("response") or body.get("thinking")
        parsed = json.loads(raw_output)
        values = {key: parsed.get(key) for key in OUTPUT_FIELDS if parsed.get(key) is not None}
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
    except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError, OSError) as exc:
        error_type = bounded_error_type(exc)
        duration_ms = round((time.perf_counter() - start) * 1000)
        context = failure_context(exc, settings, operation, model, duration_ms)
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
