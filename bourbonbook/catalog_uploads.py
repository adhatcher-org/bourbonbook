"""Validated, recoverable source-file staging for catalog-import batches."""

from __future__ import annotations

import io
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import fitz
from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

from bourbonbook.config import Settings

_CONTENT_TYPES = {
    "image/png": ("png", b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": ("jpg", b"\xff\xd8\xff"),
    "application/pdf": ("pdf", b"%PDF-"),
}
_TEMP_BATCH_DIRECTORY = re.compile(r"^\.[1-9][0-9]*-[0-9a-f]{32}\.tmp$")


@dataclass(frozen=True)
class StagedCatalogFile:
    extension: str
    content: bytes


def catalog_import_root(settings: Settings) -> Path:
    return settings.data_dir / "catalog-imports"


def catalog_import_batch_directory(settings: Settings, batch_id: int) -> Path:
    return catalog_import_root(settings) / str(batch_id)


def remove_catalog_import_batch_sources(settings: Settings, batch_id: int) -> None:
    shutil.rmtree(catalog_import_batch_directory(settings, batch_id), ignore_errors=True)


def validate_catalog_uploads(
    uploads: list[tuple[str | None, bytes]], settings: Settings
) -> list[StagedCatalogFile]:
    """Validate declared type, bytes, decodeability, and all aggregate limits."""
    if not uploads:
        raise HTTPException(status_code=400, detail="Upload PNG, JPEG, or PDF files.")
    if len(uploads) > settings.catalog_import_max_files:
        raise HTTPException(
            status_code=413,
            detail=f"Upload at most {settings.catalog_import_max_files} catalog files.",
        )

    per_file_limit = settings.max_upload_mb * 1024 * 1024
    total_limit = settings.catalog_import_max_total_mb * 1024 * 1024
    total_size = 0
    pdf_pages = 0
    staged: list[StagedCatalogFile] = []
    for content_type, content in uploads:
        accepted = _CONTENT_TYPES.get(content_type or "")
        if accepted is None:
            raise HTTPException(status_code=400, detail="Upload PNG, JPEG, or PDF files.")
        extension, signature = accepted
        if len(content) > per_file_limit:
            raise HTTPException(
                status_code=413,
                detail=f"Each catalog file must be smaller than {settings.max_upload_mb} MB.",
            )
        total_size += len(content)
        if total_size > total_limit:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Catalog upload total must be smaller than "
                    f"{settings.catalog_import_max_total_mb} MB."
                ),
            )
        if not content.startswith(signature):
            raise HTTPException(
                status_code=400, detail="Catalog file content does not match its type."
            )

        if extension == "pdf":
            try:
                with fitz.open(stream=content, filetype="pdf") as document:
                    page_count = document.page_count
            except (fitz.FileDataError, RuntimeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="Please choose a valid PDF.") from exc
            if page_count < 1:
                raise HTTPException(
                    status_code=400, detail="PDF files must contain at least one page."
                )
            pdf_pages += page_count
            if pdf_pages > settings.catalog_import_max_pdf_pages:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "Catalog PDFs may contain at most "
                        f"{settings.catalog_import_max_pdf_pages} pages in total."
                    ),
                )
        else:
            try:
                with Image.open(io.BytesIO(content)) as image:
                    image.verify()
            except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
                raise HTTPException(
                    status_code=400, detail="Please choose a valid catalog image."
                ) from exc
        staged.append(StagedCatalogFile(extension=extension, content=content))
    return staged


def stage_catalog_uploads(
    settings: Settings, batch_id: int, files: list[StagedCatalogFile]
) -> None:
    """Write a complete batch into a temporary directory then rename it into place."""
    root = catalog_import_root(settings)
    destination = catalog_import_batch_directory(settings, batch_id)
    temporary = root / f".{batch_id}-{uuid4().hex}.tmp"
    if destination.exists():
        raise RuntimeError("Catalog import batch source directory already exists")
    root.mkdir(parents=True, exist_ok=True)
    try:
        temporary.mkdir(mode=0o700)
        for source in files:
            name = f"{uuid4().hex}.{source.extension}"
            path = temporary / name
            path.write_bytes(source.content)
            path.chmod(0o600)
        temporary.replace(destination)
    except OSError:
        shutil.rmtree(temporary, ignore_errors=True)
        shutil.rmtree(destination, ignore_errors=True)
        raise


def cleanup_expired_catalog_import_sources(settings: Settings) -> None:
    """Remove only stale numeric batch directories; temporary directories are always stale."""
    root = catalog_import_root(settings)
    if not root.exists():
        return
    cutoff = settings.catalog_import_source_expiry_hours * 60 * 60
    now = time.time()
    for child in root.iterdir():
        if not child.is_dir() or (
            not child.name.isdigit() and not _TEMP_BATCH_DIRECTORY.fullmatch(child.name)
        ):
            continue
        try:
            expired = now - child.stat().st_mtime >= cutoff
        except OSError:
            continue
        if expired:
            shutil.rmtree(child, ignore_errors=True)
