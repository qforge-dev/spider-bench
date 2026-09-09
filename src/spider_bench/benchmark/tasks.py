"""Task builder (M0): release -> deterministic tasks.jsonl + manifest.

Closed-set species ID v1: one task per imaged taxon, candidates = full
checklist (or imaged subset). Manifest records the dataset version,
checksums, and the N=1 gallery limitation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _sha256_of_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tasks_hash(tasks: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for t in sorted(tasks, key=lambda r: r["task_id"]):
        h.update(json.dumps(t, sort_keys=True).encode())
    return h.hexdigest()


def build_tasks(taxa: list[dict[str, Any]], media: list[dict[str, Any]],
                *, suite: str = "species-id-closed-v1",
                imaged_only: bool = True) -> list[dict[str, Any]]:
    """Build closed-set species-ID tasks. Pure function, deterministic."""
    names = sorted(t["taxon"] for t in taxa)
    imaged_names = sorted({m["taxon"] for m in media if m.get("taxon")})
    pool = imaged_names if imaged_only else names
    by_media = {}
    for m in sorted(media, key=lambda r: r.get("sha256", "")):
        by_media.setdefault(m.get("taxon"), m)
    tasks = []
    for taxon in pool:
        m = by_media[taxon]
        tasks.append({
            "task_id": f"{suite}:{taxon.replace(' ', '_')}",
            "task_type": "species-id-closed",
            "image_sha256": m["sha256"],
            "image_s3_uri": m["s3_uri"],
            "image_public_url": m.get("public_url", ""),
            "prompt": ("Identify the spider species in this photograph. "
                       "Reply with exactly one scientific name from the candidate list."),
            "candidates": names,
            "correct_taxon": taxon,
            "synonyms_accepted": [],
            "meta": {"family": next((t.get("family", "") for t in taxa if t["taxon"] == taxon), ""),
                     "country": m.get("country", ""), "suite": suite},
        })
    return sorted(tasks, key=lambda r: r["task_id"])


def write_tasks(tasks: list[dict[str, Any]], out_dir: str | Path, *,
                dataset_version: str, dataset_checksums: dict[str, str]) -> Path:
    """Write tasks.jsonl + manifest.json deterministically. Returns tasks path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tp = out / "tasks.jsonl"
    with tp.open("w", encoding="utf-8") as fh:
        for t in tasks:
            fh.write(json.dumps(t, sort_keys=True) + "\n")
    manifest = {"suite": tasks[0]["meta"]["suite"] if tasks else "empty",
                "tasks": len(tasks), "tasks_hash": tasks_hash(tasks),
                "dataset_version": dataset_version,
                "dataset_checksums": dataset_checksums,
                "limitation": ("N=1 image per species: gallery-style tasks only; "
                               "no train/test split until collect-N lands.")}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return tp


def read_tasks(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
