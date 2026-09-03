"""Analysis and pricing through a LiteLLM proxy.

LiteLLM fronts the same local Ollama models this app has always used, but speaks the
OpenAI ``/chat/completions`` surface instead of Ollama's native ``/api/generate``. Keeping
it as its own provider -- rather than a transport flag on the Ollama one -- means the model
roles, context windows, and token caps are configured per gateway: a LiteLLM route name is
an alias its own config defines, not the raw Ollama tag, so the two rarely match.

Ollama-only options such as ``num_ctx`` ride along as top-level request fields; LiteLLM
forwards parameters it does not itself consume to the backing provider.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from bourbonbook.analysis import (
    OUTPUT_FIELDS,
    PHOTO_OUTPUT_FIELDS,
    PHOTO_PROMPT,
    name_prompt,
    normalize_analysis,
    price_search_prompt,
)
from bourbonbook.config import Settings
from bourbonbook.logging_config import log_event
from bourbonbook.observability import (
    UsageMetadata,
    bounded_error_type,
    chat_completion_usage_metadata,
    current_usage_recorder,
    current_usage_user_id,
)
from bourbonbook.ollama import failure_context
from bourbonbook.ollama_search import (
    MAX_TOOL_ROUNDS,
    OLLAMA_CLOUD_URL,
    TOOL_USE_INSTRUCTIONS,
    WEB_FETCH_TOOL,
    WEB_SEARCH_TOOL,
    execute_tool_call,
    extract_prices,
)
from bourbonbook.provider_clients import litellm_client_session

logger = logging.getLogger(__name__)

PROVIDER = "litellm"


def analysis_model(settings: Settings, photo: Path | None) -> str:
    return settings.litellm_model_for(vision=photo is not None)


def completions_url(settings: Settings) -> str:
    return f"{settings.litellm_url}/chat/completions"


def request_headers(settings: Settings) -> dict[str, str]:
    """Authorize against the proxy when a key is configured.

    A self-hosted LiteLLM may run with no master key at all, so the header is omitted
    rather than sent empty when none is set.
    """
    if not settings.litellm_api_key:
        return {}
    return {"Authorization": f"Bearer {settings.litellm_api_key}"}


def chat_payload(
    settings: Settings,
    messages: list[dict[str, Any]],
    *,
    vision: bool,
    temperature: float = 0.1,
    json_object: bool = True,
) -> dict[str, Any]:
    """Build one chat-completions request with this role's fixed model configuration."""
    payload: dict[str, Any] = {
        "model": settings.litellm_model_for(vision=vision),
        "messages": messages,
        "stream": False,
        "temperature": temperature,
        # Ollama-native option, forwarded by LiteLLM to the backing model.
        "num_ctx": settings.litellm_context_window(vision=vision),
    }
    if json_object:
        payload["response_format"] = {"type": "json_object"}
    max_tokens = settings.litellm_max_output_tokens(vision=vision)
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    return payload


def image_message(prompt: str, photo: Path) -> dict[str, Any]:
    encoded = base64.b64encode(photo.read_bytes()).decode("ascii")
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
            },
        ],
    }


def assistant_message(body: dict[str, Any]) -> dict[str, Any]:
    """Return the assistant message of a chat completion, or raise for a malformed body."""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise TypeError("LiteLLM response contained no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise TypeError("LiteLLM choice contained no message")
    return message


def message_content(body: dict[str, Any]) -> str:
    """Return the assistant text of a chat completion, or raise when it is empty."""
    content = assistant_message(body).get("content")
    if not isinstance(content, str) or not content.strip():
        raise TypeError("LiteLLM message contained no content")
    return content


def _log_failure(
    exc: BaseException,
    settings: Settings,
    operation: str,
    model: str,
    duration_ms: int,
    error_type: str,
    *,
    endpoint_url: str | None = None,
) -> None:
    context = failure_context(
        exc,
        settings,
        operation,
        model,
        duration_ms,
        endpoint_url=endpoint_url or settings.litellm_url or "",
    )
    context["provider"] = PROVIDER
    logger.warning(
        "LiteLLM request unavailable: operation=%(operation)s model=%(model)s "
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
            "event": "litellm_request_failed",
            "error_type": error_type,
            **context,
        },
    )


