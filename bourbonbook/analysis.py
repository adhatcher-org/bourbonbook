from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from bourbonbook.catalog import verified_product, verified_product_from_text
from bourbonbook.config import Settings

FIELDS = (
    "name",
    "brand",
    "release",
    "edition",
    "spirit_type",
    "distilled_by",
    "mash_bill",
    "proof",
    "abv",
    "size",
    "age_statement",
    "barrel_number",
    "bottle_number",
    "warehouse",
    "floor",
    "status",
    "fill_level",
    "msrp",
)
OUTPUT_FIELDS = FIELDS + ("ocr_text",)
MISSING_FIELDS = (
    "name",
    "brand",
    "release",
    "edition",
    "spirit_type",
    "distilled_by",
    "mash_bill",
    "proof",
    "abv",
    "size",
    "status",
    "fill_level",
)

PHOTO_PROMPT = """You are a meticulous American-whiskey bottle archivist. Inspect the entire image,
including the neck label, main label, small-print proof/ABV line, handwritten barrel tag, and the
visible liquid level.

Field rules:
- name is the concise full product name and must include the expression when visible, such as
  "Weller Full Proof", "Weller Antique 107", or "Blanton's Straight From The Barrel".
- brand is only the brand. release is the named expression (Full Proof, Antique 107, Straight From
  The Barrel, The Original Single Barrel). edition is a batch, vintage, store pick, or single-barrel
  designation. Never put a dumped/bottled date in release or edition.
- proof and ABV must come from the label's alcohol line, not barrel numbers, dates, age statements,
  or fill level. Proof must equal exactly 2 × ABV. Re-read the line if they disagree.
- size is only package volume such as 750ml, never an age statement.
- On a barrel tag, map text beside "Barrel No", "Bottle No", "Warehouse", "Floor", or "Rick No"
  to the corresponding field. Do not shift values between fields.
- Determine condition from the liquid boundary, not from whether a cap or seal is present. If amber
  liquid visibly continues through the shoulder and into the narrow neck with no meniscus in the
  wide body, the bottle is full: fill_level 100 and status Unopened. If a horizontal air/liquid
  boundary is visible in the shoulder or wide body, status is Opened and fill_level is the estimated
  percentage of the bottle's total capacity, rounded to the nearest 5. A meniscus near the middle of
  the body is roughly 40-50%, not 85%. At 0%, status is Empty. Status must agree with fill_level.
- Bottle-shape calibration matters. On a squat faceted Blanton's bottle, a liquid line near the top
  edge of the wide wraparound label is about 40%; it is not 85%. A full Blanton's has liquid through
  the rounded shoulder into the neck. On a tall cylindrical bottle, a line near mid-label is about
  50%.
- If the exact product identity is unambiguous, established product knowledge may supply
  distilled_by and a general mash_bill such as "wheated bourbon". Never invent exact percentages.
- Transcribe every readable bit of label text into ocr_text, preserving line breaks and
  small-print details when possible.
- MSRP must always be null; a photograph cannot establish current pricing.
- Use null for every uncertain or unreadable value. Numeric proof, ABV, and fill_level must not
  include symbols or units."""


def name_prompt(name: str) -> str:
    return f"""Identify the whiskey product named {name!r}. Use null when a value is unknown or
varies by bottle. Numeric proof and ABV must not include symbols or units. Do not invent
barrel-specific information, mash-bill percentages, or facts you are not highly confident about.
This is an ungrounded lookup, so MSRP must always be null."""


def missing_fields(values: dict[str, Any]) -> list[str]:
    return [field for field in MISSING_FIELDS if values.get(field) in (None, "")]


