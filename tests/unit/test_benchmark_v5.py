import base64
import hashlib
import io
import threading

import pytest
from PIL import Image

from spider_bench.benchmark.api_adapter import OpenAICompatAdapter, match_candidate
from spider_bench.benchmark.bedrock_adapter import BedrockAdapter
from spider_bench.benchmark.prepare import verify_observation
from spider_bench.benchmark.protocol import mixed_candidates, prepare_image, sample_tasks
from spider_bench.benchmark.runner import read_predictions, run_tasks
from spider_bench.benchmark.scorer import score
from spider_bench.benchmark.tasks import tasks_hash


def tasks(n=4):
    return [{"task_id": f"task-{i}", "image_sha256": str(i) * 64,
             "correct_taxon": "Aa a" if i < 2 else "Bb b", "candidates": ["Aa a", "Bb b"],
             "meta": {"family": "F"}} for i in range(n)]


def config(max_output_tokens=16000):
    return {"id": "test", "model": "test", "base_url": "https://example.invalid",
            "max_output_tokens": max_output_tokens, "token_param": "max_completion_tokens",
            "structured_output": False, "price_per_1k_requests": 0,
            "price_input_1k_tokens": 0, "price_output_1k_tokens": 0}


def test_mixed_shortlist_composition_order_and_seed():
    taxa = {f"Genus{i} species": "F1" if i < 30 else "F2" for i in range(100)}
    names = mixed_candidates("Genus0 species", taxa, "row-123", seed=42)
    assert len(names) == len(set(names)) == 20
    assert names.count("Genus0 species") == 1
    assert sum(taxa[n] == "F1" for n in names) == 10  # correct plus 9
    assert sum(taxa[n] == "F2" for n in names) == 10
    for _ in range(5):
        assert names == mixed_candidates("Genus0 species", dict(reversed(list(taxa.items()))), "row-123", 42)
    assert names != mixed_candidates("Genus0 species", taxa, "row-123", 43)
    positions = {mixed_candidates("Genus0 species", taxa, str(i)).index("Genus0 species") for i in range(200)}
    assert positions == set(range(20))


def test_shortlist_small_family_and_small_pool():
    taxa = {"Aa a": "A", **{f"Bb s{i}": "B" for i in range(30)}}
    assert len(mixed_candidates("Aa a", taxa, "row")) == 20
    names = mixed_candidates("Aa a", {"Aa a": "A", "Bb b": "B"}, "row")
    assert set(names) == {"Aa a", "Bb b"}
    with pytest.raises(ValueError):
        mixed_candidates("Aa a", taxa, "row", size=800)


def test_sampling_spreads_species_instead_of_alphabetical_prefix():
    rows = [{"task_id": f"{s}-{i}", "correct_taxon": s} for s in "ABCDE" for i in range(4)]
    chosen = sample_tasks(rows, 5, 42)
    assert {r["correct_taxon"] for r in chosen} == set("ABCDE")
    assert chosen == sample_tasks(list(reversed(rows)), 5, 42)


def test_image_policy_strips_metadata_and_deduplicates_encodings():
    image = Image.new("RGB", (2000, 1000), "green")
    exif = Image.Exif()
    exif[270] = "answer secret"
    raw = io.BytesIO()
    image.save(raw, "PNG", exif=exif)
    data, meta = prepare_image(raw.getvalue())
    with Image.open(io.BytesIO(data)) as normalized:
        assert normalized.size == (1536, 768)
        assert normalized.format == "JPEG" and not normalized.getexif()
    assert len(data) < 3_000_000 and meta["sha256"] == hashlib.sha256(data).hexdigest()
    # Different container bytes, identical original pixels.
    bmp = io.BytesIO()
    image.save(bmp, "BMP")
    assert prepare_image(bmp.getvalue())[1]["pixel_sha256"] == meta["pixel_sha256"]
    tiny = io.BytesIO()
    Image.new("RGB", (75, 75)).save(tiny, "PNG")
    with pytest.raises(ValueError, match="resolution"):
        prepare_image(tiny.getvalue())


