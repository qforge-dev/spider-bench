# Benchmark Runner Plan (image classification, encapsulated)

Status: design only. No code, no benchmark runs yet.
Dataset dependency: release `0.4.0` (854 taxa, 753 imaged, 1 image/species).

## 1. What the runner is

An encapsulated harness that turns dataset rows into scores:

```text
task row:  { image_ref, prompt, candidates?, correct_taxon, meta }
                │                     │
                ▼                     ▼
         model adapter ──► predictions ──► scorer ──► results + report
```

- **Task builder** (`benchmark build-tasks --dataset 0.4.0 --out ...`): reads a
  dataset release, emits versioned, deterministic `tasks.jsonl` (sorted,
  content-hashed). Each row carries the question (image S3 URI + public URL +
  sha256, prompt text, candidate taxon list or nothing for open-set) and the
  correct answer (accepted taxon + synonyms accepted as correct).
- **Model adapter** (narrow interface, the only thing model owners write):
  `predict(image_bytes, context) -> [{"taxon": str, "score": float}]`
  Ranked list, no threshold games inside the adapter. Local weights, API
  models, or third-party harnesses all sit behind this interface.
- **Runner** (`benchmark run --tasks ... --model ...`): executes rows with
  timeouts, retries only on infra errors, caches predictions by image sha256,
  resumes from a checkpoint. Models run subprocess/container-isolated with no
  network except declared API endpoints; API keys come from env, never files.
- **Scorer** (`benchmark score`): top-1/top-5 accuracy overall + per-family,
  per-image-status (PL vs worldwide photo), confusion pairs, abstention-aware
  metrics if unknown-labeled rows are included. Deterministic from
  predictions + tasks.
- **Results store**: `s3://spiders-dataset-088543363904/benchmarks/<suite>/<run-id>/`
  (private prefix — model outputs are not public data): predictions, config +
  code/dataset/model digests, scores, `COMPLETE` marker last. Same immutable
  discipline as dataset releases.

## 2. Blocking dataset gap (honest)

One image per species cannot make a train/test split. Options, pick one:

- **A. Zero-shot / gallery protocol (no new images).** The 753 images are the
  gallery; models must classify without training on them. Still needs *query*
  images distinct from gallery ones — not yet collected.
- **B. Collect N images/species, then split (recommended).** Extend
  `collect-one` to `collect-N` (e.g. up to 10/species, same license +
  validation + dedup pipeline), then split by *observation* (never the same
  photo/observation on both sides), stratify by family, cap per-observer and
  per-location dominance. Query images stay hidden; gallery may be public.
- **C. Leave-one-family-out / few-shot episodes** built from whatever N we
  have. Compatible with B, decides task format, not data volume.

Recommendation: B with N=10, then A-style zero-shot AND supervised splits
from the same pool. The collection pipeline already supports this; it is a
data-volume job, not new architecture.

## 3. Task formats (v1)

1. **Closed-set species ID**: image → pick from the 854 checklist (or the 753
   imaged subset; unlisted prediction = wrong). Primary metric top-1/top-5.
2. **Abstention**: rows include `no_image`/uncertain taxa represented by
   *text-only or out-of-gallery* queries where the correct answer is
   "unknown". Scores reward abstaining over hallucinating (risk-coverage
   curves, not just accuracy).
3. **(Later)** sex/stage, family-level fallback scoring, retrieval@k.

## 4. Encapsulation and reproducibility

- One Docker image per runner version: `Dockerfile.runner` pins Python,
  deps lock, CUDA base for local models. API-model runs use the same image
  minus weights; keys via env at runtime.
- Run manifest records: dataset version + checksums, tasks hash, runner
  image digest, model id + weight/API snapshot, seed, hardware, timestamps.
- No live services in the scoring path: scorer is pure (`tasks + predictions
  -> scores`), unit-tested, runnable offline.
- Leaderboard is a *generated static artifact* from result manifests, not a
  service. Publishing a score = uploading a result manifest that re-verifies.

## 5. Multi-model operation

- `configs/models/*.yaml` registry: model id, adapter type
  (local-weights | api | external-harness), resource limits, batch size,
  cost caps. Secrets only via env (`OPENAI_API_KEY`, etc.).
- Shared prediction cache keyed by (tasks-hash, model-id, image-sha) so
  adding a model never recomputes others; `--resume` continues crashed runs.
- Cost guard: every run needs `--max-cost` or `--max-rows` for API models;
  dry-run prints the bill of materials first.

## 6. Milestones

- **M0**: task schema + `build-tasks` from a release (works on 0.4.0, emits
  gallery-style tasks, marks the N=1 limitation in the manifest).
- **M1**: adapter interface + local subprocess runner + scorer + unit tests
  (all offline, fixture images).
- **M2**: `collect-N` + splitter (observation-disjoint, stratified) → first
  real suite version; Dockerfile.runner; results store layout on S3.
- **M3**: model registry + API adapters + cost guards + static leaderboard
  generator. First multi-model run only after M3 review.

## 7. Decisions needed before code

1. Zero-shot only, supervised splits, or both (recommends both, §2B)?
2. Closed 854-class, imaged-753 subset, or open-set with abstention?
3. Local-weight models, API models, or both at the start?
4. Query images hidden (private S3 prefix) or public?
5. Who approves a suite version as leaderboard-eligible, and what is the
   minimum N per species (recommends 10)?
6. Cost ceiling for the first multi-model run?
