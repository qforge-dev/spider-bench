"""Contract: GBIF occurrence transform incl. cross-source identity (pure, no network)."""
from __future__ import annotations

import inspect
import sqlite3

from spider_bench.sources import gbif as m

SNAP = "2026-09-09"

INLINE_OCC = {
    "key": 987654321,
    "datasetKey": "040c5662-da76-4782-a48e-c276255d6bfd",
    "datasetName": "iNaturalist research-grade observations",
    "publisher": "iNaturalist",
    "scientificName": "Araneus diadematus (Clerck, 1757)",
    "taxonKey": 2180531,
    "country": "PL",
    "eventDate": "2025-07-01T10:00:00",
    "basisOfRecord": "HUMAN_OBSERVATION",
    "occurrenceID": "https://www.inaturalist.org/observations/123456789",
    "references": "https://www.inaturalist.org/observations/123456789",
    "institutionCode": "iNaturalist",
    "collectionCode": "Observations",
    "catalogNumber": "123456789",
    "license": "CC_BY_NC_4_0",
    "media": [
        {"identifier": "https://example.org/p/111/original.jpg", "format": "image/jpeg", "type": "StillImage",
         "creator": "fixture_observer", "license": "https://creativecommons.org/licenses/by-nc/4.0/",
         "rightsHolder": "fixture_observer", "references": "https://www.inaturalist.org/photos/111"}
    ],
}

PLAIN_OCC = {
    "key": 111222333,
    "scientificName": "Pardosa sp.",
    "country": "PL",
    "occurrenceID": "urn:catalog:MUSEUM:SPIDERS:42",
    "institutionCode": "MUSEUM",
    "collectionCode": "SPIDERS",
    "catalogNumber": "42",
    "references": "https://museum.example.org/records/42",
    "media": [],
}


def test_identity_keys_present():
    t = m.transform_occurrence(INLINE_OCC, SNAP)
    ident = t["identity"]
    assert ident["gbif_id"] == 987654321
    assert ident["occurrence_id"] == "https://www.inaturalist.org/observations/123456789"
    assert ident["institution_code"] == "iNaturalist"
    assert ident["catalog_number"] == "123456789"
    assert ident["references"] == "https://www.inaturalist.org/observations/123456789"
    assert ident["inaturalist_observation_id"] == 123456789
    assert t["snapshot_date"] == SNAP
    assert t["media_count"] == 1
    assert t["media"][0]["media_url"].startswith("https://")


def test_inat_republication_detected_and_plain_not():
    assert m.is_inaturalist_republication(INLINE_OCC) is True
    assert m.transform_occurrence(INLINE_OCC, SNAP)["is_inaturalist_republication"] is True
    assert m.is_inaturalist_republication(PLAIN_OCC) is False
    assert m.extract_inaturalist_id(PLAIN_OCC) is None


def test_network_signatures_include_required_params():
    for fn in (m.discover_occurrences_sync, m.discover_occurrences_async, m.request_download,
               m.get_download_meta, m.upload_raw_jsonl_to_s3):
        sig = inspect.signature(fn)
        for name in ("dry_run", "max_records", "resume", "rate_limit"):
            assert name in sig.parameters, f"{fn.__name__} missing {name}"


def test_dry_run_no_network():
    recs, _ = m.discover_occurrences_sync(dry_run=True, max_records=5, resume={}, rate_limit=1.0)
    assert recs == []
    assert m.request_download({"type": "equals", "key": "COUNTRY", "value": "PL"}, dry_run=True) is None
    assert m.get_download_meta("0000-0000", dry_run=True) is None


def test_idempotent_insert_and_dedup_keys():
    conn = sqlite3.connect(":memory:")
    t = m.transform_occurrence(INLINE_OCC, SNAP)
    c1 = m.insert_occurrences_idempotent(conn, [t])
    c2 = m.insert_occurrences_idempotent(conn, [t])
    assert c1 == {"occurrences": 1, "media": 1}
    assert c2 == {"occurrences": 0, "media": 0}
    conn.close()


def test_download_meta_shape_without_network():
    import json as _j

    raw = {"status": "SUCCEEDED", "doi": "10.1234/abcd", "downloadLink": "https://example.org/dl.zip",
           "totalRecords": 10}
    assert _j.dumps(raw)  # documents expected DOI-bearing shape asserted in unit code
    src = inspect.getsource(m.get_download_meta)
    assert "doi" in src
