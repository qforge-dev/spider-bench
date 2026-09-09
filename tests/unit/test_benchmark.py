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


def test_split_is_disjoint_and_deterministic():
    from spider_bench.benchmark.split import split_gallery_query

    rows = [{"taxon": "Aa a", "sha256": f"{i:064d}", "observation_id": i,
             "s3_uri": f"s3://b/{i}", "family": "F1"} for i in range(6)]
    a = split_gallery_query(rows, query_per_taxon=2, seed=7)
    b = split_gallery_query(rows, query_per_taxon=2, seed=7)
    assert a == b
    assert a["gallery_n"] == 1 and a["query_n"] == 2
    g = a["gallery"][0]
    assert all(q["observation_id"] != g["observation_id"] and q["sha256"] != g["sha256"]
               for q in a["query"])


def test_collect_n_skips_known_observations():
    from spider_bench.media.collect_one import collect_n_candidates

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [
                {"id": 1, "quality_grade": "research", "user": {"login": "u"},
                 "photos": [{"id": 11, "license_code": "cc0",
                             "original_url": "https://static.inaturalist.org/11.jpg"}]},
                {"id": 2, "quality_grade": "research", "user": {"login": "u"},
                 "photos": [{"id": 22, "license_code": "cc0",
                             "original_url": "https://static.inaturalist.org/22.jpg"}]},
            ]}

    class Client:
        def get(self, url, params=None, timeout=None):
            return Resp()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import httpx
    _orig = httpx.Client
    httpx.Client = lambda *a, **k: Client()  # noqa: E731
    try:
        m = collect_n_candidates(["Aa a"], rate_limit=10000, per_species=10,
                                 skip_observation_ids={1})
    finally:
        httpx.Client = _orig
    assert [c["observation_id"] for c in m["candidates"]] == [2]


def test_registry_resolves_env_and_hides_secrets(tmp_path, monkeypatch):
    from spider_bench.benchmark.registry import describe_registry, load_registry, resolve_model

    (tmp_path / "m.yaml").write_text(
        "id: terra\nadapter: openai-compatible\nbase_url_env: T_BASE\n"
        "api_key_env: T_KEY\nmodel_env: T_MODEL\nmodel: placeholder\n"
        "price_per_1k_requests: 0.5\n")
    monkeypatch.setenv("T_KEY", "sekret")
    monkeypatch.setenv("T_MODEL", "terra-1")
    reg = load_registry(tmp_path)
    r = resolve_model(reg["terra"])
    assert r["model"] == "terra-1" and r["has_key"] is True
    assert "sekret" not in str(describe_registry(tmp_path))
    monkeypatch.delenv("T_KEY")
    try:
        resolve_model(reg["terra"])
        raise AssertionError("expected missing-key error")
    except RuntimeError:
        pass


def test_api_adapter_parses_and_tracks_cost(monkeypatch):
    from spider_bench.benchmark.api_adapter import OpenAICompatAdapter, match_candidate

    assert match_candidate("Araneus diadematus", ["Pisaura mirabilis", "Araneus diadematus"]) == ("Araneus diadematus", True)
    assert match_candidate("Araneus diadematus (nice spider)", ["Araneus diadematus"])[1] is True
    assert match_candidate("a mushroom", ["Araneus diadematus"])[1] is False

    calls = []

    def fake_post(url, body, headers):
        calls.append((url, body, headers))
        assert "sekret" not in str(body)
        return {"choices": [{"message": {"content": "Bb b"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 5}}

    monkeypatch.setenv("T_KEY", "sekret")
    a = OpenAICompatAdapter({"id": "t", "base_url": "https://x/v1", "model": "m",
                             "key_env": "T_KEY", "temperature": 0.0, "max_output_tokens": 10,
                             "price_per_1k_requests": 1.0, "price_input_1k_tokens": 2.0,
                             "price_output_1k_tokens": 4.0}, post=fake_post)
    preds = a.predict(b"img", {"candidates": ["Aa a", "Bb b"], "image_public_url": "https://x/i.jpg"})
    assert preds[0]["taxon"] == "Bb b" and preds[0]["matched"] is True
    assert a.totals == {"requests": 1, "input_tokens": 100, "output_tokens": 5}
    assert a.estimated_cost() == 1 / 1000 * 1.0 + 100 / 1000 * 2.0 + 5 / 1000 * 4.0
    assert "Authorization" in calls[0][2]


def test_api_version_appended_for_azure(monkeypatch):
    from spider_bench.benchmark.api_adapter import OpenAICompatAdapter

    seen = {}

    def fake_post(url, body, headers):
        seen["url"] = url
        return {"choices": [{"message": {"content": "Aa a"}}], "usage": {}}

    monkeypatch.setenv("T_KEY", "sekret")
    a = OpenAICompatAdapter({"id": "az", "base_url": "https://r.openai.azure.com/openai/deployments/d",
                             "model": "d", "key_env": "T_KEY", "temperature": 0.0,
                             "max_output_tokens": 10, "api_version": "2024-10-21",
                             "price_per_1k_requests": 0.0, "price_input_1k_tokens": 0.0,
                             "price_output_1k_tokens": 0.0}, post=fake_post)
    a.predict(b"", {"candidates": ["Aa a"]})
    assert seen["url"].endswith("/chat/completions?api-version=2024-10-21")


def test_budget_stops_run_early(tmp_path):
    from spider_bench.benchmark.runner import run_tasks

    class Priced:
        model_id = "priced"

        def __init__(self):
            self.n = 0

        def predict(self, image_bytes, context):
            self.n += 1
            return [{"taxon": "Aa a", "score": 1.0}]

        def estimated_cost(self):
            return self.n * 1.0

    tasks = _mini()
    out = tmp_path / "p.jsonl"
    s = run_tasks(tasks, Priced(), out, loader=lambda t: b"x", max_cost=1.0)
    assert s["wrote"] == 1 and s.get("stopped_early")


def test_leaderboard_sorts_by_top1(tmp_path):
    import json

    from spider_bench.benchmark.leaderboard import collect_runs, render_leaderboard

    for rid, top1 in (("r-good", 0.9), ("r-bad", 0.1)):
        d = tmp_path / rid
        d.mkdir()
        (d / "scores.json").write_text(json.dumps({"scored": 10, "top1": top1, "top5": 1.0, "errors": 0}))
        (d / "manifest.json").write_text(json.dumps({"model_id": rid, "suite": "s"}))
    md, rows = render_leaderboard(collect_runs(tmp_path))
    assert [r["run_id"] for r in rows] == ["r-good", "r-bad"]
    assert "| 1 | r-good |" in md
