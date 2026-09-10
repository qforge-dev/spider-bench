"""Shared evaluation rules. No provider calls or model-dependent selection."""
from __future__ import annotations

import hashlib
import io
import random
from collections import Counter, defaultdict

from PIL import Image, ImageOps

from spider_bench.media.hash import ahash, dhash

PROTOCOL_VERSION = 5
IMAGE_POLICY = {"min_short_side": 320, "max_long_side": 1536,
                "jpeg_quality": 90, "max_bytes": 3_000_000,
                "orientation": "exif-transpose", "metadata": "stripped", "upsample": False}


def prepare_image(data: bytes) -> tuple[bytes, dict]:
    with Image.open(io.BytesIO(data)) as source:
        if getattr(source, "n_frames", 1) != 1:
            raise ValueError("animated_or_multiframe")
        source.load()
        oriented = ImageOps.exif_transpose(source)
        if min(oriented.size) < IMAGE_POLICY["min_short_side"]:
            raise ValueError("source_resolution_below_320")
        rgb = Image.new("RGB", oriented.size, "white")
        if "A" in oriented.getbands():
            rgb.paste(oriented, mask=oriented.getchannel("A"))
        else:
            rgb.paste(oriented.convert("RGB"))
        pixels = hashlib.sha256(str(rgb.size).encode() + rgb.tobytes()).hexdigest()
        source_size = list(rgb.size)
        rgb.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
        if min(rgb.size) < 320:
            raise ValueError("aspect_ratio_too_extreme")
        # Rebuild the image to remove EXIF, ICC profiles, comments and filenames.
        clean = Image.frombytes("RGB", rgb.size, rgb.tobytes())
        out = io.BytesIO()
        clean.save(out, "JPEG", quality=90, optimize=True)
        payload = out.getvalue()
        if len(payload) > IMAGE_POLICY["max_bytes"]:
            raise ValueError("normalized_image_exceeds_3MB")
        return payload, {"source_size": source_size, "width": clean.width,
                         "height": clean.height, "pixel_sha256": pixels,
                         "dhash": f"{dhash(clean):016x}", "ahash": f"{ahash(clean):016x}",
                         "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}


def sample_tasks(tasks: list[dict], limit: int | None, seed: int = 42) -> list[dict]:
    """Seeded species round-robin; a task cap never takes an alphabetical prefix."""
    groups = defaultdict(list)
    for task in sorted(tasks, key=lambda t: t["task_id"]):
        groups[task["correct_taxon"]].append(task)
    rng = random.Random(seed)
    names = sorted(groups)
    rng.shuffle(names)
    for items in groups.values():
        rng.shuffle(items)
    out = []
    for depth in range(max((len(x) for x in groups.values()), default=0)):
        for name in names:
            if depth < len(groups[name]):
                out.append(groups[name][depth])
    if limit is not None and limit <= 0:
        raise ValueError("max_tasks must be positive")
    return out[:limit] if limit else out


def mixed_candidates(correct: str, taxa: dict[str, str], row_id: str,
                     seed: int = 42, size: int = 20, same_family: int = 9) -> list[str]:
    """Correct + same-family + other-family distractors, seeded per image row."""
    if correct not in taxa or size < 2 or size > 20 or len(taxa) < 2:
        raise ValueError("candidate pool must contain the correct species; size must be 2..20")
    rng = random.Random(f"{seed}:{row_id}")
    related = sorted(n for n in taxa if n != correct and taxa[n] == taxa[correct])
    other = sorted(n for n in taxa if taxa[n] != taxa[correct])
    rng.shuffle(related)
    rng.shuffle(other)
    chosen = related[:min(same_family, size - 1)]
    chosen += other[:size - 1 - len(chosen)]
    remaining = [n for n in related + other if n not in chosen]
    rng.shuffle(remaining)
    chosen += remaining[:max(0, size - 1 - len(chosen))]
    result = [correct] + chosen
    rng.shuffle(result)
    return result


def response_status(predictions: list[dict], info: dict, error: str | None = None) -> str:
    if error:
        return "transport_error"
    if info.get("finish_reason") in {"length", "max_tokens", "model_context_window_exceeded"}:
        return "truncated"
    if info.get("refusal") or info.get("finish_reason") in {
        "content_filter", "guardrail_intervened", "content_filtered", "refusal"
    }:
        return "refused"
    if not predictions or not str(predictions[0].get("raw", predictions[0].get("taxon", ""))).strip():
        return "empty"
    if predictions[0].get("matched") is False or not predictions[0].get("taxon"):
        return "invalid_answer"
    return "answered"


def baselines(tasks: list[dict]) -> dict:
    if not tasks:
        return {}
    uniform = largest_genus = 0.0
    for t in tasks:
        names = t["candidates"]
        uniform += 1 / len(names)
        counts = Counter(n.split()[0] for n in names)
        pool = [n for n in names if counts[n.split()[0]] == max(counts.values())]
        largest_genus += 1 / len(pool) if t["correct_taxon"] in pool else 0
    return {"uniform_candidate": uniform / len(tasks),
            "largest_genus_candidate_only": largest_genus / len(tasks)}
