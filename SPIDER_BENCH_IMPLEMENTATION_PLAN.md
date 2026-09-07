# Spider Bench: Data Collection and Benchmark Implementation Plan

## 1. Purpose

Build a reproducible benchmark for evaluating how well vision and multimodal models detect and identify spiders from photographs. The first release will focus on spiders observed in Poland. A later, separate dataset may evaluate European medically significant spiders and their harmless lookalikes.

This repository will contain the collection software, schemas, configuration, tests, benchmark definitions, and operational documentation. It will not contain downloaded photographs, secrets, or large generated datasets.

The intended production environment is the Bestia EC2 machine. Amazon S3 will be the durable store for images and immutable dataset releases. SQLite will be used as a local, resumable working index on EC2, but it will not be the only copy of dataset metadata.

## 2. Scope and guiding decisions

### 2.1 First release

- Geographic scope: Poland.
- Initial target: 50–100 sufficiently photographed and visually distinguishable species.
- Primary task: identify whether an image contains a spider and predict the most specific taxonomic rank supported by the photograph.
- Secondary tasks: family, genus, and species classification; uncertainty calibration; abstention; and hard-negative recognition.
- Data volume target: approximately 5,000–10,000 accepted images after validation and deduplication.
- The pipeline must report coverage for every Polish species even when a species has insufficient usable photographs.

Poland has roughly 850 recorded spider species, but the benchmark must not imply that all can be identified from ordinary photographs. Many require microscopy or examination of genital morphology. Each example therefore needs a `maximum_visually_supported_rank` and may have `species_identifiable_from_photo = false`.

### 2.2 Later release

Create a separate European medical-risk and lookalike benchmark. Do not mix this into the initial Polish species-identification score.

- Include medically significant taxa, geographic applicability, and confusing harmless taxa.
- Use an expert-reviewed, evidence-backed risk table.
- Never infer human medical risk merely from venom or toxin presence.
- Support `medically_significant`, `not_medically_significant`, and `uncertain` rather than forcing an unsafe binary label.

### 2.3 Explicit non-goals for the first implementation

- Training a spider-identification model.
- Scraping arbitrary search-engine results or sites without clear reuse permission.
- Redistributing images whose licenses prohibit the intended use.
- Claiming species-level identifiability when the visible evidence supports only genus or family.
- Using SQLite on a single EC2 disk as the authoritative dataset store.
- Deploying a public API or web application.

## 3. Data sources

Use official APIs or bulk exports wherever possible. Every adapter must preserve source provenance and licensing metadata.

### 3.1 Polish and European species membership

Planned inputs:

- Polish national spider checklists and occurrence databases.
- Spiders of Europe country lists for Polish membership and regional reference.
- GBIF checklist datasets published by Polish biodiversity institutions.

Use these sources to establish whether a species is recorded in Poland. Do not automatically download their photographs unless the relevant media license and terms explicitly permit it.

### 3.2 World Spider Catalog

Purpose:

- Accepted scientific names.
- Family and genus hierarchy.
- Synonyms and changed names.
- World Spider Catalog LSIDs.
- Taxonomic snapshot version.

Prefer the official export over HTML scraping. Preserve the original source name on every observation and map it to a versioned accepted taxon. Taxonomy changes must be reproducible: an existing dataset release keeps its original taxonomy snapshot even if a newer catalog changes a name later.

### 3.3 iNaturalist

Primary image source.

Use the licensed iNaturalist Open Data snapshot for bulk collection. The REST API may be used for discovery, small smoke tests, and current coverage reports, but not as an uncontrolled high-volume scraper.

Initial filters:

- Taxon: order Araneae.
- Place: Poland.
- Quality: research grade for species-level candidate labels.
- Media: photographs present.
- License: selected by an explicit license profile.
- Wild observations only where supported.

Important rules:

- Treat the observation as the grouping unit; one observation can contain multiple photos.
- Store observation ID, photo ID, observer ID, taxon ID, identification quality, observed date, creation date, coordinates or safe geographic cell, photo URL, creator, and photo license.
- Multiple photos from one observation must always remain in the same benchmark split.
- Record the source snapshot date because identifications and licenses can change.

### 3.4 GBIF

Purpose:

