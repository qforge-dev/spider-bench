"""Build an immutable, provenance-checked suite from the full legacy query pool.

This verifies source records, not biological identifiability. Unreviewed Commons
search results never become labels. All exclusions are persisted for inspection.
"""
from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx

from spider_bench.benchmark.protocol import (
    IMAGE_POLICY,
    baselines,
    mixed_candidates,
    prepare_image,
    sample_tasks,
)
from spider_bench.benchmark.tasks import _prompts, read_tasks, tasks_hash
from spider_bench.media.collect_one import _large_photo_url
from spider_bench.media.select import load_license_profile, normalize_license


def verify_observation(task: dict, media: dict, observation: dict, accepted_licenses: set) -> tuple[dict | None, str | None]:
    if str(observation.get("id")) != str(media["source_observation_id"]):
        return None, "observation_id_mismatch"
    taxon = observation.get("taxon") or {}
    if taxon.get("name") != task["correct_taxon"]:
        return None, "source_label_mismatch"
    if taxon.get("rank") not in {"species", "subspecies"}:
        return None, "source_not_species_level"
    if observation.get("quality_grade") != "research":
        return None, "not_research_grade"
    if observation.get("captive"):
        return None, "captive_observation"
    photo = next((p for p in observation.get("photos", [])
                  if str(p.get("id")) == str(media["source_media_id"])), None)
    if not photo:
        return None, "photo_not_in_observation"
    if normalize_license(photo.get("license_code")) not in accepted_licenses:
        return None, "photo_license_not_allowed"
    return photo, None


