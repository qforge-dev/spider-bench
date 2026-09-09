"""iNaturalist REST discovery client (metadata-only, S3-native).

- Filters: taxon Araneae, place Poland, photos required, license allowlist,
  research-grade optional.
- Observation is the grouping unit; photos become media rows.
- Preserves observation_id / photo_id / observer / licenses / urls / timestamps.
- Raw JSONL snapshots are uploaded to
  ``<prefix>raw-metadata/inaturalist/<snapshot>/`` via boto3.
- DB inserts are idempotent (INSERT OR IGNORE into staging tables).
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

BASE_URL = "https://api.inaturalist.org/v1"
OBSERVATIONS_ENDPOINT = f"{BASE_URL}/observations"

# iNaturalist taxon id for order Araneae and place id for Poland.
TAXON_ARANEAE_ID = 47118
PLACE_POLAND_ID = 7800

# Conservative default allowlist. CC BY-SA / CC BY-NC-SA need a documented
# ShareAlike decision before inclusion (see plan section 7).
LICENSE_ALLOWLIST_DEFAULT = frozenset({"cc0", "cc-by", "cc-by-nc"})
LICENSE_ALLOWLIST_RESEARCH = frozenset({"cc0", "cc-by", "cc-by-nc", "cc-by-sa", "cc-by-nc-sa"})
LICENSE_CODE_MAP = {
    "CC0": "cc0",
    "CC-BY": "cc-by",
    "CC-BY-NC": "cc-by-nc",
    "CC-BY-SA": "cc-by-sa",
    "CC-BY-NC-SA": "cc-by-nc-sa",
    "CC-BY-ND": "cc-by-nd",
    "CC-BY-NC-ND": "cc-by-nc-nd",
}


def snapshot_today() -> str:
    return _dt.date.today().isoformat()


def normalize_license(code: str | None) -> str | None:
    if not code:
        return None
    c = code.strip()
    if c in LICENSE_CODE_MAP:
        return LICENSE_CODE_MAP[c]
    low = c.lower()
    for v in LICENSE_CODE_MAP.values():
        if low == v:
            return v
    return low or None


def build_search_params(
    *,
    taxon_id: int = TAXON_ARANEAE_ID,
    place_id: int = PLACE_POLAND_ID,
    license_allowlist: set[str] | frozenset | None = None,
    research_grade_only: bool = False,
    photos_only: bool = True,
    per_page: int = 200,
    page: int = 1,
    id_above: int | None = None,
    order_by: str = "id",
    order: str = "asc",
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "taxon_id": taxon_id,
        "place_id": place_id,
        "per_page": max(1, min(200, per_page)),
        "page": max(1, page),
        "order_by": order_by,
        "order": order,
    }
    if photos_only:
        params["photos"] = "true"
    if research_grade_only:
        params["quality_grade"] = "research"
    if id_above is not None:
        params["id_above"] = id_above
    # iNat `photo_license` accepts comma-separated codes like CC-BY,CC-BY-NC,CC0.
    allow = license_allowlist if license_allowlist is not None else LICENSE_ALLOWLIST_DEFAULT
    if allow:
        reverse = {v: k for k, v in LICENSE_CODE_MAP.items()}
        codes = sorted(reverse.get(a, a) for a in allow)
        params["photo_license"] = ",".join(codes)
    return params


def _safe_geo(obs: dict) -> dict[str, Any]:
    geo = obs.get("geojson") or {}
    coords = geo.get("coordinates") if isinstance(geo, dict) else None
    return {
        "latitude": obs.get("latitude"),
        "longitude": obs.get("longitude"),
        "geoprivacy": obs.get("geoprivacy"),
        "coordinates_obscured": obs.get("coordinates_obscured"),
        "positional_accuracy": obs.get("positional_accuracy"),
        "place_guess": obs.get("place_guess"),
        "_geojson_coordinates": coords,
    }


def transform_observation(raw: dict, snapshot_date: str) -> dict[str, Any]:
    """Transform one raw iNat observation into observation + media dicts.

    Pure function (no network, no DB). Groups photos under the observation.
    """
    obs_id = raw.get("id")
    photos = raw.get("photos") or []
    user = raw.get("user") or {}
    taxon = raw.get("taxon") or {}
    media: list[dict[str, Any]] = []
    for p in photos:
        photo_id = p.get("id")
        lic = normalize_license(p.get("license_code"))
        media.append(
            {
                "source": "inaturalist",
                "observation_id": obs_id,
                "photo_id": photo_id,
                "license": lic,
                "license_accepted": lic,
                "creator": (p.get("user") or user or {}).get("login") or user.get("name"),
                "observer_login": user.get("login"),
                "observer_id": user.get("id"),
                "media_url": p.get("url") or p.get("original_url"),
                "original_url": p.get("original_url"),
                "large_url": p.get("large_url"),
                "medium_url": p.get("medium_url"),
                "small_url": p.get("small_url"),
                "source_url": f"https://www.inaturalist.org/photos/{photo_id}" if photo_id else None,
                "attribution": p.get("attribution"),
                "snapshot_date": snapshot_date,
            }
        )
    return {
        "source": "inaturalist",
        "observation_id": obs_id,
        "uuid": raw.get("uuid"),
        "observer_id": user.get("id"),
        "observer_login": user.get("login"),
        "quality_grade": raw.get("quality_grade"),
        "captive": raw.get("captive"),
        "observed_on": raw.get("observed_on") or raw.get("observed_on_string"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "license": normalize_license(raw.get("license_code")),
        "source_taxon_id": taxon.get("id"),
        "source_taxon_name": taxon.get("name"),
        "source_taxon_rank": taxon.get("rank"),
        "taxon": taxon,
        "description": raw.get("description"),
        "source_url": raw.get("uri") or (f"https://www.inaturalist.org/observations/{obs_id}" if obs_id else None),
        "geo": _safe_geo(raw),
        "photos_count": len(photos),
        "snapshot_date": snapshot_date,
        "raw": raw,
        "media": media,
    }


def transform_search_response(payload: dict, snapshot_date: str) -> list[dict[str, Any]]:
    results = payload.get("results") or []
    return [transform_observation(r, snapshot_date) for r in results]


# ---------------------------------------------------------------- network


def discover_observations_sync(
    *,
    taxon_id: int = TAXON_ARANEAE_ID,
    place_id: int = PLACE_POLAND_ID,
    license_allowlist: set[str] | frozenset | None = None,
    research_grade_only: bool = False,
    per_page: int = 200,
    dry_run: bool = False,
    max_records: int = 1000,
    resume: dict | None = None,
    rate_limit: float = 2.0,
    timeout: float = 30.0,
    retries: int = 4,
    client: httpx.Client | None = None,
) -> tuple[list[dict[str, Any]], dict]:
    """Fetch observation metadata pages (JSON only, never image bytes).

    Returns (records, next_resume_cursor). Honors dry_run / max_records /
    resume / rate_limit.
    """
    if dry_run:
        return [], dict(resume or {})
    cursor = PageCursor.from_dict(resume)
    limiter = RateLimiter(rate_limit)
    snapshot = snapshot_today()
    out: list[dict[str, Any]] = []
    page = cursor.page
    id_above = cursor.last_id if isinstance(cursor.last_id, int) else None
    while len(out) < max_records:
        params = build_search_params(
            taxon_id=taxon_id,
            place_id=place_id,
            license_allowlist=license_allowlist,
            research_grade_only=research_grade_only,
            per_page=per_page,
            page=page,
            id_above=id_above if page == cursor.page else None,
        )
        payload = fetch_json_sync(
            OBSERVATIONS_ENDPOINT,
            params=params,
            timeout=timeout,
            retries=retries,
            rate_limiter=limiter,
            dry_run=False,
            client=client,
        )
        if not isinstance(payload, dict):
            break
        batch = transform_search_response(payload, snapshot)
        batch = apply_record_cap(batch, max_records - len(out))
        out.extend(batch)
        total = payload.get("total_results")
        if not batch:
            break
        last = out[-1].get("observation_id")
        page += 1
        if isinstance(total, int) and len(out) >= total:
            last = out[-1].get("observation_id")
            return out, {"page": page, "last_id": last, "done": True}
        if len(batch) < params["per_page"]:
            return out, {"page": page, "last_id": last, "done": True}
    last = out[-1].get("observation_id") if out else cursor.last_id
    return out, {"page": page, "last_id": last, "done": False}


async def discover_observations_async(
    *,
    taxon_id: int = TAXON_ARANEAE_ID,
    place_id: int = PLACE_POLAND_ID,
    license_allowlist: set[str] | frozenset | None = None,
    research_grade_only: bool = False,
    per_page: int = 200,
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
    page = cursor.page
    async with httpx.AsyncClient(timeout=timeout) as client:
        while len(out) < max_records:
            params = build_search_params(
                taxon_id=taxon_id,
                place_id=place_id,
                license_allowlist=license_allowlist,
                research_grade_only=research_grade_only,
                per_page=per_page,
                page=page,
            )
            payload = await fetch_json_async(
                client, OBSERVATIONS_ENDPOINT, params=params, retries=retries, rate_limiter=limiter, dry_run=False
            )
            if not isinstance(payload, dict):
                break
            batch = transform_search_response(payload, snapshot)
            batch = apply_record_cap(batch, max_records - len(out))
            out.extend(batch)
            if not batch or len(batch) < params["per_page"]:
                last = out[-1].get("observation_id") if out else cursor.last_id
                return out, {"page": page + 1, "last_id": last, "done": True}
            page += 1
    last = out[-1].get("observation_id") if out else cursor.last_id
    return out, {"page": page, "last_id": last, "done": False}


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
    resume: dict | None = None,  # accepted for uniform network signature; unused
    rate_limit: float = 2.0,  # accepted for uniform signature; unused
    filename: str = "observations.jsonl",
) -> str | None:
    """Upload raw observation JSONL to raw-metadata/inaturalist/<snapshot>/. Returns S3 URI or None."""
    _ = (resume, rate_limit, max_records)
    if dry_run or not records:
        return None
    import boto3

    s3_client = s3_client or boto3.client("s3")
    key = raw_metadata_key(prefix, "inaturalist", snapshot, filename)
    body = records_to_jsonl(records[:max_records]).encode("utf-8")
    s3_client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/x-ndjson")
    return f"s3://{bucket}/{key}"


_STAGING_DDL = """
CREATE TABLE IF NOT EXISTS inat_observations (
  observation_id INTEGER PRIMARY KEY,
  uuid TEXT,
  observer_id INTEGER,
  observer_login TEXT,
  quality_grade TEXT,
  source_taxon_id INTEGER,
  source_taxon_name TEXT,
  source_url TEXT,
  observed_on TEXT,
  snapshot_date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inat_media (
  photo_id INTEGER PRIMARY KEY,
  observation_id INTEGER NOT NULL,
  license TEXT,
  creator TEXT,
  media_url TEXT,
  source_url TEXT,
  snapshot_date TEXT NOT NULL
);
"""


def insert_observations_idempotent(conn: sqlite3.Connection, records: list[dict[str, Any]]) -> dict[str, int]:
    """Idempotent insert of transformed observations+media. Returns counts."""
    with conn:
        conn.executescript(_STAGING_DDL)
        n_obs = n_media = 0
        for r in records:
            cur = conn.execute(
                """INSERT OR IGNORE INTO inat_observations
                   (observation_id, uuid, observer_id, observer_login, quality_grade,
                    source_taxon_id, source_taxon_name, source_url, observed_on, snapshot_date)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    r.get("observation_id"),
                    r.get("uuid"),
                    r.get("observer_id"),
                    r.get("observer_login"),
                    r.get("quality_grade"),
                    r.get("source_taxon_id"),
                    r.get("source_taxon_name"),
                    r.get("source_url"),
                    r.get("observed_on"),
                    r.get("snapshot_date"),
                ),
            )
            n_obs += cur.rowcount
            for m in r.get("media") or []:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO inat_media
                       (photo_id, observation_id, license, creator, media_url, source_url, snapshot_date)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        m.get("photo_id"),
                        m.get("observation_id"),
                        m.get("license"),
                        m.get("creator"),
                        m.get("media_url"),
                        m.get("source_url"),
                        m.get("snapshot_date"),
                    ),
                )
                n_media += cur.rowcount
    return {"observations": n_obs, "media": n_media}