- Supplementary occurrence and media records.
- Geographic validation.
- Dataset, institution, and specimen provenance.
- Reproducible bulk downloads with download keys and DOIs.

GBIF includes records imported from iNaturalist and other providers. Deduplicate using GBIF occurrence IDs, `references`, `institutionCode`, `catalogNumber`, source URLs, iNaturalist IDs, and image hashes. Do not count the same observation as independent evidence merely because it appears in both systems.

### 3.5 Wikimedia Commons

Optional source for species with insufficient coverage or for curated reference images.

Use the MediaWiki API, including `imageinfo` and extended metadata, to capture creator, attribution text, license name, license URL, source page, image URL, and revision. Multi-licensed or ambiguous files must enter a manual-review queue.

### 3.6 Unsupported or permission-dependent sources

Spiders of Europe, Polish galleries, museum websites, and similar resources may be used as taxonomic references. Their images must not be downloaded or redistributed unless machine-readable media licensing or explicit written permission supports it. The pipeline may store reference page URLs without copying the media.

## 4. Proposed repository layout

```text
spider-bench/
├── pyproject.toml
├── README.md
├── configs/
│   ├── poland.yaml
│   ├── license-profiles.yaml
│   └── logging.yaml
├── schemas/
│   ├── manifest.schema.json
│   ├── risk-label.schema.json
│   └── dataset-release.schema.json
├── src/spider_bench/
│   ├── cli.py
│   ├── config.py
│   ├── db.py
│   ├── models.py
│   ├── sources/
│   │   ├── inaturalist.py
│   │   ├── gbif.py
│   │   ├── commons.py
│   │   ├── world_spider_catalog.py
│   │   └── polish_checklist.py
│   ├── taxonomy/
│   │   ├── normalize.py
│   │   └── reconcile.py
│   ├── media/
│   │   ├── download.py
│   │   ├── validate.py
│   │   ├── hash.py
│   │   └── deduplicate.py
│   ├── dataset/
│   │   ├── select.py
│   │   ├── split.py
│   │   ├── manifest.py
│   │   └── publish.py
│   ├── benchmark/
│   │   ├── tasks.py
│   │   ├── predictions.py
│   │   └── metrics.py
│   └── storage/
│       ├── local.py
│       └── s3.py
├── migrations/
├── tests/
│   ├── fixtures/
│   ├── unit/
│   ├── integration/
│   └── contract/
├── scripts/
│   ├── run_collection.sh
│   ├── run_validation.sh
│   └── run_benchmark.sh
├── deploy/
│   ├── systemd/
│   ├── iam/
│   └── cloud-init/
└── docs/
    ├── architecture.md
    ├── data-sources.md
    ├── licensing.md
    ├── ec2-operations.md
    └── benchmark-protocol.md
```

Use Python 3.12 or the version already standardized on Bestia. Prefer a typed CLI framework, HTTP client with retry support, Pydantic-style configuration validation, SQLAlchemy or direct SQLite migrations, Pillow for image validation, and PyArrow for Parquet. Keep dependencies modest and pin them with a lock file.

## 5. Command-line design

The CLI should expose small, composable, restartable stages.

```bash
# Initialize a local working database.
spider-bench init --config configs/poland.yaml

# Collect and normalize the Polish species list and taxonomy.
spider-bench taxonomy collect --country PL
spider-bench taxonomy reconcile

# Discover records without downloading image bytes.
spider-bench discover inaturalist --country PL
spider-bench discover gbif --country PL
spider-bench discover commons --country PL

# Produce coverage and license reports.
spider-bench audit coverage
spider-bench audit licenses

# Select candidates and download them to local or S3 storage.
spider-bench select --min-observations 50 --license-profile research
spider-bench download --storage s3 --resume

# Validate files and find duplicate groups.
spider-bench validate media
spider-bench deduplicate

# Create leakage-safe dataset partitions and a release candidate.
spider-bench split --strategy observer-geography-time --seed 20260907
spider-bench release build --version 0.1.0
spider-bench release verify --version 0.1.0
spider-bench release publish --version 0.1.0

# Run and score a model adapter.
spider-bench benchmark run --release 0.1.0 --adapter adapters/example.yaml
spider-bench benchmark score --run-id RUN_ID
```

