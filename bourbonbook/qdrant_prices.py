from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from collections import Counter
from dataclasses import dataclass

import httpx

from bourbonbook.catalog import normalize_product_name
from bourbonbook.config import Settings
from bourbonbook.logging_config import log_event
from bourbonbook.models import CatalogPrice

logger = logging.getLogger(__name__)

VECTOR_NAME = "product_text"
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class PriceMatch:
    catalog_price_id: int
    score: float


def sparse_text_vector(value: str) -> dict[str, list[int] | list[float]]:
    """Produce a stable local sparse vector without sending product names to an embedder."""
    tokens = TOKEN_PATTERN.findall(normalize_product_name(value))
    counts = Counter(
        int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:4], "big")
        for token in tokens
    )
    if not counts:
        return {"indices": [], "values": []}
    norm = math.sqrt(sum(count * count for count in counts.values()))
    indices = sorted(counts)
    return {
        "indices": indices,
        "values": [counts[index] / norm for index in indices],
    }


class QdrantPriceIndex:
    """A rebuildable local product-name retrieval index; SQLite remains authoritative."""

    def __init__(self, settings: Settings) -> None:
        self.url = settings.qdrant_url
        self.collection = settings.qdrant_price_collection
        headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
        self.client = httpx.AsyncClient(timeout=5.0, headers=headers) if self.url else None

    @property
    def enabled(self) -> bool:
        return self.client is not None and bool(self.url)

    async def close(self) -> None:
        if self.client:
            await self.client.aclose()

    async def ensure_collection(self) -> bool:
        if not self.client or not self.url:
            return False
        start = time.perf_counter()
        try:
            existing = await self.client.get(f"{self.url}/collections/{self.collection}/exists")
            existing.raise_for_status()
            if not bool(existing.json().get("result", {}).get("exists")):
                response = await self.client.put(
                    f"{self.url}/collections/{self.collection}",
                    json={"vectors": {}, "sparse_vectors": {VECTOR_NAME: {}}},
                )
                response.raise_for_status()
            log_event(
                logger,
                logging.INFO,
                "qdrant_price_index_ready",
                "Qdrant price index ready",
                collection=self.collection,
                duration_ms=round((time.perf_counter() - start) * 1000),
            )
            return True
        except httpx.HTTPError as exc:
            log_event(
                logger,
                logging.WARNING,
                "qdrant_price_index_unavailable",
                "Qdrant price index unavailable",
                collection=self.collection,
                error_type=exc.__class__.__name__,
                duration_ms=round((time.perf_counter() - start) * 1000),
            )
            return False

    async def upsert(self, price: CatalogPrice) -> bool:
        if not self.client or not self.url:
            return False
        vector = sparse_text_vector(price.product_key)
        if not vector["indices"]:
            return False
        try:
            response = await self.client.put(
                f"{self.url}/collections/{self.collection}/points",
                params={"wait": "true"},
                json={
                    "points": [
                        {
                            "id": price.id,
                            "vector": {VECTOR_NAME: vector},
                            "payload": {
                                "application": "bourbonbook",
                                "catalog_price_id": price.id,
                                "product_key": price.product_key,
                                "size_key": price.size_key,
                            },
                        }
                    ]
                },
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            log_event(
                logger,
                logging.WARNING,
                "qdrant_price_index_write_failed",
                "Qdrant price index write failed",
                collection=self.collection,
                error_type=exc.__class__.__name__,
            )
            return False

    async def find(self, product_key: str, size_key: str) -> PriceMatch | None:
        if not self.client or not self.url:
            return None
        vector = sparse_text_vector(product_key)
        if not vector["indices"]:
            return None
        start = time.perf_counter()
        try:
            response = await self.client.post(
                f"{self.url}/collections/{self.collection}/points/query",
                json={
                    "query": vector,
                    "using": VECTOR_NAME,
                    "limit": 1,
                    "with_payload": True,
                    "filter": {
                        "must": [
                            {"key": "application", "match": {"value": "bourbonbook"}},
                            {"key": "size_key", "match": {"value": size_key}},
                        ]
                    },
                },
            )
            response.raise_for_status()
            points = response.json().get("result", {}).get("points", [])
            if not points:
                return None
            point = points[0]
            payload = point.get("payload") or {}
            price_id = payload.get("catalog_price_id")
            if not isinstance(price_id, int):
                return None
            score = float(point.get("score") or 0)
            log_event(
                logger,
                logging.INFO,
                "qdrant_price_match_found",
                "Qdrant local price match found",
                collection=self.collection,
                score=round(score, 4),
                duration_ms=round((time.perf_counter() - start) * 1000),
            )
            return PriceMatch(catalog_price_id=price_id, score=score)
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            log_event(
                logger,
                logging.WARNING,
                "qdrant_price_search_failed",
                "Qdrant local price search failed",
                collection=self.collection,
                error_type=exc.__class__.__name__,
                duration_ms=round((time.perf_counter() - start) * 1000),
            )
            return None
