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


def render_page(runs: list[dict[str, Any]], title: str = "Spider benchmark leaderboard") -> str:
    """Self-contained HTML: top-1/top-5 grouped bars (inline SVG) + table. No deps."""
    _, rows = render_leaderboard(runs)
    W, BH, GAP = 640, 26, 14
    H = max(60, len(rows) * (2 * BH + GAP) + 50)
    bars = []
    y = 40
    for r in rows:
        label = f"{r['model']} ({r['run_id']})"
        for val, color in ((r["top1"], "#2563eb"), (r["top5"], "#93c5f5")):
            w = max(2, round(val * (W - 220)))
            bars.append(
                f'<text x="0" y="{y + 17}" font-size="12">{label if color == "#2563eb" else ""}</text>'
                f'<rect x="210" y="{y}" width="{w}" height="18" fill="{color}"/>'
                f'<text x="{215 + w}" y="{y + 14}" font-size="12">{val:.3f}</text>')
            y += BH if color == "#2563eb" else BH + GAP
    table_rows = "\n".join(
        f"<tr><td>{i}</td><td>{r['run_id']}</td><td>{r['model']}</td><td>{r['suite']}</td>"
        f"<td>{r['tasks']}</td><td>{r['top1']:.4f}</td><td>{r['top5']:.4f}</td>"
        f"<td>{r['errors']}</td><td>{r['cost_usd'] or '-'}</td></tr>"
        for i, r in enumerate(rows, 1))
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:0 auto;padding:16px}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:6px;font-size:14px}"
        ".legend{font-size:13px;color:#444}</style></head><body>"
        f"<h1>{title}</h1>"
        "<p class='legend'><span style='color:#2563eb'>&#9632;</span> top-1 &nbsp;"
        "<span style='color:#93c5f5'>&#9632;</span> top-5 (closed-set species ID)</p>"
        f"<svg width='{W + 60}' height='{H}' role='img'>{''.join(bars)}</svg>"
        "<h2>Runs</h2>"
        "<table><tr><th>#</th><th>run</th><th>model</th><th>suite</th><th>tasks</th>"
        "<th>top-1</th><th>top-5</th><th>errors</th><th>cost USD</th></tr>"
        f"{table_rows}</table></body></html>\n"
    )
