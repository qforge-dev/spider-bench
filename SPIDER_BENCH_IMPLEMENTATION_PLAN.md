# Polish Spider Dataset: Collection and Publication Plan

## 1. Purpose

Build a reproducible, versioned dataset of spider species recorded in Poland. The dataset combines:

- a normalized Polish spider checklist;
- accepted taxonomy and synonyms;
- licensed, validated, and deduplicated photographs;
- complete source, creator, and license provenance; and
- evidence-backed information about potential danger to humans in Poland.

This phase is only about building and publishing the dataset. It does not include model training, model evaluation, benchmark scoring, train/test splits, hard negatives, or a public application.

The repository contains collection software, schemas, configuration, tests, release tools, and operational documentation. It must not contain downloaded photographs, credentials, private host configuration, or large generated datasets.

The first implementation runs entirely on a local machine. A gitignored local data directory stores source artifacts, images, working snapshots, and immutable dataset releases. SQLite is the resumable working index, while release metadata is also exported to portable Parquet and JSON files.

## 2. First-release scope

### 2.1 Geographic and taxonomic scope

- Geographic scope: species recorded in Poland.
- Taxonomic scope: order Araneae.
- Report every checklist species, including species for which no acceptable image is available.
- Preserve original source names and reconcile them to a pinned taxonomic snapshot.
- Represent disputed, doubtful, historical, introduced, and uncertain Polish records explicitly rather than silently accepting or removing them.

The initial image collection should prioritize species with adequate licensed coverage. An approximate target of 50–100 well-photographed species and 5,000–10,000 accepted images may be used for capacity planning, but it is not a release requirement. Actual targets must be set after a metadata-only coverage audit.

### 2.2 Human-danger information

Danger information belongs to a taxon in a geographic context, not to an individual photograph. It must be supported by cited evidence and expert review.

Do not infer danger from any of the following alone:

- the presence of venom;
- body size or appearance;
- common names, anecdotes, or search-engine summaries;
- an unverified occurrence record; or
- a model-generated assessment.

The primary field is `medical_significance`, with these values:

- `none_known`: no evidence found of medically significant effects under the defined geographic scope; this does not mean that a bite is impossible or symptom-free;
- `minor_local_effects`: credible evidence primarily supports transient local effects;
- `medically_significant`: credible evidence supports a meaningful risk of effects requiring medical assessment;
- `uncertain`: evidence is insufficient, conflicting, or not applicable to Poland.

Avoid presenting an unsupported numeric danger score. If a consumer requires numeric sorting, expose a documented display ordinal derived from the reviewed category, never as an independent scientific measurement.

Each assessment must include geographic applicability, evidence citations, reviewer status, rationale, and review date. The dataset must clearly distinguish:

- the species being present in Poland;
- the species being capable of biting a human;
- the severity of documented effects; and
- the likelihood of meaningful exposure in Poland.

The dataset must not provide diagnosis or treatment advice. User-facing exports should include a short safety notice recommending appropriate medical or emergency services when symptoms are severe or uncertain.

### 2.3 Explicit non-goals

- Training or evaluating spider-identification models.
- Creating benchmark partitions or leaderboards.
- Collecting non-spider images or hard negatives.
- Claiming that all Polish species can be identified from ordinary photographs.
- Automatically assigning medical significance.
- Scraping arbitrary websites or search results without clear permission.
- Redistributing images whose licenses do not permit the intended use.
- Deploying a public API or web application.

## 3. Dataset products

Each release should contain or reference:

1. `taxa.parquet`: accepted taxa, hierarchy, synonyms, source identifiers, and Polish membership status.
2. `media.parquet`: image metadata, taxon association, validation state, checksums, and durable object references.
3. `attribution.parquet`: creator, source, license, and required attribution text.
4. `danger_assessments.parquet`: geographic, evidence-backed medical-significance assessments.
5. `evidence.parquet`: normalized bibliographic and expert-review references used by danger assessments and taxonomic decisions.
6. `release.json`: version, configuration, source snapshots, taxonomy snapshot, code commit, counts, policies, and checksums.
7. Coverage, taxonomy-conflict, licensing, rejected-media, duplicate, and review-status reports.

