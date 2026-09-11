"""Shared HTTP primitives for metadata-only source discovery.

No image bytes are ever downloaded here. All helpers support dry-run,
bounded concurrency, retry with backoff, rate limiting, and record caps.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx

T = TypeVar("T")

# Wikimedia-family APIs reject generic/bot-like User-Agents (HTTP 403).
# Keep this descriptive; override via SPIDER_BENCH_UA.
DEFAULT_USER_AGENT = os.environ.get(
    "SPIDER_BENCH_UA",
    "spider-bench/0.2 (Polish spider image dataset; "
    "https://spiders-dataset-088543363904.s3.us-east-1.amazonaws.com/)",
)


def default_headers() -> dict[str, str]:
    return {"User-Agent": DEFAULT_USER_AGENT}


class RateLimiter:
    """Minimum-interval rate limiter.

    Args:
        rate_per_sec: allowed requests per second (<=0 means no limit).
    """

    def __init__(self, rate_per_sec: float = 2.0) -> None:
        self.min_interval = 1.0 / rate_per_sec if rate_per_sec and rate_per_sec > 0 else 0.0
        self._last: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire_async(self) -> None:
        if self.min_interval <= 0:
            return
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._last + self.min_interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = asyncio.get_event_loop().time()

    def acquire_sync(self) -> None:
        if self.min_interval <= 0:
            return
        now = time.monotonic()
        wait = self._last + self.min_interval - now
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    return min(cap, base * (2.0**attempt))


def is_retryable_status(status: int) -> bool:
    return status == 429 or 500 <= status < 600


def should_retry_exc(exc: Exception) -> bool:
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError))


def fetch_json_sync(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = 30.0,
    retries: int = 4,
    backoff_base: float = 1.0,
    rate_limiter: RateLimiter | None = None,
    dry_run: bool = False,
    client: httpx.Client | None = None,
) -> dict | list | None:
    """GET JSON synchronously with retry. Returns None when dry_run=True."""
    if dry_run:
        return None
    own = client is None
    c = client or httpx.Client(timeout=timeout)
    send_headers = headers if headers is not None else default_headers()
    try:
        for attempt in range(retries + 1):
            if rate_limiter is not None:
                rate_limiter.acquire_sync()
            try:
                r = c.get(url, params=params, headers=send_headers)
            except Exception as e:
                if should_retry_exc(e) and attempt < retries:
                    time.sleep(backoff_delay(attempt, backoff_base))
                    continue
                raise
            if r.status_code == 200:
                return r.json()
            if is_retryable_status(r.status_code) and attempt < retries:
                ra = r.headers.get("retry-after")
                delay = float(ra) if ra and ra.isdigit() else backoff_delay(attempt, backoff_base)
                time.sleep(delay)
                continue
            r.raise_for_status()
        return None
    finally:
        if own:
            c.close()


async def fetch_json_async(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    retries: int = 4,
    backoff_base: float = 1.0,
    rate_limiter: RateLimiter | None = None,
    semaphore: asyncio.Semaphore | None = None,
    dry_run: bool = False,
) -> dict | list | None:
    """GET JSON with retry/rate-limit/concurrency bound. Returns None when dry_run=True."""
    if dry_run:
        return None

    async def _do() -> dict | list:
        send_headers = headers if headers is not None else default_headers()
        for attempt in range(retries + 1):
            if rate_limiter is not None:
                await rate_limiter.acquire_async()
            try:
                r = await client.get(url, params=params, headers=send_headers)
            except Exception as e:
                if should_retry_exc(e) and attempt < retries:
                    await asyncio.sleep(backoff_delay(attempt, backoff_base))
                    continue
                raise
            if r.status_code == 200:
                return r.json()
            if is_retryable_status(r.status_code) and attempt < retries:
                ra = r.headers.get("retry-after")
                try:
                    delay = float(ra) if ra else backoff_delay(attempt, backoff_base)
                except ValueError:
                    delay = backoff_delay(attempt, backoff_base)
                await asyncio.sleep(delay)
                continue
            r.raise_for_status()
        raise RuntimeError("unreachable")

    if semaphore is not None:
        async with semaphore:
            return await _do()
    return await _do()


def apply_record_cap(items: list[T], max_records: int) -> list[T]:
    return items[:max(0, max_records)]


@dataclass
class PageCursor:
    """Generic resume cursor for paged discovery."""

    page: int = 1
    last_id: int | str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"page": self.page, "last_id": self.last_id, "extra": self.extra}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> PageCursor:
        d = d or {}
        return cls(page=int(d.get("page", 1) or 1), last_id=d.get("last_id"), extra=dict(d.get("extra", {})))