def merge_analysis(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if key == "msrp" or value in (None, ""):
            continue
        if merged.get(key) in (None, ""):
            merged[key] = value
    return merged


def enrich_from_verified_catalog(values: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    match = None
    for candidate in (values.get("name"), values.get("ocr_text")):
        if candidate:
            match = verified_product(candidate) or verified_product_from_text(candidate)
        if match:
            break
    if not match:
        return values, False
    return merge_analysis(values, match), True


def analysis_prompt(values: dict[str, Any], *, source: str) -> str:
    known = {
        key: value
        for key, value in values.items()
        if key in OUTPUT_FIELDS and value not in (None, "")
    }
    return f"""Use the {source} and the known bottle values to fill any missing fields.
Do not change any field already present in the JSON below.
Do not invent pricing. MSRP must stay null.
If the exact bottle is not certain, leave the field null.

Known values:
{json.dumps(known, indent=2, sort_keys=True, default=str)}

Return only JSON with these keys: {", ".join(OUTPUT_FIELDS)}."""


def normalize_analysis(values: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(values)
    fill_level = normalized.get("fill_level")
    try:
        fill = max(0, min(100, int(round(float(str(fill_level).rstrip("%"))))))
    except (TypeError, ValueError):
        return normalized
    if fill >= 90:
        normalized["fill_level"] = 100
        normalized["status"] = "Unopened"
    elif fill == 0:
        normalized["fill_level"] = 0
        normalized["status"] = "Empty"
    else:
        normalized["fill_level"] = fill
        normalized["status"] = "Opened"
    return normalized


async def _request_provider_analysis(
    prompt: str, settings: Settings, photo: Path | None = None
) -> tuple[dict[str, Any], str]:
    if settings.analysis_provider == "openai":
        from bourbonbook.openai_provider import request_analysis

        return await request_analysis(prompt, settings, photo)
    if settings.analysis_provider == "ollama":
        from bourbonbook.ollama import request_analysis

        return await request_analysis(prompt, settings, photo)
    return {}, "unavailable"


def _settings_for_provider(settings: Settings, provider: str) -> Settings:
    return replace(settings, analysis_provider=provider)


async def _refine_analysis(
    values: dict[str, Any], settings: Settings, photo: Path | None, *, source: str
) -> tuple[dict[str, Any], str]:
    prompt = analysis_prompt(values, source=source)
    refined, status = await _request_provider_analysis(prompt, settings, photo)
    values = merge_analysis(values, refined)
    values, matched = enrich_from_verified_catalog(values)
    if matched or not missing_fields(values):
        return values, "verified" if matched else "complete"
    if settings.openai_api_key:
        fallback_settings = _settings_for_provider(settings, "openai")
        fallback, fallback_status = await _request_provider_analysis(
            prompt, fallback_settings, photo
        )
        values = merge_analysis(values, fallback)
        values, matched = enrich_from_verified_catalog(values)
        if matched or not missing_fields(values):
            return values, "verified" if matched else "complete"
        return values, fallback_status if fallback_status == "complete" else status
    return values, status


async def analyze_bottle(photo: Path, settings: Settings) -> tuple[dict[str, Any], str]:
    values, status = await _request_provider_analysis(PHOTO_PROMPT, settings, photo)
    if not values:
        return values, status
    values, matched = enrich_from_verified_catalog(values)
    if matched:
        return values, "verified"
    if settings.analysis_provider == "ollama" and missing_fields(values):
        return await _refine_analysis(
            values, settings, photo, source="photo and transcribed bottle-label text"
        )
    return values, status


async def analyze_bottle_name(name: str, settings: Settings) -> tuple[dict[str, Any], str]:
    values, matched = enrich_from_verified_catalog({"name": name})
    if matched:
        return values, "verified"
    analyzed, status = await _request_provider_analysis(name_prompt(name), settings)
    if not analyzed:
        return {}, status
    values = merge_analysis(values, analyzed)
    values, matched = enrich_from_verified_catalog(values)
    if matched:
        return values, "verified"
    if values and settings.analysis_provider == "ollama" and missing_fields(values):
        return await _refine_analysis(values, settings, None, source="known bottle name")
    return values, status


async def search_bottle_prices(
    name: str, settings: Settings, *, size: str | None = None
) -> tuple[dict[str, float], list[dict[str, str]], str]:
    if not settings.openai_api_key:
        return {}, [], "unavailable"
    from bourbonbook.openai_provider import search_prices

    return await search_prices(name, settings, size=size)