async def request_analysis(
    prompt: str, settings: Settings, photo: Path | None = None
) -> tuple[dict[str, Any], str]:
    if not settings.litellm_url:
        logger.warning("LiteLLM analysis unavailable: LITELLM_URL is not configured")
        return {}, "unavailable"

    vision = photo is not None
    model = analysis_model(settings, photo)
    output_fields = PHOTO_OUTPUT_FIELDS if photo else OUTPUT_FIELDS
    field_list = ", ".join(output_fields)
    instructed = f"{prompt}\nReturn ONLY one JSON object with these keys: {field_list}."
    messages = (
        [image_message(instructed, photo)] if photo else [{"role": "user", "content": instructed}]
    )
    payload = chat_payload(settings, messages, vision=vision)

    recorder = current_usage_recorder()
    operation = "photo_analysis" if photo else "name_analysis"
    start = time.perf_counter()
    metadata = UsageMetadata()
    log_event(
        logger,
        logging.INFO,
        "litellm_analysis_started",
        "LiteLLM analysis started",
        operation=operation,
        model=model,
    )
    try:
        async with litellm_client_session() as client:
            response = await client.post(
                completions_url(settings), json=payload, headers=request_headers(settings)
            )
            response.raise_for_status()
        body = response.json()
        duration_ms = round((time.perf_counter() - start) * 1000)
        metadata = chat_completion_usage_metadata(body)
        parsed = json.loads(message_content(body))
        values = {key: parsed.get(key) for key in output_fields if parsed.get(key) is not None}
        if recorder:
            recorder.record(
                provider=PROVIDER,
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
            "litellm_analysis_completed",
            "LiteLLM analysis completed",
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
        if recorder:
            recorder.record(
                provider=PROVIDER,
                operation=operation,
                model=model,
                success=False,
                duration_ms=duration_ms,
                metadata=metadata,
                error_type=error_type,
                user_id=current_usage_user_id(),
            )
        _log_failure(exc, settings, operation, model, duration_ms, error_type)
        return {}, "unavailable"


async def analyze_bottle(photo: Path, settings: Settings) -> tuple[dict[str, Any], str]:
    return await request_analysis(PHOTO_PROMPT, settings, photo)


async def analyze_bottle_name(name: str, settings: Settings) -> tuple[dict[str, Any], str]:
    return await request_analysis(name_prompt(name), settings)


async def search_prices(
    name: str, settings: Settings, *, size: str | None = None
) -> tuple[dict[str, float], list[dict[str, str]], str]:
    """Research one price with the model behind LiteLLM driving Ollama Cloud web tools.

    The chat model is reached through the proxy; the grounded ``web_search`` / ``web_fetch``
    tools remain Ollama Cloud's and still need ``OLLAMA_API_KEY``. Without that key there is
    no cited source to require, so the answer is `unavailable` rather than an ungrounded guess.
    """
    if not settings.litellm_url:
        logger.warning("LiteLLM price search unavailable: LITELLM_URL is not configured")
        return {}, [], "unavailable"
    if not settings.ollama_api_key:
        logger.warning("LiteLLM price search unavailable: OLLAMA_API_KEY is not configured")
        return {}, [], "unavailable"

    model = settings.litellm_model_for(vision=False)
    prompt = f"{price_search_prompt(name, size=size)}\n\n{TOOL_USE_INSTRUCTIONS}"
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    consulted_urls: set[str] = set()
    last_endpoint_url = settings.litellm_url

    recorder = current_usage_recorder()
    start = time.perf_counter()
    metadata = UsageMetadata()
    log_event(
        logger,
        logging.INFO,
        "litellm_price_search_started",
        "LiteLLM price search started",
        model=model,
    )
    try:
        async with litellm_client_session() as client:
            for _ in range(MAX_TOOL_ROUNDS):
                last_endpoint_url = settings.litellm_url
                payload = chat_payload(settings, messages, vision=False, json_object=False)
                payload["tools"] = [WEB_SEARCH_TOOL, WEB_FETCH_TOOL]
                response = await client.post(
                    completions_url(settings), json=payload, headers=request_headers(settings)
                )
                response.raise_for_status()
                body = response.json()
                message = assistant_message(body)
                tool_calls = message.get("tool_calls") or []
                if not tool_calls:
                    duration_ms = round((time.perf_counter() - start) * 1000)
                    metadata = chat_completion_usage_metadata(body)
                    try:
                        parsed = json.loads(message.get("content") or "")
                    except json.JSONDecodeError:
                        parsed = {}
                    prices, sources = extract_prices(parsed, consulted_urls)
                    status = "complete" if prices else "unavailable"
                    if recorder:
                        recorder.record(
                            provider=PROVIDER,
                            operation="price_search",
                            model=model,
                            success=True,
                            duration_ms=duration_ms,
                            metadata=metadata,
                            user_id=current_usage_user_id(),
                        )
                    log_event(
                        logger,
                        logging.INFO,
                        "litellm_price_search_completed",
                        "LiteLLM price search completed",
                        model=model,
                        result=status,
                        duration_ms=duration_ms,
                        sources_found=len(sources),
                    )
                    return prices, sources, status

                messages.append(message)
                last_endpoint_url = OLLAMA_CLOUD_URL
                for call in tool_calls:
                    result = await execute_tool_call(client, call, settings, consulted_urls)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id", ""),
                            "content": json.dumps(result),
                        }
                    )

            duration_ms = round((time.perf_counter() - start) * 1000)
            if recorder:
                recorder.record(
                    provider=PROVIDER,
                    operation="price_search",
                    model=model,
                    success=False,
                    duration_ms=duration_ms,
                    metadata=metadata,
                    error_type="max_tool_rounds_exceeded",
                    user_id=current_usage_user_id(),
                )
            logger.warning(
                "LiteLLM price search unavailable: tool-calling loop exceeded %s rounds",
                MAX_TOOL_ROUNDS,
                extra={
                    "event": "litellm_price_search_failed",
                    "error_type": "max_tool_rounds_exceeded",
                },
            )
            return {}, [], "unavailable"
    except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError) as exc:
        error_type = bounded_error_type(exc)
        duration_ms = round((time.perf_counter() - start) * 1000)
        if recorder:
            recorder.record(
                provider=PROVIDER,
                operation="price_search",
                model=model,
                success=False,
                duration_ms=duration_ms,
                metadata=metadata,
                error_type=error_type,
                user_id=current_usage_user_id(),
            )
        _log_failure(
            exc,
            settings,
            "price_search",
            model,
            duration_ms,
            error_type,
            endpoint_url=last_endpoint_url,
        )
        return {}, [], "unavailable"


async def warm_vision_model(settings: Settings) -> None:
    """Best-effort pre-load of the vision model behind the proxy.

    LiteLLM has no bare load call, so this sends the smallest possible completion; the point
    is only to make Ollama resident before the user's photo arrives. Failures are non-fatal:
    this is a latency optimization, not a required step.
    """
    if not settings.litellm_url:
        return
    model = settings.litellm_model_for(vision=True)
    payload = chat_payload(
        settings,
        [{"role": "user", "content": "."}],
        vision=True,
        json_object=False,
    )
    payload["max_tokens"] = 1
    try:
        async with litellm_client_session() as client:
            response = await client.post(
                completions_url(settings), json=payload, headers=request_headers(settings)
            )
            response.raise_for_status()
        log_event(
            logger,
            logging.INFO,
            "litellm_model_warmed",
            "LiteLLM vision model warm-up requested",
            model=model,
        )
    except (httpx.HTTPError, OSError) as exc:
        logger.info(
            "LiteLLM model warm-up failed (non-fatal): model=%(model)s "
            "exception_type=%(exception_type)s",
            {"model": model, "exception_type": exc.__class__.__name__},
            extra={"event": "litellm_model_warm_failed", "model": model},
        )
