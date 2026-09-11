# Benchmark v5

**Nine models, 16 runs, the same 2,000 photos.** [Results and charts](../README.md) · [Full leaderboard](../data/benchmarks/leaderboard.md) · [Reproduction commands and task hash](../src/spider_bench/README.md#reproduce-the-published-2000-photo-setup)

## Setup

| Setting | Value |
| :--- | :--- |
| Photos per run | 2,000, sampled from the 2,183-photo v5 suite |
| Species and subspecies | 671 |
| Choices per photo | 20 scientific names |
| Suite / sample seed | 42 / 42 |
| Completion-token ceiling | 16,000 per response |
| Accuracy | Correct species / all 2,000 assigned photos |

Use `--max-tasks 2000` to match the published comparison. Omitting it evaluates the full suite.

## Images and labels

- **Source:** research-grade, wild iNaturalist observations with matching scientific names and photo IDs and checked licenses. Taxa come from a Polish checklist; photos are worldwide.
- **Selection:** 2,655 source records → 2,183 photos. Before model runs, 472 records were excluded: 367 not research grade, 53 requiring source review, 30 name mismatches, 19 wrong taxon rank, and 3 undersized images.
- **Preparation:** EXIF orientation applied, metadata removed, no upscaling; minimum short side 320 px, maximum long side 1,536 px, JPEG quality 90, under 3 MB. All models received the same prepared image bytes.
- **Deduplication:** observation IDs, decoded pixels, and perceptual similarity. Source records and image checksums are preserved.

## Task

Each photo comes with the correct taxon, up to nine same-family alternatives, and other-family names to reach 20 unique choices. Seeded selection and ordering are frozen across runs. The model returns one name in `<SPIDER_NAME>…</SPIDER_NAME>` tags; exact bare candidate names are also accepted. Ambiguous or multiple answers are rejected.

Calculated baselines: **5%** for uniform guessing; **9.93%** for a candidate-only largest-genus heuristic.

## Scoring and cost

All **16 runs remain included**. Wrong, missing, invalid, empty, refused, truncated, and transport-failed answers count as incorrect. Only transport failures were retried.

Hidden reasoning shares the completion ceiling where the API counts it that way.

Costs are estimates from recorded usage and configured prices. Muse Spark 1.3 uses Contributor pricing, which permits provider training and data use.

[Run artifacts](../data/benchmarks/runs) preserve exact tasks, predictions, settings, scores, and reports. Existing answers can be scored offline.
