# Local operations (S3-native)

Prereqs: Python 3.12+, AWS creds (env/SSO), `configs/poland.yaml` pointing at
`s3://spiders-dataset-088543363904` + `poland/` prefix.

```bash
spider-bench init --config configs/poland.yaml
spider-bench status
spider-bench doctor
spider-bench taxonomy collect --country PL --max-records 100 --dry-run
spider-bench discover inaturalist --country PL --max-records 100 --resume
spider-bench media select --input candidates.json --out data/work/selected.json --dry-run
spider-bench danger evidence import evidence.json [--dry-run]
spider-bench release build --version 0.1.0 --dry-run
spider-bench release verify --version 0.1.0
spider-bench release publish --version 0.1.0 --dry-run
```

- Every mutating command accepts `--dry-run`; network commands accept
  `--max-records/--resume/--concurrency`.
- Local `data/work/` (gitignored): SQLite index, logs, reports cache. No image
  bytes locally; bytes live at S3 content-addressed keys.
- Releases: build to temp dir, `verify` (§11 gates), publish stages via
  `releases/.staging-<v>-tmp/` then copies to `releases/<v>/`, `COMPLETE` last.
  Never edit a completed release — cut a new version.
- Logging: `configs/logging.yaml` (console INFO). Structured logs, no secrets.
