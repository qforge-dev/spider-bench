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
See `docs/benchmark-runner-plan.md` for the benchmark design.

## Commands (`spider-bench …`, every mutating command takes `--dry-run`)

Setup: `init` (work dir + SQLite + S3 check), `status` (bucket/SQLite at a glance), `doctor` (creds, versioning, encryption, policy).

Taxonomy: `taxonomy collect` (checklist + snapshot ingest, idempotent), `taxonomy reconcile` (name matching + conflict report).

Discovery (metadata only, never image bytes): `discover inaturalist|gbif|commons` (rate-limited, resumable cursors).

Audit: `audit coverage` (per-taxon image candidates vs `no_image`), `audit licenses` (classify vs profile), `audit taxonomy` (snapshot conflicts).

Media: `media collect-one` (1 image/species, `--scope poland|worldwide`), `media collect-n` (up to N/species top-up), `media gap-fill` (Commons fallback, `--include-sharealike`), `media select|download|validate|deduplicate` (pipeline stages).

Danger: `danger evidence|assessments import` (validated imports), `danger audit` (release dir gates).

Release: `release build|verify|publish --version X` (deterministic Parquet + checksums, §11 gates, immutable S3 path + `COMPLETE`).

Benchmark: `benchmark build-tasks` (release → tasks), `benchmark split` (gallery + disjoint query), `benchmark run --model luna` (parallel, `--max-cost`, resume), `benchmark score`, `benchmark leaderboard`, `benchmark models` (safe registry listing), `benchmark publish` (private S3 results).

## Typical flows

```bash
pip install -e ".[dev]"            # add [app] for the search app
spider-bench audit coverage        # where the gaps are
spider-bench media collect-one --scope worldwide --only-missing
spider-bench benchmark run --model luna --max-tasks 10 --max-cost 2.0
spider-bench benchmark score       # scores latest run
python -m app.app                  # local search UI → http://127.0.0.1:5000
```

## Corrected benchmark protocol

Use [benchmark v5](docs/benchmark-v5.md) for fresh model comparisons. It specifies
source validation, normalized images, deterministic shuffled 20-name shortlists,
5,000-token limits, explicit failure accounting and the rerun commands.
Earlier runs remain historical exploratory results.