Images should live in a gitignored, content-addressed local media directory. Whether a release may redistribute image bytes or only publish references is a release-policy decision.

## 4. Data sources

Use official APIs, published datasets, or versioned bulk exports wherever possible. Every adapter must preserve source provenance and source-specific identifiers.

### 4.1 Polish species membership

Candidate inputs include published Polish national checklists, Polish biodiversity databases, Spiders of Europe country lists, and GBIF checklist datasets from relevant Polish institutions.

Before implementation, select at least one primary membership authority and define how secondary sources resolve omissions or disagreements. Store source versions, citations, and retrieval dates. Do not download images from a checklist or gallery unless its media terms independently permit it.

### 4.2 World Spider Catalog

Use a pinned World Spider Catalog snapshot for accepted names, hierarchy, synonyms, authorship, and identifiers. Prefer an official export or other permitted structured source over HTML scraping.

Confirm access and reuse terms before relying on the source. A published dataset release must retain its original taxonomy snapshot even if later taxonomy changes.

### 4.3 iNaturalist

Use licensed iNaturalist Open Data for bulk collection when feasible. The REST API may support discovery, small smoke tests, and current coverage reports, but must not be used as an uncontrolled high-volume scraper.

Candidate filters:

- taxon: Araneae;
- place: Poland;
- photographs present;
- explicit accepted image license;
- wild observations where the source reliably exposes that status; and
- research-grade observations as candidates, not unquestioned ground truth.

Treat the observation as the grouping unit. Preserve observation ID, photo ID, observer ID, source taxon, identification quality, timestamps, safe geography, source URLs, creator, and photo license. Record the source snapshot date because identifications and licenses may later change.

### 4.4 GBIF

Use GBIF for supplementary occurrence and media records, geographic corroboration, institutional provenance, and reproducible downloads with download keys and DOIs.

GBIF may contain records imported from iNaturalist and other providers. Detect shared records using occurrence IDs, references, institution and catalog identifiers, source URLs, iNaturalist IDs, and image hashes. Cross-publication is not independent evidence.

### 4.5 Wikimedia Commons

Use Wikimedia Commons optionally when priority taxa have insufficient coverage. Capture the file revision, creator, attribution, license name and URL, source page, and media URL through the MediaWiki API. Send ambiguous or multi-licensed files to manual review.

### 4.6 Medical-significance evidence

Danger assessments should use sources appropriate to medical and geographic claims, such as peer-reviewed case reports and reviews, poison-center or public-health publications, recognized clinical or toxicological reference works, and expert statements with recorded authorship and review date.

Record enough bibliographic metadata to locate the claim, including DOI, PMID, ISBN, or stable URL when available. Store the claim supported by each citation; a citation alone must not be treated as a complete assessment.

## 5. Proposed repository layout

```text
spider-bench/
├── pyproject.toml
├── README.md
├── configs/
│   ├── poland.yaml
│   ├── license-profiles.yaml
│   └── logging.yaml
├── schemas/
│   ├── taxon.schema.json
│   ├── media.schema.json
│   ├── danger-assessment.schema.json
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
│   ├── danger/
│   │   ├── evidence.py
│   │   ├── assessments.py
│   │   └── review.py
│   ├── dataset/
│   │   ├── select.py
│   │   ├── manifest.py
│   │   └── publish.py
│   └── storage/
│       └── local.py
├── migrations/
├── tests/
│   ├── fixtures/
│   ├── unit/
│   ├── integration/
│   └── contract/
├── scripts/
└── docs/
    ├── architecture.md
    ├── data-sources.md
    ├── licensing.md
    ├── danger-classification.md
    ├── review-protocol.md
    └── local-operations.md
```

Use Python 3.12. Prefer typed configuration and CLI libraries, an HTTP client with retry support, SQLite migrations, Pillow for image validation, and PyArrow for Parquet. Pin dependencies with a lock file.

## 6. Command-line design

Commands should be composable, idempotent, and restartable.

