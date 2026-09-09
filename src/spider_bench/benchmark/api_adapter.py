"""OpenAI-compatible vision adapter (M3): Terra / Luna / any chat-completions endpoint.

Sends the task's public image URL (no byte uploads); asks for exactly one
candidate-list name; normalizes the reply and matches it against candidates.
Unmatched text is recorded verbatim (scores wrong, flagged unmatched).
Token usage accumulates in .totals for cost guards. HTTP layer injectable.
"""
from __future__ import annotations

import os
from typing import Any, Callable


def _norm(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())


def match_candidate(text: str, candidates: list[str]) -> tuple[str, bool]:
    """Match free-text reply to a candidate. Returns (taxon, matched)."""
    first_line = (text or "").strip().splitlines()[0].strip().strip("*.\"'") if text.strip() else ""
    normed = { _norm(c): c for c in candidates }
    if _norm(first_line) in normed:
        return normed[_norm(first_line)], True
    # tolerate "Genus species (comment)" prefix form
    head = " ".join(_norm(first_line).split()[:2])
    if head in normed:
        return normed[head], True
    return first_line[:200], False


class OpenAICompatAdapter:
    """Vision chat-completions adapter built from a resolved registry entry."""

    def __init__(self, resolved: dict[str, Any],
                 post: Callable[..., Any] | None = None):
        self.model_id = resolved["id"]
        self._cfg = resolved
        self._post = post  # injectable transport (tests); default below
        self.totals = {"requests": 0, "input_tokens": 0, "output_tokens": 0}

    def _key(self) -> str:
        return os.environ.get(self._cfg.get("key_env", ""), "")

    def estimated_cost(self) -> float:
        t = self.totals
        return (t["requests"] / 1000 * self._cfg["price_per_1k_requests"]
                + t["input_tokens"] / 1000 * self._cfg["price_input_1k_tokens"]
                + t["output_tokens"] / 1000 * self._cfg["price_output_1k_tokens"])

    def _prompt(self, context: dict[str, Any]) -> str:
        cands = context.get("candidates", [])
        return ((context.get("prompt") or "Identify the spider species in this photograph.")
                + f" Valid answers ({len(cands)}): " + "; ".join(cands))

    def predict(self, image_bytes: bytes, context: dict[str, Any]) -> list[dict[str, Any]]:
        import base64

        import httpx

        if context.get("image_public_url"):
            content = [{"type": "text", "text": self._prompt(context)},
                       {"type": "image_url", "image_url": {"url": context["image_public_url"]}}]
        else:
            b64 = base64.b64encode(image_bytes or b"").decode()
            content = [{"type": "text", "text": self._prompt(context)},
                       {"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]
        body = {"model": self._cfg["model"],
                "messages": [{"role": "user", "content": content}],
                "temperature": self._cfg["temperature"],
                "max_tokens": self._cfg["max_output_tokens"]}
        headers = {"Authorization": f"Bearer {self._key()}"}
        url = self._cfg["base_url"] + "/chat/completions"
        if self._post is not None:
            payload = self._post(url, body, headers)
        else:
            r = httpx.post(url, json=body, headers=headers, timeout=self._cfg["timeout_s"])
            r.raise_for_status()
            payload = r.json()
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            text = ""
        usage = payload.get("usage", {}) or {}
        self.totals["requests"] += 1
        self.totals["input_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
        self.totals["output_tokens"] += int(usage.get("completion_tokens", 0) or 0)
        taxon, matched = match_candidate(text, context.get("candidates", []))
        return [{"taxon": taxon, "score": 1.0 if matched else 0.0,
                 "matched": matched, "raw": text[:200]}]
