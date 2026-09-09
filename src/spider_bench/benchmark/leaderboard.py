"""Static leaderboard generator (M3): run dirs -> markdown + json. Pure, offline."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def collect_runs(runs_dir: str | Path) -> list[dict[str, Any]]:
    """Gather {run_id, manifest, scores} for every run dir containing scores.json."""
    out = []
    for d in sorted(Path(runs_dir).iterdir()):
        if not d.is_dir():
            continue
        sj, mj = d / "scores.json", d / "manifest.json"
        if not sj.exists():
            continue
        scores = json.loads(sj.read_text(encoding="utf-8"))
        manifest = json.loads(mj.read_text(encoding="utf-8")) if mj.exists() else {}
        out.append({"run_id": d.name, "scores": scores, "manifest": manifest})
    return out


def render_leaderboard(runs: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Sort by top1 desc. Returns (markdown, rows)."""
    rows = sorted(
        ({"run_id": r["run_id"], "model": r["manifest"].get("model_id", "?"),
          "suite": r["manifest"].get("suite", "?"),
          "tasks": r["manifest"].get("tasks", r["scores"].get("scored", 0)),
          "top1": round(r["scores"].get("top1", 0.0), 4),
          "top5": round(r["scores"].get("top5", 0.0), 4),
          "errors": r["scores"].get("errors", 0),
          "cost_usd": r["manifest"].get("estimated_cost_usd")}
         for r in runs),
        key=lambda x: x["top1"], reverse=True)
    lines = ["# Leaderboard", "",
             "| rank | run | model | suite | tasks | top-1 | top-5 | errors | cost USD |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for i, r in enumerate(rows, 1):
        lines.append(f"| {i} | {r['run_id']} | {r['model']} | {r['suite']} | {r['tasks']} | "
                     f"{r['top1']:.4f} | {r['top5']:.4f} | {r['errors']} | {r['cost_usd'] or '-'} |")
    return "\n".join(lines) + "\n", rows
