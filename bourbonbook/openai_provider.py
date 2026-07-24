from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from openai import APIError, AsyncOpenAI  # noqa: F401
from pydantic import BaseModel

from bourbonbook.analysis import canonical_url, normalize_analysis, price_search_prompt
from bourbonbook.config import Settings
from bourbonbook.logging_config import log_event
from bourbonbook.observability import (
    UsageMetadata,
    bounded_error_type,
    current_usage_recorder,
    current_usage_user_id,
    openai_usage_metadata,
)
from bourbonbook.provider_clients import openai_client_session

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
    ocr_text: str | None = None


class PriceAnalysis(BaseModel):
    msrp: float | None
    msrp_source_title: str | None
    msrp_source_url: str | None
    msrp_basis: str | None


def web_source_urls(response: Any) -> set[str]:
    urls: set[str] = set()
    for item in response.output:
        if getattr(item, "type", None) != "web_search_call":
            continue
        data = item.model_dump()
        for source in (data.get("action") or {}).get("sources") or []:
            if source.get("url"):
                urls.add(canonical_url(source["url"]))
    return urls


async def search_prices(
    name: str, settings: Settings, *, size: str | None = None
) -> tuple[dict[str, float], list[dict[str, str]], str]:
    if not settings.openai_api_key:
        logger.warning("OpenAI price search unavailable: OPENAI_API_KEY is not configured")
        return {}, [], "unavailable"

    prompt = price_search_prompt(name, size=size)

    recorder = current_usage_recorder()
    start = time.perf_counter()
    metadata = UsageMetadata()
    log_event(
        logger,
        logging.INFO,
        "openai_price_search_started",
        "OpenAI price search started",
        model=settings.openai_model,
    )
    try:
        async with openai_client_session(settings) as client:
            response = await client.responses.parse(
                model=settings.openai_model,
                tools=[{"type": "web_search", "search_context_size": "medium"}],
                include=["web_search_call.action.sources"],
                reasoning={"effort": "low"},
                input=prompt,
                text_format=PriceAnalysis,
            )
        metadata = openai_usage_metadata(response)
        parsed = response.output_parsed
        if parsed is None:
            if recorder:
                recorder.record(
                    provider="openai",
                    operation="price_search",
                    model=settings.openai_model,
                    success=False,
                    duration_ms=round((time.perf_counter() - start) * 1000),
                    metadata=metadata,
                    error_type="parse_error",
                    user_id=current_usage_user_id(),
                )
            logger.warning(
                "OpenAI price search unavailable: response did not contain parsed output"
            )
            return {}, [], "unavailable"

        consulted_urls = web_source_urls(response)
        prices: dict[str, float] = {}
        sources: list[dict[str, str]] = []
        for kind, price in (("msrp", parsed.msrp),):
            url = parsed.msrp_source_url
            if price is None or not url or canonical_url(url) not in consulted_urls:
                continue
            prices["msrp"] = price
            sources.append(
                {
                    "kind": kind,
                    "title": parsed.msrp_source_title or urlsplit(url).netloc,
                    "url": url,
                    "basis": parsed.msrp_basis or "",
                }
            )
        status = "complete" if prices else "unavailable"
        if recorder:
            recorder.record(
                provider="openai",
                operation="price_search",
                model=settings.openai_model,
                success=True,
                duration_ms=round((time.perf_counter() - start) * 1000),
                metadata=metadata,
                user_id=current_usage_user_id(),
            )
        log_event(
            logger,
            logging.INFO,
            "openai_price_search_completed",
            "OpenAI price search completed",
            model=settings.openai_model,
            result=status,
            duration_ms=round((time.perf_counter() - start) * 1000),
            sources_found=len(sources),
        )
        return prices, sources, status
    except (APIError, OSError, ValueError, TypeError) as exc:
        error_type = bounded_error_type(exc)
        if recorder:
            recorder.record(
                provider="openai",
                operation="price_search",
                model=settings.openai_model,
                success=False,
                duration_ms=round((time.perf_counter() - start) * 1000),
                metadata=metadata,
                error_type=error_type,
                user_id=current_usage_user_id(),
            )
        logger.warning(
            "OpenAI price search unavailable",
            extra={"event": "openai_price_search_failed", "error_type": error_type},
        )
        return {}, [], "unavailable"


async def request_analysis(
    prompt: str, settings: Settings, photo: Path | None = None
) -> tuple[dict[str, Any], str]:
    if not settings.openai_api_key:
        logger.warning("OpenAI analysis unavailable: OPENAI_API_KEY is not configured")
        return {}, "unavailable"

    recorder = current_usage_recorder()
    operation = "photo_analysis" if photo else "name_analysis"
    start = time.perf_counter()
    metadata = UsageMetadata()
    log_event(
        logger,
        logging.INFO,
        "openai_analysis_started",
        "OpenAI analysis started",
        operation=operation,
        model=settings.openai_model,
    )
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
        async with openai_client_session(settings) as client:
            response = await client.responses.parse(
                model=settings.openai_model,
                input=[{"role": "user", "content": content}],
                text_format=BottleAnalysis,
            )
        metadata = openai_usage_metadata(response)
        if response.output_parsed is None:
            if recorder:
                recorder.record(
                    provider="openai",
                    operation=operation,
                    model=settings.openai_model,
                    success=False,
                    duration_ms=round((time.perf_counter() - start) * 1000),
                    metadata=metadata,
                    error_type="parse_error",
                    user_id=current_usage_user_id(),
                )
            logger.warning("OpenAI analysis unavailable: response did not contain parsed output")
            return {}, "unavailable"
        values = response.output_parsed.model_dump(exclude_none=True)
        if recorder:
            recorder.record(
                provider="openai",
                operation=operation,
                model=settings.openai_model,
                success=True,
                duration_ms=round((time.perf_counter() - start) * 1000),
                metadata=metadata,
                user_id=current_usage_user_id(),
            )
        log_event(
            logger,
            logging.INFO,
            "openai_analysis_completed",
            "OpenAI analysis completed",
            operation=operation,
            model=settings.openai_model,
            result="success",
            duration_ms=round((time.perf_counter() - start) * 1000),
            fields_returned=len(values),
        )
        return normalize_analysis(values), "complete"
    except (APIError, OSError, ValueError, TypeError) as exc:
        error_type = bounded_error_type(exc)
        if recorder:
            recorder.record(
                provider="openai",
                operation=operation,
                model=settings.openai_model,
                success=False,
                duration_ms=round((time.perf_counter() - start) * 1000),
                metadata=metadata,
                error_type=error_type,
                user_id=current_usage_user_id(),
            )
        logger.warning(
            "OpenAI analysis unavailable",
            extra={
                "event": "openai_analysis_failed",
                "operation": operation,
                "error_type": error_type,
            },
        )
        return {}, "unavailable"
