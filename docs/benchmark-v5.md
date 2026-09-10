# Benchmark v5: protocol and fresh-run procedure

The v1–v4 runs are historical exploratory results. They must not be combined with v5.
The new suite is `data/benchmarks/species-id-v5`: 2,183 images covering 671 taxa,
prepared from the entire 2,655-row v2 query pool, not the first 2,000 alphabetical rows.

## Images and labels

Each included photo has a frozen iNaturalist observation response. Its returned
scientific name must exactly match the expected taxon, its rank must be species or
subspecies, its identification must be research grade, and the requested photo ID
must actually belong to that observation. Photo licenses are rechecked. Captive
observations are excluded. Search queries are never accepted as identity evidence.

The preparation removed 53 records requiring manual source review, 30 source-name
mismatches, 367 observations without research-grade identification, 19 records
without species/subspecies rank, and 3 images below the resolution threshold.
See `exclusions.jsonl` for row-level reasons. The exclusions were determined before
new model results; do not select exclusions based on which model answered correctly.

Unreviewed Wikimedia search results are excluded, including the previously detected
Callobius/Cybaeus mismatch. The collection command now puts Commons results in the
manual label-review queue instead of automatically assigning the searched species.

Images are fetched again at the large source size; thumbnails are never upscaled.
The source must have a short side of at least 320 pixels. Processing applies EXIF
orientation, removes metadata, preserves aspect ratio, caps the long side at 1536,
and emits JPEG quality 90 under 3 MB. A prepared image must still have a short side
of at least 320. Both provider adapters send these exact bytes. No model fetches a
different live image URL. Original and prepared checksums are stored.

Deduplication checks observation IDs, full decoded pixels and conservative
perceptual similarity (aHash and dHash Hamming distances <= 3, aspect ratio difference
< 0.02). Suspected near duplicates are excluded for later review, not silently
relabelled. The known PNG/JPEG duplicate was removed through source exclusion.

These checks verify provenance and technical quality, **not expert biological
identifiability from each photograph**. Research-grade community labels can still
be wrong, and some species require diagnostic anatomy. Public images may have
appeared in model training. Photos are worldwide; the taxon pool derives from the
Polish checklist. This is a zero-shot query experiment, not a claim of a private
training/test split.

## Exactly 20 choices in this frozen suite

For each row: the correct species, up to 9 randomly selected same-family species,
and enough other-family species to reach 20 choices (normally 10). If a pool is too
small, fill from the remaining unused candidates. There are no duplicates. The
implementation supports smaller pools with fewer than 20 taxa, but every v5 row
has exactly 20 choices.

Selection **and final ordering** use a seeded RNG keyed by the full prepared image
hash. The candidate pool is sorted before sampling. The source image identifies
the row; the model name never participates in selection. Seed 42 is frozen into
this suite. Thus all models receive byte-for-byte identical prompts and ordered
candidate lists for the same task. The correct choice is not fixed in one position.

`benchmark run --seed 42` checks the frozen suite seed; it does not regenerate lists.
To use different choices, prepare a separately named suite with a different seed.
`--model-seed` is a separate, optional provider RNG parameter. Bedrock does not
support that parameter in this adapter; it is rejected rather than falsely recorded
as applied. Identical tasks do not imply identical model responses between runs.

If `--max-tasks` is used, selection is a deterministic round-robin over shuffled
species and their images (`--sample-seed`, default 42). For the final experiment,
omit `--max-tasks` to evaluate all 2,183 rows. Always compare identical task hashes.

Taxonomic choices deliberately give clues. This is classification with a supplied
shortlist, not open-world identification. Uniform guessing is 5%; the saved
candidate-only largest-genus heuristic has expected accuracy about 9.90% here.
Include no-image controls; do not attribute all accuracy above 5% to vision.

## Model calls and scoring

All seven registered model configurations use a **fixed 16,000-completion-token
ceiling**. The completed 100-row Luna HIGH pilot had 2 truncated responses,
3 invalid answers and 16 correct answers. Average output usage, including reasoning,
was 2,298 tokens; estimated cost was $0.2973. Remaining truncations count as failed
answers. Keep the ceiling fixed for this experiment and do not selectively retry
those rows. Final comparisons use the same ceiling.

For Chat Completions this includes hidden reasoning and visible output; see the
[official parameter documentation](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).
A higher reasoning setting can still exhaust the ceiling. Stop reasons, raw answer
text, provider usage, reasoning-token counts where supplied, response identifiers,
and returned model identifiers are retained. Never treat a limit-hit response as
a normal completed identification. Increase the limit only as a new, predeclared
experiment; do not selectively rerun difficult rows until they produce an answer.

The parser accepts one explicit tagged answer or an exact bare candidate name.
It rejects ambiguous prose and multiple tagged answers. Bedrock text fragments
are joined before parsing. The parser never chooses whichever candidate happened
to be mentioned last in an explanation. A format-match flag is not confidence.

Only transport failures are retried (including HTTP 500 and connection resets),
up to the recorded retry limit. Truncated, empty and invalid completed answers are
not retried. In-flight requests are drained and checkpointed when a cost guard
stops dispatch. Provider transport timeouts are configured directly.

