"""Per-run report (samples with previews, answers vs truth). Pure, offline."""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any


def render_run_report(tasks: list[dict[str, Any]], predictions: list[dict[str, Any]],
                      manifest: dict[str, Any]) -> str:
    """HTML grid: image thumb, correct vs predicted, matched flag. No deps."""
    by_id = {t["task_id"]: t for t in tasks}
    cards = []
    for p in predictions:
        t = by_id.get(p.get("task_id", ""), {})
        pred = (p.get("predictions") or [{}])[0]
        ok = pred.get("matched")
        img = t.get("image_public_url", "")
        cards.append(
            f"<div class='card {'ok' if ok else 'miss'}'>"
            f"<a href='{img}'><img loading='lazy' src='{img}' alt=''></a>"
            f"<div><b><i>{html.escape(t.get('correct_taxon', '?'))}</i></b>"
            f"<span class='badge'>{'✓' if ok else ('ERR' if p.get('error') else '✗')}</span></div>"
            f"<div class='pred'>→ <i>{html.escape(str(pred.get('taxon') or '(no answer)'))}</i></div>"
            f"<div class='meta'>{html.escape(t.get('task_id', ''))}"
            f"{' · ' + html.escape(str(p.get('error', ''))[:120]) if p.get('error') else ''}</div>"
            "</div>")
    mid = manifest.get("model_id", "?")
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Run {html.escape(manifest.get('run_id', ''))} — {html.escape(mid)}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:16px}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}"
        ".card{border:1px solid #ddd;border-radius:8px;overflow:hidden;padding:8px}"
        ".card.ok{border-color:#16a34a}.card.miss{border-color:#dc2626}"
        ".card img{width:100%;height:150px;object-fit:cover;display:block}"
        ".badge{float:right;font-weight:bold}.ok .badge{color:#16a34a}.miss .badge{color:#dc2626}"
        ".pred{font-size:14px}.meta{font-size:12px;color:#666;word-break:break-all}</style></head><body>"
        f"<h1>{html.escape(mid)} <small>{html.escape(manifest.get('run_id', ''))}</small></h1>"
        f"<p>{len(cards)} samples · cost ${manifest.get('estimated_cost_usd') or '-'} · "
        f"tasks <code>{html.escape(str(manifest.get('tasks_hash', ''))[:12])}</code></p>"
        f"<div class='grid'>{''.join(cards)}</div></body></html>\n"
    )


def write_run_report(run_dir: str | Path, tasks: list[dict[str, Any]]) -> Path:
    """Read predictions+manifest from run_dir, write report.html. Returns path."""
    import json

    run_dir = Path(run_dir)
    preds = [json.loads(line) for line in (run_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")) if (run_dir / "manifest.json").exists() else {}
    out = run_dir / "report.html"
    out.write_text(render_run_report(tasks, preds, manifest), encoding="utf-8")
    return out
