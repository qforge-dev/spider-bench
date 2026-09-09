"""Reconcile checklist names to a pinned WSC snapshot. Pure functions.

Match tiers per checklist entry: exact (normalized name + authorship match on an
accepted name) > synonym (matches a known synonym -> resolves to accepted taxon)
> fuzzy (close string candidates needing review) > unresolved.

Never silently overwrite reviewed decisions: if ``reviewed`` maps the checklist
name to a decision, that decision is kept and reported with match_type
'kept_reviewed'.
"""
from __future__ import annotations

import difflib
from typing import Any, Literal

from spider_bench.taxonomy.normalize import normalize_authorship, normalize_name

MatchType = Literal[
    "exact", "synonym", "fuzzy", "unresolved", "conflict", "kept_reviewed"
]

WscRecord = dict[str, Any]
ReconcileResult = dict[str, Any]


def build_index(records: list[WscRecord]) -> dict[str, list[WscRecord]]:
    """Index WSC records by normalized accepted/synonym name.

    Each record may be ``{"scientific_name": ..., "authorship": ...,
    "accepted_name": ... (for synonyms), "wsc_id": ...}``. Both the record's own
    name and (for synonyms) the accepted name are indexed so lookups find them.
    """
    index: dict[str, list[WscRecord]] = {}
    for rec in records:
        keys = {normalize_name(str(rec.get("scientific_name", "")))}
        accepted = rec.get("accepted_name")
        if accepted:
            keys.add(normalize_name(str(accepted)))
        for key in keys:
            if key:
                index.setdefault(key, []).append(rec)
    return index


def _authorship_matches(checklist_auth: str | None, rec_auth: Any) -> bool:
    if not checklist_auth and not rec_auth:
        return True
    if not checklist_auth or not rec_auth:
        return False
    return normalize_authorship(checklist_auth) == normalize_authorship(str(rec_auth))


def reconcile_one(
    original_name: str,
    authorship: str | None = None,
    index: dict[str, list[WscRecord]] | None = None,
    reviewed: dict[str, dict[str, Any]] | None = None,
    fuzzy_cutoff: float = 0.85,
    fuzzy_limit: int = 5,
) -> ReconcileResult:
    """Reconcile a single checklist name. Pure; no I/O."""
    if reviewed and original_name in reviewed:
        decision = reviewed[original_name]
        return {
            "original_name": original_name,
            "normalized_name": normalize_name(original_name),
            "match_type": "kept_reviewed",
            "accepted_name": decision.get("accepted_name"),
            "candidates": [],
            "needs_review": False,
            "note": "kept prior reviewed decision; not overwritten",
        }
    index = index or {}
    norm = normalize_name(original_name)
    hits = index.get(norm, [])
    if len(hits) == 1:
        rec = hits[0]
        is_synonym = bool(rec.get("accepted_name")) and normalize_name(
            str(rec.get("accepted_name"))
        ) != normalize_name(str(rec.get("scientific_name", "")))
        if is_synonym:
            return {
                "original_name": original_name,
                "normalized_name": norm,
                "match_type": "synonym",
                "accepted_name": rec.get("accepted_name"),
                "candidates": [rec],
                "needs_review": False,
                "note": "synonym resolved to accepted name",
            }
        return {
            "original_name": original_name,
            "normalized_name": norm,
            "match_type": "exact",
            "accepted_name": rec.get("scientific_name"),
            "candidates": [rec],
            "needs_review": False,
            "note": "",
        }
    if len(hits) > 1:
        # Disambiguate by authorship when possible.
        auth_matches = [
            h for h in hits if _authorship_matches(authorship, h.get("authorship"))
        ]
        if len(auth_matches) == 1:
            rec = auth_matches[0]
            kind: MatchType = (
                "synonym"
                if rec.get("accepted_name")
                and normalize_name(str(rec.get("accepted_name")))
                != normalize_name(str(rec.get("scientific_name", "")))
                else "exact"
            )
            return {
                "original_name": original_name,
                "normalized_name": norm,
                "match_type": kind,
                "accepted_name": rec.get("accepted_name") or rec.get("scientific_name"),
                "candidates": [rec],
                "needs_review": False,
                "note": "disambiguated by authorship",
            }
        return {
            "original_name": original_name,
            "normalized_name": norm,
            "match_type": "conflict",
            "accepted_name": None,
            "candidates": hits,
            "needs_review": True,
            "note": "multiple WSC records share this name; authorship required",
        }
    # No exact hit: fuzzy candidates.
    candidates = difflib.get_close_matches(
        norm, list(index.keys()), n=fuzzy_limit, cutoff=fuzzy_cutoff
    )
    if candidates:
        flat: list[WscRecord] = []
        for c in candidates:
            flat.extend(index[c])
        return {
            "original_name": original_name,
            "normalized_name": norm,
            "match_type": "fuzzy",
            "accepted_name": None,
            "candidates": flat[:fuzzy_limit],
            "needs_review": True,
            "note": "fuzzy candidates; manual review required",
        }
    return {
        "original_name": original_name,
        "normalized_name": norm,
        "match_type": "unresolved",
        "accepted_name": None,
        "candidates": [],
        "needs_review": True,
        "note": "no WSC match",
    }


def reconcile_all(
    entries: list[dict[str, Any]],
    wsc_records: list[WscRecord],
    reviewed: dict[str, dict[str, Any]] | None = None,
    fuzzy_cutoff: float = 0.85,
) -> list[ReconcileResult]:
    """Reconcile many checklist entries. Pure; no I/O."""
    index = build_index(wsc_records)
    return [
        reconcile_one(
            str(e.get("name", e.get("original_name", ""))),
            e.get("authorship"),
            index=index,
            reviewed=reviewed,
            fuzzy_cutoff=fuzzy_cutoff,
        )
        for e in entries
    ]


def conflict_report(results: list[ReconcileResult]) -> dict[str, Any]:
    """Summarize reconciliation outcomes needing attention."""
    counts: dict[str, int] = {}
    needs_review: list[ReconcileResult] = []
    for r in results:
        mt = str(r.get("match_type", "unresolved"))
        counts[mt] = counts.get(mt, 0) + 1
        if r.get("needs_review"):
            needs_review.append(r)
    return {
        "total": len(results),
        "counts": counts,
        "needs_review": len(needs_review),
        "items": needs_review,
    }
