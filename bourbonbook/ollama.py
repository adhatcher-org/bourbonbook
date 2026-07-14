from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from bourbonbook.analysis import OUTPUT_FIELDS, PHOTO_PROMPT, name_prompt, normalize_analysis
from bourbonbook.config import Settings
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


async def request_analysis(
    prompt: str, settings: Settings, photo: Path | None = None
) -> tuple[dict[str, Any], str]:
    field_list = ", ".join(OUTPUT_FIELDS)
    payload: dict[str, Any] = {
        "model": settings.ollama_model,
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
    try:
        async with ollama_client_session() as client:
            response = await client.post(f"{settings.ollama_url}/api/generate", json=payload)
            response.raise_for_status()
        body = response.json()
        fallback_ms = round((time.perf_counter() - start) * 1000)
        metadata = ollama_usage_metadata(body)
        raw_output = body.get("response") or body.get("thinking")
        parsed = json.loads(raw_output)
        values = {key: parsed.get(key) for key in OUTPUT_FIELDS if parsed.get(key) is not None}
        if recorder:
            recorder.record(
                provider="ollama",
                operation=operation,
                model=settings.ollama_model,
                success=True,
                duration_ms=ollama_duration_ms(body, fallback_ms),
                metadata=metadata,
                user_id=current_usage_user_id(),
            )
        return normalize_analysis(values), "complete"
    except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError, OSError) as exc:
        error_type = bounded_error_type(exc)
        if recorder:
            recorder.record(
                provider="ollama",
                operation=operation,
                model=settings.ollama_model,
                success=False,
                duration_ms=round((time.perf_counter() - start) * 1000),
                metadata=metadata,
                error_type=error_type,
                user_id=current_usage_user_id(),
            )
        logger.warning(
            "Bottle analysis unavailable",
            extra={
                "event": "ollama_analysis_failed",
                "operation": operation,
                "error_type": error_type,
            },
        )
        return ({}, "unavailable")


async def analyze_bottle(photo: Path, settings: Settings) -> tuple[dict[str, Any], str]:
    return await request_analysis(PHOTO_PROMPT, settings, photo)


async def analyze_bottle_name(name: str, settings: Settings) -> tuple[dict[str, Any], str]:
    return await request_analysis(name_prompt(name), settings)