```bash
spider-bench init --config configs/poland.yaml

spider-bench taxonomy collect --country PL
spider-bench taxonomy reconcile

spider-bench discover inaturalist --country PL
spider-bench discover gbif --country PL
spider-bench discover commons --country PL

spider-bench audit coverage
spider-bench audit licenses
spider-bench audit taxonomy

spider-bench media select --license-profile research
spider-bench media download --storage local --resume
spider-bench media validate
spider-bench media deduplicate

spider-bench danger evidence import INPUT
spider-bench danger assessments import INPUT
spider-bench danger audit

spider-bench release build --version 0.1.0
spider-bench release verify --version 0.1.0
spider-bench release publish --version 0.1.0

spider-bench status
spider-bench doctor
```

Every mutating command must support `--dry-run`. Network operations must support bounded concurrency, configurable rate limits, retry limits, maximum-record limits, and `--resume`.

## 7. Configuration and licensing

Configuration belongs in version-controlled YAML. Local data paths should be relative to the project by default and overrideable for larger disks. Any source API tokens must come from the process environment or the operating-system credential store, never committed files. Startup validation must fail clearly when required values or writable paths are absent.

Define centrally versioned license profiles. A conservative permissive profile may accept CC0 and CC BY. A research profile may additionally accept CC BY-NC and compatible licenses after documenting the intended use. Do not accept unknown, all-rights-reserved, or transformation-incompatible media.

CC BY-SA and CC BY-NC-SA require a documented decision about ShareAlike obligations before inclusion. Store license decisions with the release rather than relying on the current source state.

Every accepted image needs a relative media path and SHA-256 checksum, source record and URLs, creator, license identifier and URL, required attribution, and source snapshot or retrieval identifier.

## 8. Working data model

Use SQLite with foreign keys, WAL mode, migrations, and explicit schema versions.

Core entities:

- `collection_runs`: status, timestamps, Git commit, configuration hash, source snapshots, host metadata, and errors.
- `source_datasets`: source, version or DOI, retrieval time, license, checksum, and raw artifact location.
- `taxa`: accepted name, authorship, rank, hierarchy, WSC identifier, and taxonomy snapshot.
- `taxon_names`: original names, synonyms, normalized forms, source, and accepted-taxon mapping.
- `country_taxa`: Polish membership status, supporting source, dates, and review state.
- `observations`: source identifiers, taxon, pseudonymous observer key, timestamps, quality grade, safe geography, captive/wild status, source URL, and raw checksum.
- `media`: observation, source media ID, URLs, creator, license, dimensions, MIME type, relative local path, hashes, validation status, and rejection reason.
- `duplicate_groups`: members, match method, score, canonical media, and review state.
- `evidence_sources`: bibliographic identifiers, title, authorship, publication date, source type, URL, and access date.
- `evidence_claims`: evidence source, taxon, geography, supported claim, locator, and curator notes.
- `danger_assessments`: taxon, geographic scope, medical-significance category, exposure context, rationale, evidence set, reviewer, review date, confidence, and status.
- `review_events`: entity, proposed change, reviewer, timestamp, decision, and notes.
- `dataset_releases`: version, configuration hash, taxonomy and source snapshots, license profile, manifest checksums, local release path, and status.
- `dataset_members`: release, taxon or media member, inclusion state, and reason.

Do not silently overwrite reviewed taxonomy or danger assessments. Amend them through a new version with an auditable review event.

## 9. Local data layout and durability

Use content-addressed media paths and immutable release directories. The entire `data/` directory must be excluded from Git.

```text
data/
├── raw-metadata/<source>/<snapshot>/...
├── media/sha256/<first-2>/<next-2>/<sha256>.<ext>
├── work/
│   ├── spider-bench.sqlite
│   ├── backups/<timestamp>/spider-bench.sqlite.zst
│   ├── downloads/
│   ├── logs/
│   └── reports/
└── releases/polish-spiders/<version>/
    ├── COMPLETE
    ├── release.json
    ├── taxa.parquet
    ├── media.parquet
    ├── attribution.parquet
    ├── danger_assessments.parquet
    ├── evidence.parquet
    ├── checksums.sha256
    └── reports/
```

Create consistent SQLite backups through the SQLite backup API and export normalized Parquet checkpoints after meaningful stages. Do not copy a live database file directly. Release building must write to a temporary sibling directory, verify it, rename it atomically to the final version path, and create `COMPLETE` last. Never modify a completed release in place.

