"""Unit tests: full SQLite schema, migrations, idempotent upserts. No boto3."""
from __future__ import annotations

import sqlite3

from spider_bench.db import ensure_migrated, get_connection, init_db
from spider_bench.sources.polish_checklist import ingest_checklist
from spider_bench.sources.world_spider_catalog import ingest_wsc

EXPECTED_TABLES = {
    "collection_runs", "source_datasets", "taxa", "taxon_names", "country_taxa",
    "observations", "media", "duplicate_groups", "duplicate_group_members",
    "evidence_sources", "evidence_claims", "danger_assessments", "review_events",
    "dataset_releases", "dataset_members", "schema_migrations",
}


def _mem() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_migrated(conn)
    return conn


def test_schema_tables_created(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    init_db(db)
    conn = get_connection(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert EXPECTED_TABLES <= tables
    # WAL + FK pragmas
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_migrations_idempotent(tmp_path) -> None:
    db = tmp_path / "t.sqlite"
    init_db(db)
    init_db(db)  # second run must not fail or duplicate
    conn = get_connection(db)
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] >= 1
    conn.close()


def test_checklist_upsert_idempotent() -> None:
    conn = _mem()
    rows = [{"name": "Pardosa lugubris", "status": "present"},
            {"name": "Araneus diadematus", "status": "doubtful"}]
    r1 = ingest_checklist(conn, rows, source="test-checklist", version="2026.01")
    r2 = ingest_checklist(conn, rows, source="test-checklist", version="2026.01")
    assert r1["rows"] == 2 and r2["rows"] == 2
    n = conn.execute("SELECT COUNT(*) FROM country_taxa").fetchone()[0]
    assert n == 2
    assert conn.execute("SELECT COUNT(*) FROM source_datasets").fetchone()[0] == 1
    conn.close()


def test_wsc_upsert_idempotent() -> None:
    conn = _mem()
    rows = [{"scientific_name": "Pardosa lugubris", "authorship": "(Walckenaer, 1802)",
             "rank": "species", "family": "Lycosidae", "genus": "Pardosa",
             "wsc_id": "urn:wsc:1", "synonyms": ["Lycosa lugubris"]}]
    ingest_wsc(conn, rows, snapshot_id="wsc-26.0")
    ingest_wsc(conn, rows, snapshot_id="wsc-26.0")
    assert conn.execute("SELECT COUNT(*) FROM taxa").fetchone()[0] == 1
    # accepted name + 1 synonym, no duplicates on re-ingest
    assert conn.execute("SELECT COUNT(*) FROM taxon_names").fetchone()[0] == 2
    conn.close()


def test_foreign_key_enforced() -> None:
    conn = _mem()
    with conn:
        conn.execute("INSERT INTO taxa (scientific_name, rank, snapshot_id) VALUES (?,?,?)",
                     ("Pardosa lugubris", "species", "wsc-26.0"))
    taxon_id = conn.execute("SELECT id FROM taxa").fetchone()[0]
    # valid FK ok
    with conn:
        conn.execute(
            "INSERT INTO danger_assessments (taxon_id, geographic_scope,"
            " medical_significance, rationale, evidence_ids, status)"
            " VALUES (?,?,?,?,?,?)",
            (taxon_id, "PL", "uncertain", "no data", "[]", "draft"))
    # invalid FK must fail
    try:
        with conn:
            conn.execute(
                "INSERT INTO danger_assessments (taxon_id, geographic_scope,"
                " medical_significance, rationale, evidence_ids, status)"
                " VALUES (?,?,?,?,?,?)",
                (99999, "PL", "uncertain", "x", "[]", "draft"))
        raise AssertionError("FK violation not raised")
    except sqlite3.IntegrityError:
        pass
    # invalid medical_significance must fail (CHECK)
    try:
        with conn:
            conn.execute(
                "INSERT INTO danger_assessments (taxon_id, geographic_scope,"
                " medical_significance, rationale, evidence_ids, status)"
                " VALUES (?,?,?,?,?,?)",
                (taxon_id, "PL", "deadly", "x", "[]", "draft"))
        raise AssertionError("CHECK violation not raised")
    except sqlite3.IntegrityError:
        pass
    conn.close()


def test_media_unique_constraints() -> None:
    conn = _mem()
    with conn:
        conn.execute(
            "INSERT INTO media (source, source_media_id, s3_uri, license) VALUES (?,?,?,?)",
            ("inat", "m1", "s3://b/poland/media/x.jpg", "CC-BY"))
        try:
            conn.execute(
                "INSERT INTO media (source, source_media_id, s3_uri, license) VALUES (?,?,?,?)",
                ("inat", "m1", "s3://b/poland/media/y.jpg", "CC-BY"))
            raise AssertionError("UNIQUE violation not raised")
        except sqlite3.IntegrityError:
            pass
    conn.close()
