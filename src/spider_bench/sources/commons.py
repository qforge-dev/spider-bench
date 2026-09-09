"""Wikimedia Commons discovery client (metadata-only, S3-native).

- Uses the MediaWiki API (query imageinfo) — no scraping, no byte downloads.
- Captures file revision (revid/timestamp/sha1), creator/artist, license
  name + URL, attribution text, description/file page URLs, media URL.
- Ambiguous or multi-license files are flagged ``needs_review=True``.
- Raw JSONL -> ``<prefix>raw-metadata/commons/<snapshot>/`` via boto3.
- Idempotent DB inserts (INSERT OR IGNORE into staging table).
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import re
import sqlite3
from typing import Any

import httpx

from spider_bench.sources.http import (
    DEFAULT_USER_AGENT,
    PageCursor,
    RateLimiter,
    apply_record_cap,
    fetch_json_async,
    fetch_json_sync,
)
from spider_bench.storage.s3 import raw_metadata_key

API_URL = "https://commons.wikimedia.org/w/api.php"

# License templates that map to accepted SPDX-ish identifiers.
LICENSE_TEMPLATE_MAP = {
    "cc-zero": "cc0",
    "cc0": "cc0",
    "cc-by-1.0": "cc-by",
    "cc-by-2.0": "cc-by",
    "cc-by-2.5": "cc-by",
    "cc-by-3.0": "cc-by",
    "cc-by-4.0": "cc-by",
    "cc-by-sa-1.0": "cc-by-sa",
    "cc-by-sa-2.0": "cc-by-sa",
    "cc-by-sa-2.5": "cc-by-sa",
    "cc-by-sa-3.0": "cc-by-sa",
    "cc-by-sa-4.0": "cc-by-sa",
    "cc-by-nc": "cc-by-nc",
    "cc-by-nc-sa": "cc-by-nc-sa",
    "gfdl": "gfdl",
}
# Any of these present alongside/instead of a known license -> manual review.
AMBIGUOUS_LICENSE_MARKERS = frozenset(
    {"gfdl", "fair", "fairuse", "fair-use", "copyrighted", "permission", "pd", "public-domain", "unknown", "cc-by-nd",
     "cc-by-nc-nd", "multi-license", "dual", "self"}
)


def snapshot_today() -> str:
    return _dt.date.today().isoformat()


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def parse_license(extmeta: dict) -> dict[str, Any]:
    """Parse Commons extmetadata LicenseShortName/UsageTerms into normalized license info."""
    short = _strip_html(str((extmeta.get("LicenseShortName") or {}).get("value", "")))
    url = str((extmeta.get("LicenseUrl") or {}).get("value", "") or "")
    usage = _strip_html(str((extmeta.get("UsageTerms") or {}).get("value", "")))
    low = f"{short} {usage} {url}".lower().replace(" ", "-").replace("_", "-")
    norm: str | None = None
    for key, val in LICENSE_TEMPLATE_MAP.items():
        if key in low:
            norm = val
            break
    if norm is None and "cc0" in low.replace("-", ""):
        norm = "cc0"
    return {"license_short": short or None, "license_url": url or None, "usage_terms": usage or None,
            "license": norm}


def detect_ambiguous_licenses(raw_text_blobs: list[str], parsed_license: str | None) -> tuple[bool, str | None]:
    """Return (needs_review, reason). Multi/unknown licenses -> review."""
    blob = " ".join(b.lower() for b in raw_text_blobs if b)
    hits = sorted({m for m in AMBIGUOUS_LICENSE_MARKERS if m in blob})
    distinct = set(re.findall(r"cc-by(?:-nc)?(?:-sa)?|cc-zero|cc0|gfdl", blob))
    multi = len(distinct) > 1 or ("multi-license" in blob) or ("dual" in blob)
    if parsed_license is None:
        return True, "unknown-license" + (f" markers={hits}" if hits else "")
    if multi:
        return True, f"multi-license markers={hits}" if hits else "multi-license"
    if "gfdl" in blob and parsed_license != "gfdl":
        return True, "gfdl-dual-license"
    if any(m in ("fairuse", "fair-use", "permission", "unknown") for m in hits):
        return True, f"ambiguous markers={hits}"
    return False, None


def transform_file(page: dict, snapshot_date: str) -> dict[str, Any]:
    imageinfo = (page.get("imageinfo") or [{}])[0]
    extmeta = imageinfo.get("extmetadata") or {}
    lic = parse_license(extmeta)
    artist = _strip_html(str((extmeta.get("Artist") or {}).get("value", ""))) or None
    credit = _strip_html(str((extmeta.get("Credit") or {}).get("value", ""))) or None
    attribution = credit or artist
    title: str = page.get("title", "")
    file_page = f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}" if title else None
    desc_url = imageinfo.get("descriptionurl") or file_page
    categories = [c.get("title", "") for c in (page.get("categories") or [])]
    templates = [t.get("title", "") for t in (page.get("templates") or [])]
    blob_texts = [str(imageinfo.get("extmetadata", {}))] + categories + templates + [lic.get("license_short") or ""]
    needs_review, reason = detect_ambiguous_licenses(blob_texts, lic.get("license"))
    # Explicit multi-template case: >1 distinct CC license templates listed.
    # Layout/flag helper templates (cc-*-layout, cc-country-flags) are not licenses.
    cc_templates = {t.lower().split(":")[-1].strip() for t in templates
                    if ("cc-" in t.lower() or "cc0" in t.lower() or "cc-zero" in t.lower())
                    and not t.lower().rstrip().endswith("-layout")
                    and "cc-country-flags" not in t.lower()}
    if len(cc_templates) > 1 and not needs_review:
        needs_review, reason = True, f"multi-license templates={sorted(cc_templates)}"
    return {
        "source": "commons",
        "pageid": page.get("pageid"),
        "title": title,
        "file_name": title.split(":", 1)[-1] if ":" in title else title,
        "revid": imageinfo.get("revid") or page.get("lastrevid"),
        "file_timestamp": imageinfo.get("timestamp"),
        "sha1": imageinfo.get("sha1"),
        "size": imageinfo.get("size"),
        "width": imageinfo.get("width"),
        "height": imageinfo.get("height"),
        "mime": imageinfo.get("mime"),
        "media_url": imageinfo.get("url"),
        "thumb_url": imageinfo.get("thumburl"),
        "description_url": desc_url,
        "file_page_url": file_page,
        "creator": artist,
        "attribution": attribution,
        "license": lic.get("license"),
        "license_short": lic.get("license_short"),
        "license_url": lic.get("license_url"),
        "usage_terms": lic.get("usage_terms"),
        "categories": categories,
        "needs_review": needs_review,
        "review_reason": reason,
        "snapshot_date": snapshot_date,
        "raw": page,
        "media": [
            {
                "source": "commons",
                "title": title,
                "license": lic.get("license"),
                "creator": artist,
                "attribution": attribution,
                "media_url": imageinfo.get("url"),
                "source_url": desc_url,
                "needs_review": needs_review,
                "snapshot_date": snapshot_date,
            }
        ],
    }


def transform_query_response(payload: dict, snapshot_date: str) -> list[dict[str, Any]]:
    pages = (payload.get("query") or {}).get("pages") or {}
    items = list(pages.values()) if isinstance(pages, dict) else pages
    return [transform_file(p, snapshot_date) for p in items if not p.get("missing", False)]


def build_query_params(
    *,
    search: str = "Araneae Poland",
    namespace: int = 6,
    limit: int = 50,
    continue_token: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "generator": "search",
        "gsrsearch": search,
        "gsrnamespace": namespace,
        "gsrlimit": max(1, min(50, limit)),
        "prop": "imageinfo|categories|templates",
        "iiprop": "timestamp|user|url|size|sha1|mime|extmetadata",
        "cllimit": "50",
        "tllimit": "50",
    }
    if continue_token:
        params["gsroffset"] = continue_token
    return params


# ---------------------------------------------------------------- network


def discover_files_sync(
    *,
    search: str = "Araneae Poland",
    dry_run: bool = False,
    max_records: int = 500,
    resume: dict | None = None,
    rate_limit: float = 2.0,
    timeout: float = 30.0,
    retries: int = 4,
    client: httpx.Client | None = None,
) -> tuple[list[dict[str, Any]], dict]:
    """Query Commons imageinfo metadata pages (JSON only). Returns (records, cursor)."""
    if dry_run:
        return [], dict(resume or {})
    cursor = PageCursor.from_dict(resume)
    limiter = RateLimiter(rate_limit)
    snapshot = snapshot_today()
    out: list[dict[str, Any]] = []
    cont = cursor.extra.get("gsroffset") if cursor.extra else None
    while len(out) < max_records:
        params = build_query_params(search=search, continue_token=cont)
        payload = fetch_json_sync(
            API_URL, params=params, timeout=timeout, retries=retries, rate_limiter=limiter,
            dry_run=False, client=client,
        )
        if not isinstance(payload, dict):
            break
        batch = transform_query_response(payload, snapshot)
        batch = apply_record_cap(batch, max_records - len(out))
        out.extend(batch)
        cont = (payload.get("continue") or {}).get("gsroffset")
        if not cont:
            return out, {"page": cursor.page + 1, "last_id": out[-1]["title"] if out else None,
                         "extra": {}, "done": True}
    return out, {"page": cursor.page + 1, "last_id": out[-1]["title"] if out else None,
                 "extra": {"gsroffset": cont} if cont else {}, "done": cont is None}


async def discover_files_async(
    *,
    search: str = "Araneae Poland",
    dry_run: bool = False,
    max_records: int = 500,
    resume: dict | None = None,
    rate_limit: float = 2.0,
    timeout: float = 30.0,
    retries: int = 4,
) -> tuple[list[dict[str, Any]], dict]:
    if dry_run:
        return [], dict(resume or {})
    cursor = PageCursor.from_dict(resume)
    limiter = RateLimiter(rate_limit)
    snapshot = snapshot_today()
    out: list[dict[str, Any]] = []
    cont = cursor.extra.get("gsroffset") if cursor.extra else None
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": DEFAULT_USER_AGENT}) as client:
        while len(out) < max_records:
            params = build_query_params(search=search, continue_token=cont)
            payload = await fetch_json_async(
                client, API_URL, params=params, retries=retries, rate_limiter=limiter, dry_run=False
            )
            if not isinstance(payload, dict):
                break
            batch = transform_query_response(payload, snapshot)
            batch = apply_record_cap(batch, max_records - len(out))
            out.extend(batch)
            cont = (payload.get("continue") or {}).get("gsroffset")
            if not cont:
                break
    done = cont is None
    return out, {"page": cursor.page + 1, "last_id": out[-1]["title"] if out else None,
                 "extra": {"gsroffset": cont} if cont else {}, "done": done}


# ---------------------------------------------------------------- S3 + DB


def records_to_jsonl(records: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    for r in records:
        buf.write(json.dumps(r.get("raw", r), ensure_ascii=False) + "\n")
    return buf.getvalue()


def upload_raw_jsonl_to_s3(
    records: list[dict[str, Any]],
    *,
    bucket: str,
    prefix: str,
    snapshot: str,
    s3_client: Any = None,
    dry_run: bool = False,
    max_records: int = 500,
    resume: dict | None = None,
    rate_limit: float = 2.0,
    filename: str = "files.jsonl",
) -> str | None:
    _ = (resume, rate_limit)
    if dry_run or not records:
        return None
    import boto3

    s3_client = s3_client or boto3.client("s3")
    key = raw_metadata_key(prefix, "commons", snapshot, filename)
    body = records_to_jsonl(records[:max_records]).encode("utf-8")
    s3_client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/x-ndjson")
    return f"s3://{bucket}/{key}"


_STAGING_DDL = """
CREATE TABLE IF NOT EXISTS commons_files (
  title TEXT PRIMARY KEY,
  pageid INTEGER,
  revid INTEGER,
  creator TEXT,
  attribution TEXT,
  license TEXT,
  license_url TEXT,
  media_url TEXT,
  source_url TEXT,
  needs_review INTEGER NOT NULL DEFAULT 0,
  review_reason TEXT,
  snapshot_date TEXT NOT NULL
);
"""


def insert_files_idempotent(conn: sqlite3.Connection, records: list[dict[str, Any]]) -> dict[str, int]:
    with conn:
        conn.executescript(_STAGING_DDL)
        n = 0
        for r in records:
            cur = conn.execute(
                """INSERT OR IGNORE INTO commons_files
                   (title, pageid, revid, creator, attribution, license, license_url,
                    media_url, source_url, needs_review, review_reason, snapshot_date)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    r.get("title"),
                    r.get("pageid"),
                    r.get("revid"),
                    r.get("creator"),
                    r.get("attribution"),
                    r.get("license"),
                    r.get("license_url"),
                    r.get("media_url"),
                    r.get("description_url"),
                    1 if r.get("needs_review") else 0,
                    r.get("review_reason"),
                    r.get("snapshot_date"),
                ),
            )
            n += cur.rowcount
    return {"files": n}
