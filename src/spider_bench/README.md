# Using Spider Bench

[Back to repository](../../README.md)

Spider Bench is a Python CLI for running and scoring spider
identification benchmarks. Run the commands below **from the repository root**,
not from this package directory. Python **3.12 or newer** is required.

## Install and choose a model

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
spider-bench benchmark models
```

Model settings live in [configs/models](../../configs/models). Credentials come
from the environment; the registry listing reports whether they are present
without printing their values.

| CLI model ID | Credentials | Optional overrides |
| :--- | :--- | :--- |
| `luna`, `terra`, `sol`, `astra` | `LUNA_API_KEY`, `TERRA_API_KEY`, `SOL_API_KEY`, `ASTRA_API_KEY` respectively | Matching `*_BASE_URL` and `*_MODEL` |
| `gemini` | `GEMINI_API_KEY` | `GEMINI_BASE_URL`, `GEMINI_MODEL` |
| `deepseek`, `muse`, `grok`, `glm` | `OPENROUTER_API_KEY` | `OPENROUTER_BASE_URL`; `DEEPSEEK_MODEL`, `MUSE_MODEL`, `GROK_MODEL`, or `GLM_MODEL` |
| `fable` | Standard AWS credential chain: environment, profile, SSO, or IAM role | `FABLE_REGION`, `FABLE_MODEL` |

Azure configs point to the original experiment's resource. Set the matching
`*_BASE_URL` and `*_MODEL` for your own Azure resource and deployment. Choose a
model whose account access is already configured. Muse's Contributor tier also
requires accepting its provider data-use conditions in OpenRouter.

Set credentials in your shell or secret manager. The CLI does **not** automatically
load `.env` files. Do not put keys in model YAML files or commit them to Git.

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

Use a new run ID for each new experiment. Check `status_counts`, `execution_valid`,
token usage, and estimated cost before starting a larger run. A successfully
parsed species name is not necessarily the correct answer.

## Dataset commands

```bash
spider-bench benchmark sync --suite species-id-v5
spider-bench benchmark validate --suite species-id-v5
spider-bench benchmark sync --suite species-id-v5 --archive
```

## Reproduce the published 2,000-photo setup

Keep the same task count, suite seed, sample seed, image condition, and
**16,000-token completion ceiling**. Change the model and reasoning effort to the
configuration you want to measure. The cost below is a budget guard for this Luna
example, not a quote for every model.

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

**Do not omit `--max-tasks 2000` when comparing with the published runs.** Omitting
it selects all 2,183 suite rows and produces a different task hash. The explicit
leaderboard hash above selects the published 2,000-row comparison.

The leaderboard includes every scored run in that comparison, including runs with
request errors, invalid responses, or other execution issues. It keeps separate
rows for each run and shows their status and failure counts.

The CLI supports `--effort low`, `medium`, and `high`; provider support varies.
DeepSeek uses `high` in the published run. Effort names do not mean equal compute
across providers. `--seed` verifies the frozen choices, while `--sample-seed`
controls the deterministic subset. Neither makes model responses deterministic.
`--model-seed` is separate and depends on provider support.

`--max-cost` uses configured price estimates and stops new dispatch after the
guard is reached. Already running requests finish, so it is not a hard billing
cap. A run stopped by the guard is incomplete. Keep transport retries separate
from model failures: empty, invalid, refused, and truncated answers are not retried.

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

Scoring existing run files is offline and requires no provider key. Reports use
public image links, so loading their photos requires network access. Download or
open `report.html` locally; GitHub displays its source rather than the rendered page.

For an interrupted run, repeat its original command with the same run ID and
configuration. Resume is enabled by default and skips saved rows. Changing the
budget, concurrency, model settings, task snapshot, or benchmark code changes the
run fingerprint and is rejected. Completed runs require a new run ID to rerun.

Saved execution-validity flags describe completion, unresolved transport errors,
and available finish reasons. They do not filter the published leaderboard: all
16 runs remain included, and failures count against accuracy.

For a no-image control, use a new run ID and add `--image-source none` to the same
command. Compare it separately with `benchmark leaderboard --condition no_image`
and the same task hash.

Photo attribution and licenses are preserved in the tasks. See
[licensing](../../docs/licensing.md) for the terms recorded for these images.

[Benchmark documentation](../../docs/README.md) ·
[Full benchmark methodology](../../docs/benchmark-v5.md) ·
[Data sources](../../docs/data-sources.md)
