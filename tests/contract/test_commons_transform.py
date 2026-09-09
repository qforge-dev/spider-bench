"""Contract: Wikimedia Commons file transform incl. review flag (pure, no network)."""
from __future__ import annotations

import inspect
import sqlite3

from spider_bench.sources import commons as m

SNAP = "2026-09-09"


def _page(**over):
    base = {
        "pageid": 7654321,
        "title": "File:Araneus diadematus (fixture).jpg",
        "lastrevid": 100200300,
        "templates": [{"title": "Template:CC-BY-SA-4.0"}, {"title": "Template:GFDL"}],
        "categories": [{"title": "Category:Araneus diadematus"}],
        "imageinfo": [
            {"timestamp": "2024-05-01T08:00:00Z", "user": "FixtureUploader",
             "url": "https://upload.wikimedia.org/wikipedia/commons/f/f1/fixture.jpg",
             "descriptionurl": "https://commons.wikimedia.org/wiki/File:Araneus_diadematus_(fixture).jpg",
             "size": 1, "width": 10, "height": 10, "sha1": "abc", "mime": "image/jpeg", "revid": 100200300,
             "extmetadata": {
                 "Artist": {"value": "Fixture Uploader"},
                 "LicenseShortName": {"value": "CC BY-SA 4.0, GFDL"},
                 "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"},
                 "UsageTerms": {"value": "Dual licensed CC BY-SA 4.0 and GFDL multi-license"}}}
        ],
    }
    base.update(over)
    return base


def _clean_page():
    p = _page(templates=[{"title": "Template:CC-BY-4.0"}])
    p["imageinfo"][0]["extmetadata"]["LicenseShortName"] = {"value": "CC BY 4.0"}
    p["imageinfo"][0]["extmetadata"]["LicenseUrl"] = {"value": "https://creativecommons.org/licenses/by/4.0/"}
    p["imageinfo"][0]["extmetadata"]["UsageTerms"] = {"value": "Licensed CC BY 4.0"}
    return p


def test_captures_revision_creator_license_attribution_urls():
    t = m.transform_file(_clean_page(), SNAP)
    assert t["revid"] == 100200300
    assert t["creator"] == "Fixture Uploader"
    assert t["attribution"]
    assert t["license"] == "cc-by"
    assert t["license_url"].startswith("https://")
    assert t["media_url"].startswith("https://")
    assert t["file_page_url"].startswith("https://commons.wikimedia.org/wiki/")
    assert t["snapshot_date"] == SNAP
    assert len(t["media"]) == 1


def test_ambiguous_multi_license_goes_to_review():
    t = m.transform_file(_page(), SNAP)
    assert t["needs_review"] is True
    assert t["review_reason"]
    assert t["media"][0]["needs_review"] is True


def test_unknown_license_goes_to_review():
    p = _clean_page()
    p["imageinfo"][0]["extmetadata"]["LicenseShortName"] = {"value": ""}
    p["imageinfo"][0]["extmetadata"]["LicenseUrl"] = {"value": ""}
    p["imageinfo"][0]["extmetadata"]["UsageTerms"] = {"value": "All rights reserved, permission required"}
    t = m.transform_file(p, SNAP)
    assert t["needs_review"] is True


def test_network_signatures_include_required_params():
    for fn in (m.discover_files_sync, m.discover_files_async, m.upload_raw_jsonl_to_s3):
        sig = inspect.signature(fn)
        for name in ("dry_run", "max_records", "resume", "rate_limit"):
            assert name in sig.parameters, f"{fn.__name__} missing {name}"


def test_dry_run_no_network_and_idempotent_insert():
    recs, _ = m.discover_files_sync(search="x", dry_run=True, max_records=5, resume={}, rate_limit=1.0)
    assert recs == []
    conn = sqlite3.connect(":memory:")
    t = m.transform_file(_clean_page(), SNAP)
    assert m.insert_files_idempotent(conn, [t]) == {"files": 1}
    assert m.insert_files_idempotent(conn, [t]) == {"files": 0}
    conn.close()
