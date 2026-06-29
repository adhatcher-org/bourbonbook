from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from bourbonbook.analysis import FIELDS, PHOTO_PROMPT, name_prompt, normalize_analysis
from bourbonbook.config import Settings

logger = logging.getLogger(__name__)

async def request_analysis(
    prompt: str, settings: Settings, photo: Path | None = None
) -> tuple[dict[str, Any], str]:
    field_list = ", ".join(FIELDS)
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
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(f"{settings.ollama_url}/api/generate", json=payload)
            response.raise_for_status()
        body = response.json()
        raw_output = body.get("response") or body.get("thinking")
        parsed = json.loads(raw_output)
        values = {key: parsed.get(key) for key in FIELDS if parsed.get(key) is not None}
        return normalize_analysis(values), "complete"
    except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError, OSError) as exc:
        logger.warning("Bottle analysis unavailable: %s", exc)
        return ({}, "unavailable")


async def analyze_bottle(photo: Path, settings: Settings) -> tuple[dict[str, Any], str]:
    return await request_analysis(PHOTO_PROMPT, settings, photo)


async def analyze_bottle_name(name: str, settings: Settings) -> tuple[dict[str, Any], str]:
    return await request_analysis(name_prompt(name), settings)