Every mutating command should support `--dry-run`. Network operations should support bounded concurrency, request rate configuration, retry limits, and `--resume`.

## 6. Configuration

Configuration belongs in version-controlled YAML. Credentials and host-specific secrets must come from the EC2 instance role, AWS configuration, or a secret manager—not YAML or environment files committed to Git.

Example:

```yaml
project: polish-spiders
country_code: PL
taxonomy:
  authority: world_spider_catalog
  snapshot: "2026-09"
sources:
  inaturalist:
    enabled: true
    quality_grade: research
    place_id: 7800
  gbif:
    enabled: true
  commons:
    enabled: false
selection:
  minimum_independent_observations: 50
  maximum_images_per_observer_per_taxon: 10
license_profile: research
storage:
  backend: s3
  bucket: ${SPIDER_BENCH_BUCKET}
  prefix: spider-bench
aws:
  region: ${AWS_REGION}
```

Startup validation must fail clearly when required values are absent. It must never invent a bucket, region, account, SSH host, or credential.

## 7. Licensing profiles

Define licenses centrally and make the selected policy part of every release manifest.

### 7.1 Permissive profile

Default candidate set:

- CC0.
- CC BY.

CC BY-SA may be supported only after the project documents how ShareAlike obligations apply to distributed dataset artifacts and transformations.

### 7.2 Non-commercial research profile

May additionally include:

- CC BY-NC.
- CC BY-NC-SA, subject to documented ShareAlike handling.

Exclude:

- All rights reserved.
- Unknown or missing media license.
- NoDerivatives licenses when resizing, re-encoding, annotation overlays, or other transformations may create compliance ambiguity.

### 7.3 Attribution

Every release must include a machine-readable attribution table with at least:

- Image object key and checksum.
- Source page and media URL.
- Creator or observer display name.
- License identifier and canonical URL.
- Required attribution text when supplied by the source.
- Source record and snapshot identifiers.

License changes discovered during a future refresh should not silently rewrite an immutable old release. Flag affected objects and follow the documented takedown policy.

## 8. SQLite working database

SQLite is the local operational index. Enable foreign keys and WAL mode. Use migrations and explicit schema versions.

### 8.1 Core tables

`collection_runs`

- `id`, `started_at`, `finished_at`, `status`.
- Git commit, config hash, source snapshot IDs, host metadata, and error summary.

`source_datasets`

- Source name, dataset key, DOI or version, retrieval timestamp, license, and raw artifact location.

`taxa`

- Internal taxon ID, rank, accepted scientific name, authorship, family, genus, WSC LSID, source taxon IDs, and taxonomy snapshot.

`taxon_names`

- Original name, normalized name, synonym status, source, and accepted taxon ID.

`country_taxa`

- Country, taxon, presence status, evidence source, first/last recorded dates, and review status.

`observations`

- Internal ID, source, source observation ID, taxon, observer pseudonymous ID, observed timestamp, created timestamp, quality grade, geographic cell, positional accuracy, captive/wild status, source URL, and raw metadata checksum.

`media`

- Internal ID, observation ID, source media ID, original URL, source page, creator, license, dimensions, MIME type, local path, S3 key, SHA-256, perceptual hash, download status, validation status, and rejection reason.

`duplicate_groups`

- Duplicate group ID, media ID, match method, score, canonical media ID, and review state.

`review_labels`

- Media or observation ID, visible taxon rank, image-quality labels, spider bounding box if available, reviewer, review timestamp, evidence, and adjudication state.

`risk_labels`

- Taxon, geographic scope, risk category, evidence citations, reviewer, validity interval, and confidence/review status.

`dataset_releases`

- Semantic version, creation timestamp, config hash, taxonomy snapshot, license profile, selection query hash, split seed, manifest checksums, S3 URI, and status.

`dataset_members`

- Release, media, observation grouping ID, duplicate grouping ID, split, final label, supported rank, and inclusion/exclusion reason.

`benchmark_runs`

- Release, model/adapter, prompt version, execution config, start/end timestamps, raw predictions URI, metrics URI, and status.

### 8.2 Database durability

