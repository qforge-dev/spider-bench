"""Pydantic v2 models for the spider-bench working index.

Mirrors the SQLite schema (migrations/001_init.sql). No network, no boto3.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MembershipStatus = Literal[
    "present", "doubtful", "disputed", "historical", "introduced", "uncertain", "absent"
]
ReviewState = Literal["unreviewed", "pending", "approved", "rejected"]
NameType = Literal["accepted", "synonym", "original", "misapplied"]
MedicalSignificance = Literal[
    "none_known", "minor_local_effects", "medically_significant", "uncertain"
]
AssessmentStatus = Literal["draft", "pending_review", "approved", "rejected"]
ValidationStatus = Literal["pending", "accepted", "rejected", "needs_review"]
ReleaseStatus = Literal["draft", "candidate", "published", "deprecated"]
TaxonomicStatus = Literal["accepted", "synonym", "doubtful", "disputed"]
TaxonRank = Literal["order", "family", "genus", "species", "subspecies", "variety"]


class Taxon(BaseModel):
    id: int | None = None
    scientific_name: str
    authorship: str | None = None
    rank: TaxonRank
    family: str | None = None
    genus: str | None = None
    wsc_id: str | None = None
    snapshot_id: str
    taxonomic_status: TaxonomicStatus = "accepted"


class TaxonName(BaseModel):
    id: int | None = None
    name: str
    normalized_name: str
    authorship: str | None = None
    normalized_authorship: str | None = None
    rank: str | None = None
    source: str | None = None
    source_id: str | None = None
    accepted_taxon_id: int | None = None
    name_type: NameType = "original"


class CountryTaxon(BaseModel):
    id: int | None = None
    taxon_id: int | None = None
    original_name: str
    normalized_name: str
    country_code: str = "PL"
    membership_status: MembershipStatus = "present"
    supporting_source_id: int | None = None
    review_state: ReviewState = "unreviewed"
    notes: str | None = None


class Observation(BaseModel):
    id: int | None = None
    source: str
    source_observation_id: str
    source_taxon: str | None = None
    taxon_id: int | None = None
    observer_key: str | None = None
    observed_at: str | None = None
    quality_grade: str | None = None
    country_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    captive_wild: str | None = None
    source_url: str | None = None
    snapshot_id: str | None = None
    raw_checksum: str | None = None


class Media(BaseModel):
    id: int | None = None
    observation_id: int | None = None
    source: str
    source_media_id: str
    s3_uri: str
    public_url: str | None = None
    creator: str | None = None
    license: str
    license_url: str | None = None
    attribution: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    sha256: str | None = None
    phash: str | None = None
    validation_status: ValidationStatus = "pending"
    rejection_reason: str | None = None


class DuplicateGroup(BaseModel):
    id: int | None = None
    canonical_media_id: int | None = None
    match_method: str
    score: float | None = None
    review_state: ReviewState = "unreviewed"
    member_ids: list[int] = Field(default_factory=list)


class EvidenceSource(BaseModel):
    id: int | None = None
    cite_key: str
    title: str | None = None
    authors: str | None = None
    pub_date: str | None = None
    source_type: str | None = None
    doi: str | None = None
    pmid: str | None = None
    isbn: str | None = None
    url: str | None = None
    access_date: str | None = None


class EvidenceClaim(BaseModel):
    id: int | None = None
    evidence_source_id: int
    taxon_id: int | None = None
    geography: str | None = None
    claim: str
    locator: str | None = None
    curator_notes: str | None = None


class DangerAssessment(BaseModel):
    id: int | None = None
    taxon_id: int
    geographic_scope: str
    medical_significance: MedicalSignificance
    exposure_context: str | None = None
    rationale: str
    evidence_ids: list[int] = Field(default_factory=list)
    reviewer: str | None = None
    review_date: str | None = None
    confidence: Literal["low", "medium", "high"] | None = None
    status: AssessmentStatus = "draft"


class DatasetRelease(BaseModel):
    version: str
    config_hash: str | None = None
    taxonomy_snapshot: str | None = None
    source_snapshots: dict[str, str] = Field(default_factory=dict)
    license_profile: str | None = None
    manifest_checksums: dict[str, str] = Field(default_factory=dict)
    s3_prefix: str | None = None
    status: ReleaseStatus = "draft"
