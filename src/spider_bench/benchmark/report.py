"""Per-run report (samples with previews, answers vs truth). Pure, offline."""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from spider_bench.benchmark.protocol import response_status
from spider_bench.benchmark.scorer import is_correct, score


def render_run_report(tasks: list[dict[str, Any]], predictions: list[dict[str, Any]],
                      manifest: dict[str, Any],
                      family_of: dict[str, str] | None = None) -> str:
    """HTML grid: image thumb, correct vs predicted, matched flag. No deps."""
    score(tasks, predictions)  # reject duplicate IDs or a mismatched task snapshot
    by_pred = {p["task_id"]: p for p in predictions}
    fam = {t["correct_taxon"].lower(): t.get("meta", {}).get("family", "") for t in tasks}
    fam.update({k.lower(): v for k, v in (family_of or {}).items()})
    n = top1 = genus = family = valid = err = fam_known = 0
    cards = []
    from collections import Counter
    statuses = Counter()
    for t in tasks:
        p = by_pred.get(t["task_id"], {"task_id": t["task_id"], "predictions": []})
        pred = (p.get("predictions") or [{}])[0]
        guess = str(pred.get("taxon") or "")
        correct = str(t.get("correct_taxon") or "")
        n += 1
        status = response_status(p.get("predictions") or [],
                                 {**(p.get("provider") or {}), "finish_reason": p.get("finish_reason")}, p.get("error"))
        if t["task_id"] not in by_pred:
            status = "missing"
        statuses[status] += 1
        if status != "answered":
            badge, cls, err = status.upper(), "miss", err + 1
        else:
            if pred.get("matched", bool(guess)):
                valid += 1
            if pred.get("matched", bool(guess)) and is_correct(t, guess):
                badge, cls, top1 = "✓", "ok", top1 + 1
            else:
                badge, cls = "✗", "miss"
            if guess.split()[:1] == correct.split()[:1] and guess:
                genus += 1
            cf, gf = (t.get("meta") or {}).get("family", ""), fam.get(guess.lower(), "")
            if cf and gf:
                fam_known += 1
                if cf == gf:
                    family += 1
        img = html.escape(t.get("image_public_url", "") or (
            Path(t["image_local_path"]).resolve().as_uri() if t.get("image_local_path") else ""))
        meta = t.get("meta") or {}
        credit = html.escape(str(meta.get("attribution") or ""))
        source_url = html.escape(str(meta.get("source_url") or ""))
        if source_url:
            credit += f" <a href='{source_url}'>Source</a>"
        license_id = str(meta.get("license") or "")
        license_url = {
            "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
            "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
            "CC-BY-NC-4.0": "https://creativecommons.org/licenses/by-nc/4.0/",
        }.get(license_id)
        if license_url:
            credit += f" · <a href='{license_url}'>{html.escape(license_id)}</a>"
        elif license_id:
            credit += f" · {html.escape(license_id)}"
        sys_txt = html.escape(str(t.get("system_prompt") or t.get("prompt") or ""))
        user_txt = html.escape(str(t.get("user_prompt") or (
            "Identify the spider in this photograph. Reply with ONLY "
            "<SPIDER_NAME>NAME</SPIDER_NAME> containing exactly one scientific name "
            "from the candidate list, and nothing outside the tags. [legacy v2 task: "
            "user text was not stored; this is the adapter default]")))
        raw_txt = html.escape(str(pred.get("raw") or ""))
        cards.append(
            f"<div class='card {cls}'>"
            f"<a href='{img}'><img loading='lazy' src='{img}' alt=''></a>"
            f"<div><b><i>{html.escape(t.get('correct_taxon', '?'))}</i></b>"
            f"<span class='badge'>{badge}</span></div>"
            f"<div class='pred'>→ <i>{html.escape(str(pred.get('taxon') or '(no answer)'))}</i></div>"
            f"<div class='meta'>{html.escape(t.get('task_id', ''))}"
            f"{' · ' + html.escape(str(p.get('error', ''))[:120]) if p.get('error') else ''}</div>"
            f"<div class='credit'>{credit}</div>"
            f"<details><summary>Prompts and answer</summary><b>SYSTEM</b><pre>{sys_txt}</pre>"
            f"<b>USER</b><pre>{user_txt}</pre>"
            f"<b>MODEL RAW</b><pre>{raw_txt}</pre></details>"
            "</div>")
    mid = manifest.get("model_id", "?")
    def pct(a, b):
        return f"{100.0 * a / b:.1f}%" if b else "n/a"
    family_pct = pct(family, n) if fam_known == statuses["answered"] else "n/a"
    header = (
        f"<p>{n} samples \u00b7 cost ${manifest.get('estimated_cost_usd') or '-'} \u00b7 "
        f"tasks <code>{html.escape(str(manifest.get('tasks_hash', ''))[:12])}</code></p>"
        f"<p><strong>top-1 {pct(top1, n)}</strong> \u00b7 genus {pct(genus, n)} \u00b7 "
        f"family {family_pct} <small>(all {n} tasks; shortlist-assisted)</small> \u00b7 "
        f"valid answers {pct(valid, n)} \u00b7 failures {err}</p>"
        f"<p>{html.escape(str(dict(statuses)))}</p>")
    if fam_known < statuses["answered"]:
        header += f"<p>Family taxonomy unavailable for {statuses['answered'] - fam_known} answers.</p>"
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
        ".pred{font-size:14px}.meta{font-size:12px;color:#666;word-break:break-all}"
        ".credit{font-size:12px;color:#555;margin:8px 0;overflow-wrap:anywhere}"
        "details{font-size:12px}summary{cursor:pointer}"
        "details pre{white-space:pre-wrap;word-break:break-word;margin:4px 0 10px}"
        ".legend{font-size:13px;color:#444}</style></head><body>"
        f"<h1>{html.escape(mid)} <small>{html.escape(manifest.get('run_id', ''))}</small></h1>"
        f"{header}"
        "<p class='legend'>Photo credits and licenses appear below each image. "
        "Prepared photos are EXIF-oriented, metadata-stripped, resized without upscaling, "
        "and JPEG-encoded; previews are cropped to fit.</p>"
        f"<div class='grid'>{''.join(cards)}</div></body></html>\n"
    )


def write_run_report(run_dir: str | Path, tasks: list[dict[str, Any]],
                     family_of: dict[str, str] | None = None) -> Path:
    """Read predictions+manifest from run_dir, write report.html. Returns path."""
    import json

    run_dir = Path(run_dir)
    preds = [json.loads(line) for line in (run_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")) if (run_dir / "manifest.json").exists() else {}
    out = run_dir / "report.html"
    out.write_text(render_run_report(tasks, preds, manifest, family_of), encoding="utf-8")
    return out
