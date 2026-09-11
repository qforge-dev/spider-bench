"""Ingest a versioned Polish checklist into source_datasets + country_taxa.

Idempotent upserts via UNIQUE constraints + INSERT OR IGNORE / ON CONFLICT.
Pure helpers (parse/normalize) are dry-run friendly; DB writers take a
sqlite3.Connection. No network, no boto3.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from spider_bench.taxonomy.normalize import normalize_name

VALID_STATUSES = {
    "present",
    "doubtful",
    "disputed",
    "historical",
    "introduced",
    "uncertain",
    "absent",
}


def parse_checklist_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize raw checklist dicts to canonical records. Pure function.

    Accepts keys: name|scientific_name|original_name, authorship, status|
    membership_status, notes. Returns dicts with original_name,
    normalized_name, authorship, membership_status, notes.
    """
    out: list[dict[str, Any]] = []
    for r in rows:
        name = r.get("original_name") or r.get("name") or r.get("scientific_name") or ""
        name = str(name).strip()
        if not name:
            continue
        status = str(
            r.get("membership_status") or r.get("status") or "present"
        ).strip().lower()
        if status not in VALID_STATUSES:
            status = "uncertain"
        out.append(
            {
                "original_name": name,
                "normalized_name": normalize_name(name),
                "authorship": r.get("authorship"),
                "membership_status": status,
                "notes": r.get("notes"),
            }
        )
    return out


def upsert_source_dataset(
    conn: sqlite3.Connection,
    *,
    source: str,
    version: str | None,
    citation: str | None = None,
    retrieval_date: str | None = None,
    license: str | None = None,
    checksum: str | None = None,
    raw_s3_uri: str | None = None,
    notes: str | None = None,
) -> int:
    conn.execute(
        """INSERT INTO source_datasets
           (source, version, retrieval_date, citation, license, checksum, raw_s3_uri, notes)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(source, version) DO UPDATE SET
             retrieval_date=excluded.retrieval_date,
             citation=excluded.citation,
             license=excluded.license,
             checksum=excluded.checksum,
             raw_s3_uri=excluded.raw_s3_uri,
             notes=excluded.notes""",
        (source, version, retrieval_date, citation, license, checksum, raw_s3_uri, notes),
    )
    row = conn.execute(
        "SELECT id FROM source_datasets WHERE source=? AND "
        + ("version IS ?" if version is None else "version=?"),
        (source, version),
    ).fetchone()
    return int(row[0])


def ingest_checklist(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
    *,
    source: str,
    version: str | None,
    country_code: str = "PL",
    citation: str | None = None,
    retrieval_date: str | None = None,
    license: str | None = None,
    raw_s3_uri: str | None = None,
) -> dict[str, int]:
    """Ingest parsed/raw checklist rows idempotently. Returns counts."""
    parsed = parse_checklist_rows(rows)
    with conn:
        source_id = upsert_source_dataset(
            conn,
            source=source,
            version=version,
            citation=citation,
            retrieval_date=retrieval_date,
            license=license,
            raw_s3_uri=raw_s3_uri,
        )
        inserted = 0
        for rec in parsed:
            cur = conn.execute(
                """INSERT INTO country_taxa
                   (taxon_id, original_name, normalized_name, country_code,
                    membership_status, supporting_source_id, review_state, notes)
                   VALUES (NULL,?,?,?,?,?,'unreviewed',?)
                   ON CONFLICT(country_code, normalized_name, supporting_source_id)
                   DO UPDATE SET original_name=excluded.original_name,
                                 membership_status=excluded.membership_status,
                                 notes=excluded.notes""",
                (
                    rec["original_name"],
                    rec["normalized_name"],
                    country_code,
                    rec["membership_status"],
                    source_id,
                    rec["notes"],
                ),
            )
            inserted += cur.rowcount or 0
    return {"rows": len(parsed), "source_id": source_id, "upserted": inserted}
