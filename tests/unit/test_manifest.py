"""Unit tests: manifest determinism + §11 verify gates."""
import hashlib
import json

import pyarrow.parquet as pq

from spider_bench.dataset.manifest import build_release
from spider_bench.dataset.publish import verify_release


def _payload(sha="a" * 64):
    taxa = [{"taxon": "Cheiracanthium punctorium", "authorship": "(Villers, 1789)",
             "family": "Cheiracanthiidae"}]
    media = [{"sha256": sha, "s3_uri": f"s3://b/poland/media/{sha}.jpg",
              "creator": "Jane Doe", "license": "CC-BY-4.0",
              "source_record": "inat:obs:1", "taxon": "Cheiracanthium punctorium"}]
    attribution = [{"sha256": sha, "creator": "Jane Doe", "license": "CC-BY-4.0",
                    "attribution": "Jane Doe, licensed under CC-BY-4.0."}]
    evidence = [{"id": "s1", "taxon": "Cheiracanthium punctorium",
                 "doi": "10.1/abc", "pmid": "", "isbn": "", "url": ""}]
    danger = [{"taxon": "Cheiracanthium punctorium", "category": "minor_local_effects",
               "geographic_scope": "Poland", "rationale": "case reports",
               "evidence_ids": ["s1"], "reviewer": "r@example.org",
               "review_date": "2026-01-15", "status": "approved"}]
    return taxa, media, attribution, danger, evidence


def test_build_release_deterministic(tmp_path):
    taxa, media, attribution, danger, evidence = _payload()
    d1, d2 = tmp_path / "r1", tmp_path / "r2"
    build_release(taxa, media, attribution, danger, evidence, d1, version="0.1.0")
    build_release(taxa, media, attribution, danger, evidence, d2, version="0.1.0")
    h1 = hashlib.sha256((d1 / "media.parquet").read_bytes()).hexdigest()
    h2 = hashlib.sha256((d2 / "media.parquet").read_bytes()).hexdigest()
    assert h1 == h2
    # sorted columns + rows
    table = pq.read_table(d1 / "taxa.parquet")
    assert table.column_names == sorted(table.column_names)
    assert json.loads((d1 / "release.json").read_text())["counts"]["media"] == 1


def test_verify_ok(tmp_path):
    taxa, media, attribution, danger, evidence = _payload()
    build_release(taxa, media, attribution, danger, evidence, tmp_path, version="0.1.0")
    assert verify_release(tmp_path, expected_version="0.1.0") == []


def test_verify_missing_creator_fails(tmp_path):
    taxa, media, attribution, danger, evidence = _payload()
    media[0] = {**media[0], "creator": ""}
    build_release(taxa, media, attribution, danger, evidence, tmp_path, version="0.1.0")
    errors = verify_release(tmp_path, expected_version="0.1.0")
    assert any("creator" in e for e in errors)


def test_verify_missing_evidence_as_none_known_fails(tmp_path):
    taxa, media, attribution, danger, evidence = _payload()
    danger = [{**danger[0], "category": "none_known", "evidence_ids": []}]
    build_release(taxa, media, attribution, [], evidence, tmp_path, version="0.1.0")
    # rewrite danger parquet with the invalid row (build would also accept it —
    # the gate must catch it)
    import pyarrow as pa
    import pyarrow.parquet as pq2

    table = pa.table({k: [r[k]] for k in sorted(danger[0].keys()) for r in danger})
    pq2.write_table(table, tmp_path / "danger_assessments.parquet")
    errors = verify_release(tmp_path, expected_version="0.1.0")
    assert any("none_known" in e or "evidence" in e for e in errors)


def test_verify_bad_evidence_ref_fails(tmp_path):
    taxa, media, attribution, danger, evidence = _payload()
    danger[0] = {**danger[0], "evidence_ids": ["missing-id"]}
    build_release(taxa, media, attribution, danger, evidence, tmp_path, version="0.1.0")
    errors = verify_release(tmp_path, expected_version="0.1.0")
    assert any("unsupported evidence" in e for e in errors)


def test_verify_checksum_mismatch(tmp_path):
    taxa, media, attribution, danger, evidence = _payload()
    build_release(taxa, media, attribution, danger, evidence, tmp_path, version="0.1.0")
    with (tmp_path / "taxa.parquet").open("ab") as fh:
        fh.write(b"corrupt")
    errors = verify_release(tmp_path, expected_version="0.1.0")
    assert any("checksum" in e for e in errors)


def test_verify_duplicate_media_fails(tmp_path):
    taxa, media, attribution, danger, evidence = _payload()
    media = [media[0], dict(media[0])]
    build_release(taxa, media, attribution, danger, evidence, tmp_path, version="0.1.0")
    errors = verify_release(tmp_path, expected_version="0.1.0")
    assert any("duplicate" in e.lower() for e in errors)
