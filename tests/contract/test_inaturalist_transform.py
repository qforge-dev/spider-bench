"""Contract: iNaturalist observation transform (pure, no network)."""
from __future__ import annotations

import inspect
import sqlite3

from spider_bench.sources import inaturalist as m

SNAP = "2026-09-09"

INLINE_OBS = {
    "id": 123456789,
    "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "observed_on": "2025-06-14",
    "created_at": "2025-06-14T10:00:00+02:00",
    "updated_at": "2025-06-16T12:00:00+02:00",
    "quality_grade": "research",
    "captive": False,
    "license_code": "CC-BY-NC",
    "uri": "https://www.inaturalist.org/observations/123456789",
    "geoprivacy": "open",
    "coordinates_obscured": False,
    "latitude": 52.2297,
    "longitude": 21.0122,
    "place_guess": "Warszawa, Poland",
    "geojson": {"type": "Point", "coordinates": [21.0122, 52.2297]},
    "user": {"id": 4242, "login": "fixture_observer"},
    "taxon": {"id": 47118, "name": "Araneae", "rank": "order"},
    "photos": [
        {"id": 111, "license_code": "CC-BY-NC", "attribution": "(c) fixture_observer (CC BY-NC)",
         "url": "https://example.org/p/111/square.jpg", "original_url": "https://example.org/p/111/original.jpg",
         "user": {"id": 4242, "login": "fixture_observer"}},
        {"id": 112, "license_code": "CC-BY-NC", "attribution": "(c) fixture_observer (CC BY-NC)",
         "url": "https://example.org/p/112/square.jpg", "original_url": "https://example.org/p/112/original.jpg",
         "user": {"id": 4242, "login": "fixture_observer"}},
    ],
}


def test_groups_photos_by_observation_and_preserves_ids():
    t = m.transform_observation(INLINE_OBS, SNAP)
    assert t["observation_id"] == 123456789
    assert t["observer_login"] == "fixture_observer"
    assert t["source_url"] == "https://www.inaturalist.org/observations/123456789"
    assert t["snapshot_date"] == SNAP
    assert t["source_taxon_id"] == 47118
    assert len(t["media"]) == 2
    assert {x["photo_id"] for x in t["media"]} == {111, 112}
    assert all(x["observation_id"] == 123456789 for x in t["media"])
    assert all(x["license"] == "cc-by-nc" for x in t["media"])
    assert all(x["media_url"] for x in t["media"])
    assert all(x["snapshot_date"] == SNAP for x in t["media"])
    # timestamps preserved
    assert t["created_at"].startswith("2025-06-14")


def test_search_response_groups_each_observation():
    payload = {"total_results": 1, "results": [INLINE_OBS]}
    out = m.transform_search_response(payload, SNAP)
    assert len(out) == 1 and len(out[0]["media"]) == 2


def test_search_params_encode_required_filters():
    p = m.build_search_params()
    assert p["taxon_id"] == m.TAXON_ARANEAE_ID
    assert p["place_id"] == m.PLACE_POLAND_ID
    assert p["photos"] == "true"
    assert "CC" in p["photo_license"] or "cc" in p["photo_license"].lower()


def test_network_signatures_include_required_params():
    for fn in (m.discover_observations_sync, m.discover_observations_async, m.upload_raw_jsonl_to_s3):
        sig = inspect.signature(fn)
        for name in ("dry_run", "max_records", "resume", "rate_limit"):
            assert name in sig.parameters, f"{fn.__name__} missing {name}"


def test_dry_run_returns_empty_without_network():
    recs, cur = m.discover_observations_sync(dry_run=True, max_records=5, resume={}, rate_limit=1.0)
    assert recs == []
    assert m.upload_raw_jsonl_to_s3(recs, bucket="b", prefix="poland/", snapshot=SNAP, dry_run=True) is None


def test_idempotent_insert():
    conn = sqlite3.connect(":memory:")
    t = m.transform_observation(INLINE_OBS, SNAP)
    c1 = m.insert_observations_idempotent(conn, [t])
    c2 = m.insert_observations_idempotent(conn, [t])
    assert c1 == {"observations": 1, "media": 2}
    assert c2 == {"observations": 0, "media": 0}
    conn.close()


def test_never_touches_image_bytes():
    src = inspect.getsource(m)
    assert "original.jpg" not in src or "original_url" in src  # metadata URLs only
    for banned in ("PIL.Image", "Image.open", "response.content", ".read()", "shutil.copyfileobj"):
        assert banned not in src
