"""License-profile selection with per-taxon caps and domination limits.

Reads accepted/review license sets from configs/license-profiles.yaml.
Limits domination by a single observation, observer, location, or season
(Stage C.5 of the plan). Pure function — no network/S3.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

DEFAULT_PROFILES_PATH = Path("configs/license-profiles.yaml")

# Normalize common license spellings to canonical SPDX-ish ids used in the yaml.
_LICENSE_ALIASES = {
    "cc0": "CC0-1.0",
    "cc0-1.0": "CC0-1.0",
    "cc-by": "CC-BY-4.0",
    "cc by": "CC-BY-4.0",
    "cc-by-3.0": "CC-BY-3.0",
    "cc-by-4.0": "CC-BY-4.0",
    "cc by 3.0": "CC-BY-3.0",
    "cc by 4.0": "CC-BY-4.0",
    "cc-by-nc": "CC-BY-NC-4.0",
    "cc-by-nc-3.0": "CC-BY-NC-3.0",
    "cc-by-nc-4.0": "CC-BY-NC-4.0",
    "cc-by-sa": "CC-BY-SA-4.0",
    "cc-by-sa-3.0": "CC-BY-SA-3.0",
    "cc-by-sa-4.0": "CC-BY-SA-4.0",
    "cc-by-nc-sa-3.0": "CC-BY-NC-SA-3.0",
    "cc-by-nc-sa-4.0": "CC-BY-NC-SA-4.0",
}


def normalize_license(value: str | None) -> str | None:
    if value is None:
        return None
    key = value.strip().lower().replace("_", "-")
    key = key.replace("creative commons ", "cc-")
    if key in _LICENSE_ALIASES:
        return _LICENSE_ALIASES[key]
    # Already canonical like "CC-BY-4.0"? compare case-insensitively.
    upper = value.strip().upper().replace("_", "-")
    for alias, canon in _LICENSE_ALIASES.items():
        if alias.upper().replace("_", "-") == upper:
            return canon
    return value.strip()


def load_license_profile(
    profile_name: str, profiles_path: str | Path = DEFAULT_PROFILES_PATH
) -> tuple[set[str], set[str]]:
    """Return (accept, review_required) canonical license sets."""
    data = yaml.safe_load(Path(profiles_path).read_text())
    profiles = data.get("profiles", {})
    if profile_name not in profiles:
        raise ValueError(f"unknown license profile {profile_name!r}")
    prof = profiles[profile_name]
    accept = {normalize_license(x) for x in prof.get("accept", [])}  # type: ignore[misc]
    review = {normalize_license(x) for x in prof.get("review_required", [])}  # type: ignore[misc]
    return accept, review


def _get(cand: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(cand, Mapping):
        return cand.get(key, default)
    return getattr(cand, key, default)


def _season_of(cand: Mapping[str, Any] | Any) -> str | None:
    season = _get(cand, "season")
    if season:
        return str(season)
    month = _get(cand, "month")
    if month is None:
        return None
    try:
        m = int(month)
    except (TypeError, ValueError):
        return str(month)
    if m in (12, 1, 2):
        return "winter"
    if m in (3, 4, 5):
        return "spring"
    if m in (6, 7, 8):
        return "summer"
    if m in (9, 10, 11):
        return "autumn"
    return None


def filter_by_license(
    candidates: list[Any],
    profile_name: str = "research",
    profiles_path: str | Path = DEFAULT_PROFILES_PATH,
) -> tuple[list[Any], list[Any], list[Any]]:
    """Split candidates into (accepted, needs_review, rejected) by license."""
    accept, review = load_license_profile(profile_name, profiles_path)
    accepted, needs_review, rejected = [], [], []
    for c in candidates:
        lic = normalize_license(_get(c, "license"))
        if lic in accept:
            accepted.append(c)
        elif lic in review:
            needs_review.append(c)
        else:
            rejected.append(c)
    return accepted, needs_review, rejected


@dataclass
class SelectionResult:
    selected: list[Any] = field(default_factory=list)
    skipped_caps: list[Any] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)


def select_media(
    candidates: list[Any],
    profile_name: str = "research",
    profiles_path: str | Path = DEFAULT_PROFILES_PATH,
    per_taxon_cap: int = 50,
    max_per_observation: int = 2,
    max_per_observer: int = 10,
    max_per_location: int = 10,
    max_per_season: int = 15,
) -> SelectionResult:
    """Apply license-profile filter, then per-taxon caps + domination limits.

    Deterministic: candidates sorted by (taxon, id/source_media_id/url).
    Domination accounting is per taxon: counts of observation_id, observer
    key, location key, and season within that taxon.
    """
    accept, _review = load_license_profile(profile_name, profiles_path)
    licensed = [c for c in candidates if normalize_license(_get(c, "license")) in accept]
    rejected_license = len(candidates) - len(licensed)

    def sort_key(c: Any) -> tuple:
        return (
            str(_get(c, "taxon", "")),
            str(_get(c, "id", _get(c, "source_media_id", _get(c, "url", "")))),
        )

    ordered = sorted(licensed, key=sort_key)
    selected: list[Any] = []
    skipped: list[Any] = []
    per_taxon: Counter = Counter()
    obs_counts: Counter = Counter()
    observer_counts: Counter = Counter()
    location_counts: Counter = Counter()
    season_counts: Counter = Counter()

    for c in ordered:
        taxon = str(_get(c, "taxon", ""))
        obs = f"{taxon}|{_get(c, 'observation_id', _get(c, 'observation', '?'))}"
        observer = f"{taxon}|{_get(c, 'observer_key', _get(c, 'observer_id', _get(c, 'observer', '?')))}"
        location = f"{taxon}|{_get(c, 'location_key', _get(c, 'location', _get(c, 'grid', '?')))}"
        season = f"{taxon}|{_season_of(c)}"
        if (
            per_taxon[taxon] >= per_taxon_cap
            or obs_counts[obs] >= max_per_observation
            or observer_counts[observer] >= max_per_observer
            or location_counts[location] >= max_per_location
            or season_counts[season] >= max_per_season
        ):
            skipped.append(c)
            continue
        selected.append(c)
        per_taxon[taxon] += 1
        obs_counts[obs] += 1
        observer_counts[observer] += 1
        location_counts[location] += 1
        season_counts[season] += 1

    report = {
        "profile": profile_name,
        "input": len(candidates),
        "licensed": len(licensed),
        "rejected_license": rejected_license,
        "selected": len(selected),
        "skipped_caps": len(skipped),
        "per_taxon": dict(per_taxon),
    }
    return SelectionResult(selected=selected, skipped_caps=skipped, report=report)
