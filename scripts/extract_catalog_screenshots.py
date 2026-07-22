"""Extract local price-catalog records from PNG/JPEG/PDF files using the reusable package API."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import httpx

from bourbonbook.catalog_cli import ingest_jsonl
from bourbonbook.catalog_extract import (
    DEFAULT_CHUNK_HEIGHT,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_TIMEOUT_SECONDS,
    extract_catalog_files,
)
from bourbonbook.config import Settings


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="Extract product names, current prices, and bottle sizes from local files."
    )
    argument_parser.add_argument(
        "inputs", nargs="+", type=Path, help="PNG/JPEG/PDF screenshot files"
    )
    argument_parser.add_argument(
        "--output", required=True, type=Path, help="Output JSON Lines file"
    )
    argument_parser.add_argument("--ingest", action="store_true", help="Upsert extracted records.")
    argument_parser.add_argument("--chunk-height", type=int, default=DEFAULT_CHUNK_HEIGHT)
    argument_parser.add_argument("--overlap", type=int, default=DEFAULT_CHUNK_OVERLAP)
    argument_parser.add_argument("--max-chunks", type=int, default=0)
    argument_parser.add_argument(
        "--price-updated-at", help="Known price date in YYYY-MM-DD format."
    )
    return argument_parser


async def extract(arguments: argparse.Namespace, settings: Settings) -> int:
    price_updated_at = arguments.price_updated_at or datetime.now(UTC).date().isoformat()
    try:
        datetime.strptime(price_updated_at, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("--price-updated-at must use YYYY-MM-DD") from exc
    async with httpx.AsyncClient(timeout=DEFAULT_CHUNK_TIMEOUT_SECONDS) as client:
        proposals = await extract_catalog_files(
            arguments.inputs,
            client,
            settings,
            chunk_height=arguments.chunk_height,
            overlap=arguments.overlap,
            max_chunks=arguments.max_chunks or None,
        )
    records = [
        {**proposal.as_record(), "price_updated_at": price_updated_at} for proposal in proposals
    ]
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8"
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
