from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from bourbonbook.catalog import normalize_product_name

PRICE_PATTERN = re.compile(r"\$?\s*([0-9]+(?:\.[0-9]{1,2})?)")
SIZE_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(ml|l)\s*$", re.IGNORECASE)


def parse_catalog_items(raw: str) -> list[dict[str, str | float]]:
    """Validate one vision-model response without trusting its formatting."""
    value = json.loads(raw.removeprefix("```json").removesuffix("```").strip())
    items: Iterable[Any] = value.get("items", []) if isinstance(value, dict) else value
    if not isinstance(items, list):
        raise ValueError("catalog response must contain an items list")
    parsed: list[dict[str, str | float]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        size = canonical_size(item.get("size"))
        price = parse_price(item.get("price"))
        if name and size and price is not None:
            parsed.append({"name": name, "size": size, "msrp": price})
    return parsed


def parse_price(value: object) -> float | None:
    match = PRICE_PATTERN.search(str(value or ""))
    if not match:
        return None
    price = float(match.group(1))
    return price if 0 < price < 100_000 else None


def canonical_size(value: object) -> str:
    match = SIZE_PATTERN.match(str(value or ""))
    if not match:
        return ""
    amount, unit = match.groups()
    return f"{amount.rstrip('0').rstrip('.') if '.' in amount else amount}{unit.upper()}"


def deduplicate_catalog_items(
    items: Iterable[dict[str, str | float]],
) -> list[dict[str, str | float]]:
    """Preserve distinct package sizes while removing overlap between image crops."""
    unique: dict[tuple[str, str, float], dict[str, str | float]] = {}
    for item in items:
        name = str(item["name"])
        size = str(item["size"])
        price = float(item["msrp"])
        unique[(normalize_product_name(name), normalize_product_name(size), price)] = item
    return sorted(unique.values(), key=lambda item: (str(item["name"]).lower(), str(item["size"])))
