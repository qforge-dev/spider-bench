"""Isolated runner (M1): tasks + adapter -> predictions JSONL.

- Per-row timeout (adapter runs in a worker thread; overruns recorded as
  errors, never crash the run).
- Checkpoint after every row; --resume continues; prediction cache keyed by
  (tasks-hash, model-id, image-sha) avoids recompute.
- Image bytes loader: local release dir or S3 (offline tests inject bytes).
"""
from __future__ import annotations

import concurrent.futures as _fut
import json
import time
from pathlib import Path
from typing import Any, Callable

from spider_bench.benchmark.tasks import tasks_hash


def _load_image_bytes(task: dict[str, Any], loader: Callable[[dict[str, Any]], bytes] | None) -> bytes:
    if loader is not None:
        return loader(task)
    raise RuntimeError("no image loader: pass loader= or run via CLI (local/S3 supported)")


def run_tasks(tasks: list[dict[str, Any]], adapter: Any, out_path: str | Path, *,
              loader: Callable[[dict[str, Any]], bytes] | None = None,
              timeout_s: float = 120.0, resume: bool = True,
              max_cost: float | None = None) -> dict[str, Any]:
    """Run all tasks, stream predictions JSONL. Returns run summary.

    max_cost stops the run once adapter.estimated_cost() (if available)
    reaches it; the stop is recorded, never a crash.
    """
    """Run all tasks, stream predictions JSONL. Returns run summary."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    done: dict[str, dict] = {}
    if resume and out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["task_id"]] = r
    thash = tasks_hash(tasks)
    wrote = errors = 0
    stopped_early = False
    t0 = time.time()
    with out.open("a", encoding="utf-8") as fh, _fut.ThreadPoolExecutor(max_workers=1) as ex:
        for t in tasks:
            if t["task_id"] in done:
                continue
            if max_cost is not None and hasattr(adapter, "estimated_cost"):
                try:
                    if adapter.estimated_cost() >= max_cost:
                        stopped_early = True
                        break
                except Exception:
                    pass
            try:
                img = _load_image_bytes(t, loader)
                fut = ex.submit(adapter.predict, img, {"candidates": t.get("candidates", []),
                                                       "correct_taxon": t.get("correct_taxon", ""),
                                                       "prompt": t.get("prompt", ""),
                                                       "image_public_url": t.get("image_public_url", ""),
                                                       "task_id": t.get("task_id", "")})
                preds = fut.result(timeout=timeout_s)
                rec = {"task_id": t["task_id"], "model_id": getattr(adapter, "model_id", "?"),
                       "tasks_hash": thash, "image_sha256": t["image_sha256"],
                       "predictions": preds, "error": None}
            except Exception as e:  # noqa: BLE001 - per-row isolation
                rec = {"task_id": t["task_id"], "model_id": getattr(adapter, "model_id", "?"),
                       "tasks_hash": thash, "image_sha256": t["image_sha256"],
                       "predictions": [], "error": f"{type(e).__name__}: {e}"}
                errors += 1
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            fh.flush()
            wrote += 1
    summary: dict[str, Any] = {"tasks": len(tasks), "wrote": wrote, "resumed": len(done),
                               "errors": errors, "seconds": round(time.time() - t0, 1),
                               "tasks_hash": thash, "predictions_path": str(out)}
    if stopped_early:
        summary["stopped_early"] = "max_cost reached"
    if hasattr(adapter, "totals"):
        summary["usage"] = dict(adapter.totals)
    if hasattr(adapter, "estimated_cost"):
        try:
            summary["estimated_cost_usd"] = round(adapter.estimated_cost(), 4)
        except Exception:
            pass
    return summary


def read_predictions(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
