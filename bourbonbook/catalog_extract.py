"""Reusable, local-only catalog screenshot extraction primitives.

The worker added by a later action owns persistence, retries, and lifecycle state.
This module deliberately only renders staged local files, asks an injected Ollama
client for one bounded chunk at a time, and returns validated proposal values.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
from PIL import Image

from bourbonbook.catalog import normalize_product_name
from bourbonbook.catalog_uploads import (
    PDF_RENDER_SCALE,
    CatalogInputLimitError,
    validate_catalog_image_dimensions,
    validate_catalog_pdf_render_dimensions,
)
from bourbonbook.config import Settings
from bourbonbook.logging_config import log_event

logger = logging.getLogger("catalog_extract")

CATALOG_EXTRACTION_PROMPT = """Read every visible product card in this screenshot crop.
Return ONLY JSON:
{"items":[{"name":"exact product name","price":12.34,"size":"750 ML"}]}

Include a product only when its visible name, current displayed price, and bottle size are all
visible. For sale cards, use the current `Now` price, not the crossed-out previous price. Do not
infer, combine cards, include availability, or return commentary."""
DEFAULT_CHUNK_HEIGHT = 2400
DEFAULT_CHUNK_OVERLAP = 120
DEFAULT_CHUNK_TIMEOUT_SECONDS = 120.0

PRICE_PATTERN = re.compile(r"\$?\s*([0-9]+(?:\.[0-9]{1,2})?)")
NOW_PRICE_PATTERN = re.compile(r"\bnow\b[^0-9$]*\$?\s*([0-9]+(?:\.[0-9]{1,2})?)", re.I)
SIZE_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(ml|l)\s*$", re.IGNORECASE)


class CatalogOllamaClient(Protocol):
    """The deliberately small interface required from an injected local Ollama client."""

    async def post(self, url: str, **kwargs: Any) -> httpx.Response: ...


class CatalogExtractionError(RuntimeError):
    """A bounded, safe error suitable for future batch-state mapping."""

    def __init__(self, failure_kind: str) -> None:
        super().__init__(f"Catalog extraction failed: {failure_kind}")
        self.failure_kind = failure_kind


@dataclass(frozen=True)
class CatalogExtractionProposal:
    """A normalized proposal value before a worker attaches it to a batch."""

    name: str
    size: str
    msrp: float

    def as_record(self) -> dict[str, str | float]:
        return {"name": self.name, "size": self.size, "msrp": self.msrp}


@dataclass(frozen=True)
class CatalogRenderChunk:
    """One JPEG image sent to the local vision model; it contains no persisted state."""

    label: str
    top: int
    image: bytes


def parse_catalog_items(raw: str) -> list[dict[str, str | float]]:
    """Strictly validate one model response, discarding incomplete item candidates."""
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
    text = str(value or "")
    match = NOW_PRICE_PATTERN.search(text) or PRICE_PATTERN.search(text)
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
    """Preserve distinct package sizes/prices while removing overlapping image-crop results."""
    catalog_items = list(items)
    logger.debug("Deduplicating %s catalog items", len(catalog_items))
    unique: dict[tuple[str, str, float], dict[str, str | float]] = {}
    for item in catalog_items:
        name = str(item["name"])
        size = str(item["size"])
        price = float(item["msrp"])
        unique[(normalize_product_name(name), normalize_product_name(size), price)] = item
    return sorted(unique.values(), key=lambda item: (str(item["name"]).lower(), str(item["size"])))


def catalog_proposals(items: Iterable[dict[str, str | float]]) -> list[CatalogExtractionProposal]:
    """Return the stable, normalized and de-duplicated contract for proposal persistence."""
    return [
        CatalogExtractionProposal(
            name=str(item["name"]), size=str(item["size"]), msrp=float(item["msrp"])
        )
        for item in deduplicate_catalog_items(items)
    ]


def image_chunks(
    path: Path,
    chunk_height: int = DEFAULT_CHUNK_HEIGHT,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    *,
    max_pixels: int = 20_000_000,
    max_dimension: int = 10_000,
) -> list[tuple[int, bytes]]:
    """Encode a local staged image as overlapping JPEG chunks without network access."""
    _validate_chunk_dimensions(chunk_height, overlap)
    with Image.open(path) as source:
        validate_catalog_image_dimensions(
            source, max_pixels=max_pixels, max_dimension=max_dimension
        )
        return _image_chunks(source.convert("RGB"), chunk_height, overlap)


def document_chunks(
    path: Path,
    chunk_height: int = DEFAULT_CHUNK_HEIGHT,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    *,
    max_image_pixels: int = 20_000_000,
    max_image_dimension: int = 10_000,
    max_pdf_render_pixels: int = 20_000_000,
    max_pdf_render_dimension: int = 10_000,
) -> list[CatalogRenderChunk]:
    """Render PNG/JPEG or every local PDF page into bounded vision-model chunks."""
    _validate_chunk_dimensions(chunk_height, overlap)
    if path.suffix.lower() != ".pdf":
        return [
            CatalogRenderChunk(path.name, top, image)
            for top, image in image_chunks(
                path,
                chunk_height,
                overlap,
                max_pixels=max_image_pixels,
                max_dimension=max_image_dimension,
            )
        ]

    import fitz

    chunks: list[CatalogRenderChunk] = []
    with fitz.open(path) as document:
        for page_number, page in enumerate(document, start=1):
            validate_catalog_pdf_render_dimensions(
                page,
                max_pixels=max_pdf_render_pixels,
                max_dimension=max_pdf_render_dimension,
            )
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(PDF_RENDER_SCALE, PDF_RENDER_SCALE), alpha=False
            )
            with Image.open(io.BytesIO(pixmap.tobytes("jpeg"))) as rendered:
                for top, image in _image_chunks(rendered.convert("RGB"), chunk_height, overlap):
                    chunks.append(CatalogRenderChunk(f"{path.name}:page-{page_number}", top, image))
    return chunks


async def extract_catalog_chunk(
    client: CatalogOllamaClient,
    settings: Settings,
    image: bytes,
    *,
    timeout_seconds: float = DEFAULT_CHUNK_TIMEOUT_SECONDS,
) -> list[dict[str, str | float]]:
    """Request one vision chunk with a fake-injectable client and bounded safe failures."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    model = settings.ollama_vision_model or settings.ollama_model
    started = time.perf_counter()
    payload = {
        "model": model,
        "prompt": CATALOG_EXTRACTION_PROMPT,
        "images": [base64.b64encode(image).decode("ascii")],
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"temperature": 0, "num_ctx": 8192},
    }
    try:
        async with asyncio.timeout(timeout_seconds):
            response = await client.post(
                f"{settings.ollama_url}/api/generate", json=payload, timeout=timeout_seconds
            )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise TypeError("Ollama response must be an object")
        raw = body.get("response") or body.get("thinking")
        if not isinstance(raw, str):
            raise TypeError("Ollama response is missing JSON output")
        result = parse_catalog_items(raw)
    except asyncio.CancelledError:
        raise
    except TimeoutError as exc:
        _log_failure(settings, model, "timeout", exc, started)
        raise CatalogExtractionError("timeout") from exc
    except httpx.TimeoutException as exc:
        _log_failure(settings, model, "timeout", exc, started)
        raise CatalogExtractionError("timeout") from exc
    except httpx.HTTPStatusError as exc:
        _log_failure(settings, model, "http_status", exc, started, exc.response.status_code)
        raise CatalogExtractionError("http_status") from exc
    except httpx.RequestError as exc:
        _log_failure(settings, model, "transport", exc, started)
        raise CatalogExtractionError("transport") from exc
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        _log_failure(settings, model, "invalid_response", exc, started)
        raise CatalogExtractionError("invalid_response") from exc
    log_event(
        logger,
        logging.INFO,
        "catalog_extraction_chunk_completed",
        "Catalog extraction chunk completed",
        model=model,
        result="success",
        duration_ms=round((time.perf_counter() - started) * 1000),
        items=len(result),
    )
    return result


