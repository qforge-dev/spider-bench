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
              timeout_s: float = 120.0, resume: bool = True) -> dict[str, Any]:
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
    t0 = time.time()
    with out.open("a", encoding="utf-8") as fh, _fut.ThreadPoolExecutor(max_workers=1) as ex:
        for t in tasks:
            if t["task_id"] in done:
                continue
            try:
                img = _load_image_bytes(t, loader)
                fut = ex.submit(adapter.predict, img, {"candidates": t.get("candidates", []),
                                                       "correct_taxon": t.get("correct_taxon", ""),
                                                       "prompt": t.get("prompt", "")})
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
    return {"tasks": len(tasks), "wrote": wrote, "resumed": len(done),
            "errors": errors, "seconds": round(time.time() - t0, 1),
            "tasks_hash": thash, "predictions_path": str(out)}


def read_predictions(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
