from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from bourbonbook.catalog import verified_product, verified_product_from_text
from bourbonbook.config import Settings

STANDARD_SIZES_ML = (50, 200, 375, 750, 1000, 1750)
SIZE_SNAP_TOLERANCE_ML = 15
PROOF_ABV_TOLERANCE = 1.0
SIZE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|millilit(?:er|re)s?|cl|l|lit(?:er|re)s?)")

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


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def price_search_prompt(name: str, *, size: str | None = None) -> str:
    size_requirement = f" in the {size!r} bottle size" if size else ""
    product = f"the exact whiskey {name!r}{size_requirement}"
    return f"""Research the current Ohio retail price for {product}.

Search OHLQ.com first and use its Sizes & Pricing value when an exact product and bottle-size match
is available. When a bottle size is supplied, reject prices for every other size. Treat that Ohio
retail price as MSRP for this collection. If OHLQ is inaccessible or
has no exact match, broaden the web search and use the producer, another official state price book,
or a reputable whiskey publication.
Do not use retailer asking prices, search snippets, Reddit estimates, secondary-market prices, or
an edition/store pick that does not exactly match. Use a single USD value rather than a range.
Return null when reliable evidence is unavailable or conflicting. Select one best direct source;
its title and URL must come from the web results. Keep the basis to one short sentence in plain text
without Markdown."""


def missing_fields(values: dict[str, Any]) -> list[str]:
    return [field for field in MISSING_FIELDS if values.get(field) in (None, "")]


def merge_analysis(
    base: dict[str, Any], extra: dict[str, Any], *, allow_msrp: bool = False
) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if key == "msrp" and not allow_msrp:
            continue
        if value in (None, ""):
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
    return merge_analysis(values, match, allow_msrp=True), True


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


def _as_float(value: Any) -> float | None:
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        pass
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if match is None:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


def reconcile_proof_and_abv(normalized: dict[str, Any]) -> None:
    """Derive a missing proof/ABV from the other, or resolve a mismatch deterministically.

    Proof is defined as exactly 2 x ABV, so the two fields never need to be independently
    guessed once one is known. When both are present but disagree beyond OCR/transcription
    noise, trust whichever value implies the higher proof: a dropped or misread digit is far
    more likely to understate a value than invent an extra one.
    """
    proof, abv = _as_float(normalized.get("proof")), _as_float(normalized.get("abv"))
    if proof is None and abv is None:
        return
    if proof is None:
        normalized["proof"] = round(abv * 2, 1)
        return
    if abv is None:
        normalized["abv"] = round(proof / 2, 1)
        return
    if abs(proof - abv * 2) > PROOF_ABV_TOLERANCE:
        winning_proof = max(proof, abv * 2)
        normalized["proof"] = round(winning_proof, 1)
        normalized["abv"] = round(winning_proof / 2, 1)


def snap_size(normalized: dict[str, Any]) -> None:
    """Snap a recognized bottle size to the nearest standard US spirits size.

    Sizes are read from a printed volume, not estimated, so small transcription noise around
    an obviously-standard bottle (e.g. 751ml) should resolve to the real packaged size (750ml)
    rather than being scored/stored as a one-off value.
    """
    size = normalized.get("size")
    if not size:
        return
    match = SIZE_PATTERN.fullmatch(str(size).strip().lower())
    if not match:
        return
    amount, unit = float(match.group(1)), match.group(2)
    multiplier = 1 if unit.startswith(("ml", "millil")) else 10 if unit == "cl" else 1000
    millilitres = amount * multiplier
    nearest = min(STANDARD_SIZES_ML, key=lambda candidate: abs(candidate - millilitres))
    if abs(nearest - millilitres) <= SIZE_SNAP_TOLERANCE_ML:
        normalized["size"] = f"{nearest}ml"


def normalize_analysis(values: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(values)
    reconcile_proof_and_abv(normalized)
    snap_size(normalized)
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


async def _refine_analysis(
    values: dict[str, Any], settings: Settings, *, source: str
) -> tuple[dict[str, Any], str]:
    prompt = analysis_prompt(values, source=source)
    refined, status = await _request_provider_analysis(prompt, settings)
    values = merge_analysis(values, refined)
    values, matched = enrich_from_verified_catalog(values)
    if matched or not missing_fields(values):
        return values, "verified" if matched else "complete"
    return values, status


async def analyze_bottle(photo: Path, settings: Settings) -> tuple[dict[str, Any], str]:
    values, status = await _request_provider_analysis(PHOTO_PROMPT, settings, photo)
    if not values:
        return values, status
    values, matched = enrich_from_verified_catalog(values)
    if matched:
        return values, "verified"
    if settings.analysis_provider == "ollama" and missing_fields(values):
        return await _refine_analysis(values, settings, source="transcribed bottle-label text")
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
        return await _refine_analysis(values, settings, source="known bottle name")
    return values, status


async def search_bottle_prices(
    name: str, settings: Settings, *, size: str | None = None
) -> tuple[dict[str, float], list[dict[str, str]], str]:
    if settings.analysis_provider == "openai":
        from bourbonbook.openai_provider import search_prices

        return await search_prices(name, settings, size=size)
    if settings.analysis_provider == "ollama":
        from bourbonbook.ollama_search import search_prices

        return await search_prices(name, settings, size=size)
    return {}, [], "unavailable"


async def warm_analysis_model(settings: Settings) -> None:
    """Best-effort pre-load of the vision model for providers with a real load cost.

    Only Ollama evicts and reloads a model between requests; OpenAI is a remote API with no
    load step to hide.
    """
    if settings.analysis_provider == "ollama":
        from bourbonbook.ollama import warm_vision_model

        await warm_vision_model(settings)
