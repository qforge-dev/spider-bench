"""SHA-256 streaming + content-addressed key builder + perceptual hashes.

Pure functions (no network, no S3) so they are unit-testable.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from PIL import Image

CHUNK_SIZE = 65536
HASH_SIZE = 8


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_stream(fp: BinaryIO, chunk_size: int = CHUNK_SIZE) -> str:
    h = hashlib.sha256()
    while True:
        chunk = fp.read(chunk_size)
        if not chunk:
            break
        h.update(chunk)
    return h.hexdigest()


def sha256_file(path: str | Path) -> str:
    with open(path, "rb") as f:
        return sha256_stream(f)


def normalize_ext(ext: str) -> str:
    e = ext.strip().lstrip(".").lower()
    if e in ("jpg", "jpeg"):
        return "jpg"
    return e


def content_key(prefix: str, sha256: str, ext: str) -> str:
    """Content-addressed S3 key: <prefix>media/sha256/<aa>/<bb>/<sha256>.<ext>."""
    from spider_bench.storage.s3 import media_key

    return media_key(prefix, sha256, normalize_ext(ext))


def _grayscale_resized(image: Image.Image, width: int, height: int) -> Image.Image:
    img = image.convert("L")
    resample = getattr(Image, "LANCZOS", Image.BILINEAR)
    return img.resize((width, height), resample)


def _bits_to_int(bits: list[int]) -> int:
    value = 0
    for b in bits:
        value = (value << 1) | (1 if b else 0)
    return value


def ahash(image: Image.Image, hash_size: int = HASH_SIZE) -> int:
    """Average hash: resize to hash_size^2 gray, bits above mean."""
    small = _grayscale_resized(image, hash_size, hash_size)
    pixels = list(small.getdata())
    avg = sum(pixels) / len(pixels)
    return _bits_to_int([1 if p > avg else 0 for p in pixels])


def dhash(image: Image.Image, hash_size: int = HASH_SIZE) -> int:
    """Difference hash: resize to (hash_size+1) x hash_size, compare columns."""
    small = _grayscale_resized(image, hash_size + 1, hash_size)
    pixels = list(small.getdata())
    bits: list[int] = []
    for row in range(hash_size):
        for col in range(hash_size):
            left = pixels[row * (hash_size + 1) + col]
            right = pixels[row * (hash_size + 1) + col + 1]
            bits.append(1 if left > right else 0)
    return _bits_to_int(bits)


def ahash_bytes(data: bytes, hash_size: int = HASH_SIZE) -> int:
    with Image.open(BytesIO(data)) as img:
        img.load()
        return ahash(img, hash_size)


def dhash_bytes(data: bytes, hash_size: int = HASH_SIZE) -> int:
    with Image.open(BytesIO(data)) as img:
        img.load()
        return dhash(img, hash_size)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")
