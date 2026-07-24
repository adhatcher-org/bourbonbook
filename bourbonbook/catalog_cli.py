from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select

from bourbonbook.catalog import catalog_price_key
from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.migrations import bootstrap_database
from bourbonbook.models import CatalogPrice
from bourbonbook.qdrant_prices import QdrantPriceIndex


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(
        description="Manage Bourbon Book's local price catalog."
    )
    commands = command_parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser(
        "ingest-jsonl", help="Import validated catalog prices from JSON Lines."
    )
    ingest.add_argument("path", type=Path)
    ingest.add_argument(
        "--allow-local-extract",
        action="store_true",
        help="Accept local image/PDF records without a source URL.",
    )
    commands.add_parser("reindex", help="Rebuild the configured Qdrant price index from SQLite.")
    return command_parser


def catalog_record(
    raw: dict[str, object], *, allow_local_extract: bool = False
) -> tuple[str, str, float, str, str, str, datetime]:
    name = str(raw.get("name") or "").strip()
    size = str(raw.get("size") or "").strip()
    try:
        msrp = float(raw["msrp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("msrp must be a positive number") from exc
    url = str(raw.get("url") or "").strip()
    if not name or msrp <= 0:
        raise ValueError("name and a positive msrp are required")
    if url and urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("url must use HTTP(S)")
    if not url and not allow_local_extract:
        raise ValueError("an HTTP(S) url is required unless --allow-local-extract is set")
    price_updated_at = str(raw.get("price_updated_at") or "").strip()
    if price_updated_at:
        try:
            checked_at = datetime.strptime(price_updated_at, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError as exc:
            raise ValueError("price_updated_at must use YYYY-MM-DD") from exc
    else:
        checked_at = datetime.now(UTC)
    return (
        name,
        size,
        msrp,
        str(raw.get("title") or ("Local screenshot catalog" if not url else "Local catalog")),
        url,
        str(raw.get("basis") or ""),
        checked_at,
    )


async def ingest_jsonl(settings: Settings, path: Path, *, allow_local_extract: bool = False) -> int:
    database = Database(settings)
    bootstrap_database(settings)
    index = QdrantPriceIndex(settings)
    await index.ensure_collection()
    imported = 0
    try:
        with database.session_factory() as session:
            for line_number, raw_line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not raw_line.strip():
                    continue
                try:
                    name, size, msrp, title, url, basis, checked_at = catalog_record(
                        json.loads(raw_line), allow_local_extract=allow_local_extract
                    )
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ValueError(f"{path}:{line_number}: {exc}") from exc
                product_key, size_key = catalog_price_key(name, size)
                price = session.scalar(
                    select(CatalogPrice).where(
                        CatalogPrice.product_key == product_key,
                        CatalogPrice.size_key == size_key,
                    )
                )
                if price is None:
                    price = CatalogPrice(
                        product_key=product_key, size_key=size_key, msrp=msrp, url=url
                    )
                    session.add(price)
                price.msrp = msrp
                price.title = title
                price.url = url
                price.basis = basis
                price.checked_at = checked_at
                session.flush()
                await index.upsert(price)
                imported += 1
            session.commit()
    finally:
        await index.close()
        database.engine.dispose()
    return imported


async def reindex(settings: Settings) -> int:
    database = Database(settings)
    bootstrap_database(settings)
    index = QdrantPriceIndex(settings)
    await index.ensure_collection()
    indexed = 0
    try:
        with database.session_factory() as session:
            for price in session.scalars(select(CatalogPrice)):
                indexed += int(await index.upsert(price))
    finally:
        await index.close()
        database.engine.dispose()
    return indexed


def main() -> None:
    arguments = parser().parse_args()
    settings = Settings.from_env()
    if arguments.command == "ingest-jsonl":
        count = asyncio.run(
            ingest_jsonl(
                settings, arguments.path, allow_local_extract=arguments.allow_local_extract
            )
        )
        print(f"Imported {count} local catalog price records.")
    else:
        count = asyncio.run(reindex(settings))
        print(f"Indexed {count} local catalog price records in Qdrant.")


if __name__ == "__main__":
    main()
