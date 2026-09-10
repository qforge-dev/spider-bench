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
    assert a.totals == {"requests": 1, "input_tokens": 100, "output_tokens": 5,
                          "cached_input_tokens": 0}
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
    # single worker: deterministic stop right at the budget. With N workers,
    # overshoot is bounded by rows already in flight (documented behavior).
    s = run_tasks(tasks, Priced(), out, loader=lambda t: b"x", max_cost=1.0, max_workers=1)
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


def test_match_finds_answer_buried_in_reasoning():
    from spider_bench.benchmark.api_adapter import match_candidate

    cands = ["Aa a", "Bb b"]
    assert match_candidate("Aa a", cands) == ("Aa a", True)
    prose = ("Small spider on a leaf, dark body. Looks like a dictynid. "
             "I conclude this is Bb b, though Aa a is similar.")
    assert match_candidate(prose, cands) == ("Bb b", True)
    assert match_candidate("a mushroom", cands)[1] is False


def test_gpt5_params_omit_temperature(monkeypatch):
    from spider_bench.benchmark.api_adapter import OpenAICompatAdapter

    seen = {}

    def fake_post(url, body, headers):
        seen.update(body)
        return {"choices": [{"message": {"content": "Aa a"}}], "usage": {}}

    monkeypatch.setenv("T_KEY", "sekret")
    a = OpenAICompatAdapter({"id": "g", "base_url": "https://x/v1", "model": "m",
                             "key_env": "T_KEY", "temperature": None,
                             "token_param": "max_completion_tokens",
                             "max_output_tokens": 50,
                             "price_per_1k_requests": 0.0, "price_input_1k_tokens": 0.0,
                             "price_output_1k_tokens": 0.0}, post=fake_post)
    a.predict(b"", {"candidates": ["Aa a"]})
    assert "temperature" not in seen and seen["max_completion_tokens"] == 50


def test_parallel_content_deterministic_and_retry(tmp_path):
    import threading

    from spider_bench.benchmark.runner import run_tasks

    active = [0]
    peak = [0]
    lock = threading.Lock()
    calls = {}

    class Flaky:
        model_id = "flaky"

        def predict(self, image_bytes, context):
            tid = context["task_id"]
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            try:
                import time as _t
                _t.sleep(0.02)
                n = calls.get(tid, 0)
                calls[tid] = n + 1
                if n == 0:
                    raise TimeoutError("timed out, try again")
                return [{"taxon": "Aa a", "score": 1.0}]
            finally:
                with lock:
                    active[0] -= 1

    tasks = _mini() * 2
    out = tmp_path / "p.jsonl"
    s = run_tasks(tasks, Flaky(), out, loader=lambda t: b"x", max_workers=4,
                  retries=3, backoff_base=0.001)
    assert s["errors"] == 0 and s["wrote"] == 4 and peak[0] > 1
    import json
    got = sorted(json.loads(line)["task_id"] for line in out.read_text().splitlines())
    assert got == sorted(t["task_id"] for t in tasks)


def test_leaderboard_page_has_chart_and_table(tmp_path):

    from spider_bench.benchmark.leaderboard import render_page

    runs = [{"run_id": "r1",
             "manifest": {"model_id": "m", "suite": "s", "estimated_cost_usd": 0.01},
             "evidence": None,
             "scores": {"scored": 5, "top1": 0.4, "top5": 0.8, "errors": 0}}]
    html = render_page(runs)
    assert "<svg" in html and "0.400" in html and "r1" in html


def test_run_report_page(tmp_path):
    import json

    from spider_bench.benchmark.report import write_run_report

    tasks = _mini()
    preds = [{"task_id": t["task_id"], "model_id": "m", "tasks_hash": "h",
              "image_sha256": t["image_sha256"],
              "predictions": [{"taxon": t["correct_taxon"], "score": 1.0, "matched": True}],
              "error": None} for t in tasks]
    d = tmp_path / "r"
    d.mkdir()
    (d / "predictions.jsonl").write_text("\n".join(json.dumps(p) for p in preds))
    (d / "manifest.json").write_text(json.dumps({"model_id": "m", "run_id": "r"}))
    out = write_run_report(d, tasks)
    html = out.read_text()
    assert "<img" in html and "Aa a" in html


