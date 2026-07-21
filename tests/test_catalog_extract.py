import asyncio
import json

from sqlalchemy import select

from bourbonbook.catalog_cli import catalog_record, ingest_jsonl
from bourbonbook.catalog_extract import (
    canonical_size,
    deduplicate_catalog_items,
    parse_catalog_items,
)
from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.models import CatalogPrice


def test_parse_catalog_items_keeps_name_current_price_and_size() -> None:
    result = parse_catalog_items(
        '{"items":[{"name":"Example Bourbon","price":"Now $54.99","size":"750 ML"},'
        '{"name":"Incomplete","price":12.0,"size":""}]}'
    )

    assert result == [{"name": "Example Bourbon", "size": "750ML", "msrp": 54.99}]


def test_catalog_item_deduplication_preserves_different_sizes() -> None:
    items = [
        {"name": "Example Bourbon", "size": "750ML", "msrp": 54.99},
        {"name": "Example Bourbon", "size": "750ML", "msrp": 54.99},
        {"name": "Example Bourbon", "size": "1L", "msrp": 70.0},
    ]

    assert deduplicate_catalog_items(items) == [
        {"name": "Example Bourbon", "size": "1L", "msrp": 70.0},
        {"name": "Example Bourbon", "size": "750ML", "msrp": 54.99},
    ]


def test_canonical_size_rejects_non_bottle_sizes() -> None:
    assert canonical_size("1.75 L") == "1.75L"
    assert canonical_size("750 ML") == "750ML"
    assert canonical_size("Other Sizes") == ""


def test_local_screenshot_record_uses_the_supplied_price_date() -> None:
    record = catalog_record(
        {
            "name": "Example Bourbon",
            "size": "750ML",
            "msrp": 54.99,
            "price_updated_at": "2026-07-20",
        },
        allow_local_extract=True,
    )

    assert record[3] == "Local screenshot catalog"
    assert record[4] == ""
    assert record[6].isoformat() == "2026-07-20T00:00:00+00:00"


def test_local_screenshot_ingest_creates_and_updates_catalog_price(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'catalog.db'}",
        session_secret="test",
        secure_cookies=False,
        ollama_url="http://ollama.invalid",
        ollama_model="test",
        max_users=1,
        max_upload_mb=1,
    )
    records = tmp_path / "records.jsonl"
    records.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "name": "Example Bourbon",
                        "size": "750ML",
                        "msrp": 49.99,
                        "price_updated_at": "2026-07-01",
                    }
                ),
                json.dumps(
                    {
                        "name": "Example Bourbon",
                        "size": "750ML",
                        "msrp": 54.99,
                        "price_updated_at": "2026-07-20",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    assert asyncio.run(ingest_jsonl(settings, records, allow_local_extract=True)) == 2

    database = Database(settings)
    try:
        with database.session_factory() as session:
            prices = list(session.scalars(select(CatalogPrice)))
        assert len(prices) == 1
        assert prices[0].msrp == 54.99
        assert prices[0].checked_at.date().isoformat() == "2026-07-20"
    finally:
        database.engine.dispose()
