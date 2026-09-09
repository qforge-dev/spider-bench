"""Local work-dir helpers: dirs, SQLite backup, checkpoints, disk space."""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORK_SUBDIRS = ("backups", "downloads", "logs", "reports")


def ensure_dirs(work_dir: str | Path, subdirs: tuple[str, ...] = WORK_SUBDIRS) -> dict[str, Path]:
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {"root": root}
    for sub in subdirs:
        p = root / sub
        p.mkdir(parents=True, exist_ok=True)
        out[sub] = p
    return out


def backup_sqlite(sqlite_path: str | Path, backups_root: str | Path | None = None) -> Path:
    """Consistent backup via the SQLite backup API.

    Destination: <backups_root>/<UTC ts>/spider-bench.sqlite
    (backups_root defaults to <work_dir>/backups is NOT assumed; pass
    data/work/backups explicitly, or the sqlite parent / backups).
    """
    src = Path(sqlite_path)
    if not src.exists():
        raise FileNotFoundError(f"sqlite not found: {src}")
    if backups_root is None:
        backups_root = src.parent / "backups"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_dir = Path(backups_root) / ts
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "spider-bench.sqlite"
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    return dest


def export_parquet_checkpoint(rows: list[dict[str, Any]], dest: str | Path) -> Path:
    """Write normalized rows to Parquet (pyarrow). Stub for stage checkpoints."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows) if rows else pa.table({})
    pq.write_table(table, dest)
    return dest


def disk_usage(path: str | Path) -> tuple[int, int, int]:
    u = shutil.disk_usage(str(path))
    return u.total, u.used, u.free


def ensure_free_space(path: str | Path, required_bytes: int) -> tuple[bool, int]:
    """Return (ok, free_bytes). Creates the directory if missing."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    _total, _used, free = disk_usage(p)
    return free >= required_bytes, free


def estimate_storage_use(num_files: int, avg_bytes: int) -> int:
    return max(0, num_files) * max(0, avg_bytes)
