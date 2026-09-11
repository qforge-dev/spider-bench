"""License profiles: acceptance, review gates, attribution.

Profiles live in version-controlled ``configs/license-profiles.yaml`` and the
active profile version is stored with every release (never rely on live source
license state).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PROFILES_PATH = Path("configs/license-profiles.yaml")


def load_profiles(path: str | Path = PROFILES_PATH) -> dict[str, Any]:
    """Load the versioned license-profile document.

    Returns the raw mapping ``{"version": int, "profiles": {...}}``.
    """
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "profiles" not in data:
        raise ValueError(f"{p}: expected mapping with 'profiles' key")
    return data


def _profile(profiles: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        return profiles["profiles"][name]
    except KeyError:
        raise ValueError(
            f"unknown license profile {name!r} (known: {sorted(profiles['profiles'])})"
        ) from None


def accepted_licenses(profiles: dict[str, Any], name: str) -> frozenset[str]:
    return frozenset(_profile(profiles, name).get("accept") or [])


def review_licenses(profiles: dict[str, Any], name: str) -> frozenset[str]:
    return frozenset(_profile(profiles, name).get("review_required") or [])


def is_accepted(license_id: str, profile: str, profiles: dict[str, Any] | None = None) -> bool:
    """True when the license id is directly accepted under the profile."""
    profiles = profiles if profiles is not None else load_profiles()
    return license_id in accepted_licenses(profiles, profile)


def review_required(
    license_id: str, profile: str, profiles: dict[str, Any] | None = None
) -> bool:
    """True when the license needs a documented decision before inclusion (e.g. ShareAlike)."""
    profiles = profiles if profiles is not None else load_profiles()
    return license_id in review_licenses(profiles, profile)


def classify_license(
    license_id: str, profile: str, profiles: dict[str, Any] | None = None
) -> str:
    """Return 'accepted' | 'review_required' | 'rejected'."""
    if is_accepted(license_id, profile, profiles):
        return "accepted"
    if review_required(license_id, profile, profiles):
        return "review_required"
    return "rejected"


def build_attribution(
    creator: str,
    license_id: str,
    license_url: str = "",
    source_url: str = "",
    title: str = "",
) -> str:
    """Build required attribution text. Creator + license are mandatory.

    Raises ValueError when creator or license is missing.
    """
    if not creator or not creator.strip():
        raise ValueError("attribution requires creator")
    if not license_id or not license_id.strip():
        raise ValueError("attribution requires a license identifier")
    work = f"“{title}” by " if title and title.strip() else ""
    parts = [f"{work}{creator.strip()}"]
    lic = license_id.strip()
    if license_url and license_url.strip():
        lic = f"{lic} ({license_url.strip()})"
    parts.append(f"licensed under {lic}")
    if source_url and source_url.strip():
        parts.append(f"source: {source_url.strip()}")
    return ", ".join(parts) + "."
