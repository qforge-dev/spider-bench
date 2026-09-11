"""GBIF occurrence discovery client (metadata-only, S3-native).

- Uses GBIF Occurrence search API (paged) + download API (DOI) support.
- Extracts media records (multimedia extension).
- Cross-source identity keys: occurrenceID / institutionCode / catalogNumber /
  references / iNaturalist IDs; flags iNaturalist republications.
- Raw JSONL snapshots -> ``<prefix>raw-metadata/gbif/<snapshot>/`` via boto3.
- Idempotent DB inserts (INSERT OR IGNORE into staging tables).
- Never downloads image bytes.
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import sqlite3
from typing import Any

import httpx

from spider_bench.sources.http import (
    PageCursor,
    RateLimiter,
    apply_record_cap,
    fetch_json_async,
    fetch_json_sync,
)
from spider_bench.storage.s3 import raw_metadata_key

BASE_URL = "https://api.gbif.org/v1"
OCCURRENCE_SEARCH = f"{BASE_URL}/occurrence/search"
OCCURRENCE_GET = f"{BASE_URL}/occurrence"
DOWNLOAD_REQUEST = f"{BASE_URL}/occurrence/download/request"
DOWNLOAD_GET = f"{BASE_URL}/occurrence/download"

COUNTRY_POLAND = "PL"
TAXON_ARANEAE_KEY = 1496  # GBIF backbone orderKey for order Araneae


def snapshot_today() -> str:
    return _dt.date.today().isoformat()


def identity_keys(raw: dict) -> dict[str, Any]:
    """Cross-source identity keys for dedup against iNat/Commons/institutions."""
    refs = raw.get("references") or raw.get("reference") or ""
    return {
        "gbif_id": raw.get("key") or raw.get("gbifID"),
        "occurrence_id": raw.get("occurrenceID"),
        "institution_code": raw.get("institutionCode"),
        "collection_code": raw.get("collectionCode"),
        "catalog_number": raw.get("catalogNumber"),
        "record_number": raw.get("recordNumber"),
        "references": refs,
        "dataset_key": raw.get("datasetKey"),
        "publishing_org_key": raw.get("publishingOrgKey"),
        "inaturalist_observation_id": extract_inaturalist_id(raw),
    }


def extract_inaturalist_id(raw: dict) -> int | None:
    for field in ("references", "reference", "occurrenceID", "occurrenceRemarks"):
        v = raw.get(field)
        if isinstance(v, str) and "inaturalist.org/observations/" in v:
            try:
                tail = v.split("inaturalist.org/observations/")[1].split("/")[0].split("?")[0].split("#")[0]
                digits = "".join(c for c in tail if c.isdigit())
                return int(digits) if digits else None
            except (IndexError, ValueError):
                return None
    return None


def is_inaturalist_republication(raw: dict) -> bool:
    blob = json.dumps(
        {k: raw.get(k) for k in ("datasetName", "publisher", "references", "reference", "occurrenceID", "datasetKey")}
    ).lower()
    if "inaturalist" in blob or "inaturalist.org/observations" in blob:
        return True
    return extract_inaturalist_id(raw) is not None


def _norm_media(m: dict, snapshot: str) -> dict[str, Any]:
    return {
        "source": "gbif",
        "identifier": m.get("identifier"),
        "format": m.get("format"),
        "type": m.get("type"),
        "creator": m.get("creator"),
        "license": m.get("license"),
        "rights_holder": m.get("rightsHolder"),
        "references": m.get("references"),
        "media_url": m.get("identifier"),
        "snapshot_date": snapshot,
    }


def transform_occurrence(raw: dict, snapshot_date: str) -> dict[str, Any]:
    media = [_norm_media(m, snapshot_date) for m in (raw.get("media") or [])]
    return {
        "source": "gbif",
        "gbif_id": raw.get("key") or raw.get("gbifID"),
        "scientific_name": raw.get("scientificName"),
        "taxon_key": raw.get("taxonKey"),
        "country": raw.get("country"),
        "event_date": raw.get("eventDate"),
        "basis_of_record": raw.get("basisOfRecord"),
        "source_url": f"https://www.gbif.org/occurrence/{raw.get('key')}" if raw.get("key") else None,
        "media_count": len(media),
        "identity": identity_keys(raw),
        "is_inaturalist_republication": is_inaturalist_republication(raw),
        "license": raw.get("license"),
        "rights_holder": raw.get("rightsHolder"),
        "snapshot_date": snapshot_date,
        "raw": raw,
        "media": media,
    }


def transform_search_response(payload: dict, snapshot_date: str) -> list[dict[str, Any]]:
    return [transform_occurrence(r, snapshot_date) for r in (payload.get("results") or [])]


def build_search_params(
    *,
    taxon_key: int = TAXON_ARANEAE_KEY,
    country: str = COUNTRY_POLAND,
    media_type: str = "StillImage",
    limit: int = 300,
    offset: int = 0,
) -> dict[str, Any]:
    return {
        "taxon_key": taxon_key,
        "country": country,
        "media_type": media_type,
        "limit": max(1, min(1000, limit)),
        "offset": max(0, offset),
    }


# ---------------------------------------------------------------- network


def discover_occurrences_sync(
    *,
    taxon_key: int = TAXON_ARANEAE_KEY,
    country: str = COUNTRY_POLAND,
    dry_run: bool = False,
    max_records: int = 1000,
    resume: dict | None = None,
    rate_limit: float = 2.0,
    timeout: float = 30.0,
    retries: int = 4,
    client: httpx.Client | None = None,
) -> tuple[list[dict[str, Any]], dict]:
    """Page GBIF occurrence search (JSON metadata only). Returns (records, cursor)."""
    if dry_run:
        return [], dict(resume or {})
    cursor = PageCursor.from_dict(resume)
    limiter = RateLimiter(rate_limit)
    snapshot = snapshot_today()
    out: list[dict[str, Any]] = []
    offset = int(cursor.extra.get("offset", 0)) if cursor.extra else 0
    while len(out) < max_records:
        params = build_search_params(taxon_key=taxon_key, country=country, offset=offset)
        payload = fetch_json_sync(
            OCCURRENCE_SEARCH, params=params, timeout=timeout, retries=retries, rate_limiter=limiter,
            dry_run=False, client=client,
        )
        if not isinstance(payload, dict):
            break
        batch = transform_search_response(payload, snapshot)
        batch = apply_record_cap(batch, max_records - len(out))
        out.extend(batch)
        end = payload.get("endOfRecords", True)
        if end or not batch:
            return out, {"page": cursor.page, "last_id": out[-1]["gbif_id"] if out else None,
                         "extra": {"offset": offset + len(batch)}, "done": True}
        offset += len(batch)
    return out, {"page": cursor.page, "last_id": out[-1]["gbif_id"] if out else None,
                 "extra": {"offset": offset}, "done": False}


async def discover_occurrences_async(
    *,
    taxon_key: int = TAXON_ARANEAE_KEY,
    country: str = COUNTRY_POLAND,
    dry_run: bool = False,
    max_records: int = 1000,
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
    offset = int(cursor.extra.get("offset", 0)) if cursor.extra else 0
    async with httpx.AsyncClient(timeout=timeout) as client:
        while len(out) < max_records:
            params = build_search_params(taxon_key=taxon_key, country=country, offset=offset)
            payload = await fetch_json_async(
                client, OCCURRENCE_SEARCH, params=params, retries=retries, rate_limiter=limiter, dry_run=False
            )
            if not isinstance(payload, dict):
                break
            batch = transform_search_response(payload, snapshot)
            batch = apply_record_cap(batch, max_records - len(out))
            out.extend(batch)
            if payload.get("endOfRecords", True) or not batch:
                break
            offset += len(batch)
    return out, {"page": cursor.page, "last_id": out[-1]["gbif_id"] if out else None,
                 "extra": {"offset": offset}, "done": True}


def request_download(
    predicate: dict,
    *,
    dry_run: bool = False,
    max_records: int = 1000,  # cap guard: predicate downloads can be large; caller must set explicitly
    resume: dict | None = None,
    rate_limit: float = 2.0,
    timeout: float = 60.0,
    retries: int = 3,
    client: httpx.Client | None = None,
) -> str | None:
    """Submit an async GBIF download predicate. Returns download key (or None for dry-run)."""
    _ = (max_records, resume)
    if dry_run:
        return None
    limiter = RateLimiter(rate_limit)
    limiter.acquire_sync()
    own = client is None
    c = client or httpx.Client(timeout=timeout)
    try:
        for attempt in range(retries + 1):
            try:
                r = c.post(DOWNLOAD_REQUEST, json=predicate)
            except Exception:
                if attempt < retries:
                    import time as _t

                    from spider_bench.sources.http import backoff_delay

                    _t.sleep(backoff_delay(attempt))
                    continue
                raise
            if r.status_code in (200, 201, 202):
                return r.text.strip().strip('"') or None
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                import time as _t

                from spider_bench.sources.http import backoff_delay

                _t.sleep(backoff_delay(attempt))
                continue
            r.raise_for_status()
        return None
    finally:
        if own:
            c.close()


def get_download_meta(
    download_key: str,
    *,
    dry_run: bool = False,
    max_records: int = 1000,
    resume: dict | None = None,
    rate_limit: float = 2.0,
    timeout: float = 30.0,
) -> dict | None:
    """Fetch download status incl. DOI when available. None for dry-run."""
    _ = (max_records, resume, rate_limit)
    if dry_run:
        return None
    with httpx.Client(timeout=timeout) as c:
        r = c.get(f"{DOWNLOAD_GET}/{download_key}")
        r.raise_for_status()
        data = r.json()
        return {"key": download_key, "status": data.get("status"), "doi": data.get("doi"),
                "download_link": data.get("downloadLink"), "total_records": data.get("totalRecords"),
                "raw": data}


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
    max_records: int = 1000,
    resume: dict | None = None,
    rate_limit: float = 2.0,
    filename: str = "occurrences.jsonl",
) -> str | None:
    _ = (resume, rate_limit)
    if dry_run or not records:
        return None
    import boto3

    s3_client = s3_client or boto3.client("s3")
    key = raw_metadata_key(prefix, "gbif", snapshot, filename)
    body = records_to_jsonl(records[:max_records]).encode("utf-8")
    s3_client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/x-ndjson")
    return f"s3://{bucket}/{key}"


_STAGING_DDL = """
CREATE TABLE IF NOT EXISTS gbif_occurrences (
  gbif_id INTEGER PRIMARY KEY,
  scientific_name TEXT,
  occurrence_id TEXT,
  institution_code TEXT,
  catalog_number TEXT,
  dataset_key TEXT,
  inaturalist_observation_id INTEGER,
  is_inaturalist_republication INTEGER NOT NULL DEFAULT 0,
  source_url TEXT,
  snapshot_date TEXT NOT NULL,
  UNIQUE(occurrence_id, institution_code, catalog_number)
);
CREATE TABLE IF NOT EXISTS gbif_media (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gbif_id INTEGER NOT NULL,
  media_url TEXT NOT NULL,
  license TEXT,
  creator TEXT,
  snapshot_date TEXT NOT NULL,
  UNIQUE(gbif_id, media_url)
);
"""


def insert_occurrences_idempotent(conn: sqlite3.Connection, records: list[dict[str, Any]]) -> dict[str, int]:
    with conn:
        conn.executescript(_STAGING_DDL)
        n_occ = n_media = 0
        for r in records:
            ident = r.get("identity", {})
            cur = conn.execute(
                """INSERT OR IGNORE INTO gbif_occurrences
                   (gbif_id, scientific_name, occurrence_id, institution_code, catalog_number,
                    dataset_key, inaturalist_observation_id, is_inaturalist_republication, source_url, snapshot_date)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    r.get("gbif_id"),
                    r.get("scientific_name"),
                    ident.get("occurrence_id"),
                    ident.get("institution_code"),
                    ident.get("catalog_number"),
                    ident.get("dataset_key"),
                    ident.get("inaturalist_observation_id"),
                    1 if r.get("is_inaturalist_republication") else 0,
                    r.get("source_url"),
                    r.get("snapshot_date"),
                ),
            )
            n_occ += cur.rowcount
            for m in r.get("media") or []:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO gbif_media (gbif_id, media_url, license, creator, snapshot_date)
                       VALUES (?,?,?,?,?)""",
                    (r.get("gbif_id"), m.get("media_url"), m.get("license"), m.get("creator"), m.get("snapshot_date")),
                )
                n_media += cur.rowcount
    return {"occurrences": n_occ, "media": n_media}
