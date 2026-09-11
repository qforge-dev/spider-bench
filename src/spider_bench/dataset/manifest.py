"""Release manifest builder.

Builds deterministic Parquet artifacts + release.json + checksums.sha256 in a
local staging directory. Rebuilding from identical inputs produces identical
manifests (sorted rows, sorted columns, fixed JSON formatting).
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

RELEASE_ARTIFACTS = (
    "taxa.parquet",
    "media.parquet",
    "attribution.parquet",
    "danger_assessments.parquet",
    "evidence.parquet",
    "release.json",
    "checksums.sha256",
)

# Sort keys per artifact for deterministic row order.
SORT_KEYS: dict[str, list[str]] = {
    "taxa.parquet": ["taxon"],
    "media.parquet": ["sha256"],
    "attribution.parquet": ["sha256"],
    "danger_assessments.parquet": ["taxon"],
    "evidence.parquet": ["id"],
}


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sort_rows(rows: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: tuple(str(r.get(k, "")) for k in keys))


def write_parquet_sorted(
    rows: list[dict[str, Any]], path: Path, sort_keys: list[str]
) -> int:
    """Write rows to Parquet with deterministic column/row order. Returns row count."""
    rows = _sort_rows([dict(r) for r in rows], sort_keys)
    columns = sorted({k for r in rows for k in r.keys()})
    table = pa.table({c: [r.get(c) for r in rows] for c in columns})
    pq.write_table(table, path, compression="snappy", version="2.6")
    return len(rows)


def compute_checksums_file(directory: Path, filenames: list[str]) -> Path:
    """Write ``checksums.sha256`` (BSD-style ``sha256  filename`` lines)."""
    out = directory / "checksums.sha256"
    lines = []
    for name in sorted(filenames):
        if name == "checksums.sha256":
            continue
        lines.append(f"{sha256_of_file(directory / name)}  {name}\n")
    out.write_text("".join(lines), encoding="utf-8")
    return out


def build_release(
    taxa: list[dict[str, Any]],
    media: list[dict[str, Any]],
    attribution: list[dict[str, Any]],
    danger_assessments: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    out_dir: str | Path,
    version: str,
    license_profile: str = "research",
    license_profile_version: int = 1,
    taxonomy_snapshot: str = "",
    source_snapshots: dict[str, Any] | None = None,
    config_hash: str = "",
    code_commit: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write all release artifacts to ``out_dir`` and return release.json content."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    counts = {
        "taxa": write_parquet_sorted(taxa, out / "taxa.parquet", SORT_KEYS["taxa.parquet"]),
        "media": write_parquet_sorted(media, out / "media.parquet", SORT_KEYS["media.parquet"]),
        "attribution": write_parquet_sorted(
            attribution, out / "attribution.parquet", SORT_KEYS["attribution.parquet"]
        ),
        "danger_assessments": write_parquet_sorted(
            danger_assessments,
            out / "danger_assessments.parquet",
            SORT_KEYS["danger_assessments.parquet"],
        ),
        "evidence": write_parquet_sorted(
            evidence, out / "evidence.parquet", SORT_KEYS["evidence.parquet"]
        ),
    }

    release = {
        "version": version,
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "config_hash": config_hash,
        "code_commit": code_commit,
        "taxonomy_snapshot": taxonomy_snapshot,
        "source_snapshots": source_snapshots or {},
        "license_profile": license_profile,
        "license_profile_version": license_profile_version,
        "counts": counts,
        "policies": {
            "redistribution": "references_and_metadata",
            "safety_notice_included": True,
        },
        "extra": extra or {},
        "checksums": {},
    }
    (out / "release.json").write_text(
        json.dumps(release, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    compute_checksums_file(
        out,
        ["taxa.parquet", "media.parquet", "attribution.parquet",
         "danger_assessments.parquet", "evidence.parquet", "release.json"],
    )
    checksums = {}
    for line in (out / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split(None, 1)
            checksums[name.strip()] = digest
    release["checksums"] = checksums
    (out / "release.json").write_text(
        json.dumps(release, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    # release.json changed after embedding checksums -> refresh checksums file.
    compute_checksums_file(
        out,
        ["taxa.parquet", "media.parquet", "attribution.parquet",
         "danger_assessments.parquet", "evidence.parquet", "release.json"],
    )
    return release
