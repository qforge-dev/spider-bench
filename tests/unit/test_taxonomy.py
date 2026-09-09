"""Unit tests: taxonomy normalize + reconcile. No network, no boto3."""
from __future__ import annotations

from spider_bench.taxonomy.normalize import (
    canonical_rank,
    normalize_authorship,
    normalize_name,
)
from spider_bench.taxonomy.reconcile import (
    build_index,
    conflict_report,
    reconcile_all,
    reconcile_one,
)


def test_normalize_name_case_whitespace() -> None:
    assert normalize_name("  Pardosa   lugubris ") == "pardosa lugubris"
    assert normalize_name("Araneus DIADEMATUS") == "araneus diadematus"


def test_normalize_authorship_tidy() -> None:
    assert normalize_authorship(" ( Clerck , 1757 ) ") == "(Clerck, 1757)"
    assert normalize_authorship("") is None
    assert normalize_authorship(None) is None


def test_canonical_rank_aliases() -> None:
    assert canonical_rank("sp.") == "species"
    assert canonical_rank("Subsp.") == "subspecies"
    assert canonical_rank("GENUS") == "genus"
    assert canonical_rank(None) is None
    assert canonical_rank("tribus") == "tribus"  # passthrough


def _wsc() -> list[dict]:
    return [
        {"scientific_name": "Pardosa lugubris", "authorship": "(Walckenaer, 1802)",
         "wsc_id": "w1"},
        {"scientific_name": "Araneus diadematus", "authorship": "Clerck, 1757",
         "wsc_id": "w2", "synonyms": ["Aranea diadema"]},
        {"scientific_name": "Lycosa rara", "authorship": "A", "wsc_id": "w3"},
        {"scientific_name": "Lycosa rara", "authorship": "B", "wsc_id": "w4"},
    ]


def _index() -> dict:
    recs = [
        {"scientific_name": r["scientific_name"], "authorship": r.get("authorship"),
         "wsc_id": r.get("wsc_id")}
        for r in _wsc() if r["scientific_name"] != "Lycosa rara"
    ]
    recs.append({"scientific_name": "Aranea diadema",
                 "accepted_name": "Araneus diadematus", "wsc_id": "w2s"})
    return build_index(recs)


def test_reconcile_exact() -> None:
    r = reconcile_one("Pardosa lugubris", index=_index())
    assert r["match_type"] == "exact"
    assert r["accepted_name"] == "Pardosa lugubris"
    assert not r["needs_review"]


def test_reconcile_synonym() -> None:
    r = reconcile_one("Aranea diadema", index=_index())
    assert r["match_type"] == "synonym"
    assert r["accepted_name"] == "Araneus diadematus"


def test_reconcile_fuzzy_candidates() -> None:
    r = reconcile_one("Pardosa lugubriss", index=_index())
    assert r["match_type"] == "fuzzy"
    assert r["needs_review"]
    assert r["candidates"]


def test_reconcile_unresolved() -> None:
    r = reconcile_one("Xyzzy totallyunknown", index=_index())
    assert r["match_type"] == "unresolved"
    assert r["needs_review"]


def test_reconcile_conflict_without_authorship() -> None:
    recs = [r for r in _wsc() if r["scientific_name"] == "Lycosa rara"]
    idx = build_index(recs)
    r = reconcile_one("Lycosa rara", index=idx)
    assert r["match_type"] == "conflict"
    assert r["needs_review"]
    # authorship disambiguates
    r2 = reconcile_one("Lycosa rara", authorship="B", index=idx)
    assert r2["accepted_name"] is not None
    assert not r2["needs_review"]


def test_never_overwrite_reviewed() -> None:
    reviewed = {"Pardosa lugubris": {"accepted_name": "Pardosa cf. lugubris"}}
    r = reconcile_one("Pardosa lugubris", index=_index(), reviewed=reviewed)
    assert r["match_type"] == "kept_reviewed"
    assert r["accepted_name"] == "Pardosa cf. lugubris"
    assert not r["needs_review"]


def test_reconcile_all_and_conflict_report() -> None:
    wsc = [
        {"scientific_name": "Pardosa lugubris", "wsc_id": "w1"},
        {"scientific_name": "Aranea diadema",
         "accepted_name": "Araneus diadematus", "wsc_id": "w2s"},
    ]
    entries = [{"name": "Pardosa lugubris"}, {"name": "Aranea diadema"},
               {"name": "Unknown xyzabc"}]
    results = reconcile_all(entries, wsc)
    assert [r["match_type"] for r in results] == ["exact", "synonym", "unresolved"]
    rep = conflict_report(results)
    assert rep["total"] == 3
    assert rep["needs_review"] == 1
    assert rep["counts"]["exact"] == 1
