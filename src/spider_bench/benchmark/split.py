"""Observation-disjoint splitter: media rows -> gallery + query suites.

Rules: no observation (and no sha256) on both sides; stratify by family;
gallery keeps 1 canonical image per taxon (earliest accepted), the rest are
query candidates. Deterministic (sorted input, seeded shuffle for query cap).
"""
from __future__ import annotations

import random
from typing import Any


def split_gallery_query(rows: list[dict[str, Any]], *,
                        query_per_taxon: int = 4,
                        seed: int = 42) -> dict[str, Any]:
    """Split media rows into gallery (1/taxon) and query sets. Pure function."""
    by_taxon: dict[str, list[dict]] = {}
    for r in sorted(rows, key=lambda x: (str(x.get("taxon", "")), str(x.get("sha256", "")))):
        by_taxon.setdefault(str(r.get("taxon", "")), []).append(r)
    rng = random.Random(seed)
    gallery, query = [], []
    for taxon, items in by_taxon.items():
        # gallery: first full-size image (earliest photo id), else earliest thumb
        def _rank(r: dict) -> tuple:
            w = r.get("width") or 0
            return (0 if w >= 150 else 1, str(r.get("source_media_id", "")))

        ordered = sorted(items, key=_rank)
        gallery.append({**ordered[0], "split": "gallery"})
        rest = [o for o in ordered[1:] if o.get("sha256") != ordered[0].get("sha256")
                and o.get("observation_id") != ordered[0].get("observation_id")]
        rng.shuffle(rest)
        for o in rest[:query_per_taxon]:
            query.append({**o, "split": "query"})
    # leakage assertion: no observation or sha on both sides
    g_obs = {g.get("observation_id") for g in gallery} | {g.get("sha256") for g in gallery}
    for q in query:
        assert q.get("observation_id") not in g_obs and q.get("sha256") not in g_obs, "split leakage"
    return {"gallery": gallery, "query": query,
            "taxa": len(by_taxon), "gallery_n": len(gallery), "query_n": len(query)}