def test_source_label_and_photo_must_match_not_just_search_query():
    task = {"correct_taxon": "Cybaeus tetricus"}
    media = {"source_observation_id": "1", "source_media_id": "2"}
    obs = {"id": 1, "quality_grade": "research", "taxon": {"name": "Callobius claustrarius", "rank": "species"},
           "photos": [{"id": 2, "license_code": "cc-by"}]}
    assert verify_observation(task, media, obs, {"CC-BY-4.0"})[1] == "source_label_mismatch"
    obs["taxon"]["name"] = "Cybaeus tetricus"
    assert verify_observation(task, media, obs, {"CC-BY-4.0"})[1] is None
    assert verify_observation(task, {**media, "source_media_id": "3"}, obs, {"CC-BY-4.0"})[1] == "photo_not_in_observation"
    obs["quality_grade"] = "needs_id"
    assert verify_observation(task, media, obs, {"CC-BY-4.0"})[1] == "not_research_grade"


@pytest.mark.parametrize("limit", [5000, 16000])
def test_blank_at_token_limit_is_recorded_and_not_a_success(tmp_path, limit):
    def post(url, body, headers):
        assert body["max_completion_tokens"] == limit
        return {"id": "response1", "model": "returned-model", "choices": [{
            "message": {"content": ""}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": limit,
                      "completion_tokens_details": {"reasoning_tokens": limit}}}
    rows = tasks(1)
    path = tmp_path / "predictions.jsonl"
    run_tasks(rows, OpenAICompatAdapter(config(limit), post=post), path, loader=lambda t: b"image")
    preds = read_predictions(path)
    assert preds[0]["status"] == "truncated" and preds[0]["finish_reason"] == "length"
    assert preds[0]["provider"]["reasoning_tokens"] == limit
    result = score(rows, preds)
    assert result["status_counts"] == {"truncated": 1}
    assert result["errors"] == 1 and result["execution_valid"] and result["top1"] == 0


def test_exact_prepared_bytes_and_no_image_control():
    seen = []
    def post(url, body, headers):
        seen.append(body)
        return {"choices": [{"message": {"content": "Aa a"}, "finish_reason": "stop"}]}
    adapter = OpenAICompatAdapter(config(), post=post)
    context = {"candidates": ["Aa a"], "image_public_url": "https://should-not-be-fetched/image"}
    adapter.predict(b"same prepared bytes", context)
    parts = seen[-1]["messages"][1]["content"]
    assert base64.b64decode(parts[1]["image_url"]["url"].split(",")[1]) == b"same prepared bytes"
    adapter.predict(b"", context)
    assert [p["type"] for p in seen[-1]["messages"][1]["content"]] == ["text"]


def test_bedrock_concatenates_text_blocks_and_retains_stop_reason():
    class Client:
        def converse(self, **kwargs):
            assert kwargs["inferenceConfig"]["maxTokens"] == 16000
            assert len(kwargs["messages"][0]["content"]) == 1  # no-image control
            return {"stopReason": "end_turn", "output": {"message": {"content": [
                {"reasoningContent": {"reasoningText": {"text": "trace"}}},
                {"text": "<SPIDER_"}, {"text": "NAME>Aa a</SPIDER_NAME>"}]}}}
    pred, info = BedrockAdapter(config(), client=Client()).predict(b"", {"candidates": ["Aa a"]})
    assert pred[0]["taxon"] == "Aa a" and info["finish_reason"] == "end_turn"
    assert info["reasoning"] == "trace"


def test_parser_never_selects_last_mentioned_name():
    names = ["Aa a", "Bb b"]
    assert not match_candidate("Not Aa a. Maybe Bb b?", names)[1]
    assert not match_candidate("<SPIDER_NAME>Aa a</SPIDER_NAME><SPIDER_NAME>Bb b</SPIDER_NAME>", names)[1]
    assert match_candidate("<SPIDER_NAME>Bb b</SPIDER_NAME>", names) == ("Bb b", True)


def test_score_counts_missing_and_failures_in_every_denominator():
    rows = tasks()
    ps = [{"task_id": t["task_id"], "image_sha256": t["image_sha256"], "tasks_hash": tasks_hash(rows),
           "error": None, "finish_reason": "stop",
           "predictions": [{"taxon": t["correct_taxon"], "raw": t["correct_taxon"], "matched": True}]}
          for t in rows[:3]]
    ps[1]["predictions"][0] = {"taxon": "", "raw": "", "matched": False}
    ps[2]["error"] = "HTTP 500"
    s = score(rows, ps)
    assert s["top1"] == .25 and s["missing"] == 1 and s["errors"] == 2
    assert s["per_family"]["F"]["n"] == 4 and s["per_family"]["F"]["top1"] == .25
    assert "top5" not in s and not s["execution_valid"]
    with pytest.raises(ValueError, match="top-k"):
        score(rows, ps, topk=(1, 5))
    with pytest.raises(ValueError, match="duplicate"):
        score(rows, ps + ps[:1])
    with pytest.raises(ValueError, match="hash"):
        score(rows, [{**ps[0], "tasks_hash": "wrong"}])


def test_runner_does_not_expose_answer_metadata_or_copy_stale_error_usage(tmp_path):
    class Adapter:
        model_id = "test"
        last_usage = {"output_tokens": 9999}
        def predict(self, image_bytes, context):
            assert "correct_taxon" not in context and "Aa a" not in context["task_id"]
            raise RuntimeError("permanent failure")
    p = tmp_path / "predictions.jsonl"
    run_tasks(tasks(1), Adapter(), p, loader=lambda t: b"x")
    assert read_predictions(p)[0]["usage"] == {}
    with pytest.raises(ValueError, match="duplicate"):
        run_tasks(tasks(1) * 2, Adapter(), tmp_path / "duplicate.jsonl", loader=lambda t: b"x")


def test_budget_stop_drains_and_records_inflight_requests(tmp_path):
    barrier = threading.Barrier(2)
    class Adapter:
        model_id = "budget"
        n = 0
        def predict(self, image_bytes, context):
            barrier.wait(timeout=2)
            self.n += 1
            return [{"taxon": "Aa a"}]
        def estimated_cost(self):
            return self.n
    path = tmp_path / "predictions.jsonl"
    summary = run_tasks(tasks(), Adapter(), path, loader=lambda t: b"x", max_cost=1, max_workers=2)
    assert summary["stopped_early"] and len(read_predictions(path)) == 2


def test_default_cli_validates_v5_even_when_legacy_query_exists(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from spider_bench import cli
    from spider_bench.benchmark import prepare
    from spider_bench.benchmark import suite_storage
    from spider_bench.config import CountryConfig

    monkeypatch.chdir(tmp_path)
    suite = tmp_path / "data/benchmarks/species-id-v5"
    (suite / "query").mkdir(parents=True)
    (suite / "query/tasks.jsonl").write_text("legacy rows")
    checked = []

    def validate(directory, **kwargs):
        checked.append(directory.resolve())
        raise ValueError("invalid prepared data must stop the run")

    monkeypatch.setattr(prepare, "validate_suite", validate)
    restored = []
    monkeypatch.setattr(cli, "_cfg", lambda path: CountryConfig())
    monkeypatch.setattr(suite_storage, "restore_suite", lambda directory, **kwargs: restored.append(directory.resolve()))
    result = CliRunner().invoke(cli.app, ["benchmark", "run", "--model", "perfect-reference"])
    assert checked == [suite]
    assert restored == [suite]
    assert isinstance(result.exception, ValueError)
    assert "invalid prepared data" in str(result.exception)
    assert not (tmp_path / "data/benchmarks/runs").exists()


def test_cli_rejects_unvalidated_sibling_tasks(tmp_path):
    from typer.testing import CliRunner
    from spider_bench.cli import app

    unvalidated = tmp_path / "different-tasks.jsonl"
    unvalidated.write_text("[]")
    result = CliRunner().invoke(app, ["benchmark", "run", "--tasks", str(unvalidated)])
    assert result.exit_code == 2
    assert "prepared suite's tasks.jsonl" in result.output
