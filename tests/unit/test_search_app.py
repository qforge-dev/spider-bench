"""Smoke tests for the local search app (uses local 0.2.0 release)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.app import app


def test_index_lists_all():
    c = app.test_client()
    r = c.get("/")
    assert r.status_code == 200
    assert r.text.count('class="card"') == 854


def test_search_and_filters():
    c = app.test_client()
    assert c.get("/?q=bruennichi").text.count('class="card"') == 1
    assert 'value="Araneidae" selected' in c.get("/?family=Araneidae").text
    r = c.get("/?image=without")
    assert r.status_code == 200 and "no image" in r.text


def test_detail_and_404():
    c = app.test_client()
    assert c.get("/spider/Araneus%20diadematus").status_code == 200
    assert c.get("/spider/Nope%20nada").status_code == 404