- Keep the active SQLite file on fast local EC2 storage.
- Create consistent snapshots using SQLite backup APIs, not by copying an actively written file.
- Upload snapshots and exported normalized Parquet tables to S3 after meaningful stages.
- Include checksums and schema versions.
- Make all published releases reconstructible from immutable source metadata, configuration, code commit, and manifests.

## 9. S3 layout

Use content-addressed object keys for deduplication and immutable release paths.

```text
s3://<bucket>/spider-bench/
├── raw-metadata/<source>/<snapshot>/...
├── media/sha256/<first-2>/<next-2>/<full-sha256>.<ext>
├── workspaces/<collection-run-id>/
│   ├── sqlite/spider-bench.sqlite.zst
│   ├── logs/
│   └── reports/
├── releases/<dataset-name>/<version>/
│   ├── release.json
│   ├── manifest.parquet
│   ├── attribution.parquet
│   ├── taxa.parquet
│   ├── splits/
│   ├── checksums.sha256
│   └── reports/
└── benchmark-runs/<release>/<model>/<run-id>/...
```

Recommended bucket controls:

- Block public access.
- Enable default encryption.
- Enable bucket versioning if cost permits.
- Use lifecycle rules for temporary run artifacts and incomplete multipart uploads.
- Keep release manifests and attribution records longer than disposable logs.
- Do not create duplicate S3 objects for the same SHA-256 image.

## 10. Collection pipeline

### Stage A: inventory and taxonomy

1. Fetch versioned Polish checklist inputs.
2. Fetch the WSC taxonomy snapshot.
3. Normalize Unicode, whitespace, authorship, and rank fields.
4. Reconcile synonyms to accepted taxa without discarding original names.
5. Produce unresolved-name and source-conflict reports.
6. Require manual review for ambiguous mappings.

### Stage B: discovery

1. Query or scan source metadata without downloading full images.
2. Store source records idempotently using unique source/source-ID constraints.
3. Apply geography, quality-grade, license, and taxon filters.
4. Generate per-species counts by observation, observer, license, year, region, and source.
5. Select candidate species using independent observation counts rather than raw photo counts.

### Stage C: media acquisition

1. Enqueue selected media records.
2. Use conditional HTTP requests when supported.
3. Retry transient failures with exponential backoff and jitter.
4. Respect published API guidance and configurable rate limits.
5. Stream each download while computing SHA-256.
6. Validate MIME type before selecting a safe extension.
7. Upload once to its content-addressed S3 key.
8. Persist success only after the object checksum and metadata are confirmed.

### Stage D: validation and deduplication

Reject or flag:

- Corrupt and truncated images.
- Unsupported formats.
- Extremely small images.
- Single-color or near-empty files.
- Source responses that are HTML error pages.
- Files with dimensions or MIME types inconsistent with metadata.
- Explicitly watermarked or composite imagery if excluded by policy.

Deduplicate in layers:

1. Exact source IDs and cross-source provenance links.
2. SHA-256 exact byte matches.
3. EXIF/source metadata matches.
4. Perceptual hashes for resized/re-encoded copies.
5. Optional embedding similarity for near-duplicate crops or sequences.

Near-duplicate groups must remain in one split even when not removed.

### Stage E: quality and scientific review

Automated checks may score blur, exposure, resolution, subject size, and likely presence of a spider. They must not replace taxonomic review.

Review fields should include:

- Spider present: yes/no/uncertain.
- Number of spiders.
- Bounding box or segmentation availability.
- Image quality.
- Occlusion and life stage.
- Sex when supported.
- Most specific visually supported rank.
- Label correctness: confirmed/disputed/unreviewed.
- Suitable for evaluation: yes/no and reason.

Create an adjudication workflow for disagreements. Record reviewer identities internally and expose only suitable attribution in releases.

## 11. Dataset selection and leakage-safe splitting

### 11.1 Selection

- Select taxa using minimum independent observations, not minimum photos.
- Cap contribution per observer and per observation.
- Avoid allowing a few prolific users or locations to dominate a species.
- Prefer broad temporal, seasonal, geographic, device, background, sex, and life-stage coverage.
- Include rare but important taxa only in a clearly labeled challenge subset.
- Maintain a reason code for every excluded candidate.

### 11.2 Hard negatives

Include licensed images of:

