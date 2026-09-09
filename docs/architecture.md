# Architecture (S3-native)

Bucket `spiders-dataset-088543363904` (us-east-1, public read, versioned) shared
across countries via prefix (`poland/`, ...). See `docs/s3-layout.md` for the key layout.

- `src/spider_bench/config.py` — typed YAML config (`configs/poland.yaml`).
- `src/spider_bench/db.py` — local SQLite working index (WAL). Stores `s3_uri` +
  `public_url` + sha256; no image bytes locally.
- `src/spider_bench/storage/s3.py` — key builders (`media_key`, `raw_metadata_key`,
  `release_key`), `key_exists`, client factory.
- `src/spider_bench/sources/` — source adapters (other workers): metadata-only
  discovery first, raw dumps to `poland/raw-metadata/<source>/<snapshot>/`.
- `src/spider_bench/taxonomy/` — normalization + reconciliation (other workers).
- `src/spider_bench/media/` — download/validate/dedup to content-addressed
  `poland/media/sha256/<aa>/<bb>/<sha>.ext` (other workers; dedup by sha256 + HEAD check).
- `src/spider_bench/danger/` — evidence import, assessments, review events (this worker).
- `src/spider_bench/dataset/` — `select.py` (license/dominance caps),
  `manifest.py` (deterministic parquet + release.json + checksums),
  `publish.py` (§11 gates, staging copy, COMPLETE last).
- `src/spider_bench/licensing.py` — profile loading, accept/review classification,
  attribution builder.
- `src/spider_bench/cli.py` — typer groups: taxonomy, discover, audit, media,
  danger, release + `init`/`status`/`doctor`.

Rules: never mutate `poland/releases/<version>/` in place; SQLite tracks review
state, S3 is truth for bytes; run lease + transactional cursors for concurrency.
