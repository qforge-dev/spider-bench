# S3 layout — bucket shared across countries

Bucket: `s3://spiders-dataset-088543363904` (public read, versioned, AES256).
Region: `us-east-1`. Public URL base: `https://spiders-dataset-088543363904.s3.us-east-1.amazonaws.com/`.

Per country prefix (`poland/`, future `germany/`, ...):

```text
<country>/
  raw-metadata/<source>/<snapshot>/...   # versioned source dumps, never overwritten
  media/sha256/<aa>/<bb>/<sha256>.<ext>  # content-addressed, immutable
  benchmarks/<suite>/                   # immutable prepared benchmark dataset
    tasks.jsonl, manifest.json          # portable S3 references; no absolute local paths
    source-tasks.jsonl, exclusions.jsonl, validation.json
    preparation/observations/*.json     # frozen source records, including exclusions
    preparation/images/*.source        # downloaded originals
    inventory.json, COMPLETE            # SHA-256 inventory; completion marker written last
  releases/<version>/                    # immutable: taxa/media/attribution/danger/evidence parquet + release.json + checksums + COMPLETE marker
  work/logs/...                          # optional operational scratch
```

Local (gitignored `data/work/`):
- `spider-bench.sqlite` (WAL) — index with `s3_uri` + `public_url` + sha256, no bytes.
- backups / logs / reports cache.
- `benchmark-s3-cache/<aa>/<sha256>` — disposable verified benchmark assets.

Prepared benchmark JPEGs use the existing `media/sha256/` layout. The suite inventory
records every object key, SHA-256 and byte count, including the prepared JPEGs.
`benchmark sync --archive` also restores originals and excluded source records into
the content-addressed cache. The inventory maps those hashes back to artifact names.
Benchmark result publication remains separate under `benchmarks/<suite>/<run-id>/`.

Rules:
- Never modify a release prefix in place; new version = new prefix.
- Media keys immutable; dedup by sha256 before upload; `head_object` check first.
- SQLite is source of truth for review state; S3 is source of truth for bytes.
