from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from openai import APIError, AsyncOpenAI
from pydantic import BaseModel

from bourbonbook.analysis import normalize_analysis
from bourbonbook.config import Settings
from bourbonbook.observability import (
    UsageMetadata,
    bounded_error_type,
    current_usage_recorder,
    current_usage_user_id,
    openai_usage_metadata,
)

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


class PriceAnalysis(BaseModel):
    msrp: float | None
    secondary_price: float | None
    msrp_source_title: str | None
    msrp_source_url: str | None
    msrp_basis: str | None
    secondary_source_title: str | None
    secondary_source_url: str | None
    secondary_basis: str | None


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


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
    name: str, settings: Settings
) -> tuple[dict[str, float], list[dict[str, str]], str]:
    if not settings.openai_api_key:
        logger.warning("OpenAI price search unavailable: OPENAI_API_KEY is not configured")
        return {}, [], "unavailable"

    prompt = f"""Research current United States pricing for the exact whiskey {name!r}.

Return the standard-release price per bottle in USD. For MSRP, prioritize the producer, an
official state price book, or a reputable whiskey publication. For secondary_price, estimate fair
market value from recent completed auction or sale results, preferably from the last 24 months.
Do not use retailer asking prices, search snippets, Reddit estimates, or an edition/store pick that
does not exactly match. Use a single representative value rather than a range. Return null when
reliable evidence is unavailable or conflicting. Select one best direct source for each populated
price. Source titles and URLs must come from the web results. Keep each basis to one short sentence
in plain text without Markdown."""

    recorder = current_usage_recorder()
    start = time.perf_counter()
    metadata = UsageMetadata()
    try:
        async with AsyncOpenAI(api_key=settings.openai_api_key, timeout=120.0) as client:
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
        for kind, price in (("msrp", parsed.msrp), ("secondary", parsed.secondary_price)):
            url = getattr(parsed, f"{kind}_source_url")
            if price is None or not url or canonical_url(url) not in consulted_urls:
                continue
            prices["secondary_price" if kind == "secondary" else "msrp"] = price
            sources.append(
                {
                    "kind": kind,
                    "title": getattr(parsed, f"{kind}_source_title") or urlsplit(url).netloc,
                    "url": url,
                    "basis": getattr(parsed, f"{kind}_basis") or "",
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
