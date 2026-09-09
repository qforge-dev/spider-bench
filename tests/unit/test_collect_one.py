"""Unit tests: one-per-species candidate picking (mock HTTP, no network)."""
from spider_bench.media.collect_one import pick_candidate


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _Client:
    def __init__(self, results):
        self._r = results

    def get(self, url, params=None, timeout=None):
        return _Resp({"results": self._r})


def _obs(obs_id, grade, photos):
    return {"id": obs_id, "quality_grade": grade, "user": {"login": "obs"},
            "photos": photos}


def _photo(pid, lic):
    return {"id": pid, "license_code": lic, "original_url": f"https://static.inaturalist.org/{pid}.jpg",
            "user": {"login": "photog"}, "attribution": f"photog {lic}"}


def test_pick_prefers_research_grade():
    accept = {"CC0-1.0", "CC-BY-4.0"}

    class SeqClient(_Client):
        def __init__(self):
            self.calls = 0

        def get(self, url, params=None, timeout=None):
            self.calls += 1
            if params.get("quality_grade") == "research":
                return _Resp({"results": [_obs(1, "research", [_photo(11, "cc0")])]})
            return _Resp({"results": []})

    c = pick_candidate("Araneus diadematus", accept, SeqClient(), rate_limit=1000)
    assert c is not None and c.research_grade and c.photo_id == 11


def test_pick_falls_back_and_rejects_bad_license():
    results = [_obs(2, "needs_id", [_photo(21, "all-rights-reserved"), _photo(22, "cc-by")])]
    c = pick_candidate("Pisaura mirabilis", {"CC-BY-4.0"}, _Client(results), rate_limit=1000)
    assert c is not None and c.photo_id == 22 and not c.research_grade


def test_pick_none_when_no_photos():
    c = pick_candidate("Xyz abc", {"CC0-1.0"}, _Client([]), rate_limit=1000)
    assert c is None
