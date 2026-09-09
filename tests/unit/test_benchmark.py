"""M0/M1 benchmark tests: tasks determinism, runner isolation, scorer math (offline)."""
from spider_bench.benchmark.adapters import ConstantAdapter, PerfectAdapter
from spider_bench.benchmark.runner import run_tasks
from spider_bench.benchmark.scorer import score
from spider_bench.benchmark.tasks import build_tasks, tasks_hash


def _mini():
    taxa = [{"taxon": "Aa a", "family": "F1"}, {"taxon": "Bb b", "family": "F1"},
            {"taxon": "Cc c", "family": "F2"}]
    media = [{"taxon": "Aa a", "sha256": "a" * 64, "s3_uri": "s3://b/a",
              "public_url": "https://x/a", "country": "PL"},
             {"taxon": "Bb b", "sha256": "b" * 64, "s3_uri": "s3://b/b",
              "public_url": "https://x/b", "country": "PL"}]
    return build_tasks(taxa, media)


def test_build_tasks_deterministic_and_sorted():
    t1, t2 = _mini(), _mini()
    assert tasks_hash(t1) == tasks_hash(t2)
    assert [t["task_id"] for t in t1] == sorted(t["task_id"] for t in t1)
    assert len(t1) == 2  # imaged-only drops Cc c


def test_perfect_top1_and_constant_floor(tmp_path):
    tasks = _mini()
    out = tmp_path / "p.jsonl"
    run_tasks(tasks, PerfectAdapter(), out, loader=lambda t: b"img")
    run_tasks(tasks, ConstantAdapter("Aa a"), tmp_path / "c.jsonl", loader=lambda t: b"img")
    import json

    perfect = [json.loads(line) for line in out.read_text().splitlines()]
    s = score(tasks, perfect)
    assert s["top1"] == 1.0 and s["top5"] == 1.0
    const = [json.loads(line) for line in (tmp_path / "c.jsonl").read_text().splitlines()]
    s2 = score(tasks, const)
    assert s2["top1"] == 0.5  # Aa a right, Bb b wrong


def test_runner_isolates_crashes_and_resumes(tmp_path):
    class Boom:
        model_id = "boom"

        def predict(self, image_bytes, context):
            raise RuntimeError("kaput")

    tasks = _mini()
    out = tmp_path / "b.jsonl"
    summary = run_tasks(tasks, Boom(), out, loader=lambda t: b"img")
    assert summary["errors"] == 2
    s = score(tasks, [__import__("json").loads(line) for line in out.read_text().splitlines()])
    assert s["top1"] == 0.0 and s["errors"] == 2
    # resume: second run writes nothing new
    summary2 = run_tasks(tasks, Boom(), out, loader=lambda t: b"img", resume=True)
    assert summary2["wrote"] == 0 and summary2["resumed"] == 2
