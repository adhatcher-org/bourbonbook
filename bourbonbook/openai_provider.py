from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Literal

from openai import APIError, AsyncOpenAI
from pydantic import BaseModel

from bourbonbook.analysis import normalize_analysis
from bourbonbook.config import Settings

logger = logging.getLogger(__name__)


class BottleAnalysis(BaseModel):
    name: str | None
    brand: str | None
    release: str | None
    edition: str | None
    spirit_type: str | None
    distilled_by: str | None
    mash_bill: str | None
    proof: float | None
    abv: float | None
    size: str | None
    age_statement: str | None
    barrel_number: str | None
    bottle_number: str | None
    warehouse: str | None
    floor: str | None
    status: Literal["Unopened", "Opened", "Empty"] | None
    fill_level: int | None
    msrp: None
    secondary_price: None


async def request_analysis(
    prompt: str, settings: Settings, photo: Path | None = None
) -> tuple[dict[str, Any], str]:
    if not settings.openai_api_key:
        logger.warning("OpenAI analysis unavailable: OPENAI_API_KEY is not configured")
        return {}, "unavailable"

    try:
        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        if photo:
            encoded = base64.b64encode(photo.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{encoded}",
                    "detail": "high",
                }
            )
        async with AsyncOpenAI(api_key=settings.openai_api_key, timeout=120.0) as client:
            response = await client.responses.parse(
                model=settings.openai_model,
                input=[{"role": "user", "content": content}],
                text_format=BottleAnalysis,
            )
        if response.output_parsed is None:
            logger.warning("OpenAI analysis unavailable: response did not contain parsed output")
            return {}, "unavailable"
        values = response.output_parsed.model_dump(exclude_none=True)
        return normalize_analysis(values), "complete"
    except (APIError, OSError, ValueError, TypeError) as exc:
        logger.warning("OpenAI analysis unavailable: %s", exc)
        return {}, "unavailable"
