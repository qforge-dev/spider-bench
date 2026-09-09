-- Spider-bench SQLite working index, migration 001.
-- S3-native: stores s3_uri links only, no image bytes locally.
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  git_commit TEXT,
  config_hash TEXT,
  source_snapshots TEXT,
  host_metadata TEXT,
  notes TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS source_datasets (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  version TEXT,
  retrieval_date TEXT,
  citation TEXT,
  license TEXT,
  checksum TEXT,
  raw_s3_uri TEXT,
  notes TEXT,
  UNIQUE (source, version)
);

CREATE TABLE IF NOT EXISTS taxa (
  id INTEGER PRIMARY KEY,
  scientific_name TEXT NOT NULL,
  authorship TEXT,
  rank TEXT NOT NULL,
  family TEXT,
  genus TEXT,
  wsc_id TEXT,
  snapshot_id TEXT NOT NULL,
  taxonomic_status TEXT NOT NULL DEFAULT 'accepted',
  UNIQUE (snapshot_id, scientific_name),
  UNIQUE (wsc_id)
);

CREATE TABLE IF NOT EXISTS taxon_names (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  authorship TEXT,
  normalized_authorship TEXT NOT NULL DEFAULT '',
  rank TEXT,
  source TEXT,
  source_id TEXT,
  accepted_taxon_id INTEGER REFERENCES taxa(id),
  name_type TEXT NOT NULL DEFAULT 'original',
  UNIQUE (normalized_name, normalized_authorship, source)
);

CREATE TABLE IF NOT EXISTS country_taxa (
  id INTEGER PRIMARY KEY,
  taxon_id INTEGER REFERENCES taxa(id),
  original_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  country_code TEXT NOT NULL DEFAULT 'PL',
  membership_status TEXT NOT NULL DEFAULT 'present',
  supporting_source_id INTEGER REFERENCES source_datasets(id),
  review_state TEXT NOT NULL DEFAULT 'unreviewed',
  notes TEXT,
  UNIQUE (country_code, normalized_name, supporting_source_id)
);

CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  source_observation_id TEXT NOT NULL,
  source_taxon TEXT,
  taxon_id INTEGER REFERENCES taxa(id),
  observer_key TEXT,
  observed_at TEXT,
  quality_grade TEXT,
  country_code TEXT,
  latitude REAL,
  longitude REAL,
  captive_wild TEXT,
  source_url TEXT,
  snapshot_id TEXT,
  raw_checksum TEXT,
  UNIQUE (source, source_observation_id)
);

CREATE TABLE IF NOT EXISTS media (
  id INTEGER PRIMARY KEY,
  observation_id INTEGER REFERENCES observations(id),
  source TEXT NOT NULL,
  source_media_id TEXT NOT NULL,
  s3_uri TEXT NOT NULL,
  public_url TEXT,
  creator TEXT,
  license TEXT NOT NULL,
  license_url TEXT,
  attribution TEXT,
  mime_type TEXT,
  width INTEGER,
  height INTEGER,
  sha256 TEXT UNIQUE,
  phash TEXT,
  validation_status TEXT NOT NULL DEFAULT 'pending',
  rejection_reason TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (source, source_media_id)
);

CREATE TABLE IF NOT EXISTS duplicate_groups (
  id INTEGER PRIMARY KEY,
  canonical_media_id INTEGER REFERENCES media(id),
  match_method TEXT NOT NULL,
  score REAL,
  review_state TEXT NOT NULL DEFAULT 'unreviewed',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS duplicate_group_members (
  group_id INTEGER NOT NULL REFERENCES duplicate_groups(id) ON DELETE CASCADE,
  media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
  UNIQUE (group_id, media_id)
);

CREATE TABLE IF NOT EXISTS evidence_sources (
  id INTEGER PRIMARY KEY,
  cite_key TEXT NOT NULL UNIQUE,
  title TEXT,
  authors TEXT,
  pub_date TEXT,
  source_type TEXT,
  doi TEXT,
  pmid TEXT,
  isbn TEXT,
  url TEXT,
  access_date TEXT
);

CREATE TABLE IF NOT EXISTS evidence_claims (
  id INTEGER PRIMARY KEY,
  evidence_source_id INTEGER NOT NULL REFERENCES evidence_sources(id),
  taxon_id INTEGER REFERENCES taxa(id),
  geography TEXT,
  claim TEXT NOT NULL,
  locator TEXT,
  curator_notes TEXT
);

CREATE TABLE IF NOT EXISTS danger_assessments (
  id INTEGER PRIMARY KEY,
  taxon_id INTEGER NOT NULL REFERENCES taxa(id),
  geographic_scope TEXT NOT NULL,
  medical_significance TEXT NOT NULL
    CHECK (medical_significance IN ('none_known','minor_local_effects','medically_significant','uncertain')),
  exposure_context TEXT,
  rationale TEXT,
  evidence_ids TEXT NOT NULL DEFAULT '[]',
  reviewer TEXT,
  review_date TEXT,
  confidence TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  UNIQUE (taxon_id, geographic_scope)
);

CREATE TABLE IF NOT EXISTS review_events (
  id INTEGER PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_id INTEGER NOT NULL,
  proposed_change TEXT,
  reviewer TEXT,
  decided_at TEXT,
  decision TEXT NOT NULL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS dataset_releases (
  version TEXT PRIMARY KEY,
  config_hash TEXT,
  taxonomy_snapshot TEXT,
  source_snapshots TEXT,
  license_profile TEXT,
  manifest_checksums TEXT,
  s3_prefix TEXT,
  status TEXT NOT NULL DEFAULT 'draft',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS dataset_members (
  id INTEGER PRIMARY KEY,
  release_version TEXT NOT NULL REFERENCES dataset_releases(version),
  member_type TEXT NOT NULL,
  member_id INTEGER NOT NULL,
  inclusion_state TEXT,
  reason TEXT,
  UNIQUE (release_version, member_type, member_id)
);
