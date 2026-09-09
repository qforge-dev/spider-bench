"""Media pipeline: hashing, validation, selection, download, deduplication.

S3-native: image bytes live at <prefix>media/sha256/<aa>/<bb>/<sha256>.<ext>.
SQLite (local) stores s3_uri + public_url + sha256 only.
"""
from __future__ import annotations

from spider_bench.media.deduplicate import (
    DuplicateGroup,
    MediaRecord,
    duplicate_report,
    find_duplicate_groups,
)
from spider_bench.media.hash import (
    ahash_bytes,
    content_key,
    dhash_bytes,
    hamming,
    sha256_bytes,
    sha256_file,
    sha256_stream,
)
from spider_bench.media.select import SelectionResult, filter_by_license, select_media
from spider_bench.media.validate import ValidationResult, validate_bytes, validate_file

__all__ = [
    "DuplicateGroup",
    "MediaRecord",
    "SelectionResult",
    "ValidationResult",
    "ahash_bytes",
    "content_key",
    "dhash_bytes",
    "duplicate_report",
    "filter_by_license",
    "find_duplicate_groups",
    "hamming",
    "select_media",
    "sha256_bytes",
    "sha256_file",
    "sha256_stream",
    "validate_bytes",
    "validate_file",
]
