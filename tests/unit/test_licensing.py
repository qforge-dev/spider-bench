"""Unit tests: license profiles + attribution."""
import pytest

from spider_bench.licensing import (
    build_attribution,
    classify_license,
    is_accepted,
    load_profiles,
    review_required,
)

PROFILES = {
    "version": 1,
    "profiles": {
        "research": {
            "accept": ["CC0-1.0", "CC-BY-4.0"],
            "review_required": ["CC-BY-SA-4.0"],
        }
    },
}


def test_load_real_profiles():
    profiles = load_profiles("configs/license-profiles.yaml")
    assert "research" in profiles["profiles"]
    assert profiles["version"] >= 1


def test_classify():
    assert classify_license("CC0-1.0", "research", PROFILES) == "accepted"
    assert classify_license("CC-BY-SA-4.0", "research", PROFILES) == "review_required"
    assert classify_license("ARR", "research", PROFILES) == "rejected"
    assert is_accepted("CC-BY-4.0", "research", PROFILES)
    assert review_required("CC-BY-SA-4.0", "research", PROFILES)
    assert not is_accepted("CC-BY-SA-4.0", "research", PROFILES)


def test_unknown_profile():
    with pytest.raises(ValueError, match="unknown license profile"):
        classify_license("CC0-1.0", "nope", PROFILES)


def test_attribution_ok():
    text = build_attribution("Jane Doe", "CC-BY-4.0",
                             "https://creativecommons.org/licenses/by/4.0/",
                             "https://example.org/photo/1", "Spider")
    assert "Jane Doe" in text and "CC-BY-4.0" in text and text.endswith(".")


def test_attribution_requires_creator_and_license():
    with pytest.raises(ValueError, match="creator"):
        build_attribution("", "CC-BY-4.0")
    with pytest.raises(ValueError, match="license"):
        build_attribution("Jane", "")