- Harvestmen.
- Ticks and mites.
- Scorpions and pseudoscorpions.
- Insects with spider-like silhouettes.
- Spider webs without visible spiders.
- Empty natural scenes.
- Toys, illustrations, tattoos, and artificial spiders in an out-of-domain set.

Negative sources and licenses require the same provenance standards as spider images.

### 11.3 Group constraints

The following must never cross splits:

- Photos belonging to one observation.
- Exact and near-duplicate groups.
- Burst sequences or specimen series where detected.

Prefer keeping an observer in only one split for the strongest evaluation. If that causes unacceptable class loss, use a documented hierarchical strategy that first groups observations and duplicates, then minimizes observer overlap and measures any residual overlap.

### 11.4 Recommended split strategy

- Training/development: older observations with broad coverage.
- Validation: observer-disjoint where feasible.
- Public test: observer-disjoint and regionally stratified.
- Private or rolling test: recent observations collected after a declared cutoff.

Use deterministic seeded assignment over stable grouping keys. Store the complete assignment algorithm, seed, and constraint report. Never use a random image-level split.

## 12. Benchmark task definitions

### 12.1 Spider presence

Input: one photograph.

Output:

- `is_spider`: yes/no/uncertain.
- Confidence.
- Optional bounding box.

Metrics: precision, recall, F1, AUROC where applicable, calibration error, and detection mAP when boxes exist.

### 12.2 Hierarchical identification

Output:

- Family, genus, species.
- Predicted rank.
- Confidence.
- Abstention reason.

Metrics:

- Macro and micro accuracy/F1 at family, genus, and species levels.
- Hierarchical distance or taxonomic loss.
- Top-k accuracy.
- Per-species and per-family results.
- Selective accuracy versus coverage.
- Accuracy conditioned on `maximum_visually_supported_rank`.

A model must not be penalized for declining to give a species label when the reference states that the photograph supports only genus, provided its higher-rank answer is correct.

### 12.3 Metadata-assisted identification

Run separate tracks:

- Image only.
- Image plus country.
- Image plus coarse geographic cell and observation month.

This measures whether location and season improve identification without contaminating the image-only score.

### 12.4 Medical-risk challenge

Keep this as a later separate release and score:

- Sensitivity for medically significant taxa.
- False reassurance rate.
- False alarm rate on lookalikes.
- Calibration.
- Correct use of geographic scope.
- Appropriate uncertainty and recommendation for expert review.

Do not score treatment advice as part of taxonomic identification without a separately reviewed medical protocol.

## 13. Release and reproducibility protocol

Each immutable release must contain:

- Semantic dataset version.
- Release timestamp.
- Git commit.
- Configuration and configuration hash.
- Taxonomy authority and snapshot.
- Source snapshot/download identifiers and GBIF DOI where available.
- License profile.
- Selection and split policies.
- Random seed.
- Manifest, attribution table, taxa table, and checksums.
- Coverage, duplicate, rejected-media, leakage, and license reports.
- Known limitations and takedown contact/process.

The release manifest should reference S3 media keys rather than embed images. A material change to membership, labels, taxonomy snapshot, or split assignment requires a new release version.

## 14. EC2 and Bestia operations

### 14.1 Deployment model

1. Clone or pull this Git repository on Bestia.
2. Create a virtual environment or reproducible container.
3. Attach an EC2 instance profile with least-privilege S3 permissions.
4. Set non-secret runtime configuration such as bucket and region through the service environment or invocation.
5. Store the working SQLite database and temporary files on an explicitly sized local volume.
6. Run collection inside `systemd`, `tmux`, or another established Bestia job mechanism.
7. Periodically publish checkpoints to S3.
8. Stop compute or collection processes when work completes.

Do not put long-lived AWS access keys on the instance if an instance role can be used.

### 14.2 Minimum IAM permissions

Limit the instance role to the configured bucket and prefix. Expected actions:

- `s3:ListBucket` restricted by prefix.
- `s3:GetObject`.
- `s3:PutObject`.
- `s3:AbortMultipartUpload`.
- Optional `s3:GetObjectVersion` if versioning is used.

The collector does not require bucket deletion, policy mutation, public-access changes, or object deletion for its normal workflow. Add KMS permissions only if the chosen bucket uses a customer-managed KMS key.

