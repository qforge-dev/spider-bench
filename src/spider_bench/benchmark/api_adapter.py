"""OpenAI-compatible vision adapter (M3): Terra / Luna / any chat-completions endpoint.

Sends the task's public image URL (no byte uploads); asks for exactly one
candidate-list name; normalizes the reply and matches it against candidates.
Unmatched text is recorded verbatim (scores wrong, flagged unmatched).
Token usage accumulates in .totals for cost guards. HTTP layer injectable.
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable


def _norm(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())


TAG_RE = re.compile(r"<\s*spider_name\s*>(.*?)<\s*/\s*spider_name\s*>",
                      re.IGNORECASE | re.DOTALL)


def match_candidate(text: str, candidates: list[str]) -> tuple[str, bool]:
    """Match free-text reply to a candidate. Returns (taxon, matched).

    Reasoning models bury the answer in prose: scan every line for a
    candidate mention (exact or 'Genus species (…)' head form) and take the
    LAST match — conclusions come after reasoning. Single-name replies
    behave exactly as before.
    """
    tagged = TAG_RE.findall(text or "")
    if tagged:
        text = tagged[-1]  # conclusions come last; ignore everything outside tags
    normed = {_norm(c): c for c in candidates}
    lines = (text or "").strip().splitlines() or [""]
    found: str | None = None
    for raw_line in lines:
        line = raw_line.strip().strip("* .\"'")
        if not line:
            continue
        if _norm(line) in normed:
            found = normed[_norm(line)]
            continue
        head = " ".join(_norm(line).split()[:2])
        if head in normed:
            found = normed[head]
            continue
        for cand_norm, cand in normed.items():
            if cand_norm and re.search(rf"(?<![a-z]){re.escape(cand_norm)}(?![a-z])", _norm(line)):
                found = cand
    if found is not None:
        return found, True
    first = lines[0].strip().strip("* .\"'") if lines else ""
    return first[:200], False


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
        cached = t.get("cached_input_tokens", 0)
        fresh_in = max(t["input_tokens"] - cached, 0)
        return (t["requests"] / 1000 * self._cfg["price_per_1k_requests"]
                + fresh_in / 1000 * self._cfg["price_input_1k_tokens"]
                + cached / 1000 * self._cfg.get("price_cached_1k_tokens",
                                                self._cfg["price_input_1k_tokens"])
                + t["output_tokens"] / 1000 * self._cfg["price_output_1k_tokens"])

    def _system_prompt(self, context: dict[str, Any]) -> str:
        """Task-defined system text when present, else legacy full-list prompt."""
        if context.get("system_prompt"):
            return context["system_prompt"]
        cands = context.get("candidates", [])
        return ((context.get("prompt") or "Identify the spider species in this photograph.")
                + f" Valid answers ({len(cands)}): " + "; ".join(cands))

    def _prompt(self, context: dict[str, Any]) -> str:
        cands = context.get("candidates", [])
        return ((context.get("prompt") or "Identify the spider species in this photograph.")
                + f" Valid answers ({len(cands)}): " + "; ".join(cands))

    def predict(self, image_bytes: bytes, context: dict[str, Any]) -> list[dict[str, Any]]:
        import base64

        import httpx

        # Static system message first: identical across tasks, so providers
        # cache it (candidate list ~5k tokens). Per-image content stays in user.
        system_text = self._system_prompt(context)
        user_text = context.get("user_prompt") or (
            "Identify the spider in this photograph. "
            "Reply with ONLY <SPIDER_NAME>NAME</SPIDER_NAME> containing exactly one "
            "scientific name from the candidate list, and nothing outside the tags.")
        if context.get("image_public_url"):
            user_content: list[dict[str, Any]] = [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": context["image_public_url"]}}]
        else:
            b64 = base64.b64encode(image_bytes or b"").decode()
            user_content = [
                {"type": "text", "text": user_text},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]
        body: dict[str, Any] = {"model": self._cfg["model"],
                "messages": [{"role": "system", "content": system_text},
                             {"role": "user", "content": user_content}]}
        if self._cfg.get("structured_output"):
            import json as _json2

            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "identify_species",
                    "strict": True,
                    "schema": _json2.loads(_json2.dumps({
                        "type": "object",
                        "properties": {"species": {"type": "string"}},
                        "required": ["species"],
                        "additionalProperties": False,
                    })),
                },
            }
        if self._cfg.get("temperature") is not None:
            body["temperature"] = self._cfg["temperature"]
        if self._cfg.get("reasoning_effort"):
            body["reasoning_effort"] = self._cfg["reasoning_effort"]
        body[self._cfg.get("token_param", "max_tokens") or "max_tokens"] = self._cfg["max_output_tokens"]
        headers = {"Authorization": f"Bearer {self._key()}"}
        url = self._cfg["base_url"] + "/chat/completions"
        if self._cfg.get("api_version"):
            import urllib.parse

            url += "?" + urllib.parse.urlencode({"api-version": self._cfg["api_version"]})
        if self._post is not None:
            payload = self._post(url, body, headers)
        else:
            r = httpx.post(url, json=body, headers=headers, timeout=self._cfg["timeout_s"])
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}") from e
            payload = r.json()
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            text = ""
        if self._cfg.get("structured_output"):
            try:
                import json as _json3

                text = _json3.loads(text).get("species", "") or ""
            except (ValueError, AttributeError):
                pass
        usage = payload.get("usage", {}) or {}
        self.totals["requests"] += 1
        self.totals["input_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
        self.totals["output_tokens"] += int(usage.get("completion_tokens", 0) or 0)
        details = usage.get("input_token_details") or usage.get("prompt_tokens_details") or {}
        self.totals["cached_input_tokens"] = self.totals.get("cached_input_tokens", 0) + int(
            details.get("cached_tokens", 0) or 0)
        self.last_usage = {"input_tokens": int(usage.get("prompt_tokens", 0) or 0),
                           "output_tokens": int(usage.get("completion_tokens", 0) or 0),
                           "cached_input_tokens": int(
                               ((usage.get("input_token_details") or usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)) or 0)}
        taxon, matched = match_candidate(text, context.get("candidates", []))
        return [{"taxon": taxon, "score": 1.0 if matched else 0.0,
                 "matched": matched, "raw": text}]
