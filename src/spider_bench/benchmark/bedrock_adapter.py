"""AWS Bedrock Converse adapter (Fable): system prompt + image bytes.

Auth via the standard boto3 chain (env/SSO/IAM — never in config).
Same candidate matching, usage and cost tracking as the API adapter.
Bedrock client injectable for offline tests.
"""
from __future__ import annotations

from typing import Any

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

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        import boto3

        return boto3.client("bedrock-runtime", region_name=self._cfg.get("region", "us-east-1"))

    def estimated_cost(self) -> float:
        t = self.totals
        return (t["requests"] / 1000 * self._cfg.get("price_per_1k_requests", 0.0)
                + t["input_tokens"] / 1000 * self._cfg.get("price_input_1k_tokens", 0.0)
                + t["output_tokens"] / 1000 * self._cfg.get("price_output_1k_tokens", 0.0))

    def predict(self, image_bytes: bytes, context: dict[str, Any]) -> list[dict[str, Any]]:
        cands = context.get("candidates", [])
        system_text = ((context.get("prompt") or "Identify the spider species in this photograph.")
                       + f" Valid answers ({len(cands)}): " + "; ".join(cands))
        user_text = ("Identify the spider in this photograph. "
                     "Reply with exactly one scientific name from the candidate list.")
        try:
            resp = self._get_client().converse(
                modelId=self._cfg["model"],
                system=[{"text": system_text}],
                messages=[{"role": "user", "content": [
                    {"text": user_text},
                    {"image": {"format": _image_format(image_bytes or b""),
                               "source": {"bytes": image_bytes or b""}}>}]}],
                inferenceConfig={"maxTokens": self._cfg.get("max_output_tokens", 2000)},
            )
        except Exception as e:  # noqa: BLE001 - surfaced per-row, with service detail
            raise RuntimeError(f"bedrock converse failed: {e}") from e
        try:
            text = resp["output"]["message"]["content"][0].get("text", "") or ""
        except (KeyError, IndexError, TypeError):
            text = ""
        usage = resp.get("usage", {}) or {}
        self.totals["requests"] += 1
        self.totals["input_tokens"] += int(usage.get("inputTokens", 0) or 0)
        self.totals["output_tokens"] += int(usage.get("outputTokens", 0) or 0)
        taxon, matched = match_candidate(text, cands)
        return [{"taxon": taxon, "score": 1.0 if matched else 0.0,
                 "matched": matched, "raw": text[:200]}]
