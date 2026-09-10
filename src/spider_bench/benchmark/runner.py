"""Isolated runner: tasks + adapter -> predictions JSONL.

- Parallel workers (default 4) with per-row timeout; overruns recorded as
  errors, never crash the run.
- Transient failures (timeouts, 429/5xx) retried with exponential backoff.
- Optional global rate limit across workers.
- Checkpoint on every row; --resume continues. Rows stream in completion
  order (content deterministic, order not).
- Image bytes loader: local release dir or S3 (offline tests inject bytes).
"""
from __future__ import annotations

import concurrent.futures as _fut
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from spider_bench.benchmark.tasks import tasks_hash

TRANSIENT_MARKERS = ("429", "503", "502", "504", "timeout", "timed out",
                     "overloaded", "rate limit", "try again", "temporarily")


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (_fut.TimeoutError, TimeoutError)):
        return True
    text = f"{type(exc).__name__} {exc}".lower()
    return any(m in text for m in TRANSIENT_MARKERS)


def _load_image_bytes(task: dict[str, Any], loader: Callable[[dict[str, Any]], bytes] | None) -> bytes:
    if loader is not None:
        return loader(task)
    raise RuntimeError("no image loader: pass loader= or run via CLI (local/S3 supported)")


def _context(t: dict[str, Any]) -> dict[str, Any]:
    return {"candidates": t.get("candidates", []), "correct_taxon": t.get("correct_taxon", ""),
            "prompt": t.get("prompt", ""), "system_prompt": t.get("system_prompt", ""),
            "user_prompt": t.get("user_prompt", ""),
            "image_public_url": t.get("image_public_url", ""),
            "task_id": t.get("task_id", "")}


def _predict_once(adapter: Any, task: dict[str, Any],
                  loader: Callable[[dict[str, Any]], bytes] | None,
                  timeout_s: float, ex: _fut.ThreadPoolExecutor) -> list[dict[str, Any]]:
    img = _load_image_bytes(task, loader)
    return ex.submit(adapter.predict, img, _context(task)).result(timeout=timeout_s)


def run_tasks(tasks: list[dict[str, Any]], adapter: Any, out_path: str | Path, *,
              loader: Callable[[dict[str, Any]], bytes] | None = None,
              timeout_s: float = 120.0, resume: bool = True,
              max_cost: float | None = None,
              max_workers: int = 4, rate_limit: float = 0.0,
              retries: int = 3, backoff_base: float = 1.0, backoff_cap: float = 30.0,
              progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    """Run all tasks, stream predictions JSONL. Returns run summary.

    max_cost stops the run once adapter.estimated_cost() (if available)
    reaches it; the stop is recorded, never a crash.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    done: dict[str, dict] = {}
    if resume and out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["task_id"]] = r
    thash = tasks_hash(tasks)
    pending = [t for t in tasks if t["task_id"] not in done]
    wrote = errors = retried = 0
    stopped_early: str | None = None
    t0 = time.time()
    gate = threading.Lock()
    last_call = [0.0]
    min_interval = 1.0 / rate_limit if rate_limit and rate_limit > 0 else 0.0

    def call_limited(task: dict[str, Any]) -> list[dict[str, Any]]:
        if min_interval:
            with gate:
                wait = last_call[0] + min_interval - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                last_call[0] = time.monotonic()
        with _fut.ThreadPoolExecutor(max_workers=1) as single:
            return _predict_once(adapter, task, loader, timeout_s, single)

    def cost_reached() -> bool:
        if max_cost is None or not hasattr(adapter, "estimated_cost"):
            return False
        try:
            return adapter.estimated_cost() >= max_cost
        except Exception:
            return False

    def work(task: dict[str, Any]) -> dict[str, Any]:
        nonlocal retried
        attempt = 0
        last: BaseException | None = None
        for attempt in range(retries + 1):
            try:
                preds = call_limited(task)
                return {"task_id": task["task_id"], "model_id": getattr(adapter, "model_id", "?"),
                        "tasks_hash": thash, "image_sha256": task["image_sha256"],
                        "predictions": preds, "error": None}
            except Exception as e:  # noqa: BLE001 - per-row isolation
                last = e
                if _is_transient(e) and attempt < retries:
                    retried += 1
                    time.sleep(min(backoff_cap, backoff_base * (2.0 ** attempt)))
                    continue
                return {"task_id": task["task_id"], "model_id": getattr(adapter, "model_id", "?"),
                        "tasks_hash": thash, "image_sha256": task["image_sha256"],
                        "predictions": [], "error": f"{type(e).__name__}: {e}"}
        return {"task_id": task["task_id"], "model_id": getattr(adapter, "model_id", "?"),
                "tasks_hash": thash, "image_sha256": task["image_sha256"],
                "predictions": [], "error": f"{type(last).__name__}: {last}"}

    with out.open("a", encoding="utf-8") as fh:
        if cost_reached():
            stopped_early = "max_cost reached"
        elif pending:
            waiting = list(pending)
            with _fut.ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
                inflight: dict[_fut.Future, dict[str, Any]] = {}
                try:
                    while waiting or inflight:
                        while waiting and len(inflight) < max(1, max_workers) and not cost_reached():
                            t = waiting.pop(0)
                            inflight[pool.submit(work, t)] = t
                        if not inflight:
                            if waiting:
                                stopped_early = "max_cost reached"
                            break
                        for fut in _fut.as_completed(list(inflight)):
                            rec = fut.result()
                            del inflight[fut]
                            fh.write(json.dumps(rec, sort_keys=True) + "\n")
                            fh.flush()
                            wrote += 1
                            if rec["error"]:
                                errors += 1
                            if progress is not None:
                                try:
                                    progress(len(done) + wrote, len(tasks))
                                except Exception:
                                    pass
                            break  # refill dispatch queue (budget-aware)
                        if cost_reached() and waiting:
                            stopped_early = "max_cost reached"
                            break
                finally:
                    for f in inflight:
                        f.cancel()
    summary: dict[str, Any] = {"tasks": len(tasks), "wrote": wrote, "resumed": len(done),
                               "errors": errors, "retried": retried,
                               "seconds": round(time.time() - t0, 1),
                               "workers": max_workers,
                               "tasks_hash": thash, "predictions_path": str(out)}
    if stopped_early:
        summary["stopped_early"] = stopped_early
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