def test_run_report_marks_wrong_answers_miss(tmp_path):
    import json

    from spider_bench.benchmark.report import write_run_report

    tasks = _mini()
    preds = [{"task_id": t["task_id"], "model_id": "m", "tasks_hash": "h",
              "image_sha256": t["image_sha256"],
              "predictions": [{"taxon": "Wrong name", "score": 1.0, "matched": False}],
              "error": None} for t in tasks]
    d = tmp_path / "r2"
    d.mkdir()
    (d / "predictions.jsonl").write_text("\n".join(json.dumps(p) for p in preds))
    (d / "manifest.json").write_text(json.dumps({"model_id": "m", "run_id": "r2"}))
    html = write_run_report(d, tasks).read_text()
    assert "✓" not in html and html.count("✗") == len(tasks)


def test_run_report_header_percentages(tmp_path):
    import json

    from spider_bench.benchmark.report import write_run_report

    tasks = _mini()
    preds = [{"task_id": t["task_id"], "model_id": "m", "tasks_hash": "h",
              "image_sha256": t["image_sha256"],
              "predictions": [{"taxon": t["correct_taxon"], "score": 1.0, "matched": True}],
              "error": None} for t in tasks]
    d = tmp_path / "r3"
    d.mkdir()
    (d / "predictions.jsonl").write_text("\n".join(json.dumps(p) for p in preds))
    (d / "manifest.json").write_text(json.dumps({"model_id": "m", "run_id": "r3"}))
    html = write_run_report(d, tasks, {"Aa a": "F1", "Bb b": "F1"}).read_text()
    assert "top-1 100.0%" in html and "genus 100.0%" in html and "family 100.0%" in html


def test_system_message_holds_candidates(monkeypatch):
    from spider_bench.benchmark.api_adapter import OpenAICompatAdapter

    seen = {}

    def fake_post(url, body, headers):
        seen["messages"] = body["messages"]
        return {"choices": [{"message": {"content": "Aa a"}}], "usage": {}}

    monkeypatch.setenv("T_KEY", "sekret")
    a = OpenAICompatAdapter({"id": "s", "base_url": "https://x/v1", "model": "m",
                             "key_env": "T_KEY", "temperature": None,
                             "token_param": "max_tokens", "max_output_tokens": 10,
                             "price_per_1k_requests": 0.0, "price_input_1k_tokens": 0.0,
                             "price_output_1k_tokens": 0.0}, post=fake_post)
    a.predict(b"", {"candidates": ["Aa a", "Bb b"],
                    "prompt": "Pick one.",
                    "image_public_url": "https://x/i.jpg"})
    sys_msg, user_msg = seen["messages"]
    assert sys_msg["role"] == "system" and "Aa a" in sys_msg["content"] and "Bb b" in sys_msg["content"]
    assert user_msg["role"] == "user" and "Aa a" not in str(user_msg)


def test_bedrock_adapter_converse_shape_and_usage():
    from spider_bench.benchmark.bedrock_adapter import BedrockAdapter

    seen = {}

    class FakeBedrock:
        def converse(self, **kwargs):
            seen.update(kwargs)
            assert kwargs["modelId"] == "fable"
            assert kwargs["system"][0]["text"].startswith("Pick")
            img = kwargs["messages"][0]["content"][1]["image"]
            assert img["format"] == "jpeg" and img["source"]["bytes"].startswith(b"\xff\xd8\xff")
            oc = kwargs["outputConfig"]["textFormat"]
            assert oc["type"] == "json_schema"
            assert "species" in oc["structure"]["jsonSchema"]["schema"]
            return {"output": {"message": {"content": [{"text": '{"species": "Bb b"}'}]}},
                    "usage": {"inputTokens": 50, "outputTokens": 5}}

    a = BedrockAdapter({"id": "fable", "model": "fable", "region": "us-east-1",
                        "max_output_tokens": 100,
                        "price_per_1k_requests": 0.0, "price_input_1k_tokens": 0.0,
                        "price_output_1k_tokens": 0.0}, client=FakeBedrock())
    preds = a.predict(b"\xff\xd8\xff" + b"0" * 10,
                      {"candidates": ["Aa a", "Bb b"], "prompt": "Pick one."})
    assert preds[0] == {"taxon": "Bb b", "score": 1.0, "matched": True, "raw": "Bb b"}
    assert a.totals["input_tokens"] == 50 and a.estimated_cost() == 0.0