The tool should check available disk space before downloads, support an alternate local data root, and report expected and actual storage use. Backing up `data/` to another disk is recommended operationally but is not required by this phase.

## 10. Collection and review workflow

### Stage A: feasibility audit

Before building all adapters:

1. Select the primary Polish checklist and confirm its reuse terms.
2. Confirm permitted access to a pinned taxonomic source.
3. Run metadata-only coverage queries for candidate image sources.
4. Count licensed observations, unique observers, years, regions, and usable resolutions per taxon.
5. Identify suitable medical-evidence sources and at least one qualified reviewer.
6. Decide whether image bytes may be redistributed or only retained privately.
7. Publish a short go/no-go report and revise volume targets from evidence.

### Stage B: taxonomy inventory

1. Ingest versioned Polish checklist inputs and the pinned taxonomy snapshot.
2. Normalize names, authorship, and ranks.
3. Reconcile synonyms without discarding source names.
4. Report unresolved names, membership conflicts, and doubtful records.
5. Require manual review for ambiguous mappings.

### Stage C: image discovery

1. Scan source metadata without downloading full image bytes.
2. Store source records idempotently under source-specific unique constraints.
3. Apply geography, license, and taxon filters.
4. Generate coverage reports before choosing image-volume targets.
5. Select images while limiting domination by one observation, observer, location, or season.

### Stage D: acquisition and validation

1. Download only selected, licensed media from approved hosts.
2. Stream downloads while calculating SHA-256.
3. Validate MIME type, dimensions, decodability, and safe file extension.
4. Reject HTML error pages, corrupt files, unsupported formats, extremely small images, and near-empty images.
5. Store each unique byte object once under its content-addressed local path.
6. Persist success only after verifying the stored checksum and metadata.
7. Retain normalized failure codes and retry only transient failures automatically.

### Stage E: deduplication and image review

Detect duplicates using source links, identifiers, SHA-256, EXIF metadata, perceptual hashes, and optionally embedding similarity for cropped or re-encoded copies.

Review should record spider presence, taxon-label correctness, most specific visibly supportable rank, image quality, occlusion, life stage and sex when supported, and an inclusion decision with reason. These fields improve dataset quality but are not benchmark annotations in this phase.

### Stage F: danger evidence and assessment

1. Define and version the review rubric before labeling taxa.
2. Import bibliographic records and structured claims.
3. Draft one Poland-specific assessment per applicable accepted taxon.
4. Require citations and a written rationale for every non-default claim.
5. Send conflicting, severe, or uncertain assessments to qualified review.
6. Record reviewer identity internally, decision date, and adjudication history.
7. Mark unreviewed assessments explicitly; never convert missing data to `none_known`.

### Stage G: release

1. Build a candidate release from pinned source snapshots and reviewed records.
2. Generate attribution, coverage, conflict, duplicate, rejection, and danger-review reports.
3. Verify all object references, checksums, licenses, citations, and schema constraints.
4. Write all immutable release objects.
5. Write the final completion marker only after verification succeeds.

## 11. Release validation

Publication must fail when:

- an included image lacks provenance, required creator data, or an accepted license;
- a referenced local media file is missing or its checksum differs;
- a taxon is absent from the pinned taxonomy snapshot;
- duplicate records are presented as distinct canonical images;
- a danger assessment has an invalid category, missing geography, missing review state, or unsupported evidence reference;
- missing danger evidence has been represented as `none_known`;
- required manifests or reports do not match their recorded checksums; or
- an immutable release path already contains conflicting content.

A material change to membership, taxonomy, images, attribution, evidence, or danger assessment requires a new dataset version.

## 12. Testing strategy

Unit tests cover configuration, license policies, taxonomy reconciliation, storage keys, hashing, duplicate grouping, source transformations, evidence and danger schemas, attribution, and checksums.

Contract tests use sanitized fixtures for every external source. Live checks are optional; normal tests must not depend on external services.

Integration tests run the full workflow against a temporary local data directory and SQLite database. They cover interruption, resume, retries, corrupt media, duplicates, license rejection, taxonomy conflicts, incomplete danger evidence, and failed release validation.

## 13. Security, privacy, and operations

