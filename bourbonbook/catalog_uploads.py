"""Validated, recoverable source-file staging for catalog-import batches."""

from __future__ import annotations

import io
import re
import shutil
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import fitz
from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from bourbonbook.config import Settings
from bourbonbook.models import CatalogImportBatch

_CONTENT_TYPES = {
    "image/png": ("png", b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": ("jpg", b"\xff\xd8\xff"),
    "application/pdf": ("pdf", b"%PDF-"),
}
_TEMP_BATCH_DIRECTORY = re.compile(r"^\.[1-9][0-9]*-[0-9a-f]{32}\.tmp$")
PDF_RENDER_SCALE = 2
_SOURCE_RETAINING_BATCH_STATES = frozenset({"queued", "extracting"})


@dataclass(frozen=True)
class StagedCatalogFile:
    extension: str
    content: bytes


class CatalogInputLimitError(ValueError):
    """A decoded input would exceed a configured memory or dimension budget."""


def catalog_import_root(settings: Settings) -> Path:
    return settings.data_dir / "catalog-imports"


def catalog_import_batch_directory(settings: Settings, batch_id: int) -> Path:
    return catalog_import_root(settings) / str(batch_id)


def remove_catalog_import_batch_sources(settings: Settings, batch_id: int) -> None:
    shutil.rmtree(catalog_import_batch_directory(settings, batch_id), ignore_errors=True)


def catalog_import_sources_available(settings: Settings, batch_id: int) -> bool:
    """Return whether a retry has at least one staged regular source file available."""
    directory = catalog_import_batch_directory(settings, batch_id)
    try:
        return directory.is_dir() and any(path.is_file() for path in directory.iterdir())
    except OSError:
        return False


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
                    for page in document:
                        _validate_pdf_render_dimensions(
                            page,
                            max_pixels=settings.catalog_import_max_pdf_render_pixels,
                            max_dimension=settings.catalog_import_max_pdf_render_dimension,
                        )
            except CatalogInputLimitError as exc:
                raise HTTPException(
                    status_code=413, detail="Catalog PDF exceeds the allowed render dimensions."
                ) from exc
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
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(io.BytesIO(content)) as image:
                        _validate_image_dimensions(
                            image,
                            max_pixels=settings.catalog_import_max_image_pixels,
                            max_dimension=settings.catalog_import_max_image_dimension,
                        )
                        image.verify()
            except CatalogInputLimitError as exc:
                raise HTTPException(
                    status_code=413, detail="Catalog image exceeds the allowed dimensions."
                ) from exc
            except (
                Image.DecompressionBombError,
                Image.DecompressionBombWarning,
                UnidentifiedImageError,
                OSError,
            ) as exc:
                raise HTTPException(
                    status_code=400, detail="Please choose a valid catalog image."
                ) from exc
        staged.append(StagedCatalogFile(extension=extension, content=content))
    return staged


def validate_catalog_image_dimensions(
    image: Image.Image, *, max_pixels: int, max_dimension: int
) -> None:
    """Reject image headers that would exceed the catalog extraction memory budget."""
    _validate_image_dimensions(image, max_pixels=max_pixels, max_dimension=max_dimension)


def validate_catalog_pdf_render_dimensions(
    page: fitz.Page, *, max_pixels: int, max_dimension: int
) -> None:
    """Reject a PDF page before PyMuPDF allocates its rendered pixel buffer."""
    _validate_pdf_render_dimensions(page, max_pixels=max_pixels, max_dimension=max_dimension)


def _validate_image_dimensions(image: Image.Image, *, max_pixels: int, max_dimension: int) -> None:
    width, height = image.size
    if width > max_dimension or height > max_dimension or width * height > max_pixels:
        raise CatalogInputLimitError("catalog image dimensions exceed configured limit")


def _validate_pdf_render_dimensions(
    page: fitz.Page, *, max_pixels: int, max_dimension: int
) -> None:
    bounds = page.bound()
    width = int(bounds.width * PDF_RENDER_SCALE + 0.999999)
    height = int(bounds.height * PDF_RENDER_SCALE + 0.999999)
    if width < 1 or height < 1:
        raise CatalogInputLimitError("catalog PDF page has invalid dimensions")
    if width > max_dimension or height > max_dimension or width * height > max_pixels:
        raise CatalogInputLimitError("catalog PDF render dimensions exceed configured limit")


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


def cleanup_expired_catalog_import_sources(settings: Settings, session: Session) -> None:
    """Remove expired terminal/orphan sources while preserving runnable batch input.

    A queued batch, including one requeued after an interrupted extraction lease, must keep its
    source files no matter how old their directory is.  The durable batch state is the authority
    for that decision; directory mtimes only control retention for terminal and orphan sources.
    """
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
        if not expired:
            continue
        if child.name.isdigit():
            batch = session.get(CatalogImportBatch, int(child.name))
            if batch is not None and batch.state in _SOURCE_RETAINING_BATCH_STATES:
                continue
        shutil.rmtree(child, ignore_errors=True)
