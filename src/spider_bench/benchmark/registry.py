"""Model registry (M3): configs/models/*.yaml. Secrets via env, never logged."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_DIR = Path("configs/models")


def load_registry(directory: str | Path = DEFAULT_DIR) -> dict[str, dict[str, Any]]:
    """Load all model configs keyed by id. Raises on duplicate ids."""
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(Path(directory).glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        mid = data.get("id") or f.stem
        if mid in out:
            raise ValueError(f"duplicate model id: {mid}")
        data["id"] = mid
        data["_file"] = str(f)
        out[mid] = data
    return out


def resolve_model(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolve env-backed fields. Returns explisafe dict (no secret values)."""
    key_env = cfg.get("api_key_env", "")
    if key_env and not os.environ.get(key_env):
        raise RuntimeError(f"missing required env var: {key_env} (model {cfg.get('id')})")
    base = (os.environ.get(cfg.get("base_url_env", ""), "")
            or cfg.get("base_url", "") or "https://api.openai.com/v1")
    model = os.environ.get(cfg.get("model_env", ""), "") or cfg.get("model", "")
    return {"id": cfg.get("id"), "display_name": cfg.get("display_name", cfg.get("id")),
            "adapter": cfg.get("adapter", "openai-compatible"),
            "base_url": base.rstrip("/"), "model": model,
            "has_key": bool(key_env and os.environ.get(key_env)),
            "key_env": key_env,
            "max_output_tokens": int(cfg.get("max_output_tokens", 50)),
            "temperature": (None if cfg.get("temperature") is None
                            else float(cfg.get("temperature"))),
            "token_param": str(cfg.get("token_param", "max_tokens")),
            "price_per_1k_requests": float(cfg.get("price_per_1k_requests", 0.0)),
            "price_input_1k_tokens": float(cfg.get("price_input_1k_tokens", 0.0)),
            "price_cached_1k_tokens": float(cfg.get("price_cached_1k_tokens",
                                                    cfg.get("price_input_1k_tokens", 0.0))),
            "price_output_1k_tokens": float(cfg.get("price_output_1k_tokens", 0.0)),
            "timeout_s": float(cfg.get("timeout_s", 120)),
            "api_version": str(cfg.get("api_version", "") or ""),
            "notes": cfg.get("notes", ""), "file": cfg.get("_file", "")}


def describe_registry(directory: str | Path = DEFAULT_DIR) -> list[dict[str, Any]]:
    """Safe listing: ids, endpoints (host only), pricing, key presence. No secrets."""
    rows = []
    for mid, cfg in sorted(load_registry(directory).items()):
        try:
            r = resolve_model(cfg)
        except RuntimeError as e:
            r = {"id": mid, "error": str(e), "has_key": False}
        rows.append({k: v for k, v in r.items() if k != "base_url"} |
                    {"base_host": r.get("base_url", "").split("/")[2] if r.get("base_url") else "?"})
    return rows
