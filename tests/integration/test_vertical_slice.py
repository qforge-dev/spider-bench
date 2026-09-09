"""Integration: full workflow against temp SQLite (offline; S3 not touched)."""

from spider_bench.dataset.manifest import build_release
from spider_bench.dataset.publish import verify_release
from spider_bench.db import ensure_migrated, get_connection
from spider_bench.media.deduplicate import MediaRecord, find_duplicate_groups
from spider_bench.media.validate import validate_bytes
from spider_bench.sources.polish_checklist import ingest_checklist
from spider_bench.sources.world_spider_catalog import ingest_wsc
from spider_bench.taxonomy.reconcile import reconcile_all


def _wsc():
    return [
        {"scientific_name": "Araneus diadematus", "authorship": "Clerck, 1757",
         "rank": "species", "family": "Araneidae", "genus": "Araneus", "wsc_id": "w1"},
        {"scientific_name": "Argiope bruennichi", "authorship": "(Scopoli, 1772)",
         "rank": "species", "family": "Araneidae", "genus": "Argiope", "wsc_id": "w2"},
    ]


def test_vertical_slice(tmp_path):
    db = tmp_path / "t.sqlite"
    conn = get_connection(db)
    ensure_migrated(conn)
    assert ingest_wsc(conn, _wsc(), snapshot_id="s1")["taxa"] == 2
    # resume: ingesting twice does not duplicate
    assert ingest_wsc(conn, _wsc(), snapshot_id="s1")["taxa"] == 2
    rows = [{"original_name": r["scientific_name"], "membership_status": "present"} for r in _wsc()]
    assert ingest_checklist(conn, rows, source="chk", version="v1")["rows"] == 2

    recs = reconcile_all(
        [{"original_name": r["scientific_name"]} for r in _wsc()],
        [{"scientific_name": r["scientific_name"]} for r in _wsc()],
    )
    assert all(r["match_type"] == "exact" for r in recs)

    # corrupt media rejected, duplicates grouped
    assert not validate_bytes(b"not-an-image").ok
    recs_m = [MediaRecord(key="a", sha256="s", ahash=1, dhash=1, source="inat", source_media_id="o1"),
              MediaRecord(key="b", sha256="s", ahash=1, dhash=1, source="inat", source_media_id="o1")]
    groups = find_duplicate_groups(recs_m)
    assert len(groups) == 1 and len(groups[0].members) == 2

    taxa = [{"taxon": r["scientific_name"]} for r in _wsc()]
    evidence = [{"id": f"e{i}", "taxon": t["taxon"], "doi": f"10.1234/x.{i}"}
                for i, t in enumerate(taxa)]
    danger = [{"taxon": t["taxon"], "category": "uncertain",
               "geographic_scope": "Poland", "rationale": "insufficient evidence",
               "evidence_ids": [e["id"]], "reviewer": "r@x.pl",
               "review_date": "2026-09-09", "status": "approved"}
              for t, e in zip(taxa, evidence)]
    out = tmp_path / "rel"
    build_release(taxa, [], [], danger, evidence, out, version="0.1.0-test")
    assert verify_release(out, expected_version="0.1.0-test") == []

    # incomplete danger (no evidence) must fail gates
    bad = [dict(danger[0], evidence_ids=[])]
    build_release(taxa, [], [], bad, evidence, out, version="0.1.0-test")
    assert verify_release(out, expected_version="0.1.0-test") != []
    conn.close()
