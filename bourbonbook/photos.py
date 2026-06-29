from __future__ import annotations

import io
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError


async def save_photo(upload: UploadFile, upload_dir: Path, max_mb: int) -> str:
    content = await upload.read(max_mb * 1024 * 1024 + 1)
    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"Photo must be smaller than {max_mb} MB")
    try:
        image = Image.open(io.BytesIO(content))
        image.verify()
        image = Image.open(io.BytesIO(content))
        image = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=400, detail="Please choose a valid image") from exc
    image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
    upload_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.jpg"
    image.save(upload_dir / name, "JPEG", quality=88, optimize=True)
    return name

