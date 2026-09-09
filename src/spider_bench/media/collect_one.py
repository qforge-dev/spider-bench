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
    country: str | None = None
    place_guess: str | None = None


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


def _large_photo_url(url: str) -> str:
    """iNat serves size variants by path token; API hands us 75px squares.

    Upgrade square/small/medium to large (~1024px): plenty for classification,
    far lighter than originals. Non-iNat URLs pass through untouched.
    """
    if "inaturalist" not in url:
        return url
    for small in ("/square.", "/small.", "/medium."):
        if small in url:
            return url.replace(small, "/large.")
    return url


def pick_candidates(taxon: str, accept: set[str], client: httpx.Client,
                    rate_limit: float = 2.0, place_id: int | None = PLACE_POLAND_ID,
                    n: int = 10) -> list[SpeciesCandidate]:
    """Up to n candidates from distinct observations, research-grade first. Pure HTTP."""
    codes = _photo_codes(accept)
    out: list[SpeciesCandidate] = []
    seen_obs: set[int] = set()
    for grade in ("research", None):
        if len(out) >= n:
            break
        params: dict[str, Any] = {"taxon_name": taxon,
                                  "photos": "true", "photo_licensed": "true",
                                  "photo_license": codes, "per_page": 50,
                                  "order_by": "votes", "order": "desc"}
        if place_id is not None:
            params["place_id"] = place_id
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
            if len(out) >= n or obs.get("id") in seen_obs:
                continue
            user = obs.get("user") or {}
            for p in obs.get("photos") or []:
                lic = normalize_license(p.get("license_code"))
                if lic not in accept:
                    continue
                url = p.get("original_url") or p.get("large_url") or p.get("url")
                if not url:
                    continue
                url = _large_photo_url(url)
                seen_obs.add(obs.get("id"))
                out.append(SpeciesCandidate(
                    taxon=taxon, url=url, license=lic,
                    creator=(p.get("user") or user or {}).get("login") or user.get("login"),
                    observer=user.get("login"), observation_id=obs.get("id"), photo_id=p.get("id"),
                    attribution=p.get("attribution"),
                    research_grade=obs.get("quality_grade") == "research",
                    country=("PL" if place_id == PLACE_POLAND_ID else None),
                    place_guess=obs.get("place_guess")))
                break
    return out[:n]


def pick_candidate(taxon: str, accept: set[str], client: httpx.Client,
                   rate_limit: float = 2.0, place_id: int | None = PLACE_POLAND_ID) -> SpeciesCandidate | None:
    """Best licensed photo for one species, research-grade first.

    place_id=None searches worldwide (fallback for species with no Poland
    photo); observation geography is captured on the candidate either way.
    Pure HTTP.
    """
    out = pick_candidates(taxon, accept, client, rate_limit, place_id, n=1)
    return out[0] if out else None


def collect_candidates(taxa: list[str], profile: str = "research",
                       rate_limit: float = 2.0, limit: int | None = None,
                       progress_every: int = 50,
                       place_id: int | None = PLACE_POLAND_ID) -> dict[str, Any]:
    """Metadata-only pass over a species list. place_id=None searches worldwide."""
    accept, _ = load_license_profile(profile)
    names = taxa[:limit] if limit else taxa
    found: list[dict] = []
    missing: list[str] = []
    with httpx.Client() as client:
        for i, name in enumerate(names, 1):
            cand = pick_candidate(name, accept, client, rate_limit, place_id=place_id)
            if cand:
                found.append(cand.__dict__)
            else:
                missing.append(name)
            if i % progress_every == 0:
                print(f"collect-one: {i}/{len(names)} found={len(found)} missing={len(missing)}", flush=True)
    return {"species_total": len(names), "found": len(found), "missing": len(missing),
            "candidates": found, "missing_taxa": missing}


def collect_n_candidates(taxa: list[str], profile: str = "research",
                         rate_limit: float = 2.0, limit: int | None = None,
                         progress_every: int = 50,
                         place_id: int | None = None,
                         per_species: int = 10,
                         skip_observation_ids: set[int] | None = None) -> dict[str, Any]:
    """Up to per_species distinct-observation candidates per taxon (worldwide default).

    Already-collected observation IDs are skipped so reruns only fetch new
    material (idempotent top-up). Returns manifest with per-taxon counts.
    """
    accept, _ = load_license_profile(profile)
    skip = skip_observation_ids or set()
    names = taxa[:limit] if limit else taxa
    found: list[dict] = []
    per_taxon: dict[str, int] = {}
    with httpx.Client() as client:
        for i, name in enumerate(names, 1):
            cands = pick_candidates(name, accept, client, rate_limit, place_id, n=per_species + 10)
            fresh = [c for c in cands if c.observation_id not in skip][:per_species]
            for c in fresh:
                found.append(c.__dict__)
            per_taxon[name] = len(fresh)
            if i % progress_every == 0:
                print(f"collect-n: {i}/{len(names)} images={len(found)}", flush=True)
    return {"species_total": len(names), "per_species": per_species,
            "images": len(found), "per_taxon": per_taxon, "candidates": found}


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
                        raw_url = m["media_url"]
                        clean_url = raw_url.split("?")[0]  # drop ?utm_source tracking junk
                        picked = {"taxon": name, "url": clean_url, "license": lic,
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
                     country: str | None = "PL", place_guess: str | None = None,
                     width: int | None = None, height: int | None = None) -> None:
    obs_row_id = None
    if observation_id:
        conn.execute(
            """INSERT INTO observations (source, source_observation_id, source_taxon, quality_grade, country_code, place_guess)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(source, source_observation_id) DO UPDATE SET quality_grade=excluded.quality_grade,
                 place_guess=COALESCE(excluded.place_guess, observations.place_guess)""",
            (source, str(observation_id), taxon, quality_grade, country, place_guess))
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
