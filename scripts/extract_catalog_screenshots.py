"""Extract local price-catalog records from user-supplied image and PDF files.

This program does not browse or scrape. It reads only local files and sends their rendered image
chunks to the configured Ollama vision model.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from bourbonbook.catalog_cli import ingest_jsonl
from bourbonbook.catalog_extract import deduplicate_catalog_items, parse_catalog_items
from bourbonbook.config import Settings

logger = logging.getLogger("bourbonbook.catalog_extract")

PROMPT = """Read every visible OHLQ product card in this screenshot crop. Return ONLY JSON:
{"items":[{"name":"exact product name","price":12.34,"size":"750 ML"}]}

Include a product only when name, current displayed price, and bottle size are all visible.
For sale cards, use the current `Now` price, not the crossed-out previous price. Do not infer,
combine cards, include availability, or return commentary."""


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description=("Extract product names, current prices, and bottle sizes from local files.")
    )
    argument_parser.add_argument("inputs", nargs="+", type=Path, help="PNG/JPEG screenshot files")
    argument_parser.add_argument(
        "--output", required=True, type=Path, help="Output JSON Lines file"
    )
    argument_parser.add_argument(
        "--ingest",
        action="store_true",
        help="Upsert extracted records into the local price catalog.",
    )
    argument_parser.add_argument("--chunk-height", type=int, default=2400)
    argument_parser.add_argument("--overlap", type=int, default=120)
    argument_parser.add_argument("--max-chunks", type=int, default=0)
    argument_parser.add_argument(
        "--price-updated-at",
        help="Known price date in YYYY-MM-DD format; defaults to the extraction date.",
    )
    return argument_parser


def image_chunks(path: Path, chunk_height: int, overlap: int) -> list[tuple[int, bytes]]:
    if chunk_height < 600 or overlap < 0 or overlap >= chunk_height:
        raise ValueError("chunk-height must be at least 600 and overlap must be smaller than it")
    with Image.open(path) as source:
        image = source.convert("RGB")
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


def document_chunks(path: Path, chunk_height: int, overlap: int) -> list[tuple[str, int, bytes]]:
    if path.suffix.lower() != ".pdf":
        return [(path.name, top, image) for top, image in image_chunks(path, chunk_height, overlap)]
    import fitz

    chunks: list[tuple[str, int, bytes]] = []
    with fitz.open(path) as document:
        for page_number, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            rendered = Image.open(io.BytesIO(pixmap.tobytes("jpeg"))).convert("RGB")
            encoded = io.BytesIO()
            rendered.save(encoded, format="JPEG", quality=92)
            chunks.append((f"{path.name}:page-{page_number}", 0, encoded.getvalue()))
    return chunks


async def extract_chunk(
    client: httpx.AsyncClient, settings: Settings, image: bytes
) -> list[dict[str, str | float]]:
    model = settings.ollama_vision_model or settings.ollama_model
    started = time.perf_counter()
    response = await client.post(
        f"{settings.ollama_url}/api/generate",
        json={
            "model": model,
            "prompt": PROMPT,
            "images": [base64.b64encode(image).decode("ascii")],
            "stream": False,
            "think": False,
            "format": "json",
            "options": {"temperature": 0, "num_ctx": 8192},
        },
    )
    response.raise_for_status()
    body: dict[str, Any] = response.json()
    result = parse_catalog_items(str(body.get("response") or body.get("thinking") or ""))
    logger.info(
        "catalog_screenshot_chunk_extracted model=%s items=%s duration_ms=%s",
        model,
        len(result),
        round((time.perf_counter() - started) * 1000),
    )
    return result


async def extract(arguments: argparse.Namespace, settings: Settings) -> int:
    all_items: list[dict[str, str | float]] = []
    limit = arguments.max_chunks or None
    timeout = httpx.Timeout(180.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        chunk_count = 0
        for path in arguments.inputs:
            if not path.is_file():
                raise FileNotFoundError(path)
            for label, top, chunk in document_chunks(
                path, arguments.chunk_height, arguments.overlap
            ):
                if limit is not None and chunk_count >= limit:
                    break
                logger.info("catalog_screenshot_chunk_started file=%s top=%s", label, top)
                all_items.extend(await extract_chunk(client, settings, chunk))
                chunk_count += 1
            if limit is not None and chunk_count >= limit:
                break
    price_updated_at = arguments.price_updated_at or datetime.now(UTC).date().isoformat()
    try:
        datetime.strptime(price_updated_at, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("--price-updated-at must use YYYY-MM-DD") from exc
    records = [
        {**item, "price_updated_at": price_updated_at}
        for item in deduplicate_catalog_items(all_items)
    ]
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8"
    )
    logger.info(
        "catalog_screenshot_extraction_completed records=%s output=%s",
        len(records),
        arguments.output,
    )
    if arguments.ingest:
        await ingest_jsonl(settings, arguments.output, allow_local_extract=True)
    return len(records)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    arguments = parser().parse_args()
    count = asyncio.run(extract(arguments, Settings.from_env()))
    print(f"Extracted {count} name, price, and size records to {arguments.output}.")


if __name__ == "__main__":
    main()
