# Using Spider Bench

[Back to repository](../../README.md)

Python **3.12+**. Run these commands **from the repository root**.

## Install and choose a model

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
spider-bench benchmark models
```

Model settings live in [configs/models](../../configs/models). Export the matching credentials before running.

| CLI model ID | Required setup | Optional overrides |
| :--- | :--- | :--- |
| `luna`, `terra`, `sol`, `astra` | Matching `*_API_KEY` and `*_BASE_URL` (e.g. `LUNA_API_KEY`, `LUNA_BASE_URL`) | Matching `*_MODEL` deployment name |
| `gemini` | `GEMINI_API_KEY` | `GEMINI_BASE_URL`, `GEMINI_MODEL` |
| `deepseek`, `muse`, `grok`, `glm` | `OPENROUTER_API_KEY` | `OPENROUTER_BASE_URL`; `DEEPSEEK_MODEL`, `MUSE_MODEL`, `GROK_MODEL`, or `GLM_MODEL` |
| `fable` | Standard AWS credential chain: environment, profile, SSO, or IAM role | `FABLE_REGION`, `FABLE_MODEL` |

For Azure, set `*_BASE_URL` to `https://YOUR-RESOURCE.openai.azure.com/openai/v1`
and `*_MODEL` to your deployment name. For OpenAI, use `https://api.openai.com/v1`.
Muse's Contributor tier requires accepting its provider data-use conditions in OpenRouter.

The CLI reads environment variables; load `.env` files into your shell before running.

## Start with a pilot

With the chosen provider credentials loaded:

```bash
spider-bench benchmark run \
  --model luna --effort medium \
  --max-tasks 10 --max-cost 2 --concurrency 3 \
  --seed 42 --sample-seed 42 --run-id luna-medium-pilot

spider-bench benchmark score --run-id luna-medium-pilot
spider-bench benchmark report --run-id luna-medium-pilot
```

Use a new run ID for each experiment. Inspect `status_counts`, token usage, and cost before a larger run.

## Dataset commands

```bash
spider-bench benchmark sync --suite species-id-v5
spider-bench benchmark validate --suite species-id-v5
spider-bench benchmark sync --suite species-id-v5 --archive
```

## Reproduce the published 2,000-photo setup

This uses the published subset, seeds, image condition, and **16,000-token completion ceiling**. Adjust `--model`, `--effort`, and `--max-cost` for your run.

```bash
spider-bench benchmark run \
  --model luna --effort medium \
  --max-tasks 2000 --max-cost 10 --concurrency 10 \
  --seed 42 --sample-seed 42 --run-id luna-medium-2000

spider-bench benchmark score --run-id luna-medium-2000
spider-bench benchmark report --run-id luna-medium-2000

spider-bench benchmark leaderboard \
  --tasks-hash f9d9061c790f4c98de707be7e39270c87dfff2a025b162f535ec98d271071545 \
  --condition image
```

`--max-tasks 2000` selects the published subset; the full suite contains 2,183 photos.

The leaderboard includes every scored run, with status and failure counts.

`--effort` supports `low`, `medium`, and `high`, depending on the provider. `--seed` verifies the frozen choices; `--sample-seed` selects the subset. `--model-seed` sets the provider RNG seed where supported.

`--max-cost` stops new requests when estimated spending reaches the budget; in-flight requests finish. Only transport failures are retried.

## Inspect results and resume

Each run writes to `data/benchmarks/runs/<run-id>/`:

| File | Contents |
| :--- | :--- |
| `manifest.json` | Model, settings, task hash, code provenance, completion status, usage, and estimated cost |
| `suite-manifest.json` | Frozen source-suite provenance |
| `tasks.jsonl` | Exact images, prompts, ordered choices, labels, attribution, and licenses |
| `predictions.jsonl` | Answers, finish reasons, errors, attempts, usage, and provider metadata |
| `scores.json` | Accuracy, failure counts, and execution validity; created by `benchmark score` |
| `report.html` | Individual examples and results; created by `benchmark report` |

Score saved answers offline. Open `report.html` locally in a browser.

For an interrupted run, repeat its original command with the same run ID and
configuration. Resume is enabled by default and skips saved rows. Changing the
budget, concurrency, model settings, task snapshot, or benchmark code changes the
run fingerprint and is rejected. Completed runs require a new run ID to rerun.

For a no-image control, use a new run ID and add `--image-source none` to the same
command. Compare it separately with `benchmark leaderboard --condition no_image`
and the same task hash.

Photo attribution and licenses are preserved in the tasks. See
[licensing](../../docs/licensing.md) for the terms recorded for these images.

[Benchmark documentation](../../docs/README.md) ·
[Full benchmark methodology](../../docs/benchmark-v5.md) ·
[Data sources](../../docs/data-sources.md)