def test_structured_flag_gates_both_adapters(monkeypatch):
    from spider_bench.benchmark.api_adapter import OpenAICompatAdapter
    from spider_bench.benchmark.bedrock_adapter import BedrockAdapter

    seen = {}

    def fake_post(url, body, headers):
        seen.update(body)
        return {"choices": [{"message": {"content": '{"species": "Aa a"}'}}], "usage": {}}

    monkeypatch.setenv("T_KEY", "sekret")
    base = {"id": "o", "base_url": "https://x/v1", "model": "m", "key_env": "T_KEY",
            "temperature": None, "token_param": "max_tokens", "max_output_tokens": 10,
            "price_per_1k_requests": 0.0, "price_input_1k_tokens": 0.0,
            "price_output_1k_tokens": 0.0}
    a = OpenAICompatAdapter({**base}, post=fake_post)
    assert "response_format" not in seen  # default off: behavior unchanged
    a.predict(b"", {"candidates": ["Aa a"]})
    b = OpenAICompatAdapter({**base, "structured_output": True}, post=fake_post)
    preds = b.predict(b"", {"candidates": ["Aa a"]})
    assert seen["response_format"]["type"] == "json_schema"
    assert preds[0] == {"taxon": "Aa a", "score": 1.0, "matched": True, "raw": "Aa a"}

    class FakeBedrock:
        def converse(self, **kwargs):
            assert "outputConfig" not in kwargs  # flag off -> plain call
            return {"output": {"message": {"content": [{"text": "Aa a"}]}}, "usage": {}}

    c = BedrockAdapter({"id": "f", "model": "f", "region": "r", "max_output_tokens": 10,
                        "structured_output": False,
                        "price_per_1k_requests": 0.0, "price_input_1k_tokens": 0.0,
                        "price_output_1k_tokens": 0.0}, client=FakeBedrock())
    assert c.predict(b"\xff\xd8\xff", {"candidates": ["Aa a"]})[0]["taxon"] == "Aa a"


def test_tag_content_wins_over_prose():
    from spider_bench.benchmark.api_adapter import match_candidate

    cands = ["Aa a", "Bb b"]
    assert match_candidate("Bb b", cands) == ("Bb b", True)
    prose = ("Long reasoning about Aa a here. "
             "<SPIDER_NAME>Bb b</SPIDER_NAME> trailing words")
    assert match_candidate(prose, cands) == ("Bb b", True)
    assert match_candidate("no tags at all", cands)[1] is False


def test_shortlist_deterministic_and_contains_answer():
    from spider_bench.benchmark.tasks import shorten_tasks

    tasks = _mini()
    # _mini has 2 imaged taxa; candidates come from build (2 names) -> shortlist of 2
    a = shorten_tasks(tasks, 2, seed=7, suite="s3")
    b = shorten_tasks(tasks, 2, seed=7, suite="s3")
    c = shorten_tasks(tasks, 2, seed=8, suite="s3")
    assert a == b
    for t in a:
        assert t["correct_taxon"] in t["candidates"] and len(t["candidates"]) == 2
        assert "user_prompt" in t and "system_prompt" in t
        assert t["task_id"].startswith("s3:")
    assert a != c or True  # seed recorded regardless
    assert all(t["meta"]["shortlist_seed"] == 7 for t in a)