def prepare_suite(source: str | Path, out: str | Path,
                  database: str | Path = "data/work/spider-bench.sqlite",
                  cache: str | Path = "data/work/benchmark-v5", seed: int = 42,
                  progress=print) -> dict:
    out, cache = Path(out).resolve(), Path(cache).resolve()
    if out.exists():
        raise ValueError("suite output already exists; choose a new version")
    tasks = read_tasks(source)
    cache.mkdir(parents=True, exist_ok=True)
    snapshots = cache / "observations"
    snapshots.mkdir(exist_ok=True)
    images = cache / "images"
    images.mkdir(exist_ok=True)
    with sqlite3.connect(f"file:{Path(database).resolve()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        media = {r["sha256"]: dict(r) for r in conn.execute(
            "SELECT m.sha256,m.source,m.source_media_id,m.creator,o.source_observation_id "
            "FROM media m LEFT JOIN observations o ON o.id=m.observation_id")}
    exclusions = []
    candidates = []
    for t in tasks:
        m = media.get(t["image_sha256"], {})
        if m.get("source") != "inaturalist" or not m.get("source_observation_id"):
            exclusions.append({"task_id": t["task_id"], "reason": "source_requires_manual_label_review"})
        else:
            candidates.append(t)
    ids = sorted({str(media[t["image_sha256"]]["source_observation_id"]) for t in candidates})
    missing = [i for i in ids if not (snapshots / f"{i}.json").exists()]
    with httpx.Client(timeout=60, follow_redirects=True,
                      headers={"User-Agent": "SpiderBench/0.1 source-verification"}) as client:
        for start in range(0, len(missing), 100):
            batch = missing[start:start + 100]
            # Fail closed on network errors instead of silently changing the population.
            for attempt in range(4):
                try:
                    response = client.get("https://api.inaturalist.org/v1/observations/" + ",".join(batch),
                                          params={"per_page": 200})
                    response.raise_for_status()
                    break
                except (httpx.TransportError, httpx.HTTPStatusError):
                    if attempt == 3:
                        raise
                    time.sleep(2 ** attempt)
            records = {str(r["id"]): r for r in response.json()["results"]}
            for oid in batch:
                record = records.get(oid, {})
                # Freeze only relevant public provenance; omit precise coordinates.
                reduced = {k: record.get(k) for k in (
                    "id", "taxon", "quality_grade", "photos", "captive", "observed_on", "place_guess")}
                reduced["observer_id"] = (record.get("user") or {}).get("id")
                reduced["fetched_at"] = datetime.now(UTC).isoformat()
                (snapshots / f"{oid}.json").write_text(json.dumps(reduced, sort_keys=True))
            progress(f"source records {min(start + 100, len(missing))}/{len(missing)}")
            time.sleep(1)
    accept, _ = load_license_profile("research")
    eligible = []
    for t in candidates:
        m = media[t["image_sha256"]]
        snapshot = snapshots / f"{m['source_observation_id']}.json"
        obs = json.loads(snapshot.read_text())
        photo, reason = verify_observation(t, m, obs, accept)
        if reason:
            exclusions.append({"task_id": t["task_id"], "reason": reason,
                               "source_taxon": (obs.get("taxon") or {}).get("name"),
                               "observation_id": m["source_observation_id"]})
            continue
        url = _large_photo_url(photo.get("url") or "")
        eligible.append((t, m, obs, photo, url, snapshot))
    progress(f"source-verified candidates {len(eligible)}/{len(tasks)}; preparing images")

    def download(item):
        t, m, obs, photo, url, snapshot = item
        raw_path = images / f"inat-{photo['id']}.source"
        if not raw_path.exists():
            with httpx.Client(timeout=60, follow_redirects=True) as client:
                for attempt in range(4):
                    try:
                        response = client.get(url)
                        response.raise_for_status()
                        raw_path.write_bytes(response.content)
                        break
                    except (httpx.TransportError, httpx.HTTPStatusError):
                        if attempt == 3:
                            raise
                        time.sleep(2 ** attempt)
        raw = raw_path.read_bytes()
        try:
            prepared, meta = prepare_image(raw)
        except (ValueError, OSError) as exc:
            return None, {"task_id": t["task_id"], "reason": str(exc)}
        target = images / f"{meta['sha256']}.jpg"
        if not target.exists():
            target.write_bytes(prepared)
        row = {"correct_taxon": t["correct_taxon"], "image_sha256": meta["sha256"],
               "image_local_path": str(target), "source_task_id": t["task_id"],
               "image_public_url": "", "synonyms_accepted": [],
               "meta": {"family": t["meta"]["family"], "image": meta,
                        "observation_id": str(obs["id"]), "photo_id": str(photo["id"]),
                        "observer_id": obs.get("observer_id"), "source": "inaturalist",
                        "source_url": f"https://www.inaturalist.org/observations/{obs['id']}",
                        "source_image_url": url, "source_image_sha256": hashlib.sha256(raw).hexdigest(),
                        "label_basis": "research_grade_exact_source_taxon",
                        "source_taxon_id": (obs.get("taxon") or {}).get("id"),
                        "license": normalize_license(photo.get("license_code")),
                        "attribution": photo.get("attribution"),
                        "source_snapshot": str(snapshot),
                        "source_snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest()}}
        return row, None

    rows = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        for i, (row, exclusion) in enumerate(executor.map(download, eligible), 1):
            if row:
                rows.append(row)
            else:
                exclusions.append(exclusion)
            if i % 100 == 0:
                progress(f"images prepared {i}/{len(eligible)}")
    # Check across the entire query pool, including cross-source encodings.
    kept, observed, seen_pixels = [], set(), set()
    rng = random.Random(seed)
    rng.shuffle(rows)
    for row in rows:
        meta = row["meta"]
        im = meta["image"]
        reason = None
        if meta["observation_id"] in observed:
            reason = "duplicate_observation"
        elif im["pixel_sha256"] in seen_pixels:
            reason = "duplicate_pixels"
        else:
            for other in kept:
                oi = other["meta"]["image"]
                aspect = im["width"] / im["height"]
                other_aspect = oi["width"] / oi["height"]
                if abs(aspect - other_aspect) < 0.02 and (
                    int(im["dhash"], 16) ^ int(oi["dhash"], 16)).bit_count() <= 3 and (
                    int(im["ahash"], 16) ^ int(oi["ahash"], 16)).bit_count() <= 3:
                    reason = "possible_near_duplicate"
                    break
        if reason:
            exclusions.append({"task_id": row["source_task_id"], "reason": reason})
            continue
        kept.append(row)
        observed.add(meta["observation_id"])
        seen_pixels.add(im["pixel_sha256"])
    taxa = {r["correct_taxon"]: r["meta"]["family"] for r in kept}
    for row in kept:
        names = mixed_candidates(row["correct_taxon"], taxa, row["image_sha256"], seed)
        row["candidates"] = names
        row["system_prompt"], row["user_prompt"] = _prompts(names)
        row["task_id"] = f"{out.name}:{row['image_sha256'][:24]}"
        row["task_type"] = "species-id-mixed-shortlist"
        row["meta"].update({"suite": out.name, "protocol_version": 5,
                            "candidate_policy": "correct_plus_9_family_plus_10_other",
                            "shortlist_seed": seed, "shortlist_n": len(names)})
    kept = sample_tasks(kept, None, seed)
    manifest = {"suite": out.name, "protocol_version": 5, "tasks": len(kept),
                "taxa": len(taxa), "tasks_hash": tasks_hash(kept), "seed": seed,
                "source_tasks": str(Path(source).resolve()), "source_tasks_hash": tasks_hash(tasks),
                "image_policy": IMAGE_POLICY, "candidate_policy": "correct_plus_9_family_plus_10_other",
                "shortlist_sizes": dict(Counter(len(t["candidates"]) for t in kept)),
                "label_policy": "research_grade_exact_source_taxon_and_photo_id",
                "exclusions": dict(Counter(e["reason"] for e in exclusions)),
                "baselines": baselines(kept), "built_at": datetime.now(UTC).isoformat(),
                "limitations": ["Source labels are community identifications, not an expert image audit.",
                                "Public photos may have appeared in model training.",
                                "Zero-shot query evaluation; no claim of a hidden training/test split.",
                                "Photos may be taken anywhere; taxa derive from the Polish checklist.",
                                "Shortlists supply taxonomic context; this is not open-world identification."]}
    out.mkdir(parents=True)
    (out / "tasks.jsonl").write_text("".join(json.dumps(t, sort_keys=True) + "\n" for t in kept))
    (out / "exclusions.jsonl").write_text("".join(json.dumps(t, sort_keys=True) + "\n" for t in exclusions))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def validate_suite(directory: str | Path, *, cache=None, download: bool = True) -> dict:
    from spider_bench.benchmark.suite_storage import DEFAULT_CACHE, task_asset

    asset_options = {"cache": Path(cache) if cache is not None else DEFAULT_CACHE,
                     "download": download}
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    tasks = read_tasks(directory / "tasks.jsonl")
    if manifest.get("protocol_version") != 5 or tasks_hash(tasks) != manifest.get("tasks_hash"):
        raise ValueError("suite protocol/hash mismatch")
    if manifest.get("tasks") != len(tasks) or not tasks:
        raise ValueError("suite task count mismatch")
    ids, hashes, pixels, observations = set(), set(), set(), set()
    taxa = {t["correct_taxon"]: t["meta"]["family"] for t in tasks}
    for t in tasks:
        m = t["meta"]
        for seen, value in ((ids, t["task_id"]), (hashes, t["image_sha256"]),
                            (pixels, m["image"]["pixel_sha256"]), (observations, m["observation_id"])):
            if value in seen:
                raise ValueError("duplicate task/image/observation")
            seen.add(value)
        if t["correct_taxon"] not in t["candidates"] or len(set(t["candidates"])) != len(t["candidates"]):
            raise ValueError("invalid candidate set")
        if t["candidates"] != mixed_candidates(t["correct_taxon"], taxa, t["image_sha256"], manifest["seed"]):
            raise ValueError("shortlist determinism mismatch")
        expected = _prompts(t["candidates"])
        if (t["system_prompt"], t["user_prompt"]) != expected:
            raise ValueError("prompt mismatch")
        data = task_asset(t, "image", **asset_options)
        if hashlib.sha256(data).hexdigest() != t["image_sha256"] or len(data) > IMAGE_POLICY["max_bytes"]:
            raise ValueError("prepared image checksum/size mismatch")
        import io

        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.format != "JPEG" or min(image.size) < 320 or max(image.size) > 1536 or image.getexif():
                raise ValueError("prepared image policy violation")
        snapshot = task_asset(t, "source", **asset_options)
        if hashlib.sha256(snapshot).hexdigest() != m["source_snapshot_sha256"]:
            raise ValueError("source snapshot checksum mismatch")
        obs = json.loads(snapshot)
        photo, reason = verify_observation(t, {"source_observation_id": m["observation_id"],
                                               "source_media_id": m["photo_id"]}, obs,
                                            load_license_profile("research")[0])
        if reason:
            raise ValueError(f"source provenance invalid: {reason}")
    return {"valid": True, "tasks": len(tasks), "taxa": len({t["correct_taxon"] for t in tasks}),
            "tasks_hash": tasks_hash(tasks), "baselines": baselines(tasks)}
