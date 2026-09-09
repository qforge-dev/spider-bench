"""Danger (medical-significance) assessments: evidence-backed, Poland-scoped.

Never infer danger from venom presence, size/appearance, common names,
anecdotes, unverified occurrences, or model output (plan §2.2).
Missing evidence must stay ``uncertain``/unreviewed — never ``none_known``.
"""

from spider_bench.danger.assessments import (
    DISPLAY_ORDINAL,
    SAFETY_NOTICE,
    MedicalSignificance,
    DangerAssessment,
    display_ordinal,
)
from spider_bench.danger.evidence import (
    EvidenceClaim,
    EvidenceSource,
    import_evidence_file,
    import_evidence_records,
    validate_claim,
    validate_source,
)
from spider_bench.danger.review import (
    STATUS_TRANSITIONS,
    ReviewDecision,
    ReviewEvent,
    adjudicate,
    append_review_event,
    apply_transition,
    requires_second_reviewer,
)

__all__ = [
    "DISPLAY_ORDINAL",
    "SAFETY_NOTICE",
    "STATUS_TRANSITIONS",
    "DangerAssessment",
    "EvidenceClaim",
    "EvidenceSource",
    "MedicalSignificance",
    "ReviewDecision",
    "ReviewEvent",
    "adjudicate",
    "append_review_event",
    "apply_transition",
    "display_ordinal",
    "import_evidence_file",
    "import_evidence_records",
    "requires_second_reviewer",
    "validate_claim",
    "validate_source",
]