async def extract_catalog_files(
    paths: Sequence[Path],
    client: CatalogOllamaClient,
    settings: Settings,
    *,
    chunk_height: int = DEFAULT_CHUNK_HEIGHT,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    max_chunks: int | None = None,
    timeout_seconds: float = DEFAULT_CHUNK_TIMEOUT_SECONDS,
) -> list[CatalogExtractionProposal]:
    """Render local staged files and return proposal values; persistence remains out of scope."""
    if max_chunks is not None and max_chunks < 1:
        raise ValueError("max_chunks must be positive when supplied")
    extracted: list[dict[str, str | float]] = []
    chunk_count = 0
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
        try:
            chunks = document_chunks(
                path,
                chunk_height,
                overlap,
                max_image_pixels=settings.catalog_import_max_image_pixels,
                max_image_dimension=settings.catalog_import_max_image_dimension,
                max_pdf_render_pixels=settings.catalog_import_max_pdf_render_pixels,
                max_pdf_render_dimension=settings.catalog_import_max_pdf_render_dimension,
            )
        except (
            CatalogInputLimitError,
            OSError,
            RuntimeError,
            ValueError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise CatalogExtractionError("unsafe_input") from exc
        for chunk in chunks:
            if max_chunks is not None and chunk_count >= max_chunks:
                break
            extracted.extend(
                await extract_catalog_chunk(
                    client, settings, chunk.image, timeout_seconds=timeout_seconds
                )
            )
            chunk_count += 1
        if max_chunks is not None and chunk_count >= max_chunks:
            break
    proposals = catalog_proposals(extracted)
    log_event(
        logger,
        logging.INFO,
        "catalog_extraction_completed",
        "Catalog extraction completed",
        result="success",
        proposals=len(proposals),
    )
    return proposals


def _validate_chunk_dimensions(chunk_height: int, overlap: int) -> None:
    if chunk_height < 600 or overlap < 0 or overlap >= chunk_height:
        raise ValueError("chunk-height must be at least 600 and overlap must be smaller than it")


def _image_chunks(image: Image.Image, chunk_height: int, overlap: int) -> list[tuple[int, bytes]]:
    chunks: list[tuple[int, bytes]] = []
    for top in range(0, image.height, chunk_height - overlap):
        bottom = min(top + chunk_height, image.height)
        crop = image.crop((0, top, image.width, bottom))
        encoded = io.BytesIO()
        crop.save(encoded, format="JPEG", quality=92)
        chunks.append((top, encoded.getvalue()))
        if bottom == image.height:
            break
    return chunks


def _log_failure(
    settings: Settings,
    model: str,
    failure_kind: str,
    exc: BaseException,
    started: float,
    http_status: int | None = None,
) -> None:
    endpoint = urlsplit(settings.ollama_url)
    log_event(
        logger,
        logging.WARNING,
        "catalog_extraction_chunk_failed",
        "Catalog extraction chunk failed",
        provider="ollama",
        operation="catalog_extraction",
        model=model,
        endpoint_scheme=endpoint.scheme or "unknown",
        endpoint_host=endpoint.hostname or "unknown",
        failure_kind=failure_kind,
        exception_type=exc.__class__.__name__,
        http_status=http_status,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
