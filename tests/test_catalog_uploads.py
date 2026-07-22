from __future__ import annotations

import os
import time
from pathlib import Path

import fitz
import pytest
from fastapi import HTTPException

from bourbonbook.catalog_uploads import (
    catalog_import_batch_directory,
    cleanup_expired_catalog_import_sources,
    validate_catalog_uploads,
)
from bourbonbook.config import Settings
from bourbonbook.database import Database
from bourbonbook.migrations import bootstrap_database
from bourbonbook.models import CatalogImportBatch, User


def settings(tmp_path: Path, **changes: int) -> Settings:
    base = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        session_secret="test-secret",
        secure_cookies=False,
        ollama_url="http://ollama.invalid",
        ollama_model="test",
        max_users=1,
        max_upload_mb=2,
    )
    return Settings(**{**vars(base), **changes})


def pdf_with_pages(page_count: int) -> bytes:
    document = fitz.open()
    for _ in range(page_count):
        document.new_page()
    content = document.tobytes()
    document.close()
    return content


def jpeg(*, size: tuple[int, int] = (2, 2)) -> bytes:
    from io import BytesIO

    from PIL import Image

    content = BytesIO()
    Image.new("RGB", size, "white").save(content, "JPEG")
    return content.getvalue()


def png() -> bytes:
    from io import BytesIO

    from PIL import Image

    content = BytesIO()
    Image.new("RGB", (2, 2), "white").save(content, "PNG")
    return content.getvalue()


def create_batch(database: Database, *, state: str) -> int:
    with database.session_factory() as session:
        user = session.query(User).filter_by(email="admin@example.com").one_or_none()
        if user is None:
            user = User(
                username="admin@example.com",
                display_name="Admin",
                email="admin@example.com",
                screen_name="Admin",
                password_hash="not-used",
                is_admin=True,
            )
            session.add(user)
            session.flush()
        batch = CatalogImportBatch(created_by_user_id=user.id, state=state, source_file_count=1)
        session.add(batch)
        session.commit()
        return batch.id


def test_catalog_upload_validation_accepts_jpeg_and_pdf(tmp_path: Path) -> None:
    staged = validate_catalog_uploads(
        [("image/jpeg", jpeg()), ("application/pdf", pdf_with_pages(1))], settings(tmp_path)
    )

    assert [source.extension for source in staged] == ["jpg", "pdf"]


@pytest.mark.parametrize(
    ("content_type", "content", "message"),
    [
        ("image/jpeg", b"\xff\xd8\xffnot-an-image", "valid catalog image"),
        ("application/pdf", b"%PDF-not-a-document", "valid PDF"),
    ],
)
def test_catalog_upload_validation_rejects_malformed_decodable_types(
    tmp_path: Path, content_type: str, content: bytes, message: str
) -> None:
    with pytest.raises(HTTPException, match=message) as error:
        validate_catalog_uploads([(content_type, content)], settings(tmp_path))

    assert error.value.status_code == 400


def test_catalog_upload_validation_enforces_per_file_and_aggregate_byte_limits(
    tmp_path: Path,
) -> None:
    per_file = settings(tmp_path, max_upload_mb=1)
    with pytest.raises(HTTPException, match="Each catalog file") as individual:
        validate_catalog_uploads(
            [("image/png", b"\x89PNG\r\n\x1a\n" + b"x" * (1024 * 1024))], per_file
        )
    assert individual.value.status_code == 413

    aggregate = settings(tmp_path, max_upload_mb=2, catalog_import_max_total_mb=1)
    padded_png = png() + b"x" * (600 * 1024)
    with pytest.raises(HTTPException, match="upload total") as total:
        validate_catalog_uploads([("image/png", padded_png), ("image/png", padded_png)], aggregate)
    assert total.value.status_code == 413


def test_catalog_upload_validation_counts_pdf_pages_across_files(tmp_path: Path) -> None:
    configured = settings(tmp_path, catalog_import_max_pdf_pages=2)
    with pytest.raises(HTTPException, match="at most 2 pages") as error:
        validate_catalog_uploads(
            [
                ("application/pdf", pdf_with_pages(1)),
                ("application/pdf", pdf_with_pages(2)),
            ],
            configured,
        )
    assert error.value.status_code == 413


