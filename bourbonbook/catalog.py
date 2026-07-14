from __future__ import annotations

import re
from typing import Any


def normalize_product_name(value: str) -> str:
    value = value.lower().replace("’", "").replace("'", "")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def catalog_price_key(name: str, size: str | None) -> tuple[str, str]:
    """Return the stable local-cache key for an exact product and package size."""
    return normalize_product_name(name), normalize_product_name(size or "")


VERIFIED_PRODUCTS: dict[str, dict[str, Any]] = {
    "blantons-original-single-barrel": {
        "aliases": {
            "blantons original single barrel",
            "blantons the original single barrel",
            "blantons the original single barrel bourbon whiskey",
            "blantons the original single barrel kentucky straight bourbon whiskey",
            "blantons the original single barrel straight from the barrel",
        },
        "values": {
            "name": "Blanton's Original Single Barrel",
            "brand": "Blanton's",
            "release": "The Original Single Barrel",
            "edition": "Single Barrel",
            "spirit_type": "Bourbon",
            "distilled_by": "Buffalo Trace Distillery",
            "mash_bill": "Corn, rye, and malted barley (Buffalo Trace Mash Bill #2)",
            "proof": 93.0,
            "abv": 46.5,
            "size": "750ml",
        },
    },
    "weller-full-proof": {
        "aliases": {
            "weller full proof",
            "wl weller full proof",
            "w l weller full proof",
            "william larue weller full proof",
        },
        "values": {
            "name": "W.L. Weller Full Proof",
            "brand": "W.L. Weller",
            "release": "Full Proof",
            "spirit_type": "Kentucky Straight Bourbon Whiskey",
            "distilled_by": "Buffalo Trace Distillery",
            "mash_bill": "Wheated bourbon; exact mash bill undisclosed",
            "proof": 114.0,
            "abv": 57.0,
            "size": "750ml",
            "age_statement": "",
            "msrp": 60.0,
        },
    },
}


def verified_product(name: str) -> dict[str, Any] | None:
    normalized = normalize_product_name(name)
    for product in VERIFIED_PRODUCTS.values():
        aliases = {normalize_product_name(alias) for alias in product["aliases"]}
        if normalized in aliases:
            return dict(product["values"])
    return None


def verified_product_from_text(text: str) -> dict[str, Any] | None:
    normalized = normalize_product_name(text)
    if not normalized:
        return None
    for product in VERIFIED_PRODUCTS.values():
        aliases = {normalize_product_name(alias) for alias in product["aliases"]}
        if any(alias and alias in normalized for alias in aliases):
            return dict(product["values"])
    return None
