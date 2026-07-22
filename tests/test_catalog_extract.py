import asyncio
import json
import logging
from pathlib import Path

import fitz
import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from bourbonbook import catalog_cli
from bourbonbook.catalog_cli import catalog_record, ingest_jsonl, reindex
from bourbonbook.catalog_extract import (
    CatalogExtractionError,
    CatalogExtractionProposal,
    canonical_size,
    deduplicate_catalog_items,
    document_chunks,
    extract_catalog_chunk,
    extract_catalog_files,
    parse_catalog_items,
    parse_price,
)
from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.models import CatalogPrice


class FakeResponse:
    def __init__(self, body: object, status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code

    def json(self) -> object:
        return self.body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://ollama.invalid/api/generate")
            raise httpx.HTTPStatusError(
                "status", request=request, response=httpx.Response(self.status_code)
            )


class FakeCatalogClient:
    def __init__(self, response: FakeResponse | BaseException) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def catalog_settings(tmp_path: Path, *, ollama_url: str = "http://ollama.invalid") -> Settings:
    return Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'catalog.db'}",
        session_secret="test",
        secure_cookies=False,
        ollama_url=ollama_url,
        ollama_model="fallback-model",
        ollama_vision_model="vision-model",
        max_users=1,
        max_upload_mb=1,
    )


def generated_image(path: Path, *, height: int = 1300) -> None:
    Image.new("RGB", (64, height), "white").save(path, format="PNG")


def generated_pdf(path: Path) -> None:
    document = fitz.open()
    document.new_page(width=72, height=72).insert_text((12, 36), "Catalog")
    document.save(path)
    document.close()


def test_reusable_extraction_renders_generated_image_and_returns_normalized_proposals(
    tmp_path,
) -> None:
    image_path = tmp_path / "catalog.png"
    generated_image(image_path)
    client = FakeCatalogClient(
        FakeResponse(
            {
                "response": '{"items":[{"name":"Example Bourbon","size":"750 ML",'
                '"price":"Was $59.99, Now $49.99"}]}'
            }
        )
    )

    proposals = asyncio.run(
        extract_catalog_files(
            [image_path], client, catalog_settings(tmp_path), chunk_height=600, overlap=0
        )
    )

    assert proposals == [CatalogExtractionProposal("Example Bourbon", "750ML", 49.99)]
    assert len(client.calls) == 3
    payload = client.calls[0]["json"]
    assert isinstance(payload, dict)
    assert "name, current displayed price, and bottle size" in str(payload["prompt"])
    assert "Now" in str(payload["prompt"])
    assert client.calls[0]["timeout"] == 120.0


def test_reusable_extraction_renders_generated_pdf_pages(tmp_path) -> None:
    pdf_path = tmp_path / "catalog.pdf"
    generated_pdf(pdf_path)

    chunks = document_chunks(pdf_path, chunk_height=600, overlap=0)

    assert len(chunks) == 1
    assert chunks[0].label == "catalog.pdf:page-1"
    assert chunks[0].top == 0
    assert chunks[0].image.startswith(b"\xff\xd8\xff")


@pytest.mark.parametrize(
    ("response", "failure_kind"),
    [
        (FakeResponse({"response": "not json"}), "invalid_response"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("offline"), "transport"),
    ],
)
def test_catalog_extraction_maps_malformed_and_provider_failures_to_bounded_errors(
    tmp_path, response, failure_kind, caplog
) -> None:
    caplog.set_level(logging.WARNING, logger="catalog_extract")
    settings = catalog_settings(tmp_path, ollama_url="http://secret@ollama.internal:11434")

    with pytest.raises(CatalogExtractionError, match=failure_kind) as raised:
        asyncio.run(extract_catalog_chunk(FakeCatalogClient(response), settings, b"image"))

    assert raised.value.failure_kind == failure_kind
    record = caplog.records[-1]
    assert record.event == "catalog_extraction_chunk_failed"
    assert record.failure_kind == failure_kind
    assert record.endpoint_host == "ollama.internal"
    assert "secret" not in caplog.text


