"""Pure unit tests: no network, no S3. Covers hash, validate, dedup, select."""
from __future__ import annotations

import hashlib
import io
import sqlite3

from PIL import Image

from spider_bench.media.deduplicate import (
    MediaRecord,
    duplicate_report,
    find_duplicate_groups,
    write_duplicate_groups,
)
from spider_bench.media.hash import (
    ahash_bytes,
    content_key,
    dhash_bytes,
    hamming,
    sha256_bytes,
    sha256_stream,
)
from spider_bench.media.select import filter_by_license, select_media
from spider_bench.media.validate import validate_bytes

PROFILES = "configs/license-profiles.yaml"


def _img_bytes(size=(128, 128), color=(200, 30, 40), fmt="PNG") -> bytes:
    img = Image.new("RGB", size, color)
    # add variation so it is not near-empty
    px = img.load()
    for x in range(0, size[0], 4):
        for y in range(0, size[1], 4):
            px[x, y] = (10, 200, 90)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _solid(size=(128, 128), color=(128, 128, 128), fmt="PNG") -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# --- hash ---


def test_sha256_bytes_matches_hashlib():
    assert sha256_bytes(b"abc") == hashlib.sha256(b"abc").hexdigest()


def test_sha256_stream_matches_bytes():
    data = b"x" * 100_000
    assert sha256_stream(io.BytesIO(data)) == hashlib.sha256(data).hexdigest()


def test_content_key_layout():
    sha = "ab" + "cd" + "e" * 60
    assert content_key("poland/", sha, "jpg") == f"poland/media/sha256/ab/cd/{sha}.jpg"
    assert content_key("poland", sha, ".JPEG") == f"poland/media/sha256/ab/cd/{sha}.jpg"


def test_perceptual_hash_determinism_and_distance():
    a = _img_bytes()
    b = _img_bytes()
    assert ahash_bytes(a) == ahash_bytes(b)
    assert dhash_bytes(a) == dhash_bytes(b)
    assert hamming(0b1010, 0b1000) == 1


# --- validate ---


def test_validate_ok_png():
    vr = validate_bytes(_img_bytes(), declared_content_type="image/png")
    assert vr.ok and vr.failure_code is None
    assert (vr.width, vr.height) == (128, 128)
    assert vr.ext == "png"


def test_validate_rejects_html():
    vr = validate_bytes(b"<html><body>nope</body></html>" + b" " * 600)
    assert not vr.ok and vr.failure_code == "html_error_page"


def test_validate_rejects_corrupt():
    vr = validate_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000)
    assert not vr.ok and vr.failure_code in {"decode_error", "unsupported_format", "corrupt"}


def test_validate_rejects_unsupported_gif():
    img = Image.new("RGB", (64, 64), (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="GIF")
    vr = validate_bytes(buf.getvalue())
    assert not vr.ok and vr.failure_code == "unsupported_format"


def test_validate_rejects_tiny_dimensions():
    vr = validate_bytes(_img_bytes(size=(16, 16)))
    assert not vr.ok and vr.failure_code in {"tiny", "tiny_bytes"}


def test_validate_rejects_near_empty():
    vr = validate_bytes(_solid())
    assert not vr.ok and vr.failure_code == "near_empty"


def test_validate_mime_mismatch():
    vr = validate_bytes(_img_bytes(), declared_content_type="image/jpeg")
    assert not vr.ok and vr.failure_code == "mime_mismatch"


# --- dedup ---


def test_dedup_exact_sha_grouping():
    recs = [
        MediaRecord(key="a", sha256="s1", ahash=1, dhash=1),
        MediaRecord(key="b", sha256="s1", ahash=1, dhash=1),
        MediaRecord(key="c", sha256="s2", ahash=2, dhash=999),
    ]
    groups = find_duplicate_groups(recs)
    assert len(groups) == 1
    assert groups[0].members == ["a", "b"] and groups[0].method == "sha256"


def test_dedup_source_link_grouping():
    recs = [
        MediaRecord(key="a", sha256="s1", source="inaturalist", source_media_id="42"),
        MediaRecord(key="b", sha256="s2", source="inaturalist", source_media_id="42"),
    ]
    groups = find_duplicate_groups(recs)
    assert len(groups) == 1 and groups[0].method == "source_link"


def test_dedup_perceptual_threshold():
    recs = [
        MediaRecord(key="a", sha256="s1", ahash=0b0, dhash=0b0),
        MediaRecord(key="b", sha256="s2", ahash=0b11, dhash=0b01),  # distance 2/1
        MediaRecord(key="c", sha256="s3", ahash=2**64 - 1, dhash=2**64 - 1),
    ]
    groups = find_duplicate_groups(recs, phash_threshold=6)
    assert len(groups) == 1 and set(groups[0].members) == {"a", "b"}
    assert duplicate_report(groups)["groups"] == 1


def test_dedup_canonical_and_sqlite_persist():
    recs = [
        MediaRecord(key="a", sha256="s1", width=10, height=10),
        MediaRecord(key="b", sha256="s1", width=100, height=100),
    ]
    groups = find_duplicate_groups(recs)
    assert groups[0].canonical_key == "b"  # largest area wins
    conn = sqlite3.connect(":memory:")
    n = write_duplicate_groups(conn, groups)
    assert n == 2
    rows = conn.execute("SELECT COUNT(*) FROM duplicate_groups").fetchone()[0]
    assert rows == 2


# --- select ---


def _cand(i, taxon="Araneus diadematus", lic="CC-BY-4.0", obs=None, watcher="o1", loc="L1", month=6):
    return {
        "id": f"m{i}",
        "taxon": taxon,
        "license": lic,
        "observation_id": obs or f"obs{i}",
        "observer_key": watcher,
        "location_key": loc,
        "month": month,
    }


def test_select_license_filter():
    cands = [_cand(1, lic="CC-BY-4.0"), _cand(2, lic="CC-BY-NC-SA-4.0"), _cand(3, lic="ARR")]
    ok, review, rejected = filter_by_license(cands, "research", PROFILES)
    assert [c["id"] for c in ok] == ["m1"]
    assert [c["id"] for c in review] == ["m2"]
    assert [c["id"] for c in rejected] == ["m3"]


def test_select_per_taxon_cap():
    cands = [_cand(i, obs=f"obs{i}", watcher=f"o{i}", loc=f"L{i}", month=(i % 12) + 1) for i in range(10)]
    res = select_media(cands, profiles_path=PROFILES, per_taxon_cap=3)
    assert len(res.selected) == 3 and res.report["per_taxon"]
    assert len(res.skipped_caps) == 7


def test_select_domination_limits():
    # one observation dominating: max 2 kept
    cands = [_cand(i, obs="obs1", watcher=f"o{i}", loc=f"L{i}") for i in range(5)]
    res = select_media(cands, profiles_path=PROFILES, per_taxon_cap=50, max_per_observation=2)
    assert len(res.selected) == 2
    # one observer dominating
    cands = [_cand(i, obs=f"obs{i}", watcher="same", loc=f"L{i}") for i in range(5)]
    res = select_media(cands, profiles_path=PROFILES, per_taxon_cap=50, max_per_observer=2)
    assert len(res.selected) == 2
    # one season dominating (all month=6 -> summer)
    cands = [_cand(i, obs=f"obs{i}", watcher=f"o{i}", loc=f"L{i}", month=6) for i in range(5)]
    res = select_media(cands, profiles_path=PROFILES, per_taxon_cap=50, max_per_season=2)
    assert len(res.selected) == 2
