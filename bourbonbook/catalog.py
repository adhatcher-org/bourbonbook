from __future__ import annotations

import re
from typing import Any


def normalize_product_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


VERIFIED_PRODUCTS: dict[str, dict[str, Any]] = {
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
            "secondary_price": 156.0,
        },
    }
}


def verified_product(name: str) -> dict[str, Any] | None:
    normalized = normalize_product_name(name)
    for product in VERIFIED_PRODUCTS.values():
        aliases = {normalize_product_name(alias) for alias in product["aliases"]}
        if normalized in aliases:
            return dict(product["values"])
    return None

