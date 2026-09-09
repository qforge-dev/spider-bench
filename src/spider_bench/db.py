"""Local working index: SQLite lives locally, stores s3_uri links. No image bytes locally.

Full schema lives in migrations/001_init.sql. This module applies migrations
idempotently and exposes typed connection helpers. No network, no boto3.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

SCHEMA_VERSION = 1


def _migrations_dir() -> Path:
    return _MIGRATIONS_DIR


def get_connection(path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL, foreign keys, and Row factory."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def connect(path: str | Path) -> sqlite3.Connection:
    """Backwards-compatible alias for get_connection."""
    return get_connection(path)


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    try:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        return {int(r[0]) for r in rows}
    except sqlite3.OperationalError:
        return set()


def _migration_files() -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    d = _migrations_dir()
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.sql")):
        stem = f.stem  # e.g. "001_init"
        digits = "".join(ch for ch in stem.split("_")[0] if ch.isdigit())
        if digits:
            out.append((int(digits), f))
    return sorted(out)


def ensure_migrated(path_or_conn: str | Path | sqlite3.Connection) -> sqlite3.Connection | None:
    """Apply pending migrations idempotently. Returns conn if one was opened.

    Accepts a path (opens, migrates, keeps open-returned conn) or an existing
    connection (migrates in place, returns None).
    """
    if isinstance(path_or_conn, sqlite3.Connection):
        conn = path_or_conn
        _apply_pending(conn)
        return None
    conn = get_connection(path_or_conn)
    _apply_pending(conn)
    conn.commit()
    return conn


def _apply_pending(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = _applied_versions(conn)
    for version, f in _migration_files():
        if version in applied:
            continue
        sql = f.read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, _dt.datetime.now(_dt.timezone.utc).isoformat()),
        )
    conn.commit()


def init_db(path: str | Path) -> None:
    """Create/migrate the SQLite working index at path."""
    conn = ensure_migrated(path)
    assert conn is not None
    conn.close()
