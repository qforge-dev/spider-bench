"""One representative image per checklist species (plan §10C-D, one-per-taxon policy).

Metadata-only candidate search first (iNaturalist Poland, licensed photos,
research-grade preferred), then a single download per species to S3
content-addressed storage. Species with no acceptable candidate are reported
as no_image — never silently dropped.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Any

import httpx

from spider_bench.media.select import load_license_profile, normalize_license

INAT_OBS_URL = "https://api.inaturalist.org/v1/observations"
PLACE_POLAND_ID = 7800

LICENSE_CODE_MAP = {
    "CC0": "cc0", "CC-BY": "cc-by", "CC-BY-NC": "cc-by-nc",
    "CC-BY-SA": "cc-by-sa", "CC-BY-NC-SA": "cc-by-nc-sa",
}


@dataclass
class SpeciesCandidate:
    taxon: str
    url: str
    license: str
    creator: str | None = None
    observer: str | None = None
    observation_id: int | None = None
    photo_id: int | None = None
    attribution: str | None = None
    research_grade: bool = False


def _photo_codes(accept: set[str]) -> str:
    reverse = {v: k for k, v in LICENSE_CODE_MAP.items()}
    out = set()
    for a in accept:
        low = a.lower()
        for code, canon in (("cc0", "CC0-1.0"), ("cc-by", "CC-BY-4.0"), ("cc-by-nc", "CC-BY-NC-4.0"),
                            ("cc-by-sa", "CC-BY-SA-4.0"), ("cc-by-nc-sa", "CC-BY-NC-SA-4.0")):
            if low == code or low == canon.lower():
                out.add(reverse.get(code, code))
    return ",".join(sorted(out)) or "cc0,cc-by,cc-by-nc"


def pick_candidate(taxon: str, accept: set[str], client: httpx.Client,
                   rate_limit: float = 2.0) -> SpeciesCandidate | None:
    """Best licensed Poland photo for one species, research-grade first. Pure HTTP."""
    codes = _photo_codes(accept)
    for grade in ("research", None):
        params: dict[str, Any] = {"taxon_name": taxon, "place_id": PLACE_POLAND_ID,
                                  "photos": "true", "photo_licensed": "true",
                                  "photo_license": codes, "per_page": 10,
                                  "order_by": "votes", "order": "desc"}
        if grade:
            params["quality_grade"] = grade
        try:
            r = client.get(INAT_OBS_URL, params=params, timeout=30)
            r.raise_for_status()
            results = r.json().get("results", [])
        except Exception:
            results = []
        time.sleep(1.0 / max(rate_limit, 0.1))
        for obs in results:
            for p in obs.get("photos") or []:
                lic = normalize_license(p.get("license_code"))
                if lic not in accept:
                    continue
                url = p.get("original_url") or p.get("large_url") or p.get("url")
                if not url:
                    continue
                user = obs.get("user") or {}
                return SpeciesCandidate(
                    taxon=taxon, url=url, license=lic,
                    creator=(p.get("user") or user or {}).get("login") or user.get("login"),
                    observer=user.get("login"), observation_id=obs.get("id"), photo_id=p.get("id"),
                    attribution=p.get("attribution"),
                    research_grade=obs.get("quality_grade") == "research")
    return None


def collect_candidates(taxa: list[str], profile: str = "research",
                       rate_limit: float = 2.0, limit: int | None = None,
                       progress_every: int = 50) -> dict[str, Any]:
    """Metadata-only pass over the checklist. Returns manifest dict."""
    accept, _ = load_license_profile(profile)
    names = taxa[:limit] if limit else taxa
    found: list[dict] = []
    missing: list[str] = []
    with httpx.Client() as client:
        for i, name in enumerate(names, 1):
            cand = pick_candidate(name, accept, client, rate_limit)
            if cand:
                found.append(cand.__dict__)
            else:
                missing.append(name)
            if i % progress_every == 0:
                print(f"collect-one: {i}/{len(names)} found={len(found)} missing={len(missing)}", flush=True)
    return {"species_total": len(names), "found": len(found), "missing": len(missing),
            "candidates": found, "missing_taxa": missing}


def _search_variants(taxon: str) -> list[str]:
    """Full quoted name, then binomial (subspecies rarely have own files)."""
    parts = taxon.split()
    variants = [f'"{taxon}"']
    if len(parts) > 2:
        variants.append(f'"{parts[0]} {parts[1]}"')
    return variants


def gap_fill_commons(taxa: list[str], profile: str = "research",
                     rate_limit: float = 2.0, limit: int | None = None,
                     progress_every: int = 50,
                     include_sharealike: bool = False) -> dict[str, Any]:
    """Commons fallback for species with no iNat candidate. Metadata-only search.

    Returns manifest with found / still_missing / needs_review (ambiguous
    licenses always go to manual review). With include_sharealike, CC BY-SA
    files are accepted instead of quarantined; the decision is recorded in
    the manifest (files used unmodified, attribution preserved).
    """
    from spider_bench.sources.commons import discover_files_sync
    from spider_bench.sources.http import DEFAULT_USER_AGENT

    accept, review = load_license_profile(profile)
    sa = {"CC-BY-SA-4.0", "CC-BY-SA-3.0", "CC-BY-SA-2.5"}
    if include_sharealike:
        accept = set(accept) | sa
        review = set(review) - sa
    names = taxa[:limit] if limit else taxa
    found: list[dict] = []
    review_list: list[dict] = []
    missing: list[str] = []
    with httpx.Client(headers={"User-Agent": DEFAULT_USER_AGENT}) as client:
        for i, name in enumerate(names, 1):
            records: list[dict] = []
            try:
                for variant in _search_variants(name):
                    batch, _ = discover_files_sync(search=variant, max_records=10,
                                                   rate_limit=rate_limit, client=client)
                    records.extend(batch)
                    if batch:
                        break
            except Exception:
                records = []
            picked = None
            for rec in records:
                for m in rec.get("media") or []:
                    lic = normalize_license(m.get("license"))
                    if rec.get("needs_review") or lic in review:
                        review_list.append({"taxon": name, "title": m.get("title"),
                                            "license": lic, "reason": rec.get("review_reason") or "sharealike_or_ambiguous"})
                        continue
                    if lic in accept and m.get("media_url"):
                        picked = {"taxon": name, "url": m["media_url"], "license": lic,
                                  "creator": m.get("creator"), "observation_id": None,
                                  "photo_id": m.get("title"), "attribution": m.get("attribution"),
                                  "research_grade": False, "source": "commons",
                                  "file_page": m.get("source_url")}
                        break
                if picked:
                    break
            if picked:
                found.append(picked)
            elif not any(r["taxon"] == name for r in review_list):
                missing.append(name)
            if i % progress_every == 0:
                print(f"gap-fill: {i}/{len(names)} found={len(found)} review={len(review_list)} missing={len(missing)}", flush=True)
            time.sleep(1.0 / max(rate_limit, 0.1))
    return {"species_total": len(names), "found": len(found), "review": len(review_list),
            "still_missing": len(missing), "candidates": found,
            "review_list": review_list, "missing_taxa": missing,
            "sharealike_decision": ("accepted-unmodified-with-attribution"
                                    if include_sharealike else "quarantined-for-review")}


def record_media_row(conn: sqlite3.Connection, *, taxon: str, s3_uri: str, public_url: str,
                     license: str, creator: str | None, attribution: str | None,
                     source: str, source_media_id: str, sha256: str,
                     observation_id: str | None = None, quality_grade: str | None = None,
                     width: int | None = None, height: int | None = None) -> None:
    obs_row_id = None
    if observation_id:
        conn.execute(
            """INSERT INTO observations (source, source_observation_id, source_taxon, quality_grade, country_code)
               VALUES (?, ?, ?, ?, 'PL')
               ON CONFLICT(source, source_observation_id) DO UPDATE SET quality_grade=excluded.quality_grade""",
            (source, str(observation_id), taxon, quality_grade))
        row = conn.execute(
            "SELECT id FROM observations WHERE source=? AND source_observation_id=?",
            (source, str(observation_id))).fetchone()
        obs_row_id = row[0] if row else None
        taxrow = conn.execute(
            "SELECT id FROM taxa WHERE scientific_name=?", (taxon,)).fetchone()
        if taxrow and obs_row_id:
            conn.execute("UPDATE observations SET taxon_id=? WHERE id=?", (taxrow[0], obs_row_id))
    conn.execute(
        """INSERT INTO media (observation_id, source, source_media_id, s3_uri, public_url, creator, license,
                              attribution, width, height, sha256, validation_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted')
           ON CONFLICT(source, source_media_id) DO UPDATE SET
             s3_uri=excluded.s3_uri, public_url=excluded.public_url, license=excluded.license,
             observation_id=COALESCE(excluded.observation_id, media.observation_id),
             validation_status='accepted'""",
        (obs_row_id, source, source_media_id, s3_uri, public_url, creator, license, attribution,
         width, height, sha256))
    conn.commit()
