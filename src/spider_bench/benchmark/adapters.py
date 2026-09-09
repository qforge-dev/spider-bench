"""Model adapter interface (M1): the only thing model-side code writes.

predict(image_bytes, context) -> ranked [{"taxon": str, "score": float}].
Rank order counts, scores optional. Includes offline reference adapters.
"""
from __future__ import annotations

from typing import Any, Protocol


class ModelAdapter(Protocol):
    model_id: str

    def predict(self, image_bytes: bytes, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Return ranked predictions, best first. Must not raise on odd input."""
        ...


class PerfectAdapter:
    """Cheating reference: returns the correct taxon first (tests the plumbing)."""

    model_id = "perfect-reference"

    def predict(self, image_bytes: bytes, context: dict[str, Any]) -> list[dict[str, Any]]:
        correct = context.get("correct_taxon", "")
        cands = [c for c in context.get("candidates", []) if c != correct]
        return [{"taxon": correct, "score": 1.0},
                *[{"taxon": c, "score": 0.0} for c in cands[:4]]]


class ConstantAdapter:
    """Naive reference: always predicts the same taxon (baseline floor)."""

    model_id = "constant-reference"

    def __init__(self, taxon: str = "Araneus diadematus"):
        self._taxon = taxon
        self.model_id = f"constant-reference:{taxon}"

    def predict(self, image_bytes: bytes, context: dict[str, Any]) -> list[dict[str, Any]]:
        cands = context.get("candidates", [self._taxon])
        rest = [c for c in cands if c != self._taxon]
        return [{"taxon": self._taxon, "score": 1.0},
                *[{"taxon": c, "score": 0.0} for c in rest[:4]]]
