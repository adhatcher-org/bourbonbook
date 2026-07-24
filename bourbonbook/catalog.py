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
    "blantons-straight-from-the-barrel": {
        "aliases": {
            "blantons straight from the barrel",
            "blantons straight from barrel",
            "blantons sftb",
        },
        "values": {
            "name": "Blanton's Straight From The Barrel",
            "brand": "Blanton's",
            "release": "Straight From The Barrel",
            "edition": "Single Barrel",
            "spirit_type": "Bourbon",
            "distilled_by": "Buffalo Trace Distillery",
            "mash_bill": "Corn, rye, and malted barley (Buffalo Trace Mash Bill #2)",
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
    "new-riff-8-year": {
        "aliases": {
            "new riff 8 year old kentucky straight bourbon whiskey",
            "new riff kentucky straight bourbon whiskey 8 years",
            "new riff 8 years",
        },
        "values": {
            "name": "New Riff 8 Year Old Kentucky Straight Bourbon Whiskey",
            "brand": "New Riff",
            "release": "8 Year Old",
            "spirit_type": "Bourbon",
            "distilled_by": "New Riff Distilling",
            "mash_bill": "65% Corn, 30% Rye, 5% Malted Barley",
            "proof": 100.0,
            "abv": 50.0,
            "size": "750ml",
            "age_statement": "8 years",
            "msrp": 69.99,
        },
    },
    "weller-antique-107": {
        "aliases": {"weller antique 107", "wl weller antique 107", "weller 107"},
        "values": {
            "name": "Weller Antique 107",
            "brand": "Weller",
            "release": "Antique 107",
            "spirit_type": "Bourbon",
            "distilled_by": "Buffalo Trace Distillery",
            "mash_bill": "Wheated bourbon; exact mash bill undisclosed",
            "proof": 107.0,
            "abv": 53.5,
            "size": "750ml",
        },
    },
    "weller-special-reserve": {
        "aliases": {"weller special reserve", "wl weller special reserve", "weller green label"},
        "values": {
            "name": "Weller Special Reserve",
            "brand": "Weller",
            "release": "Special Reserve",
            "spirit_type": "Bourbon",
            "distilled_by": "Buffalo Trace Distillery",
            "mash_bill": "Wheated bourbon; exact mash bill undisclosed",
            "proof": 90.0,
            "abv": 45.0,
            "size": "750ml",
        },
    },
    "eagle-rare-10": {
        "aliases": {"eagle rare", "eagle rare 10", "eagle rare 10 year"},
        "values": {
            "name": "Eagle Rare 10 Year",
            "brand": "Eagle Rare",
            "release": "10 Year",
            "spirit_type": "Bourbon",
            "distilled_by": "Buffalo Trace Distillery",
            "mash_bill": "Corn, rye, and malted barley (Buffalo Trace Mash Bill #1, low rye)",
            "proof": 90.0,
            "abv": 45.0,
            "size": "750ml",
            "age_statement": "10 years",
        },
    },
    "eh-taylor-small-batch": {
        "aliases": {
            "colonel eh taylor jr small batch",
            "colonel e h taylor jr small batch",
            "e h taylor small batch",
            "eh taylor small batch",
        },
        "values": {
            "name": "Colonel E.H. Taylor Jr. Small Batch",
            "brand": "Colonel E.H. Taylor Jr.",
            "release": "Small Batch",
            "spirit_type": "Bourbon",
            "distilled_by": "Buffalo Trace Distillery",
            "mash_bill": "Corn, rye, and malted barley (Buffalo Trace Mash Bill #1, low rye)",
            "proof": 100.0,
            "abv": 50.0,
            "size": "750ml",
        },
    },
    "buffalo-trace": {
        "aliases": {"buffalo trace", "buffalo trace kentucky straight bourbon whiskey"},
        "values": {
            "name": "Buffalo Trace",
            "brand": "Buffalo Trace",
            "spirit_type": "Bourbon",
            "distilled_by": "Buffalo Trace Distillery",
            "mash_bill": "Corn, rye, and malted barley (Buffalo Trace Mash Bill #1, low rye)",
            "proof": 90.0,
            "abv": 45.0,
            "size": "750ml",
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
