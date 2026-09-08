"""Validation and actionable errors for replacement product images."""
from __future__ import annotations

import base64
import binascii
import io
import re
import warnings

MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000


class ReplacementImageSyncError(RuntimeError):
    def __init__(self, line_id: int, asin: str):
        super().__init__(f"Image sync failed [line:{int(line_id)} asin:{asin}]. Upload the replacement ASIN image manually, then retry Shopify sync.")


def image_failure_details(error: str) -> dict | None:
    match = re.search(r"Image sync failed \[line:(\d+) asin:([A-Z0-9]{10})\]", error or "")
    return {"line_id": int(match[1]), "asin": match[2]} if match else None


def validate_image_base64(value: str) -> dict:
    from PIL import Image, UnidentifiedImageError

    if not isinstance(value, str) or len(value) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError("Upload a JPEG, PNG, or WebP image no larger than 12 MB.")
    try:
        content = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("The uploaded image is invalid. Choose a JPEG, PNG, or WebP file.") from exc
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Upload a JPEG, PNG, or WebP image no larger than 12 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                width, height = image.size
                if image.format not in {"JPEG", "PNG", "WEBP"} or width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("Use a JPEG, PNG, or WebP image of at most 25 megapixels.")
                mime = Image.MIME[image.format]
                image.verify()
            with Image.open(io.BytesIO(content)) as image:
                image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("The image is damaged or unsupported. Choose a valid JPEG, PNG, or WebP file.") from exc
    # Preserve the original uploaded bytes and resolution; never upscale or recompress.
    return {"image_base64": value, "content_type": mime, "width": width, "height": height}
