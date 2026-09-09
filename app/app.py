"""Spider search app: browse the Polish spider dataset locally.

Reads the 0.2.0 release (local `data/releases/...` if present, else the
public S3 bucket). No build step, no JS framework.
Run:  python -m app.app   (or: flask --app app.app run)
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from urllib.request import urlopen

from flask import Flask, abort, render_template, request

BUCKET = "spiders-dataset-088543363904"
VERSION = "0.2.0"
LOCAL_DIR = Path("data/releases/polish-spiders") / VERSION

app = Flask(__name__)


def _read_parquet(name: str) -> list[dict]:
    local = LOCAL_DIR / f"{name}.parquet"
    if local.exists():
        import pyarrow.parquet as pq

        table = pq.read_table(local)
        cols = table.column_names
        return [{c: table.column(c)[i].as_py() for c in cols} for i in range(table.num_rows)]
    # fallback: public S3 (deploy without local data/)
    import pyarrow.parquet as pq

    url = f"https://{BUCKET}.s3.us-east-1.amazonaws.com/poland/releases/{VERSION}/{name}.parquet"
    with urlopen(url, timeout=60) as resp:
        table = pq.read_table(io.BytesIO(resp.read()))
    cols = table.column_names
    return [{c: table.column(c)[i].as_py() for c in cols} for i in range(table.num_rows)]


@lru_cache(maxsize=1)
def dataset() -> dict:
    taxa = _read_parquet("taxa")
    media = _read_parquet("media")
    attribution = _read_parquet("attribution")
    danger = _read_parquet("danger_assessments")
    media_by_taxon = {m["taxon"]: m for m in media}
    danger_by_taxon = {d["taxon"]: d for d in danger}
    families = sorted({t.get("family", "") for t in taxa if t.get("family")})
    return {"taxa": taxa, "media_by_taxon": media_by_taxon,
            "danger_by_taxon": danger_by_taxon, "families": families,
            "attribution": {a.get("sha256"): a for a in attribution}}


@app.get("/")
def index():
    ds = dataset()
    q = request.args.get("q", "").strip().lower()
    family = request.args.get("family", "")
    image = request.args.get("image", "all")  # all | with | without
    danger_cat = request.args.get("danger", "")

    rows = []
    for t in ds["taxa"]:
        name = t["taxon"]
        if q and q not in name.lower():
            continue
        if family and t.get("family") != family:
            continue
        m = ds["media_by_taxon"].get(name)
        if image == "with" and not m:
            continue
        if image == "without" and m:
            continue
        d = ds["danger_by_taxon"].get(name)
        if danger_cat and (not d or d.get("category") != danger_cat):
            continue
        rows.append({"taxon": name, "family": t.get("family", ""), "genus": t.get("genus", ""),
                     "authorship": t.get("authorship", ""), "has_image": bool(m),
                     "thumb": (m or {}).get("public_url", ""),
                     "danger": (d or {}).get("category", "not_assessed")})
    rows.sort(key=lambda r: r["taxon"])
    return render_template("index.html", rows=rows, total=len(ds["taxa"]),
                           q=request.args.get("q", ""), family=family, image=image,
                           danger=danger_cat, families=ds["families"])


@app.get("/spider/<path:name>")
def detail(name: str):
    ds = dataset()
    taxon = next((t for t in ds["taxa"] if t["taxon"] == name), None)
    if taxon is None:
        abort(404)
    m = ds["media_by_taxon"].get(name)
    d = ds["danger_by_taxon"].get(name)
    attr = ds["attribution"].get((m or {}).get("sha256", ""), {}) if m else {}
    photo_page = None
    if m and m.get("source_record", "").startswith("inaturalist:photo:"):
        photo_page = f"https://www.inaturalist.org/photos/{m['source_record'].split(':')[-1]}"
    return render_template("detail.html", taxon=taxon, media=m, danger=d,
                           attribution=attr, photo_page=photo_page)


if __name__ == "__main__":
    app.run(debug=True)
