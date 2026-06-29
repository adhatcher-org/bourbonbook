from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from bourbonbook.config import Settings

logger = logging.getLogger(__name__)

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


async def request_analysis(
    prompt: str, settings: Settings, photo: Path | None = None
) -> tuple[dict[str, Any], str]:
    payload: dict[str, Any] = {
        "model": settings.ollama_model,
        "prompt": prompt,
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
    prompt = """You are a meticulous American-whiskey bottle archivist. Inspect the entire image,
including the neck label, main label, small-print proof/ABV line, handwritten barrel tag, and the
visible liquid level. Return ONLY one JSON object with these keys: name, brand, release, edition,
spirit_type, distilled_by, mash_bill, proof, abv, size, age_statement, barrel_number,
bottle_number, warehouse, floor, status, fill_level, msrp, secondary_price.

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
- If the exact product identity is unambiguous, established product knowledge may supply
  distilled_by and a general mash_bill such as "wheated bourbon". Never invent exact percentages.
- MSRP and secondary_price must always be null; a photograph cannot establish current prices.
- Use null for every uncertain or unreadable value. Numeric proof, ABV, and fill_level must not
  include symbols or units."""
    return await request_analysis(prompt, settings, photo)


async def analyze_bottle_name(name: str, settings: Settings) -> tuple[dict[str, Any], str]:
    prompt = f"""Identify the whiskey product named {name!r}. Return ONLY a JSON object with these
keys: name, brand, release, edition, spirit_type, distilled_by, mash_bill, proof, abv, size,
age_statement, barrel_number, bottle_number, warehouse, floor, msrp, secondary_price. This is an
ungrounded fallback: MSRP and secondary_price must always be null. Use null when a value is unknown
or varies by bottle. Numeric proof and ABV must not include symbols or units. Do not invent
barrel-specific information, mash-bill percentages, or facts you are not highly confident about."""
    return await request_analysis(prompt, settings)
