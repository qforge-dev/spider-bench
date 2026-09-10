"""Regression coverage for thinking tokens observed in the Gemini smoke run."""
import pytest

from spider_bench.benchmark.api_adapter import OpenAICompatAdapter


@pytest.mark.parametrize("google,usage,output,reasoning", [
    # Actual Google response: 107 thinking tokens absent from completion_tokens.
    (True, {"prompt_tokens": 1313, "completion_tokens": 17, "total_tokens": 1437}, 124, 107),
    # Do not add thinking twice when completion_tokens already includes it.
    (True, {"prompt_tokens": 1313, "completion_tokens": 124, "total_tokens": 1437,
            "completion_tokens_details": {"reasoning_tokens": 107}}, 124, 107),
    # Missing totals cannot support inference; preserve the reported usage.
    (True, {"prompt_tokens": 1313, "completion_tokens": 17}, 17, None),
    # Other providers retain their existing accounting conventions.
    (False, {"prompt_tokens": 1313, "completion_tokens": 17, "total_tokens": 1437}, 17, None),
])
def test_google_billed_output_includes_thinking(google, usage, output, reasoning):
    base_url = ("https://generativelanguage.googleapis.com/v1beta/openai"
                if google else "https://api.openai.com/v1")
    adapter = OpenAICompatAdapter({
        "id": "test", "model": "test", "base_url": base_url,
        "max_output_tokens": 16000, "structured_output": False,
        "price_per_1k_requests": 0, "price_input_1k_tokens": 0.00075,
        "price_output_1k_tokens": 0.00375,
    }, post=lambda *_: {
        "choices": [{"message": {"content": "<SPIDER_NAME>Pirata piraticus</SPIDER_NAME>"},
                     "finish_reason": "stop"}],
        "usage": usage,
    })
    predictions, info = adapter.predict(b"image", {"candidates": ["Pirata piraticus"]})
    assert predictions[0]["matched"] is True
    assert info["usage"]["output_tokens"] == output
    assert info["reasoning_tokens"] == reasoning
    assert info["provider_usage"] == usage
    assert adapter.totals["output_tokens"] == output
    assert adapter.estimated_cost() == pytest.approx((1313 * 0.75 + output * 3.75) / 1e6)
