from __future__ import annotations

from pathlib import Path
from typing import Any

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
    "secondary_price",
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
- MSRP and secondary_price must always be null; a photograph cannot establish current prices.
- Use null for every uncertain or unreadable value. Numeric proof, ABV, and fill_level must not
  include symbols or units."""


def name_prompt(name: str) -> str:
    return f"""Identify the whiskey product named {name!r}. Use null when a value is unknown or
varies by bottle. Numeric proof and ABV must not include symbols or units. Do not invent
barrel-specific information, mash-bill percentages, or facts you are not highly confident about.
This is an ungrounded lookup, so MSRP and secondary_price must always be null."""


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


async def analyze_bottle(photo: Path, settings: Settings) -> tuple[dict[str, Any], str]:
    if settings.analysis_provider == "openai":
        from bourbonbook.openai_provider import request_analysis

        return await request_analysis(PHOTO_PROMPT, settings, photo)
    if settings.analysis_provider == "ollama":
        from bourbonbook.ollama import request_analysis

        return await request_analysis(PHOTO_PROMPT, settings, photo)
    return {}, "unavailable"


async def analyze_bottle_name(name: str, settings: Settings) -> tuple[dict[str, Any], str]:
    prompt = name_prompt(name)
    if settings.analysis_provider == "openai":
        from bourbonbook.openai_provider import request_analysis

        return await request_analysis(prompt, settings)
    if settings.analysis_provider == "ollama":
        from bourbonbook.ollama import request_analysis

        return await request_analysis(prompt, settings)
    return {}, "unavailable"