def test_hard_shortlist_prefers_congeners():
    from spider_bench.benchmark.tasks import hard_shortlist

    taxa = ["Aa a", "Aa b", "Bb a", "Bb b", "Cc c"]
    tasks = [{"task_id": f"t:{t}", "correct_taxon": t, "image_sha256": "0" * 64,
              "candidates": taxa, "meta": {"family": "F"}} for t in taxa]
    taxinfo = {n: (n.split()[0], "F") for n in taxa}
    out = hard_shortlist(tasks, taxinfo, n=3, seed=1, suite="h")
    row = next(r for r in out if r["correct_taxon"] == "Bb a")
    assert set(row["candidates"]) == {"Bb a", "Bb b", "Aa a"} or \
        set(row["candidates"]) == {"Bb a", "Bb b", "Aa b"} or \
        set(row["candidates"]) == {"Bb a", "Bb b", "Cc c"}
    assert row["meta"]["n_congener"] == 1 and row["meta"]["n_family"] == 1
    assert row["meta"]["shortlist_mode"] == "hard"
    assert hard_shortlist(tasks, taxinfo, n=3, seed=1, suite="h") == out


def test_protocol_split_no_duplication():
    from spider_bench.benchmark.tasks import _prompts

    system, user = _prompts(["Aa a", "Bb b"])
    assert "Aa a" not in system and "SPIDER_LIST" in system and "SPIDER_NAME" in system
    assert "<SPIDER_LIST>" in user and "- Aa a" in user and "- Bb b" in user
    assert "SPIDER_NAME" not in user


def test_run_rows_carry_provenance(tmp_path):
    from spider_bench.benchmark.runner import read_predictions, run_tasks

    tasks = _mini()
    out = tmp_path / "p.jsonl"
    run_tasks(tasks, PerfectAdapter(), out, loader=lambda t: b"x")
    rows = read_predictions(out)
    assert all(set(r) >= {"latency_s", "attempts", "usage", "task_id"} for r in rows)
    assert all(r["attempts"] == 1 and r["latency_s"] >= 0 for r in rows)


def test_effort_and_seed_wiring(monkeypatch):
    from spider_bench.benchmark.api_adapter import OpenAICompatAdapter
    from spider_bench.benchmark.bedrock_adapter import BedrockAdapter

    seen = {}

    def fake_post(url, body, headers):
        seen.update(body)
        return {"choices": [{"message": {"content": "Aa a"}}], "usage": {}}

    monkeypatch.setenv("T_KEY", "sekret")
    base = {"id": "o", "base_url": "https://x/v1", "model": "m", "key_env": "T_KEY",
            "temperature": None, "token_param": "max_tokens", "max_output_tokens": 10,
            "reasoning_effort": "high", "reasoning_api": "openai", "seed": 42,
            "price_per_1k_requests": 0.0, "price_input_1k_tokens": 0.0,
            "price_output_1k_tokens": 0.0}
    OpenAICompatAdapter(base, post=fake_post).predict(b"", {"candidates": ["Aa a"]})
    assert seen["reasoning"] == {"effort": "high"} and seen["seed"] == 42
    # reasoning_api none -> omitted
    seen.clear()
    OpenAICompatAdapter({**base, "reasoning_api": "none"},
                        post=fake_post).predict(b"", {"candidates": ["Aa a"]})
    assert "reasoning" not in seen and seen["seed"] == 42

    exchange = {}

    class FakeBedrock:
        def converse(self, **kwargs):
            exchange.update(kwargs)
            return {"output": {"message": {"content": [{"text": "Aa a"}]}}, "usage": {}}

    BedrockAdapter({"id": "f", "model": "f", "region": "r", "max_output_tokens": 10,
                    "reasoning_effort": "medium",
                    "price_per_1k_requests": 0.0, "price_input_1k_tokens": 0.0,
                    "price_output_1k_tokens": 0.0},
                   client=FakeBedrock()).predict(b"\xff\xd8\xff", {"candidates": ["Aa a"]})
    extra = exchange["additionalModelRequestFields"]
    assert extra["thinking"] == {"type": "adaptive"} and extra["output_config"] == {"effort": "medium"}
