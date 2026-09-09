"""Medical-significance assessments (plan §2.2, §10F).

Primary field ``medical_significance`` with values:
none_known / minor_local_effects / medically_significant / uncertain.

Rules enforced here:
- every assessment requires geography + rationale + evidence + reviewer + date;
- missing data is NEVER defaulted to ``none_known`` (raises instead);
- numeric ordinals are display-only sorting helpers, not measurements.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

SAFETY_NOTICE = (
    "Safety notice: this dataset records published evidence about spider bites; "
    "it is not medical advice. If symptoms are severe, spreading, or uncertain, "
    "contact appropriate medical or emergency services."
)


class MedicalSignificance(str, Enum):
    NONE_KNOWN = "none_known"
    MINOR_LOCAL_EFFECTS = "minor_local_effects"
    MEDICALLY_SIGNIFICANT = "medically_significant"
    UNCERTAIN = "uncertain"


# Display-only sort order (documented; NOT a danger score or measurement).
# uncertain sorts last so unresolved taxa are visually distinct.
DISPLAY_ORDINAL: dict[str, int] = {
    MedicalSignificance.NONE_KNOWN.value: 0,
    MedicalSignificance.MINOR_LOCAL_EFFECTS.value: 1,
    MedicalSignificance.MEDICALLY_SIGNIFICANT.value: 2,
    MedicalSignificance.UNCERTAIN.value: 3,
}

ASSESSMENT_STATUSES = {"draft", "in_review", "approved", "rejected", "superseded"}


def display_ordinal(category: str | MedicalSignificance) -> int:
    """Return the display sort ordinal for a reviewed category.

    Raises KeyError/ValueError for unknown categories — never guess.
    """
    key = category.value if isinstance(category, MedicalSignificance) else category
    if key not in DISPLAY_ORDINAL:
        raise ValueError(
            f"unknown medical_significance category: {key!r} "
            f"(expected one of {sorted(DISPLAY_ORDINAL)})"
        )
    return DISPLAY_ORDINAL[key]


class DangerAssessment(BaseModel):
    taxon: str = Field(min_length=1)
    geographic_scope: str = Field(min_length=1, description="e.g. 'Poland'")
    category: MedicalSignificance
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    review_date: str = Field(min_length=1, description="ISO date YYYY-MM-DD")
    status: str = Field(default="draft")
    confidence: str = Field(default="")
    exposure_context: str = Field(default="")

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: str) -> str:
        if v not in ASSESSMENT_STATUSES:
            raise ValueError(f"unknown assessment status: {v!r}")
        return v

    @field_validator("review_date")
    @classmethod
    def _iso_date(cls, v: str) -> str:
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"review_date must be ISO YYYY-MM-DD, got {v!r}")
        return v

    @field_validator("evidence_ids")
    @classmethod
    def _nonempty_refs(cls, v: list[str]) -> list[str]:
        if not v or any(not e or not e.strip() for e in v):
            raise ValueError("evidence_ids must list at least one non-empty evidence reference")
        return v


_MISSING = object()


def validate_assessment(
    raw: dict[str, Any],
    known_evidence_ids: set[str] | None = None,
    known_taxa: set[str] | None = None,
) -> DangerAssessment:
    """Validate one assessment dict. Missing category is an error, never none_known."""
    if raw.get("category", _MISSING) is _MISSING or raw.get("category") in (None, ""):
        raise ValueError(
            f"assessment for taxon {raw.get('taxon', '?')!r}: missing medical_significance "
            "category — mark 'uncertain' explicitly with rationale, never default to "
            "'none_known' (plan §2.2, §10F)"
        )
    try:
        assessment = DangerAssessment.model_validate(raw)
    except Exception as e:
        raise ValueError(f"invalid danger assessment for {raw.get('taxon', '?')!r}: {e}") from e
    if known_evidence_ids is not None:
        missing = [e for e in assessment.evidence_ids if e not in known_evidence_ids]
        if missing:
            raise ValueError(
                f"assessment for {assessment.taxon!r}: unsupported evidence reference(s): "
                f"{missing} (gate §11)"
            )
    if known_taxa is not None and assessment.taxon not in known_taxa:
        raise ValueError(
            f"assessment for {assessment.taxon!r}: taxon absent from pinned taxonomy snapshot "
            "(gate §11)"
        )
    return assessment


def import_assessments(
    records: list[dict[str, Any]],
    known_evidence_ids: set[str] | None = None,
    known_taxa: set[str] | None = None,
) -> list[DangerAssessment]:
    """Validate a batch; deterministic order by taxon."""
    out = [validate_assessment(r, known_evidence_ids, known_taxa) for r in records]
    out.sort(key=lambda a: a.taxon)
    return out
