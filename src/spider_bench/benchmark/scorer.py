"""Scorer (M1): pure tasks + predictions -> scores. Offline, deterministic.

Primary: top-1/top-5 accuracy (synonyms accepted). Slices: per-family,
per-country-of-photo. Error rows count as wrong, reported separately.
"""
from __future__ import annotations

from typing import Any


def _norm(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())


def is_correct(task: dict[str, Any], taxon: str) -> bool:
    valid = {_norm(task.get("correct_taxon", ""))}
    valid |= {_norm(s) for s in task.get("synonyms_accepted", []) if s}
    return _norm(taxon) in valid and bool(_norm(taxon))


def score(tasks: list[dict[str, Any]], predictions: list[dict[str, Any]],
          topk: tuple[int, ...] = (1, 5)) -> dict[str, Any]:
    """Score predictions against tasks. Pure function."""
    by_id = {t["task_id"]: t for t in tasks}
    n = err = 0
    hits = {k: 0 for k in topk}
    fam: dict[str, dict[str, int]] = {}
    for p in predictions:
        t = by_id.get(p.get("task_id", ""))
        if t is None:
            continue
        n += 1
        if p.get("error") or not p.get("predictions"):
            err += 1
            continue
        ranked = [r.get("taxon", "") for r in p["predictions"]]
        for k in topk:
            if any(is_correct(t, c) for c in ranked[:k]):
                hits[k] += 1
        f = (t.get("meta") or {}).get("family", "?")
        d = fam.setdefault(f, {"n": 0, "top1": 0})
        d["n"] += 1
        if ranked and is_correct(t, ranked[0]):
            d["top1"] += 1
    denom = max(n - err, 0)
    return {"tasks": len(tasks), "scored": n, "errors": err,
            **{f"top{k}": (hits[k] / n if n else 0.0) for k in topk},
            **{f"top{k}_clean": (hits[k] / denom if denom else 0.0) for k in topk},
            "per_family": {f: {"n": d["n"], "top1": d["top1"] / d["n"] if d["n"] else 0.0}
                           for f, d in sorted(fam.items())}}