### 14.3 Reliability

- Use a filesystem lock or run lease so two collectors do not mutate one SQLite workspace simultaneously.
- Persist queues and cursors transactionally.
- Upload checkpoints after each source stage and configurable download batch.
- Emit structured JSON logs with run and record IDs, never credentials or signed URLs.
- Provide `status`, `resume`, and `doctor` commands.
- Treat process termination as recoverable; completed objects and records must not download again.

### 14.4 Cost controls

- Run metadata discovery before downloading images.
- Estimate object count, bytes, S3 requests, and storage before materialization.
- Default to an appropriate resized source image rather than originals unless resolution studies justify originals.
- Limit concurrent downloads and multipart uploads.
- Use S3 lifecycle rules for temporary workspaces and failed partial artifacts.
- Avoid repeated LIST operations by using the database as the work index.
- Make all large operations require an explicit maximum-record or approved estimate.

## 15. Observability and reports

Every run should expose:

- Records discovered, accepted, rejected, and pending.
- Downloads attempted, succeeded, retried, and failed.
- Bytes downloaded and uploaded.
- Per-source latency and rate-limit events.
- Species and license coverage.
- Exact and near-duplicate counts.
- Unresolved taxonomy mappings.
- Split leakage violations.
- Estimated and actual storage volume.

Produce human-readable HTML or Markdown summaries plus machine-readable JSON/Parquet outputs. Start with logs and reports; add CloudWatch integration only if it materially improves Bestia operations.

## 16. Testing strategy

### 16.1 Unit tests

- Configuration and license-profile validation.
- Taxonomic normalization and synonym reconciliation.
- S3 key generation.
- Hashing and duplicate grouping.
- Source-record transformations.
- Deterministic split assignment.
- Attribution generation.
- Release checksum verification.

### 16.2 Contract tests

Store small sanitized fixtures representing API responses from every source. Verify required fields and fail clearly when a source contract changes. Live API checks should be optional and not required for normal unit tests.

### 16.3 Integration tests

- Run the complete pipeline against a tiny fixture dataset.
- Use temporary SQLite databases.
- Use an S3 emulator or mocked storage for normal CI.
- Test interruption and resume at each stage.
- Test retries, corrupt images, duplicate records, license rejection, and taxonomy conflicts.

### 16.4 Release validation tests

Fail publication when:

- A media row lacks provenance or an accepted license.
- A referenced S3 object is missing or has the wrong checksum.
- One observation or duplicate group crosses splits.
- A taxon is absent from the pinned taxonomy snapshot.
- Required attribution is missing.
- Manifest or configuration checksums differ.
- Split sizes or coverage violate configured thresholds.

## 17. Security and privacy

- Never commit AWS keys, API tokens, signed URLs, SSH keys, or private host configuration.
- Use the EC2 instance profile and least-privilege IAM.
- Do not retain unnecessary personal data.
- Store a stable source observer ID for leakage grouping, but pseudonymize it in public releases unless attribution requires disclosure.
- Coarsen or omit coordinates for sensitive taxa and honor source geoprivacy.
- Sanitize logs and exceptions.
- Document data removal and license-change handling.
- Validate URLs and restrict download hosts to approved source domains to reduce server-side request-forgery risk.

## 18. Failure recovery

- Every stage writes durable status and checkpoints.
- An interrupted discovery resumes from its persisted cursor or source snapshot.
- An interrupted download skips checksum-confirmed S3 objects.
- Failed records retain attempt counts, timestamps, HTTP status, and a normalized error code.
- Retry only transient failures automatically; send permanent failures to a report.
- Reconciliation and split generation run in transactions or write new candidate versions before replacing working state.
- Publishing writes all release objects first, verifies them, then writes a final completion marker. Consumers must ignore releases without that marker.

## 19. Delivery milestones

### Milestone 0: decisions and skeleton

- Confirm S3 bucket, prefix, AWS region, Python/runtime standard, and license profile.
- Establish packaging, configuration, logging, migrations, tests, and CI.
- Add a no-network fixture pipeline.

Acceptance criteria:

- CLI starts and validates configuration.
- Fixture pipeline produces a deterministic manifest.
- No secrets or data artifacts are tracked by Git.