def test_catalog_extraction_propagates_cancellation_without_logging_a_failure(
    tmp_path, caplog
) -> None:
    caplog.set_level(logging.WARNING, logger="catalog_extract")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            extract_catalog_chunk(
                FakeCatalogClient(asyncio.CancelledError()), catalog_settings(tmp_path), b"image"
            )
        )

    assert not caplog.records


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


def test_catalog_item_deduplication_handles_normalized_generator_items(caplog) -> None:
    items = (
        item
        for item in [
            {"name": "Example  Bourbon", "size": "750ML", "msrp": 54.99},
            {"name": "example bourbon", "size": "750ml", "msrp": 54.99},
        ]
    )

    caplog.set_level(logging.DEBUG, logger="catalog_extract")

    assert deduplicate_catalog_items(items) == [
        {"name": "example bourbon", "size": "750ml", "msrp": 54.99},
    ]
    assert "Deduplicating 2 catalog items" in caplog.messages


def test_canonical_size_rejects_non_bottle_sizes() -> None:
    assert canonical_size("1.75 L") == "1.75L"
    assert canonical_size("750 ML") == "750ML"
    assert canonical_size("Other Sizes") == ""


def test_catalog_extraction_rejects_invalid_items_and_price_values() -> None:
    assert parse_catalog_items('```json[{"name":"Good","size":"1.0 l","price":"$30"}, 1]```') == [
        {"name": "Good", "size": "1L", "msrp": 30.0}
    ]
    assert parse_price("not a price") is None
    assert parse_price("$0") is None
    assert parse_price("$100000") is None

    with pytest.raises(ValueError, match="items list"):
        parse_catalog_items('{"items": {}}')
    with pytest.raises(json.JSONDecodeError):
        parse_catalog_items("not json")


@pytest.mark.parametrize(
    ("record", "allow_local_extract", "message"),
    [
        ({"name": "", "msrp": 1, "url": "https://example.com"}, False, "name"),
        ({"name": "Example", "msrp": "nope", "url": "https://example.com"}, False, "msrp"),
        ({"name": "Example", "msrp": 1, "url": "file:///tmp/value"}, False, "HTTP"),
        ({"name": "Example", "msrp": 1}, False, "HTTP"),
        (
            {
                "name": "Example",
                "msrp": 1,
                "url": "https://example.com",
                "price_updated_at": "nope",
            },
            False,
            "YYYY-MM-DD",
        ),
    ],
)
def test_catalog_record_validates_required_provenance(
    record: dict[str, object], allow_local_extract: bool, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        catalog_record(record, allow_local_extract=allow_local_extract)


def test_catalog_record_defaults_to_current_timestamp_and_remote_title() -> None:
    record = catalog_record(
        {"name": "Example", "size": "750ML", "msrp": "11.5", "url": "https://x"}
    )

    assert record[:6] == ("Example", "750ML", 11.5, "Local catalog", "https://x", "")
    assert record[6].tzinfo is not None


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


def test_catalog_ingest_reports_line_context_and_reindex_is_local_only(tmp_path) -> None:
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
    records = tmp_path / "invalid.jsonl"
    records.write_text("\n{not json}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"invalid\.jsonl:2"):
        asyncio.run(ingest_jsonl(settings, records, allow_local_extract=True))
    assert asyncio.run(reindex(settings)) == 0


def test_catalog_cli_main_dispatches_each_command(monkeypatch, tmp_path, capsys) -> None:
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
    monkeypatch.setattr(catalog_cli.Settings, "from_env", lambda: settings)

    async def fake_ingest(*args, **kwargs):
        assert args[0] is settings
        assert kwargs["allow_local_extract"] is True
        return 2

    async def fake_reindex(received_settings):
        assert received_settings is settings
        return 3

    monkeypatch.setattr(catalog_cli, "ingest_jsonl", fake_ingest)
    monkeypatch.setattr(catalog_cli, "reindex", fake_reindex)
    monkeypatch.setattr(
        "sys.argv", ["catalog", "ingest-jsonl", "records.jsonl", "--allow-local-extract"]
    )
    catalog_cli.main()
    assert "Imported 2" in capsys.readouterr().out

    monkeypatch.setattr("sys.argv", ["catalog", "reindex"])
    catalog_cli.main()
    assert "Indexed 3" in capsys.readouterr().out