def test_catalog_upload_validation_rejects_decodable_image_pixel_bomb(tmp_path: Path) -> None:
    configured = settings(tmp_path, catalog_import_max_image_pixels=100)

    with pytest.raises(HTTPException, match="exceeds the allowed dimensions") as error:
        validate_catalog_uploads([("image/jpeg", jpeg(size=(11, 10)))], configured)

    assert error.value.status_code == 413


def test_catalog_upload_validation_rejects_pdf_before_oversized_rasterization(
    tmp_path: Path,
) -> None:
    document = fitz.open()
    document.new_page(width=100, height=72)
    content = document.tobytes()
    document.close()
    configured = settings(tmp_path, catalog_import_max_pdf_render_dimension=100)

    with pytest.raises(HTTPException, match="exceeds the allowed render dimensions") as error:
        validate_catalog_uploads([("application/pdf", content)], configured)

    assert error.value.status_code == 413


def test_catalog_upload_cleanup_uses_configured_expiry(tmp_path: Path) -> None:
    configured = settings(tmp_path, catalog_import_source_expiry_hours=1)
    bootstrap_database(configured)
    database = Database(configured)
    expired_id = create_batch(database, state="review")
    current_id = create_batch(database, state="review")
    expired = catalog_import_batch_directory(configured, expired_id)
    current = catalog_import_batch_directory(configured, current_id)
    expired.mkdir(parents=True)
    current.mkdir(parents=True)
    old = time.time() - 3601
    os.utime(expired, (old, old))

    with database.session_factory() as session:
        cleanup_expired_catalog_import_sources(configured, session)

    assert not expired.exists()
    assert current.exists()


def test_catalog_upload_cleanup_retains_expired_queued_batch_sources(tmp_path: Path) -> None:
    configured = settings(tmp_path, catalog_import_source_expiry_hours=1)
    bootstrap_database(configured)
    database = Database(configured)
    batch_id = create_batch(database, state="queued")
    source = catalog_import_batch_directory(configured, batch_id)
    source.mkdir(parents=True)
    old = time.time() - 3601
    os.utime(source, (old, old))

    with database.session_factory() as session:
        cleanup_expired_catalog_import_sources(configured, session)

    assert source.exists()


def test_catalog_upload_cleanup_expires_failed_batch_sources_after_retry_window(
    tmp_path: Path,
) -> None:
    configured = settings(tmp_path, catalog_import_source_expiry_hours=1)
    bootstrap_database(configured)
    database = Database(configured)
    batch_id = create_batch(database, state="failed")
    source = catalog_import_batch_directory(configured, batch_id)
    source.mkdir(parents=True)
    old = time.time() - 3601
    os.utime(source, (old, old))

    with database.session_factory() as session:
        cleanup_expired_catalog_import_sources(configured, session)

    assert not source.exists()


def test_catalog_upload_cleanup_removes_stale_temp_without_touching_other_paths(
    tmp_path: Path,
) -> None:
    configured = settings(tmp_path, catalog_import_source_expiry_hours=1)
    root = catalog_import_batch_directory(configured, 1).parent
    stale_temp = root / ".1-0123456789abcdef0123456789abcdef.tmp"
    protected = root / "operator-data.tmp"
    zero_id = root / ".0-0123456789abcdef0123456789abcdef.tmp"
    padded_id = root / ".0001-0123456789abcdef0123456789abcdef.tmp"
    stale_temp.mkdir(parents=True)
    protected.mkdir()
    zero_id.mkdir()
    padded_id.mkdir()
    old = time.time() - 3601
    os.utime(stale_temp, (old, old))
    os.utime(protected, (old, old))
    os.utime(zero_id, (old, old))
    os.utime(padded_id, (old, old))

    bootstrap_database(configured)
    database = Database(configured)
    with database.session_factory() as session:
        cleanup_expired_catalog_import_sources(configured, session)

    assert not stale_temp.exists()
    assert protected.exists()
    assert zero_id.exists()
    assert padded_id.exists()
