"""Show every scored run on identical tasks and conditions, including failures."""
from __future__ import annotations

import html
import json
from pathlib import Path


def collect_runs(runs_dir: str | Path) -> list[dict]:
    out = []
    for d in sorted(Path(runs_dir).iterdir()):
        if not (d / "scores.json").exists() or not (d / "manifest.json").exists():
            continue
        s = json.loads((d / "scores.json").read_text())
        m = json.loads((d / "manifest.json").read_text())
        if s.get("tasks_hash") != m.get("tasks_hash"):
            raise ValueError(f"score/manifest mismatch: {d.name}")
        out.append({"run_id": d.name, "manifest": m, "scores": s})
    return out


def render_leaderboard(runs: list[dict]) -> tuple[str, list[dict]]:
    cohorts = {(r["manifest"].get("tasks_hash"), r["manifest"].get("condition", "image")) for r in runs}
    if len(cohorts) > 1:
        raise ValueError("different task snapshots or image/no-image conditions; filter runs before ranking")
    rows = sorted([{
        "run_id": r["run_id"], "model": r["manifest"].get("model_id", "?"),
        "effort": r["manifest"].get("adapter", {}).get("reasoning_effort", "default"),
        "suite": r["manifest"].get("suite", "?"), "tasks": r["scores"].get("tasks", r["scores"].get("scored", 0)),
        "top1": r["scores"].get("top1", 0.0),
        "macro_species_top1": r["scores"].get("macro_species_top1"),
        "answer_rate": r["scores"].get("answer_rate"), "errors": r["scores"].get("errors", 0),
        "status": r["manifest"].get("status", "unknown"),
        "execution_valid": r["scores"].get("execution_valid"),
        "cost_usd": r["manifest"].get("estimated_cost_usd")
    } for r in runs], key=lambda r: r["top1"], reverse=True)
    lines = ["# Benchmark results", "", "All scored runs on the selected task snapshot and input condition, including runs with failures.", "",
             "Descriptive scores; differences do not establish statistical significance.", "",
             "| rank | run | model | effort | status | tasks | top-1 | failures | estimated USD |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for i, r in enumerate(rows, 1):
        lines.append(f"| {i} | {r['run_id']} | {r['model']} | {r['effort']} | {r['status']} | {r['tasks']} | "
                     f"{r['top1']:.4f} | {r['errors']} | {r['cost_usd']} |")
    return "\n".join(lines) + "\n", rows


def render_page(runs: list[dict], title: str = "Spider benchmark results") -> str:
    _, rows = render_leaderboard(runs)
    bars = []
    for i, row in enumerate(rows):
        y = 30 + i * 32
        bars.append(f'<text x="0" y="{y+14}" font-size="12">{html.escape(row["model"])} '
                    f'{html.escape(str(row["effort"]))}</text><rect x="160" y="{y}" '
                    f'width="{400*row["top1"]}" height="20" fill="#2563eb"/>'
                    f'<text x="{165+400*row["top1"]}" y="{y+14}">{row["top1"]:.3f}</text>')
    body = "".join(f'<tr><td>{html.escape(row["run_id"])}</td><td>{html.escape(row["status"])}</td><td>{row["tasks"]}</td>'
                   f'<td>{row["top1"]:.4f}</td><td>{row["errors"]}</td></tr>' for row in rows)
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            '<style>body{font-family:system-ui;max-width:1000px;margin:auto;padding:24px}'
            'td,th{padding:8px;text-align:left}</style></head><body>'
            f'<h1>{html.escape(title)}</h1><p>All scored runs on the selected task snapshot and input condition, including runs with failures. '
            'Ranking is descriptive, not a significance test.</p><svg width="650" height="'
            f'{max(70,60+len(rows)*32)}">{"".join(bars)}</svg><table><tr><th>Run</th><th>Status</th><th>Tasks</th>'
            f'<th>Top-1</th><th>Failures</th></tr>{body}</table></body></html>')
