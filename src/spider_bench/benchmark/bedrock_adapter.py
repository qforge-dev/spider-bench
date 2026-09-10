"""AWS Bedrock Converse adapter (Fable): system prompt + image bytes.

Auth via the standard boto3 chain (env/SSO/IAM — never in config).
Same candidate matching, usage and cost tracking as the API adapter.
Bedrock client injectable for offline tests.
"""
from __future__ import annotations

from typing import Any
import threading

from spider_bench.benchmark.api_adapter import match_candidate


def _image_format(image_bytes: bytes) -> str:
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "webp"
    return "jpeg"


class BedrockAdapter:
    """Converse-based vision adapter built from a resolved registry entry."""

    def __init__(self, resolved: dict[str, Any], client: Any | None = None):
        self.model_id = resolved["id"]
        self._cfg = resolved
        self._client = client  # injectable (tests); else boto3 bedrock-runtime
        self.totals = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
        self._usage_lock = threading.Lock()

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        import boto3

        from botocore.config import Config
        return boto3.client("bedrock-runtime", region_name=self._cfg.get("region", "us-east-1"),
                            config=Config(read_timeout=self._cfg.get("timeout_s", 180),
                                          retries={"total_max_attempts": 1}))

    def estimated_cost(self) -> float:
        t = self.totals
        cached = t.get("cached_input_tokens", 0)
        fresh_in = max(t["input_tokens"] - cached, 0)
        return (t["requests"] / 1000 * self._cfg.get("price_per_1k_requests", 0.0)
                + fresh_in / 1000 * self._cfg.get("price_input_1k_tokens", 0.0)
                + cached / 1000 * self._cfg.get("price_cached_1k_tokens",
                                                self._cfg.get("price_input_1k_tokens", 0.0))
                + t["output_tokens"] / 1000 * self._cfg.get("price_output_1k_tokens", 0.0))

    def predict(self, image_bytes: bytes, context: dict[str, Any]) -> list[dict[str, Any]]:
        cands = context.get("candidates", [])
        system_text = context.get("system_prompt") or (
            (context.get("prompt") or "Identify the spider species in this photograph.")
            + f" Valid answers ({len(cands)}): " + "; ".join(cands))
        user_text = context.get("user_prompt") or (
            "Identify the spider in this photograph. "
            "Reply with ONLY <SPIDER_NAME>NAME</SPIDER_NAME> containing exactly one "
            "scientific name from the candidate list, and nothing outside the tags.")
        schema = {
            "type": "object",
            "properties": {"species": {
                "type": "string",
                "description": "Exactly one scientific spider name from the candidate list."}},
            "required": ["species"],
            "additionalProperties": False,
        }
        import json as _json

        structured = bool(self._cfg.get("structured_output", True))
        extra = {}
        if structured:
            extra["outputConfig"] = {"textFormat": {"type": "json_schema", "structure": {"jsonSchema": {
                "name": "identify_species",
                "description": "Spider species identification.",
                "schema": _json.dumps(schema),
            }}}}
        try:
            resp = self._get_client().converse(
                modelId=self._cfg["model"],
                system=[{"text": system_text}],
                messages=[{
                    "role": "user",
                    "content": [{"text": user_text}] + ([{"image": {
                            "format": _image_format(image_bytes or b""),
                            "source": {"bytes": image_bytes or b""},
                        }}] if image_bytes else []),
                }],
                inferenceConfig={"maxTokens": self._cfg.get("max_output_tokens", 5000)},
                **({"additionalModelRequestFields": {
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": self._cfg["reasoning_effort"]},
                }} if self._cfg.get("reasoning_effort") else {}),
                **extra,
            )
        except Exception as e:  # noqa: BLE001 - surfaced per-row, with service detail
            raise RuntimeError(f"bedrock converse failed: {e}") from e
        blocks = resp.get("output", {}).get("message", {}).get("content", []) or []
        texts = [b.get("text", "") for b in blocks
                 if isinstance(b, dict) and b.get("text")]
        raw_text = "".join(texts)
        try:
            text = (_json.loads(raw_text).get("species", "") or "")
        except (ValueError, AttributeError):
            text = raw_text  # schema not enforced server-side: fall back to text match
        usage = resp.get("usage") or {}
        call_usage = {"input_tokens": int(usage.get("inputTokens", 0) or 0),
                      "output_tokens": int(usage.get("outputTokens", 0) or 0),
                      "cached_input_tokens": int(usage.get("cacheReadInputTokens", 0)
                                                  or usage.get("cacheReadInputTokenCount", 0) or 0)}
        with self._usage_lock:
            self.totals["requests"] += 1
            for key, value in call_usage.items():
                self.totals[key] = self.totals.get(key, 0) + value
        info = {"usage": call_usage, "provider_usage": usage,
                "finish_reason": resp.get("stopReason"),
                "response_id": (resp.get("ResponseMetadata") or {}).get("RequestId"),
                "returned_model": self._cfg["model"], "raw_response": raw_text,
                "provider_latency_ms": (resp.get("metrics") or {}).get("latencyMs"),
                "reasoning": "\n".join(
                    (b["reasoningContent"].get("reasoningText") or {}).get("text", "")
                    or b["reasoningContent"].get("text", "")
                    for b in blocks if isinstance(b, dict) and b.get("reasoningContent"))}
        taxon, matched = match_candidate(text, cands)
        return ([{"taxon": taxon, "matched": matched, "raw": raw_text}], info)
