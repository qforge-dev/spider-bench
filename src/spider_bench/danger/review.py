"""Review events, adjudication, status transitions (plan §8, §10F).

Reviewed taxonomy / danger records are never overwritten silently: changes go
through a new version plus an appended review event.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from spider_bench.danger.assessments import DangerAssessment

ReviewDecision = Literal["approve", "reject", "request_changes", "comment"]

# Allowed status transitions for danger assessments.
STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"in_review"}),
    "in_review": frozenset({"approved", "rejected", "draft"}),
    "approved": frozenset({"superseded"}),
    "rejected": frozenset({"draft"}),
    "superseded": frozenset(),
}


class ReviewEvent(BaseModel):
    entity_type: str = Field(min_length=1, description="e.g. 'danger_assessment'")
    entity_id: str = Field(min_length=1, description="e.g. taxon name")
    reviewer: str = Field(min_length=1)
    timestamp: str = Field(min_length=1, description="ISO datetime")
    decision: str = Field(description="approve|reject|request_changes|comment")
    notes: str = Field(default="")
    previous_status: str = Field(default="")
    new_status: str = Field(default="")

    def model_post_init(self, _ctx: Any) -> None:
        if self.decision not in ("approve", "reject", "request_changes", "comment"):
            raise ValueError(f"unknown review decision: {self.decision!r}")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def apply_transition(current: str, target: str) -> str:
    """Return target if allowed from current, else raise."""
    allowed = STATUS_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise ValueError(
            f"illegal status transition {current!r} -> {target!r} "
            f"(allowed: {sorted(allowed) or ['none']})"
        )
    return target


def append_review_event(
    events: list[ReviewEvent],
    entity_type: str,
    entity_id: str,
    reviewer: str,
    decision: ReviewDecision,
    notes: str = "",
    previous_status: str = "",
    new_status: str = "",
    timestamp: str | None = None,
) -> ReviewEvent:
    """Append (never mutate/reorder) a review event to the history list."""
    if decision not in ("approve", "reject", "request_changes", "comment"):
        raise ValueError(f"unknown review decision: {decision!r}")
    if previous_status and new_status and previous_status != new_status:
        apply_transition(previous_status, new_status)
    event = ReviewEvent(
        entity_type=entity_type,
        entity_id=entity_id,
        reviewer=reviewer,
        timestamp=timestamp or utcnow_iso(),
        decision=decision,
        notes=notes,
        previous_status=previous_status,
        new_status=new_status,
    )
    events.append(event)
    return event


def requires_second_reviewer(assessment: DangerAssessment, disputing_events: int = 0) -> bool:
    """Two reviewers required for medically_significant or disputed assessments."""
    return (
        assessment.category.value == "medically_significant"
        or assessment.category.value == "uncertain"
        and bool(assessment.exposure_context == "disputed")
        or disputing_events > 0
    )


def adjudicate(
    assessment: DangerAssessment,
    events: list[ReviewEvent],
    required_approvals: int | None = None,
) -> str:
    """Compute adjudicated status from review history (pure function).

    - any 'reject' -> 'rejected'
    - approvals >= required -> 'approved' (required defaults to 2 for
      medically_significant/disputed, else 1)
    - any review activity otherwise -> 'in_review'
    - no events -> 'draft'
    """
    relevant = [e for e in events if e.entity_id == assessment.taxon]
    if any(e.decision == "reject" for e in relevant):
        return "rejected"
    approvals = sum(1 for e in relevant if e.decision == "approve")
    if required_approvals is None:
        required_approvals = (
            2 if requires_second_reviewer(assessment, disputing_events=0) else 1
        )
    if approvals >= required_approvals:
        return "approved"
    if relevant:
        return "in_review"
    return "draft"


REVIEW_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS review_events (
  id INTEGER PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  reviewer TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  decision TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  previous_status TEXT NOT NULL DEFAULT '',
  new_status TEXT NOT NULL DEFAULT ''
);
"""


def record_review_event_sqlite(conn: sqlite3.Connection, event: ReviewEvent) -> int:
    """Persist one review event; returns row id. Creates table if missing."""
    conn.execute(REVIEW_EVENTS_DDL)
    cur = conn.execute(
        "INSERT INTO review_events "
        "(entity_type, entity_id, reviewer, timestamp, decision, notes, "
        " previous_status, new_status) VALUES (?,?,?,?,?,?,?,?)",
        (
            event.entity_type,
            event.entity_id,
            event.reviewer,
            event.timestamp,
            event.decision,
            event.notes,
            event.previous_status,
            event.new_status,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)
