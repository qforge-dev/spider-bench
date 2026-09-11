import json

from typer.testing import CliRunner

from spider_bench.benchmark.report import render_run_report
from spider_bench.cli import app


def test_offline_report_uses_snapshot_taxonomy_and_shows_photo_credits(tmp_path, monkeypatch):
    run = tmp_path / "runs" / "example"
    run.mkdir(parents=True)
    tasks = [
        {
            "task_id": str(i),
            "correct_taxon": name,
            "candidates": ["Araneus diadematus", "Araneus quadratus"],
            "image_sha256": str(i) * 64,
            "image_public_url": "https://example.org/spider.jpg",
            "meta": {
                "family": "Araneidae",
                "attribution": "Alice & Bob <photographers>",
                "license": "CC-BY-NC-4.0",
                "source_url": "https://www.inaturalist.org/observations/123",
            },
        }
        for i, name in enumerate(["Araneus diadematus", "Araneus quadratus"])
    ]
    predictions = [
        {
            "task_id": task["task_id"],
            "finish_reason": "stop",
            "predictions": [{"taxon": "Araneus diadematus", "matched": True}],
        }
        for task in tasks
    ]
    (run / "tasks.jsonl").write_text("\n".join(json.dumps(t) for t in tasks))
    (run / "predictions.jsonl").write_text("\n".join(json.dumps(p) for p in predictions))
    (run / "manifest.json").write_text(json.dumps({"model_id": "example", "run_id": "example"}))
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["benchmark", "report", "--run-id", "example",
                                     "--runs-dir", str(run.parent)])

    assert result.exit_code == 0, result.output
    report = (run / "report.html").read_text()
    assert "top-1 50.0%" in report
    assert "family 100.0%" in report
    assert "Alice &amp; Bob &lt;photographers&gt;" in report
    assert "https://www.inaturalist.org/observations/123" in report
    assert "https://creativecommons.org/licenses/by-nc/4.0/" in report
    assert "EXIF-oriented, metadata-stripped" in report


def test_report_does_not_treat_missing_family_taxonomy_as_wrong_answer():
    tasks = [{"task_id": "1", "correct_taxon": "Araneus diadematus",
              "candidates": ["Araneus diadematus", "Araneus quadratus"],
              "image_sha256": "a" * 64, "meta": {"family": "Araneidae"}}]
    predictions = [{"task_id": "1", "finish_reason": "stop",
                    "predictions": [{"taxon": "Araneus quadratus", "matched": True}]}]
    report = render_run_report(tasks, predictions, {})
    assert "family n/a" in report
    assert "Family taxonomy unavailable for 1 answers" in report
