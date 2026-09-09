"""Media selection for a release (plan §10C).

Filters discovery candidates by license profile, then applies deterministic
per-taxon / per-observation / per-observer caps so one source cannot dominate.
Pure function — deterministic ordering, no I/O.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from spider_bench.licensing import classify_license


def select_media(
    candidates: list[dict[str, Any]],
    profile: str,
    profiles: dict[str, Any],
    max_per_taxon: int = 50,
    max_per_observation: int = 4,
    max_per_observer: int = 10,
    require_review_decision: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split candidates into (selected, rejected).

    Rejection reasons recorded in each rejected row under ``_reject_reason``:
    license_rejected | license_review_required | cap_taxon | cap_observation |
    cap_observer | missing_key.

    Deterministic: input is sorted by (taxon, sha256) before caps apply.
    """
    ordered = sorted(
        candidates, key=lambda c: (str(c.get("taxon", "")), str(c.get("sha256", "")))
    )
    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    per_taxon: Counter[str] = Counter()
    per_obs: Counter[str] = Counter()
    per_observer: Counter[str] = Counter()

    for cand in ordered:
        taxon = str(cand.get("taxon", "") or "")
        sha = str(cand.get("sha256", "") or "")
        if not taxon or not sha:
            rejected.append({**cand, "_reject_reason": "missing_key"})
            continue
        verdict = classify_license(str(cand.get("license", "") or ""), profile, profiles)
        if verdict == "rejected":
            rejected.append({**cand, "_reject_reason": "license_rejected"})
            continue
        if verdict == "review_required" and require_review_decision:
            if not cand.get("license_review_ok"):
                rejected.append({**cand, "_reject_reason": "license_review_required"})
                continue
        obs = str(cand.get("observation_id", "") or cand.get("source_record", "") or sha)
        observer = str(cand.get("observer", "") or "unknown")
        if per_taxon[taxon] >= max_per_taxon:
            rejected.append({**cand, "_reject_reason": "cap_taxon"})
            continue
        if per_obs[obs] >= max_per_observation:
            rejected.append({**cand, "_reject_reason": "cap_observation"})
            continue
        if per_observer[observer] >= max_per_observer:
            rejected.append({**cand, "_reject_reason": "cap_observer"})
            continue
        per_taxon[taxon] += 1
        per_obs[obs] += 1
        per_observer[observer] += 1
        selected.append(cand)

    return selected, rejected
