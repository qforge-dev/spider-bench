-- 002: rebuild stale minimal `media` table (early scaffold) to full §8 schema,
-- preserving any existing rows (matched by sha256).
PRAGMA foreign_keys=OFF;

CREATE TABLE IF NOT EXISTS media_new (
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

INSERT OR IGNORE INTO media_new (sha256, s3_uri, public_url, license, validation_status, source, source_media_id)
  SELECT sha256, s3_uri, public_url, license, 'accepted', 'inaturalist', 'legacy:' || sha256
  FROM media;

DROP TABLE IF EXISTS media;
ALTER TABLE media_new RENAME TO media;

PRAGMA foreign_keys=ON;
