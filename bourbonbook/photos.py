from __future__ import annotations

import io
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

AVATAR_MAX_MB = 10
AVATAR_SIZE = (512, 512)


def validated_image(content: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(content))
        image.verify()
        image = Image.open(io.BytesIO(content))
        return ImageOps.exif_transpose(image).convert("RGB")
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=400, detail="Please choose a valid image") from exc


async def save_photo(upload: UploadFile, upload_dir: Path, max_mb: int) -> str:
    content = await upload.read(max_mb * 1024 * 1024 + 1)
    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"Photo must be smaller than {max_mb} MB")
    image = validated_image(content)
    image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
    upload_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.jpg"
    image.save(upload_dir / name, "JPEG", quality=88, optimize=True)
    return name


async def save_avatar(upload: UploadFile, avatar_dir: Path) -> str:
    content = await upload.read(AVATAR_MAX_MB * 1024 * 1024 + 1)
    if len(content) > AVATAR_MAX_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"Avatar must be smaller than {AVATAR_MAX_MB} MB",
        )
    image = ImageOps.fit(validated_image(content), AVATAR_SIZE, Image.Resampling.LANCZOS)
    avatar_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.jpg"
    image.save(avatar_dir / name, "JPEG", quality=85, optimize=True)
    return name
