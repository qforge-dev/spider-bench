"""S3-native download: only selected licensed media from approved hosts.

- Validates every URL (including redirect hops) against an allowlist.
- Streams bytes while computing SHA-256; validates before upload.
- head_object dedup skip: content-addressed key already present => skip.
- Uploads to S3 with explicit ContentType.
- Persists a resume queue in SQLite (own `download_queue` table; db.py untouched).
- Retries transient failures only; dry-run support.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

DEFAULT_APPROVED_HOSTS = (
    "inaturalist-open-data.s3.amazonaws.com",
    "static.inaturalist.org",
    "inaturalist.org",
    "upload.wikimedia.org",
    "commons.wikimedia.org",
)

MAX_BYTES = 50 * 1024 * 1024
MAX_RETRIES = 3

TRANSIENT_STATUS = {429, 500, 502, 503, 504}
PERMANENT_FAILURE_CODES = {
    "disallowed_host",
    "html_error_page",
    "unsupported_format",
    "mime_mismatch",
    "decode_error",
    "corrupt",
    "tiny",
    "tiny_bytes",
    "near_empty",
}


@dataclass
class DownloadItem:
    url: str
    license: str | None = None
    taxon: str | None = None
    source: str | None = None
    source_media_id: str | None = None


@dataclass
class DownloadResult:
    url: str
    ok: bool
    skipped: bool = False
    dry_run: bool = False
    sha256: str | None = None
    s3_key: str | None = None
    failure_code: str | None = None
    attempts: int = 0
    message: str = ""
    width: int | None = None
    height: int | None = None


def _host(url: str) -> str:
    try:
        return urlparse(url).hostname.lower()  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return ""


def is_host_allowed(url: str, allowed_hosts: tuple[str, ...] | list[str]) -> bool:
    host = _host(url)
    if not host:
        return False
    allowed = {h.lower().lstrip("*.").lstrip(".") for h in allowed_hosts}
    return host in allowed or any(host.endswith("." + a) for a in allowed)


def check_redirect_chain(
    initial_url: str, hop_urls: list[str], allowed_hosts: tuple[str, ...] | list[str]
) -> str | None:
    """Return offending URL if any hop (incl. initial) is disallowed, else None."""
    for u in [initial_url, *hop_urls]:
        if not is_host_allowed(u, allowed_hosts):
            return u
    return None


def ensure_queue_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS download_queue (
          url TEXT PRIMARY KEY,
          status TEXT NOT NULL DEFAULT 'pending',
          attempts INTEGER NOT NULL DEFAULT 0,
          last_error TEXT,
          sha256 TEXT,
          s3_key TEXT,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue(conn: sqlite3.Connection, items: list[DownloadItem]) -> int:
    ensure_queue_table(conn)
    n = 0
    with conn:
        for it in items:
            cur = conn.execute(
                "INSERT OR IGNORE INTO download_queue(url, status, updated_at) VALUES (?, 'pending', ?)",
                (it.url, _now()),
            )
            n += cur.rowcount
    return n


def mark_done(conn: sqlite3.Connection, url: str, sha256: str, s3_key: str) -> None:
    ensure_queue_table(conn)
    with conn:
        conn.execute(
            "UPDATE download_queue SET status='done', sha256=?, s3_key=?, updated_at=? WHERE url=?",
            (sha256, s3_key, _now(), url),
        )


def mark_failed(
    conn: sqlite3.Connection, url: str, error: str, *, retry: bool, attempts: int
) -> None:
    ensure_queue_table(conn)
    status = "retry" if retry else "failed"
    with conn:
        conn.execute(
            "UPDATE download_queue SET status=?, attempts=?, last_error=?, updated_at=? WHERE url=?",
            (status, attempts, error, _now(), url),
        )


def _is_transient(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "connect" in name or "network" in name:
        return True
    return any(
        t in msg for t in ("timeout", "connection reset", "temporary failure", "try again", "429", "503")
    )


def download_selected(
    items: list[DownloadItem],
    *,
    bucket: str,
    prefix: str,
    region: str,
    s3_client: Any,
    http_get: Callable[..., Any] | None = None,
    allowed_hosts: tuple[str, ...] | list[str] = DEFAULT_APPROVED_HOSTS,
    conn: sqlite3.Connection | None = None,
    dry_run: bool = False,
    max_retries: int = MAX_RETRIES,
    key_exists_fn: Callable[..., bool] | None = None,
) -> list[DownloadResult]:
    """Download + validate + upload each item. No threads; caller batches.

    `http_get(url)` must return an object with `.status_code`, `.headers`
    (dict-like), `.iter_bytes(chunk_size)` or `.content`, and optionally
    `.redirect_hops` (list of intermediate URLs). Defaults to httpx.
    """
    from spider_bench.media.validate import validate_bytes
    from spider_bench.storage.s3 import key_exists as _key_exists
    from spider_bench.storage.s3 import media_key, public_url  # noqa: F401 (re-export context)

    exists = key_exists_fn or _key_exists
    results: list[DownloadResult] = []

    if conn is not None and not dry_run:
        enqueue(conn, items)

    for item in items:
        attempts = 0
        if not is_host_allowed(item.url, allowed_hosts):
            results.append(
                DownloadResult(item.url, False, failure_code="disallowed_host", message="host not allowlisted")
            )
            if conn is not None and not dry_run:
                mark_failed(conn, item.url, "disallowed_host", retry=False, attempts=attempts)
            continue
        if dry_run:
            results.append(DownloadResult(item.url, True, dry_run=True, message="would download"))
            continue

        last_error = "unknown"
        failure_code: str | None = None
        transient = True
        result: DownloadResult | None = None
        for attempt in range(1, max_retries + 1):
            attempts = attempt
            try:
                body, content_type, hops = _fetch(item.url, http_get)
                offending = check_redirect_chain(item.url, hops, allowed_hosts)
                if offending:
                    failure_code, transient, last_error = "disallowed_host", False, f"redirect to {offending}"
                    break
                vr = validate_bytes(body, declared_content_type=content_type)
                if not vr.ok:
                    failure_code, transient = vr.failure_code, False
                    last_error = vr.message
                    break
                digest = hashlib.sha256(body).hexdigest()
                ext = vr.ext or "jpg"
                key = media_key(prefix, digest, ext)
                if exists(s3_client, bucket, key):
                    result = DownloadResult(
                        item.url, True, skipped=True, sha256=digest, s3_key=key, attempts=attempts,
                        message="already on S3",
                    )
                    break
                s3_client.put_object(
                    Bucket=bucket, Key=key, Body=body, ContentType=vr.mime or "application/octet-stream"
                )
                result = DownloadResult(
                    item.url, True, sha256=digest, s3_key=key, attempts=attempts, message="uploaded",
                    width=vr.width, height=vr.height,
                )
                failure_code = None
                break
            except _HttpStatusError as e:
                last_error = f"http {e.status}"
                if e.status in TRANSIENT_STATUS:
                    transient, failure_code = True, "transient_http"
                    time.sleep(0)
                    continue
                transient, failure_code = False, f"http_{e.status}"
                break
            except Exception as e:  # noqa: BLE001
                last_error = str(e) or type(e).__name__
                transient = _is_transient(e)
                failure_code = "transient_error" if transient else "fetch_error"
                if not transient:
                    break
                continue
        if result is None:
            result = DownloadResult(
                item.url, False, attempts=attempts, failure_code=failure_code or "fetch_error",
                message=last_error,
            )
        results.append(result)
        if conn is not None:
            if result.ok:
                mark_done(conn, item.url, result.sha256 or "", result.s3_key or "")
            else:
                retry = transient and (result.failure_code not in PERMANENT_FAILURE_CODES)
                mark_failed(conn, item.url, result.message, retry=retry, attempts=attempts)
    return results


class _HttpStatusError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


def _fetch(
    url: str, http_get: Callable[..., Any] | None
) -> tuple[bytes, str | None, list[str]]:
    if http_get is not None:
        resp = http_get(url)
        status = getattr(resp, "status_code", 200)
        if status != 200:
            raise _HttpStatusError(status)
        headers = getattr(resp, "headers", {}) or {}
        ctype = headers.get("content-type") or headers.get("Content-Type")
        hops = list(getattr(resp, "redirect_hops", []) or [])
        if hasattr(resp, "iter_bytes"):
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes(65536):
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("payload exceeds size cap")
            return b"".join(chunks), ctype, hops
        content = bytes(getattr(resp, "content", b""))
        if len(content) > MAX_BYTES:
            raise ValueError("payload exceeds size cap")
        return content, ctype, hops
    import httpx

    from spider_bench.sources.http import DEFAULT_USER_AGENT

    hops: list[str] = []
    with httpx.stream("GET", url, follow_redirects=True, timeout=30,
                      headers={"User-Agent": DEFAULT_USER_AGENT}) as resp:
        for h in getattr(resp, "history", []) or []:
            loc = h.headers.get("location")
            if loc:
                hops.append(str(loc))
        if resp.status_code != 200:
            raise _HttpStatusError(resp.status_code)
        ctype = resp.headers.get("content-type")
        chunks = []
        total = 0
        for chunk in resp.iter_bytes(65536):
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError("payload exceeds size cap")
        return b"".join(chunks), ctype, hops
