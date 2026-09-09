from spider_bench.storage.s3 import media_key, raw_metadata_key, release_key


def test_media_key():
    assert media_key("poland/", "ab" + "cd" + "e" * 60, "jpg") == "poland/media/sha256/ab/cd/" + "ab" + "cd" + "e" * 60 + ".jpg"


def test_prefix_layout():
    assert raw_metadata_key("poland/", "inaturalist", "2026-09-09", "obs.jsonl").startswith("poland/raw-metadata/")
    assert release_key("poland/", "0.1.0", "release.json") == "poland/releases/0.1.0/release.json"
