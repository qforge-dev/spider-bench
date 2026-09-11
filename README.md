# Spider Bench — Polish spiders, S3-native dataset

S3 bucket: `s3://spiders-dataset-088543363904` (public read, versioned).
Layout per country so we can expand beyond Poland:

```text
s3://spiders-dataset-088543363904/
  poland/raw-metadata/<source>/<snapshot>/...
  poland/media/sha256/<aa>/<bb>/<sha256>.<ext>
  poland/benchmarks/<suite>/... # frozen tasks, source evidence, originals, inventory
  poland/work/logs/...          # optional, private operational
  poland/releases/<version>/... # immutable releases
  germany/...                   # future
```

The repo holds **code, configs, schemas, docs, tests, and completed benchmark results**.
Local machine holds a small SQLite index in `data/work/spider-bench.sqlite`
(gitignored) with `s3_uri` links. Benchmark images and source evidence use a
disposable, checksum-verified local cache; S3 holds the permanent copies.

## Benchmark results in Git

Completed runs are versioned under `data/benchmarks/runs/<run-id>/`: frozen tasks,
predictions, run and suite manifests, scores, and generated reports when available.
The leaderboard is versioned under `data/benchmarks/leaderboard.*`. Each task retains
its source attribution and image license; image bytes remain in S3. These results
use the fixed v5 task lists and include failed model answers in the denominator.
Runs that fail execution validation, such as responses without finish reasons,
remain available for inspection but are excluded from the leaderboard.

Only stage completed, reviewed runs. Environment files (including `.env.local`),
credentials, private keys, caches, SQLite databases, operational logs, and run
backups are ignored. Endpoint addresses and public dataset URLs are intentional;
API keys are supplied through environment variables. Before pushing new results,
check the staged files and scan them for secrets, including provider error text.

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

Benchmark: `benchmark prepare` (validate sources and prepare images), `benchmark publish-suite` (immutable dataset on S3), `benchmark sync` (restore dataset/cache), `benchmark run --model luna` (automatically restores v5 from S3, `--max-cost`, resume), `benchmark score`, `benchmark leaderboard`, `benchmark models` (safe registry listing), `benchmark publish` (S3 run results).

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
16,000-token limits, explicit failure accounting and the rerun commands.
Earlier runs remain historical exploratory results.