### Milestone 1: taxonomy and coverage inventory

- Implement Polish checklist and WSC adapters.
- Implement iNaturalist metadata discovery.
- Build normalized SQLite tables and coverage reports.

Acceptance criteria:

- Reproducible Polish taxon inventory.
- Per-species licensed observation/observer counts.
- Unresolved taxonomy conflicts are reported, not silently discarded.

### Milestone 2: licensed media and S3

- Implement download queue, validation, hashing, S3 storage, attribution, resume, and checkpointing.
- Add dry-run cost estimates.

Acceptance criteria:

- Bounded Bestia smoke run survives restart without duplicate work.
- Every accepted S3 object has verified checksum, source, creator, and license.
- SQLite and Parquet checkpoints are published to S3.

### Milestone 3: GBIF, Commons, and deduplication

- Add GBIF and optional Commons adapters.
- Add cross-source identity, exact hashing, perceptual hashing, and duplicate reports.

Acceptance criteria:

- Imported iNaturalist records from GBIF are recognized.
- Duplicate groups cannot cross dataset splits.
- Ambiguous Commons licensing enters review rather than being accepted.

### Milestone 4: benchmark release builder

- Add candidate selection, review imports, hard negatives, deterministic splits, immutable releases, and release verification.

Acceptance criteria:

- Produce a versioned Poland release candidate with complete provenance.
- Pass all licensing, checksum, taxonomy, and leakage gates.
- Rebuilding from the same snapshots/config/commit yields identical manifests.

### Milestone 5: evaluation harness

- Define prediction schema and adapters for selected model providers or local models.
- Add hierarchical, abstention, calibration, and detection metrics.
- Store immutable run outputs in S3.

Acceptance criteria:

- At least one local baseline and one multimodal model adapter can run against the same release.
- Scoring is deterministic from stored predictions.
- Reports include aggregate and per-taxon results.

### Milestone 6: expert review and European risk subset

- Establish reviewer workflow and adjudication.
- Curate medical-risk and lookalike taxonomy with cited evidence and geographic scope.

Acceptance criteria:

- Risk labels have reviewer, evidence, scope, and version fields.
- No toxin-derived automatic danger labels exist.
- Safety metrics explicitly track false reassurance and false alarms.

## 20. Decisions required before implementation

The implementation should not guess these values:

1. Exact Bestia repository path and preferred job runner.
2. AWS account/region and existing or new S3 bucket.
3. S3 prefix and retention/versioning policy.
4. Whether the benchmark is strictly non-commercial research or must permit commercial use.
5. Whether CC BY-SA media is acceptable and how ShareAlike obligations will be handled.
6. Whether images may be redistributed to benchmark users or only referenced through controlled access/manifests.
7. Target model interfaces for the first evaluation harness.
8. Availability of arachnologists or experienced identifiers for review.
9. Whether precise coordinates must be retained privately or discarded after geographic grouping.

Recommended defaults:

- Start with the non-commercial research profile for the internal prototype, but design filters so a permissive-only release can be generated later.
- Keep the S3 bucket private and publish manifests separately when ready.
- Download medium or large images initially, not originals.
- Use one EC2 instance-role credential path and avoid static AWS keys.
- Keep the first benchmark at 50–100 Polish species and expand based on audited coverage rather than a predetermined species count.

## 21. Definition of done for the complete system

The system is complete when it can, from a clean Bestia checkout and documented configuration:

1. Reconstruct a versioned Polish spider taxonomy snapshot.
2. Discover licensed candidate observations from approved sources.
3. Download and validate selected images into content-addressed S3 storage.
4. Resume safely after interruption without duplicating records or objects.
5. Produce complete provenance, licensing, attribution, taxonomy, quality, and duplicate reports.
6. Build observation-, duplicate-, observer-, geography-, and time-aware benchmark splits.
7. Verify that no group crosses splits and that all referenced objects and licenses are valid.
8. Publish an immutable release with checksums and reproducibility metadata.
9. Run supported model adapters and calculate hierarchical identification, abstention, calibration, and spider-detection metrics.
10. Reproduce the same manifest from pinned inputs, configuration, seed, and Git commit.

