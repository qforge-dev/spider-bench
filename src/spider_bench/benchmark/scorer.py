"""Offline scoring with explicit denominators, provenance checks and failure counts."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from spider_bench.benchmark.protocol import baselines, response_status
from spider_bench.benchmark.tasks import tasks_hash


def _norm(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())


def is_correct(task: dict[str, Any], taxon: str) -> bool:
    valid = {_norm(task.get("correct_taxon", ""))}
    valid |= {_norm(s) for s in task.get("synonyms_accepted", []) if s}
    return bool(_norm(taxon)) and _norm(taxon) in valid


def score(tasks: list[dict], predictions: list[dict], topk: tuple[int, ...] = (1,)) -> dict:
    by_id = {t["task_id"]: t for t in tasks}
    if len(by_id) != len(tasks):
        raise ValueError("duplicate task IDs")
    thash = tasks_hash(tasks)
    by_pred = {}
    for p in predictions:
        tid = p.get("task_id")
        if tid not in by_id or tid in by_pred:
            raise ValueError("unknown or duplicate prediction task ID")
        if p.get("tasks_hash") and p["tasks_hash"] != thash:
            raise ValueError("prediction task hash does not match the scoring snapshot")
        if p.get("image_sha256") and p["image_sha256"] != by_id[tid]["image_sha256"]:
            raise ValueError("prediction image hash mismatch")
        by_pred[tid] = p
    hits = Counter()
    statuses = Counter()
    families = defaultdict(lambda: [0, 0])
    species = defaultdict(lambda: [0, 0])
    for t in tasks:
        p = by_pred.get(t["task_id"])
        ranked = (p or {}).get("predictions") or []
        status = "missing" if p is None else response_status(
            ranked, {**(p.get("provider") or {}), "finish_reason": p.get("finish_reason")}, p.get("error"))
        statuses[status] += 1
        good = status == "answered"
        if good and any(k > len(ranked) for k in topk):
            raise ValueError("top-k requested but the model did not return k ranked answers")
        correct = good and is_correct(t, ranked[0].get("taxon", ""))
        for k in topk:
            hits[k] += good and any(is_correct(t, r.get("taxon", "")) for r in ranked[:k])
        for bucket, key in ((families, t.get("meta", {}).get("family", "unknown")),
                            (species, t["correct_taxon"])):
            bucket[key][0] += 1
            bucket[key][1] += int(correct)
    n = len(tasks)
    missing_stop = sum(not p.get("error") and not p.get("finish_reason") for p in predictions)
    return {"tasks": n, "scored": len(predictions), "tasks_hash": thash,
            "complete": len(predictions) == n, "missing": statuses["missing"],
            "errors": sum(v for k, v in statuses.items() if k not in {"answered", "missing"}),
            "transport_errors": statuses["transport_error"], "status_counts": dict(statuses),
            "missing_stop_reasons": missing_stop,
            "execution_valid": bool(n) and not (statuses["missing"] or statuses["transport_error"]
                                                  or statuses["truncated"] or missing_stop),
            **{f"top{k}": hits[k] / n if n else 0.0 for k in topk},
            "answer_rate": statuses["answered"] / n if n else 0.0,
            "top1_answered_only": hits[1] / statuses["answered"] if statuses["answered"] else 0.0,
            "macro_species_top1": sum(h / count for count, h in species.values()) / len(species) if species else 0.0,
            "per_family": {f: {"n": count, "correct": h, "top1": h / count}
                           for f, (count, h) in sorted(families.items())},
            "baselines": baselines(tasks)}
