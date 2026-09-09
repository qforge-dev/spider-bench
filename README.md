# Spider Bench — Polish spiders, S3-native dataset

S3 bucket: `s3://spiders-dataset-088543363904` (public read, versioned).
Layout per country so we can expand beyond Poland:

```text
s3://spiders-dataset-088543363904/
  poland/raw-metadata/<source>/<snapshot>/...
  poland/media/sha256/<aa>/<bb>/<sha256>.<ext>
  poland/work/logs/...          # optional, private operational
  poland/releases/<version>/... # immutable releases
  germany/...                   # future
```

Local repo holds **only code, configs, schemas, docs, tests**.
Local machine holds a small SQLite index in `data/work/spider-bench.sqlite`
(gitignored) with `s3_uri` links — no image bytes locally.

## Quickstart

```bash
pip install -e ".[dev]"
spider-bench init --config configs/poland.yaml
spider-bench status
spider-bench doctor
```

See `SPIDER_BENCH_IMPLEMENTATION_PLAN.md` for full plan (adapted: S3 instead of local `data/` media).
See `docs/s3-layout.md` for bucket layout.
