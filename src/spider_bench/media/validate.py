"""Image validation: MIME, dimensions, decodability, size guards.

Rejects HTML error pages, corrupt files, unsupported formats, extremely
small images, and near-empty (solid-color) images.

Failure codes (stable, stored in reports):
  html_error_page | unsupported_format | mime_mismatch | decode_error |
  corrupt | tiny | tiny_bytes | near_empty
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageStat

MIN_WIDTH = 32
MIN_HEIGHT = 32
MIN_BYTES = 200
NEAR_EMPTY_RANGE = 3  # max-min grayscale range below this => near-empty

ALLOWED_FORMATS: dict[str, tuple[str, str]] = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}

HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<body", b"<!doctype")


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    failure_code: str | None
    message: str
    mime: str | None = None
    format: str | None = None
    ext: str | None = None
    width: int | None = None
    height: int | None = None
    size_bytes: int = 0


def _sniff_format(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if data[:2] == b"\xff\xd8":
        return "JPEG"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    if data[:2] == b"BM":
        return "BMP"
    if data[:4] in (b"\x00\x00\x01\x00", b"II*\x00", b"MM\x00*"):
        return "OTHER"
    return None


def _looks_like_html(data: bytes) -> bool:
    head = data[:2048].lstrip(b"\x00 \t\r\n\xef\xbb\xbf")
    low = head[:512].lower()
    if low.startswith(b"<"):
        return True
    for marker in HTML_MARKERS:
        if marker in low:
            return True
    return False


def validate_bytes(data: bytes, declared_content_type: str | None = None) -> ValidationResult:
    size = len(data)
    if not data:
        return ValidationResult(False, "corrupt", "empty payload", size_bytes=0)
    if _looks_like_html(data):
        return ValidationResult(False, "html_error_page", "payload looks like HTML", size_bytes=size)

    sniffed = _sniff_format(data)
    if sniffed is None:
        return ValidationResult(False, "unsupported_format", "unrecognized magic bytes", size_bytes=size)
    if sniffed not in ALLOWED_FORMATS:
        return ValidationResult(
            False, "unsupported_format", f"format {sniffed} not accepted", format=sniffed, size_bytes=size
        )
    if size < MIN_BYTES:
        return ValidationResult(False, "tiny_bytes", f"payload {size}B < {MIN_BYTES}B", size_bytes=size)

    mime, ext = ALLOWED_FORMATS[sniffed]
    if declared_content_type is not None:
        declared = declared_content_type.split(";")[0].strip().lower()
        if declared and declared != mime:
            # Tolerate jpeg/jpg alias; otherwise strict.
            aliases = {"image/jpg": "image/jpeg"}
            if aliases.get(declared, declared) != mime:
                return ValidationResult(
                    False,
                    "mime_mismatch",
                    f"declared {declared} != sniffed {mime}",
                    mime=mime,
                    format=sniffed,
                    ext=ext,
                    size_bytes=size,
                )
    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
            width, height = img.size
            fmt = img.format or sniffed
    except Exception as e:  # noqa: BLE001 - any Pillow failure means undecodable
        return ValidationResult(
            False, "decode_error", f"undecodable image: {e}", mime=mime, format=sniffed, ext=ext, size_bytes=size
        )

    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return ValidationResult(
            False,
            "tiny",
            f"dimensions {width}x{height} < {MIN_WIDTH}x{MIN_HEIGHT}",
            mime=mime,
            format=fmt,
            ext=ext,
            width=width,
            height=height,
            size_bytes=size,
        )
    try:
        with Image.open(BytesIO(data)) as img:
            gray = img.convert("L")
            stat = ImageStat.Stat(gray)
            lo, hi = stat.extrema[0]
            if hi - lo < NEAR_EMPTY_RANGE:
                return ValidationResult(
                    False,
                    "near_empty",
                    f"near-empty image (range {hi - lo})",
                    mime=mime,
                    format=fmt,
                    ext=ext,
                    width=width,
                    height=height,
                    size_bytes=size,
                )
    except Exception:  # noqa: BLE001 - conservatively ignore stat errors
        pass

    return ValidationResult(
        True, None, "ok", mime=mime, format=fmt, ext=ext, width=width, height=height, size_bytes=size
    )


def validate_file(path: str | Path) -> ValidationResult:
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as e:
        return ValidationResult(False, "corrupt", f"unreadable file: {e}", size_bytes=0)
    return validate_bytes(data)
