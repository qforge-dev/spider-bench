"""Ingest a pinned World Spider Catalog snapshot into taxa + taxon_names.

Idempotent upserts via UNIQUE constraints. Pure helpers are dry-run friendly.
No network, no boto3.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from spider_bench.taxonomy.normalize import (
    canonical_rank,
    normalize_authorship,
    normalize_name,
)


def parse_wsc_rows(rows: list[dict[str, Any]], snapshot_id: str) -> list[dict[str, Any]]:
    """Normalize raw WSC dicts to canonical taxon records. Pure function.

    Accepts: scientific_name|name, authorship, rank, family, genus, wsc_id|id|
    taxon_id, status|taxonomic_status, synonyms (list or ';'-separated str),
    accepted_name (for synonym rows).
    """
    out: list[dict[str, Any]] = []
    for r in rows:
        name = str(
            r.get("scientific_name") or r.get("name") or r.get("accepted_name") or ""
        ).strip()
        if not name:
            continue
        status = str(r.get("taxonomic_status") or r.get("status") or "accepted").lower()
        if status not in ("accepted", "synonym", "doubtful", "disputed"):
            status = "accepted"
        syns = r.get("synonyms") or []
        if isinstance(syns, str):
            syns = [s.strip() for s in syns.split(";") if s.strip()]
        out.append(
            {
                "scientific_name": name,
                "authorship": r.get("authorship"),
                "rank": canonical_rank(str(r.get("rank") or "species")) or "species",
                "family": r.get("family"),
                "genus": r.get("genus"),
                "wsc_id": r.get("wsc_id") or r.get("id") or r.get("taxon_id"),
                "snapshot_id": snapshot_id,
                "taxonomic_status": status,
                "synonyms": list(syns),
                "accepted_name": r.get("accepted_name"),
            }
        )
    return out


def load_wsc_file(path: str | Path) -> list[dict[str, Any]]:
    """Load a CSV or JSON WSC snapshot file. Pure file read, no network."""
    p = Path(path)
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text())
        if isinstance(data, dict) and "taxa" in data:
            data = data["taxa"]
        return list(data)
    with p.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def ingest_wsc(
    conn: sqlite3.Connection, rows: list[dict[str, Any]], *, snapshot_id: str
) -> dict[str, int]:
    """Ingest WSC rows idempotently into taxa + taxon_names. Returns counts."""
    parsed = parse_wsc_rows(rows, snapshot_id)
    n_taxa = 0
    n_names = 0
    with conn:
        for rec in parsed:
            if rec.get("accepted_name") and normalize_name(
                str(rec["accepted_name"])
            ) != normalize_name(rec["scientific_name"]):
                # Pure synonym row: ensure the accepted taxon exists, then link.
                acc_name = str(rec["accepted_name"]).strip()
                conn.execute(
                    """INSERT INTO taxa (scientific_name, authorship, rank, family, genus,
                                        wsc_id, snapshot_id, taxonomic_status)
                       VALUES (?,?,?,?,?,?,?, 'accepted')
                       ON CONFLICT(snapshot_id, scientific_name) DO NOTHING""",
                    (
                        acc_name,
                        None,
                        rec["rank"],
                        rec.get("family"),
                        rec.get("genus"),
                        None,
                        snapshot_id,
                    ),
                )
                acc_id = conn.execute(
                    "SELECT id FROM taxa WHERE snapshot_id=? AND scientific_name=?",
                    (snapshot_id, acc_name),
                ).fetchone()[0]
            else:
                conn.execute(
                    """INSERT INTO taxa (scientific_name, authorship, rank, family, genus,
                                        wsc_id, snapshot_id, taxonomic_status)
                       VALUES (?,?,?,?,?,?,?,?)
                       ON CONFLICT(snapshot_id, scientific_name) DO UPDATE SET
                         authorship=excluded.authorship, rank=excluded.rank,
                         family=excluded.family, genus=excluded.genus,
                         taxonomic_status=excluded.taxonomic_status""",
                    (
                        rec["scientific_name"],
                        rec.get("authorship"),
                        rec["rank"],
                        rec.get("family"),
                        rec.get("genus"),
                        rec.get("wsc_id"),
                        snapshot_id,
                        rec["taxonomic_status"],
                    ),
                )
                # Fill wsc_id on conflict when previously NULL.
                if rec.get("wsc_id"):
                    conn.execute(
                        "UPDATE taxa SET wsc_id=? WHERE snapshot_id=? AND scientific_name=?"
                        " AND wsc_id IS NULL",
                        (rec["wsc_id"], snapshot_id, rec["scientific_name"]),
                    )
                acc_id = conn.execute(
                    "SELECT id FROM taxa WHERE snapshot_id=? AND scientific_name=?",
                    (snapshot_id, rec["accepted_name"] or rec["scientific_name"]),
                ).fetchone()[0]
                n_taxa += 1
            # Accepted-name entry in taxon_names (coalesce NULL authorship to ''
            # so the UNIQUE constraint stays effective under SQLite NULL semantics).
            norm = normalize_name(rec["scientific_name"])
            norm_auth = normalize_authorship(rec.get("authorship")) or ""
            conn.execute(
                """INSERT INTO taxon_names (name, normalized_name, authorship,
                     normalized_authorship, rank, source, source_id, accepted_taxon_id, name_type)
                   VALUES (?,?,?,?,?,'wsc',?,?,?)
                   ON CONFLICT(normalized_name, normalized_authorship, source) DO UPDATE SET
                     accepted_taxon_id=excluded.accepted_taxon_id""",
                (
                    rec["scientific_name"],
                    norm,
                    rec.get("authorship"),
                    norm_auth,
                    rec["rank"],
                    rec.get("wsc_id"),
                    acc_id,
                    "synonym"
                    if rec.get("accepted_name")
                    and normalize_name(str(rec["accepted_name"])) != norm
                    else "accepted",
                ),
            )
            n_names += 1
            for syn in rec.get("synonyms", []):
                conn.execute(
                    """INSERT INTO taxon_names (name, normalized_name, authorship,
                         normalized_authorship, rank, source, source_id, accepted_taxon_id, name_type)
                       VALUES (?,?,?,?,?,'wsc',NULL,?,'synonym')
                       ON CONFLICT(normalized_name, normalized_authorship, source) DO NOTHING""",
                    (syn, normalize_name(syn), None, "", rec["rank"], acc_id),
                )
                n_names += 1
    return {"taxa": n_taxa, "names": n_names}


def ingest_wsc_file(
    conn: sqlite3.Connection, path: str | Path, *, snapshot_id: str
) -> dict[str, int]:
    """Load a CSV/JSON snapshot file and ingest it. Thin I/O wrapper."""
    return ingest_wsc(conn, load_wsc_file(path), snapshot_id=snapshot_id)
