"""Unit tests: danger evidence / assessments / review."""
import pytest

from spider_bench.danger.assessments import (
    display_ordinal,
    import_assessments,
    validate_assessment,
)
from spider_bench.danger.evidence import import_evidence_records
from spider_bench.danger.review import (
    adjudicate,
    append_review_event,
    apply_transition,
    requires_second_reviewer,
)
from spider_bench.danger.assessments import DangerAssessment


def _sources_claims():
    sources = [
        {"id": "s1", "title": "Bite case series", "source_type": "case_report",
         "doi": "10.1234/spider.1", "authors": "A et al."},
    ]
    claims = [
        {"id": "c1", "evidence_id": "s1", "taxon": "Cheiracanthium punctorium",
         "geography": "Poland", "claim": "Bites cause local necrosis in 2 cases."},
    ]
    return sources, claims


def test_evidence_import_ok():
    s, c = _sources_claims()
    sources, claims = import_evidence_records(s, c)
    assert sources[0].doi == "10.1234/spider.1"
    assert claims[0].evidence_id == "s1"


def test_evidence_requires_locator():
    with pytest.raises(ValueError, match="doi/pmid/isbn/url"):
        import_evidence_records([{"id": "s9", "title": "No locator"}], [])


def test_evidence_doi_normalized():
    sources, _ = import_evidence_records(
        [{"id": "s1", "title": "T", "doi": "https://doi.org/10.1234/abc"}], [])
    assert sources[0].doi == "10.1234/abc"


def test_claim_unknown_source_rejected():
    s, _ = _sources_claims()
    with pytest.raises(ValueError, match="unknown evidence_id"):
        import_evidence_records(s, [{"id": "c9", "evidence_id": "nope",
                                    "taxon": "X y", "geography": "Poland", "claim": "z"}])


def _assessment(**kw):
    base = {
        "taxon": "Cheiracanthium punctorium",
        "geographic_scope": "Poland",
        "category": "minor_local_effects",
        "rationale": "Two published case reports describe transient local effects.",
        "evidence_ids": ["s1"],
        "reviewer": "reviewer@example.org",
        "review_date": "2026-01-15",
        "status": "in_review",
    }
    base.update(kw)
    return base


def test_assessment_ok_with_evidence_check():
    a = validate_assessment(_assessment(), known_evidence_ids={"s1"})
    assert a.category.value == "minor_local_effects"


def test_missing_category_never_defaults_to_none_known():
    raw = _assessment()
    del raw["category"]
    with pytest.raises(ValueError, match="never default"):
        validate_assessment(raw)


def test_missing_geography_rejected():
    raw = _assessment(geographic_scope="")
    with pytest.raises(ValueError):
        validate_assessment(raw)


def test_unsupported_evidence_ref_rejected():
    with pytest.raises(ValueError, match="unsupported evidence"):
        validate_assessment(_assessment(), known_evidence_ids={"other"})


def test_display_ordinal_documented_order():
    assert display_ordinal("none_known") < display_ordinal("minor_local_effects")
    assert display_ordinal("medically_significant") < display_ordinal("uncertain")
    with pytest.raises(ValueError):
        display_ordinal("deadly")


def test_status_transitions():
    assert apply_transition("draft", "in_review") == "in_review"
    with pytest.raises(ValueError, match="illegal status transition"):
        apply_transition("draft", "approved")


def test_review_adjudication_and_second_reviewer():
    sig = DangerAssessment.model_validate(_assessment(category="medically_significant"))
    assert requires_second_reviewer(sig)
    events = []
    append_review_event(events, "danger_assessment", sig.taxon, "r1", "approve")
    assert adjudicate(sig, events) == "in_review"  # needs 2 approvals
    append_review_event(events, "danger_assessment", sig.taxon, "r2", "approve")
    assert adjudicate(sig, events) == "approved"
    append_review_event(events, "danger_assessment", sig.taxon, "r3", "reject")
    assert adjudicate(sig, events) == "rejected"


def test_import_assessments_sorted():
    recs = [_assessment(taxon="Zebra spider"), _assessment(taxon="Atypus muralis")]
    out = import_assessments(recs)
    assert [a.taxon for a in out] == ["Atypus muralis", "Zebra spider"]