Primary accuracy is correct top-1 / **all assigned tasks**, with missing results,
transport failures, truncation, refusals, empty answers and invalid answers all
counting as unsuccessful. Report these categories separately and report answer
rate. Macro species accuracy is also available. Family slices use the same all-task
denominator. The HTML family metric is explicitly shortlist-assisted. There is no
reported top-5 because the protocol requests one answer. Answered-only accuracy
is secondary and must not replace the primary denominator.

A run is execution-valid when complete, free of unresolved transport errors and
supplied with stop reasons. Truncations, refusals, empty answers and invalid answers
remain measured outcomes: they count against accuracy without disqualifying the run. The leaderboard excludes
legacy/incomplete/invalid runs and refuses to rank different task snapshots or
image/no-image conditions together. These technical gates do not certify label
quality or statistical significance. Small score differences need paired analysis,
with clustering by species, and repeated runs to assess generation variability.

## Commands

`benchmark run` defaults to `species-id-v5`, restores it from S3 and validates its
frozen `tasks.jsonl`, source records and images before making any model calls. An explicit `--tasks`
must point to a prepared suite's `tasks.jsonl`. The legacy `run-dual` command is
disabled because its old Latin/English suites bypassed this protocol.

S3 is the permanent store: `s3://spiders-dataset-088543363904/poland/benchmarks/species-id-v5/`.
Prepared JPEGs use the existing `poland/media/sha256/` object layout. The suite includes
portable tasks, its manifest, source task pool, exclusions, validation summary, all
frozen observation records and downloaded originals. A SHA-256 inventory covers
every object. Publication uses conditional writes and writes `COMPLETE` last; an
interrupted upload cannot be used as a complete dataset.

Local `data/benchmarks/species-id-v5` is a restored copy. Assets are cached by hash
in `data/work/benchmark-s3-cache`; missing or corrupt cache files are downloaded
again and verified. Neither the task hash nor task contents depend on local paths.
The published task hash is `79a3edf7edd9a89676c9ff640872bbfa5d91d36a2594312d0552085967450f1e`.
It differs from the initial local preparation hash only because storage references
changed; images, task IDs, prompts, candidate lists and their order are identical.

No manual download is needed before a normal run. `benchmark sync` restores the
dataset explicitly; add `--archive` to download originals and excluded source
records too. `--cache-dir` selects a different cache for `run`, `sync` or `validate`.
`run --image-source local` and `validate --local` require an intact offline cache.
`run --image-source none` restores the same suite but sends no image to the provider.
The bucket's public objects can be restored without AWS credentials; writes require
the usual AWS credentials. Publication never changes bucket permissions.

From the repository root, with the normal provider credentials loaded in the shell:

```bash
spider-bench benchmark validate --suite species-id-v5

# First do a small provider smoke run. Budgets below are safety ceilings, not quotes.
spider-bench benchmark run --suite species-id-v5 --model luna --effort high --seed 42 --max-tasks 100 --max-cost 3 --run-id luna-v5-high-16k-pilot
spider-bench benchmark score --run-id luna-v5-high-16k-pilot

# Inspect status_counts and execution_valid before starting full runs.
spider-bench benchmark run --suite species-id-v5 --model luna --effort medium --seed 42 --max-cost 10 --run-id luna-v5-medium
spider-bench benchmark score --run-id luna-v5-medium
spider-bench benchmark report --run-id luna-v5-medium

spider-bench benchmark run --suite species-id-v5 --model fable --effort medium --seed 42 --max-cost 100 --run-id fable-v5-medium
spider-bench benchmark score --run-id fable-v5-medium

# Same tasks/prompt/effort, but no image part is sent to either provider.
spider-bench benchmark run --suite species-id-v5 --model luna --effort medium --seed 42 --image-source none --max-cost 10 --run-id luna-v5-medium-no-image
spider-bench benchmark score --run-id luna-v5-medium-no-image

spider-bench benchmark leaderboard --suite species-id-v5 --condition image
```

Repeat the declared configurations for additional models and effort settings. A
provider's word “high” does not imply equal computation to another provider's
“high.” Prices are configuration-based estimates, not verified invoices.
The token setting is a per-response ceiling, not a fixed charge. The separate
`--max-cost` guard can stop a run before all tasks finish if HIGH consumes more
tokens. Choose the full-run budget from the pilot's observed usage.

To prepare another version, use a new output name. Existing suites are never
silently overwritten. Source snapshots and downloaded originals are cached for
reproducibility and resumable preparation:

```bash
spider-bench benchmark prepare --out data/work/prepared/species-id-v6 --seed 43
spider-bench benchmark publish-suite --suite species-id-v6 --directory data/work/prepared/species-id-v6 --dry-run
spider-bench benchmark publish-suite --suite species-id-v6 --directory data/work/prepared/species-id-v6
spider-bench benchmark sync --suite species-id-v6
```

Run manifests are written **before** inference and pin task hashes, request
configuration, code content hash, git state, condition and timing. Resume rejects
changed configurations or task snapshots. Scoring and reports use each run's saved
tasks, never a mutable global suite. On another checkout, the runner restores the
same immutable S3 suite automatically. Historical local runs keep their original
snapshots and are not rewritten by dataset publication.
