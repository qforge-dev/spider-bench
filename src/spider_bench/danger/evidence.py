"""Bibliographic evidence import + claim linkage (plan §4.6, §10F).

Each danger assessment must cite evidence sources with enough metadata to
locate the claim: DOI, PMID, ISBN, or stable URL when available. A citation
alone is not an assessment — every claim records taxon + geography + the
supported claim text.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

SOURCE_TYPES = {
    "journal_article",
    "case_report",
    "review",
    "poison_center_bulletin",
    "public_health_report",
    "clinical_reference",
    "toxicology_reference",
    "expert_statement",
    "book",
    "other",
}

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
PMID_RE = re.compile(r"^\d+$")


def normalize_doi(raw: str | None) -> str | None:
    """Strip URL prefixes / whitespace; return bare ``10.xxxx/...`` form."""
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if s.lower().startswith(prefix):
            s = s[len(prefix):]
            break
    return s


def normalize_isbn(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = re.sub(r"[-\s]", "", raw.strip())
    return s or None


class EvidenceSource(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    authors: str = Field(default="", description="Free-text authorship / authority")
    publication_date: str = Field(default="")
    source_type: str = Field(default="other")
    doi: str | None = None
    pmid: str | None = None
    isbn: str | None = None
    url: str | None = None
    access_date: str = Field(default="")

    @field_validator("source_type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in SOURCE_TYPES:
            raise ValueError(f"unknown source_type: {v!r} (expected one of {sorted(SOURCE_TYPES)})")
        return v

    @field_validator("doi", mode="before")
    @classmethod
    def _norm_doi(cls, v: Any) -> Any:
        return normalize_doi(v) if isinstance(v, str) else v

    @field_validator("isbn", mode="before")
    @classmethod
    def _norm_isbn(cls, v: Any) -> Any:
        return normalize_isbn(v) if isinstance(v, str) else v


class EvidenceClaim(BaseModel):
    id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    taxon: str = Field(min_length=1, description="Accepted scientific name the claim is about")
    geography: str = Field(min_length=1, description="Geographic applicability, e.g. 'Poland'")
    claim: str = Field(min_length=1, description="Supported claim text (not the citation itself)")
    locator: str = Field(default="", description="Page/section/table locator inside the source")
    curator_notes: str = Field(default="")


def validate_source(raw: dict[str, Any]) -> EvidenceSource:
    """Validate one bibliographic record.

    Requires title + id + at least one locator (doi/pmid/isbn/url).
    Raises ValueError with a clear message on failure.
    """
    src = EvidenceSource.model_validate(raw)
    if not any([src.doi, src.pmid, src.isbn, src.url]):
        raise ValueError(
            f"evidence source {src.id!r}: need at least one of doi/pmid/isbn/url "
            "to locate the claim (plan §4.6)"
        )
    if src.doi and not DOI_RE.match(src.doi):
        raise ValueError(f"evidence source {src.id!r}: malformed DOI: {src.doi!r}")
    if src.pmid and not PMID_RE.match(src.pmid):
        raise ValueError(f"evidence source {src.id!r}: malformed PMID: {src.pmid!r}")
    if src.isbn and not re.fullmatch(r"(\d{10}|\d{13}|\w+)", src.isbn):
        raise ValueError(f"evidence source {src.id!r}: malformed ISBN: {src.isbn!r}")
    if src.url and not re.match(r"^https?://\S+", src.url):
        raise ValueError(f"evidence source {src.id!r}: url must be http(s): {src.url!r}")
    return src


def validate_claim(raw: dict[str, Any], known_source_ids: set[str] | None = None) -> EvidenceClaim:
    """Validate one claim linkage; optionally check the evidence_id exists."""
    claim = EvidenceClaim.model_validate(raw)
    if known_source_ids is not None and claim.evidence_id not in known_source_ids:
        raise ValueError(
            f"claim {claim.id!r}: unknown evidence_id {claim.evidence_id!r} "
            "(a citation reference must resolve to an imported source)"
        )
    return claim


def import_evidence_records(
    sources: list[dict[str, Any]],
    claims: list[dict[str, Any]],
) -> tuple[list[EvidenceSource], list[EvidenceClaim]]:
    """Validate source + claim batches; enforce id uniqueness and linkage."""
    seen: set[str] = set()
    out_sources: list[EvidenceSource] = []
    for raw in sources:
        src = validate_source(raw)
        if src.id in seen:
            raise ValueError(f"duplicate evidence source id: {src.id!r}")
        seen.add(src.id)
        out_sources.append(src)
    ids = {s.id for s in out_sources}
    seen_claims: set[str] = set()
    out_claims: list[EvidenceClaim] = []
    for raw in claims:
        claim = validate_claim(raw, ids)
        if claim.id in seen_claims:
            raise ValueError(f"duplicate evidence claim id: {claim.id!r}")
        seen_claims.add(claim.id)
        out_claims.append(claim)
    out_sources.sort(key=lambda s: s.id)
    out_claims.sort(key=lambda c: c.id)
    return out_sources, out_claims


def import_evidence_file(path: str | Path) -> tuple[list[EvidenceSource], list[EvidenceClaim]]:
    """Load a JSON/YAML/CSV evidence bundle.

    JSON/YAML shape: ``{"sources": [...], "claims": [...]}``.
    CSV shape: path to a sources-only CSV with matching headers (no claims).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"evidence input not found: {p}")
    if p.suffix.lower() == ".csv":
        with p.open(newline="", encoding="utf-8") as fh:
            sources = [dict(r) for r in csv.DictReader(fh)]
        return import_evidence_records(sources, [])
    text = p.read_text(encoding="utf-8")
    data: Any = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"{p}: expected mapping with 'sources'/'claims' keys")
    return import_evidence_records(
        list(data.get("sources") or []),
        list(data.get("claims") or []),
    )
