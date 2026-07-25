from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any

from openai import APIError

from bourbonbook.analysis import normalize_analysis
from bourbonbook.config import Settings
from bourbonbook.logging_config import log_event
from bourbonbook.observability import (
    UsageMetadata,
    bounded_error_type,
    current_usage_recorder,
    current_usage_user_id,
)
from bourbonbook.openai_provider import BottleAnalysis
from bourbonbook.provider_clients import vllm_client_session

logger = logging.getLogger(__name__)


def vllm_usage_metadata(response: Any) -> UsageMetadata:
    usage = getattr(response, "usage", None)
    if usage is None:
        return UsageMetadata()
    return UsageMetadata(
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )


def resolution_overrides(settings: Settings) -> dict[str, Any] | None:
    """vLLM's Qwen2-VL family accepts explicit min/max pixel bounds via mm_processor_kwargs.

    Left unset, Ollama-style default preprocessing can downsample a whole-bottle photo enough
    to lose small print (barrel/rick/warehouse stamps); exposing this lets a deployer trade
    latency for legibility instead of guessing at the model's default.
    """
    mm_kwargs = {
        key: value
        for key, value in (
            ("min_pixels", settings.vllm_min_pixels),
            ("max_pixels", settings.vllm_max_pixels),
        )
        if value is not None
    }
    return {"mm_processor_kwargs": mm_kwargs} if mm_kwargs else None


async def request_analysis(
    prompt: str, settings: Settings, photo: Path | None = None
) -> tuple[dict[str, Any], str]:
    if not settings.vllm_base_url or not settings.vllm_model:
        logger.warning("vLLM analysis unavailable: VLLM_BASE_URL/VLLM_MODEL is not configured")
        return {}, "unavailable"

    recorder = current_usage_recorder()
    operation = "photo_analysis" if photo else "name_analysis"
    start = time.perf_counter()
    metadata = UsageMetadata()
    log_event(
        logger,
        logging.INFO,
        "vllm_analysis_started",
        "vLLM analysis started",
        operation=operation,
        model=settings.vllm_model,
    )
    try:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        extra_body: dict[str, Any] = {}
        if photo:
            encoded = base64.b64encode(photo.read_bytes()).decode("ascii")
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}
            )
            overrides = resolution_overrides(settings)
            if overrides:
                extra_body.update(overrides)
        async with vllm_client_session(settings) as client:
            response = await client.chat.completions.parse(
                model=settings.vllm_model,
                messages=[{"role": "user", "content": content}],
                response_format=BottleAnalysis,
                extra_body=extra_body or None,
            )
        metadata = vllm_usage_metadata(response)
        parsed = response.choices[0].message.parsed
        if parsed is None:
            if recorder:
                recorder.record(
                    provider="vllm",
                    operation=operation,
                    model=settings.vllm_model,
                    success=False,
                    duration_ms=round((time.perf_counter() - start) * 1000),
                    metadata=metadata,
                    error_type="parse_error",
                    user_id=current_usage_user_id(),
                )
            logger.warning("vLLM analysis unavailable: response did not contain parsed output")
            return {}, "unavailable"
        values = parsed.model_dump(exclude_none=True)
        if recorder:
            recorder.record(
                provider="vllm",
                operation=operation,
                model=settings.vllm_model,
                success=True,
                duration_ms=round((time.perf_counter() - start) * 1000),
                metadata=metadata,
                user_id=current_usage_user_id(),
            )
        log_event(
            logger,
            logging.INFO,
            "vllm_analysis_completed",
            "vLLM analysis completed",
            operation=operation,
            model=settings.vllm_model,
            result="success",
            duration_ms=round((time.perf_counter() - start) * 1000),
            fields_returned=len(values),
        )
        return normalize_analysis(values), "complete"
    except (APIError, OSError, ValueError, TypeError) as exc:
        error_type = bounded_error_type(exc)
        if recorder:
            recorder.record(
                provider="vllm",
                operation=operation,
                model=settings.vllm_model,
                success=False,
                duration_ms=round((time.perf_counter() - start) * 1000),
                metadata=metadata,
                error_type=error_type,
                user_id=current_usage_user_id(),
            )
        logger.warning(
            "vLLM analysis unavailable",
            extra={
                "event": "vllm_analysis_failed",
                "operation": operation,
                "error_type": error_type,
            },
        )
        return {}, "unavailable"
