"""Image validation and resizing for multimodal input.

Validates image format, dimensions, and file size before sending to
LLM providers.  Optionally resizes oversized images using Pillow
(``pip install mini-agent[images]``).

Constants match Anthropic's API limits.
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = {"jpeg", "jpg", "png", "gif", "webp"}
MAGIC_BYTES: dict[bytes, str] = {
    b"\xff\xd8\xff": "jpeg",
    b"\x89PNG": "png",
    b"GIF8": "gif",
    b"RIFF": "webp",
}

API_IMAGE_MAX_BASE64_SIZE = 5_242_880  # 5 MB
IMAGE_MAX_WIDTH = 1568
IMAGE_MAX_HEIGHT = 1568


@dataclass(frozen=True, slots=True)
class ImageConfig:
    max_width: int = IMAGE_MAX_WIDTH
    max_height: int = IMAGE_MAX_HEIGHT
    max_base64_size: int = API_IMAGE_MAX_BASE64_SIZE
    target_format: str = "jpeg"
    jpeg_quality: int = 85


@dataclass(frozen=True, slots=True)
class ImageInfo:
    format: str
    width: int
    height: int
    size_bytes: int


class ImageValidationError(ValueError):
    pass


def detect_format_from_bytes(data: bytes) -> str | None:
    """Detect image format from magic bytes."""
    for magic, fmt in MAGIC_BYTES.items():
        if data[:len(magic)] == magic:
            if fmt == "webp" and data[8:12] != b"WEBP":
                continue
            return fmt
    return None


def detect_format_from_base64(b64: str) -> str | None:
    """Detect format from a base64-encoded string (first few bytes)."""
    try:
        header = base64.b64decode(b64[:32])
        return detect_format_from_bytes(header)
    except Exception:
        return None


def validate_image(
    data: bytes,
    config: ImageConfig = ImageConfig(),
) -> ImageInfo:
    """Validate image data. Raises ``ImageValidationError`` on problems."""
    fmt = detect_format_from_bytes(data)
    if fmt is None:
        raise ImageValidationError("Unrecognised image format")
    if fmt not in SUPPORTED_FORMATS:
        raise ImageValidationError(f"Unsupported format: {fmt}")

    width, height = _get_dimensions(data, fmt)
    b64_size = len(base64.b64encode(data))

    if b64_size > config.max_base64_size:
        raise ImageValidationError(
            f"Image too large: {b64_size:,} bytes base64 "
            f"(max {config.max_base64_size:,})"
        )

    return ImageInfo(format=fmt, width=width, height=height, size_bytes=len(data))


def maybe_resize(
    data: bytes,
    config: ImageConfig = ImageConfig(),
) -> bytes:
    """Resize image if it exceeds dimension or size limits.

    Requires Pillow.  Returns original data if no resize is needed
    or Pillow is not installed.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.debug("Pillow not installed; skipping resize")
        return data

    fmt = detect_format_from_bytes(data)
    if fmt is None:
        return data

    img = Image.open(io.BytesIO(data))
    w, h = img.size
    needs_resize = w > config.max_width or h > config.max_height

    b64_len = len(base64.b64encode(data))
    needs_compress = b64_len > config.max_base64_size

    if not needs_resize and not needs_compress:
        return data

    if needs_resize:
        ratio = min(config.max_width / w, config.max_height / h)
        new_w = int(w * ratio)
        new_h = int(h * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    buf = io.BytesIO()
    out_format = config.target_format.upper()
    if out_format == "JPEG" and img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    save_kwargs: dict[str, Any] = {}
    if out_format == "JPEG":
        save_kwargs["quality"] = config.jpeg_quality
    img.save(buf, format=out_format, **save_kwargs)

    result = buf.getvalue()
    if needs_compress and len(base64.b64encode(result)) > config.max_base64_size:
        for quality in (70, 50, 30):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            result = buf.getvalue()
            if len(base64.b64encode(result)) <= config.max_base64_size:
                break

    return result


def image_to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _get_dimensions(data: bytes, fmt: str) -> tuple[int, int]:
    """Extract width/height without Pillow if possible, fallback to Pillow."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        return img.size
    except ImportError:
        pass

    if fmt == "png" and len(data) >= 24:
        w = int.from_bytes(data[16:20], "big")
        h = int.from_bytes(data[20:24], "big")
        return w, h

    return 0, 0
