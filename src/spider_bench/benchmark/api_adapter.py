"""OpenAI-compatible adapter: shared prepared bytes, explicit answer parsing,
provider stop reasons and per-call telemetry. Empty bytes select the no-image control."""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Callable


def _norm(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())


TAG_RE = re.compile(r"<\s*spider_name\s*>(.*?)<\s*/\s*spider_name\s*>",
                      re.IGNORECASE | re.DOTALL)


def match_candidate(text: str, candidates: list[str]) -> tuple[str, bool]:
    """Accept one explicit answer; never guess from names mentioned in prose."""
    tagged = TAG_RE.findall(text or "")
    if len(tagged) > 1:
        return (text or ""), False
    answer = tagged[0].strip() if tagged else (text or "").strip()
    names = {_norm(c): c for c in candidates}
    if _norm(answer) in names:
        return names[_norm(answer)], True
    return answer, False


class OpenAICompatAdapter:
    """Vision chat-completions adapter built from a resolved registry entry."""

    def __init__(self, resolved: dict[str, Any],
                 post: Callable[..., Any] | None = None):
        self.model_id = resolved["id"]
        self._cfg = resolved
        self._post = post  # injectable transport (tests); default below
        self.totals = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
        self._usage_lock = threading.Lock()

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

        # The system instruction is shared; each user message has its frozen shortlist.
        system_text = self._system_prompt(context)
        user_text = context.get("user_prompt") or (
            "Identify the spider in this photograph. "
            "Reply with ONLY <SPIDER_NAME>NAME</SPIDER_NAME> containing exactly one "
            "scientific name from the candidate list, and nothing outside the tags.")
        user_content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        if image_bytes:
            b64 = base64.b64encode(image_bytes).decode()
            user_content.append({"type": "image_url", "image_url": {
                "url": f"data:image/jpeg;base64,{b64}", "detail": "high"}})
        # Empty bytes deliberately mean the no-image control. Never fetch a URL here.
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
        # Effort shape depends on the endpoint. Chat Completions
        # (Azure/OpenAI, xAI native): flat reasoning_effort string.
        # OpenRouter-routed models: nested reasoning object (see OpenRouter
        # reasoning-tokens docs). reasoning_style selects; default flat.
        if self._cfg.get("reasoning_effort") and self._cfg.get("reasoning_api", "openai") != "none":
            if self._cfg.get("reasoning_style", "flat") == "nested":
                body["reasoning"] = {"effort": self._cfg["reasoning_effort"]}
            else:
                body["reasoning_effort"] = self._cfg["reasoning_effort"]
        if self._cfg.get("seed") is not None:
            body["seed"] = self._cfg["seed"]
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
        choice = (payload.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        raw_text = msg.get("content") or ""
        if isinstance(raw_text, list):
            raw_text = "".join(x.get("text", "") for x in raw_text if isinstance(x, dict))
        text = raw_text
        if self._cfg.get("structured_output"):
            try:
                text = json.loads(text).get("species", "") or ""
            except (ValueError, AttributeError):
                pass
        usage = payload.get("usage") or {}
        details = usage.get("input_token_details") or usage.get("prompt_tokens_details") or {}
        call_usage = {"input_tokens": int(usage.get("prompt_tokens", 0) or 0),
                      "output_tokens": int(usage.get("completion_tokens", 0) or 0),
                      "cached_input_tokens": int(details.get("cached_tokens", 0) or 0)}
        with self._usage_lock:
            self.totals["requests"] += 1
            for key, value in call_usage.items():
                self.totals[key] = self.totals.get(key, 0) + value
        info = {"usage": call_usage, "provider_usage": usage,
                "finish_reason": choice.get("finish_reason"),
                "refusal": msg.get("refusal"), "response_id": payload.get("id"),
                "returned_model": payload.get("model"),
                "system_fingerprint": payload.get("system_fingerprint"),
                "raw_response": raw_text,
                "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")}
        taxon, matched = match_candidate(text, context.get("candidates", []))
        return ([{"taxon": taxon, "matched": matched, "raw": raw_text}], info)