- Keep the complete local data root, SQLite files, downloaded images, temporary files, and logs out of Git.
- Never commit credentials, signed URLs, tokens, or private host configuration.
- Restrict downloads to approved source hosts and validate redirected URLs.
- Pseudonymize observer identifiers in public exports unless attribution requires disclosure.
- Coarsen or omit coordinates for sensitive taxa and honor source geoprivacy.
- Use a run lease so two processes cannot mutate the same SQLite workspace.
- Persist queues and cursors transactionally and create local checkpoints after meaningful stages.
- Emit structured logs without credentials or unnecessary personal data.
- Require an explicit record cap or accepted cost estimate for large operations.
- Document takedown, correction, and license-change procedures.

## 14. Delivery milestones

### Milestone 0: decisions and feasibility

Resolve the checklist, taxonomy access, license profile, redistribution policy, local data root, and reviewer availability. Complete a metadata-only coverage audit and a five-species vertical slice.

Acceptance criteria:

- Data and image-volume targets are supported by observed coverage.
- At least one danger assessment passes the proposed review workflow.
- The vertical slice produces valid taxonomy, media, attribution, evidence, and release records without committing images or secrets.

### Milestone 1: taxonomy and discovery

Implement checklist and taxonomy ingestion, iNaturalist discovery, and coverage/conflict reports.

Acceptance criteria: the Polish inventory is reproducible, synonyms and conflicts are preserved, and licensed coverage is reported per taxon.

### Milestone 2: licensed image pipeline

Implement selection, download, validation, hashing, local content-addressed storage, attribution, resume, and checkpointing.

Acceptance criteria: a bounded local run survives restart without duplicate work, and every accepted file has a verified checksum, provenance, creator, and license.

### Milestone 3: supplementary sources and deduplication

Add GBIF and optional Commons adapters plus cross-source identity, exact hashing, perceptual hashing, and duplicate reports.

Acceptance criteria: republished records are recognized, ambiguous licenses enter review, and canonical images retain every known source relationship.

### Milestone 4: danger assessments

Finalize the rubric and evidence schema, import cited evidence, and build review and adjudication.

Acceptance criteria:

- Every assessment records category, geography, rationale, evidence, date, and status.
- Missing evidence is distinguishable from evidence of low significance.
- No assessment is generated automatically from venom presence or appearance.

### Milestone 5: immutable dataset release

Build, verify, and publish the first versioned dataset release.

Acceptance criteria: all taxonomy, media, license, attribution, evidence, and checksum gates pass; known gaps are documented; and rebuilding from identical inputs produces identical manifests.

## 15. Decisions required before implementation

The implementation must not guess:

1. The primary authority for membership in the Polish fauna.
2. The permitted, versioned method of accessing World Spider Catalog data.
3. The image license profile and whether ShareAlike or non-commercial media are allowed.
4. Whether image bytes may be redistributed or only stored privately and referenced.
5. Which medical and toxicological sources are acceptable evidence.
6. Who is qualified to review taxonomy and danger assessments.
7. Whether one or two reviewers are required for medically significant or disputed assessments.
8. The local data root, available disk capacity, backup location, and retention policy.
9. Whether precise coordinates may be retained privately or must be discarded after geographic normalization.
10. The release audience: internal research, public non-commercial use, or unrestricted reuse.

Recommended starting defaults are a gitignored `data/` directory, medium or large source images rather than originals, a conservative license profile, no public image redistribution until legal review, and a five-species vertical slice before large-scale collection.

## 16. Definition of done

The first phase is complete when a clean local checkout with documented configuration can:

1. Reconstruct a versioned Polish spider inventory and pinned taxonomy.
2. Discover licensed image candidates from approved sources.
3. Download, validate, deduplicate, and store selected images under content-addressed local paths.
4. Resume after interruption without duplicating records or objects.
5. Produce complete provenance, attribution, license, taxonomy, quality, coverage, and duplicate reports.
6. Store evidence-backed, Poland-specific danger assessments with explicit review status and uncertainty.
7. Verify every included object, license, citation reference, and schema constraint.
8. Publish an immutable dataset release with checksums and reproducibility metadata.
9. Reproduce identical manifests from pinned inputs, configuration, code commit, and release policy.

Benchmarking, model adapters, evaluation metrics, dataset partitions, and leaderboards remain possible future phases but are outside this plan.
