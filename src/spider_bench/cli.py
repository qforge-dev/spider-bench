"""CLI: composable, idempotent, S3-native (plan §6)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from botocore.exceptions import ClientError, NoCredentialsError

from spider_bench.config import load_country_config
from spider_bench.db import init_db
from spider_bench.storage import s3 as s3mod

app = typer.Typer(no_args_is_help=True)

taxonomy_app = typer.Typer(no_args_is_help=True)
discover_app = typer.Typer(no_args_is_help=True)
audit_app = typer.Typer(no_args_is_help=True)
media_app = typer.Typer(no_args_is_help=True)
danger_app = typer.Typer(no_args_is_help=True)
release_app = typer.Typer(no_args_is_help=True)
benchmark_app = typer.Typer(no_args_is_help=True)

app.add_typer(taxonomy_app, name="taxonomy")
app.add_typer(discover_app, name="discover")
app.add_typer(audit_app, name="audit")
app.add_typer(media_app, name="media")
app.add_typer(danger_app, name="danger")
app.add_typer(release_app, name="release")
app.add_typer(benchmark_app, name="benchmark")


def _cfg(config: str):
    return load_country_config(config)


def _suite_tasks(suite: str, tasks: str | None) -> Path:
    """Explicit --tasks wins; otherwise <suite>/query/tasks.jsonl, then <suite>/tasks.jsonl."""
    if tasks:
        return Path(tasks)
    base = Path("data/benchmarks") / suite
    for cand in (base / "query" / "tasks.jsonl", base / "tasks.jsonl"):
        if cand.exists():
            return cand
    return base / "query" / "tasks.jsonl"


def _run_predictions(run_id: str | None, out: str | None, model: str) -> tuple[str, Path]:
    """Explicit --out wins; otherwise runs/<run-id>/predictions.jsonl (auto run-id)."""
    if out:
        p = Path(out)
        return run_id or p.parent.name, p
    rid = run_id or f"{model}-{__import__('datetime').datetime.now().strftime('%Y%m%d-%H%M%S')}"
    return rid, Path("data/benchmarks/runs") / rid / "predictions.jsonl"


@app.command()
def init(config: str = typer.Option("configs/poland.yaml", "--config")) -> None:
    """Init local work dir + SQLite index; verify S3 bucket/prefix reachable."""
    cfg = _cfg(config)
    init_db(cfg.local_.sqlite_path)
    s3 = s3mod.s3_client(cfg.aws.region)
    try:
        s3.head_bucket(Bucket=cfg.aws.bucket)
    except (ClientError, NoCredentialsError) as e:
        typer.echo(f"S3 head-bucket failed: {e}")
        raise typer.Exit(1)
    typer.echo(f"init ok: sqlite={cfg.local_.sqlite_path} s3=s3://{cfg.aws.bucket}/{cfg.aws.prefix}")


@app.command()
def status(config: str = typer.Option("configs/poland.yaml", "--config")) -> None:
    cfg = _cfg(config)
    db_exists = Path(cfg.local_.sqlite_path).exists()
    s3 = s3mod.s3_client(cfg.aws.region)
    try:
        resp = s3.list_objects_v2(Bucket=cfg.aws.bucket, Prefix=cfg.aws.prefix, MaxKeys=1)
        key_count = resp.get("KeyCount", 0)
        s3_ok = True
    except Exception as e:  # noqa: BLE001
        s3_ok, key_count, e = False, 0, e
        typer.echo(f"s3 error: {e}")
    typer.echo(f"bucket=s3://{cfg.aws.bucket}/{cfg.aws.prefix} reachable={s3_ok} sample_keys={key_count}")
    typer.echo(f"sqlite={cfg.local_.sqlite_path} exists={db_exists}")


@app.command()
def doctor(config: str = typer.Option("configs/poland.yaml", "--config")) -> None:
    """Check AWS creds, bucket policy (public read), versioning, encryption."""
    cfg = _cfg(config)
    s3 = s3mod.s3_client(cfg.aws.region)
    sts_ok = versioning = encryption = policy_ok = "?"
    import boto3

    try:
        boto3.client("sts", region_name=cfg.aws.region).get_caller_identity()
        sts_ok = "ok"
    except Exception as e:  # noqa: BLE001
        sts_ok = f"fail: {e}"
    try:
        versioning = s3.get_bucket_versioning(Bucket=cfg.aws.bucket).get("Status", "-")
    except Exception as e:  # noqa: BLE001
        versioning = f"fail: {e}"
    try:
        s3.get_bucket_encryption(Bucket=cfg.aws.bucket)
        encryption = "ok"
    except Exception as e:  # noqa: BLE001
        encryption = f"fail: {e}"
    try:
        pol = s3.get_bucket_policy(Bucket=cfg.aws.bucket)["Policy"]
        policy_ok = "public" if '"Effect":"Allow"' in pol.replace(" ", "") else "custom"
    except Exception as e:  # noqa: BLE001
        policy_ok = f"fail: {e}"
    typer.echo(f"sts={sts_ok} versioning={versioning} encryption={encryption} policy={policy_ok}")


# ---- taxonomy (real: araneae PL checklist + WSC/LSID snapshot) ----

ARANEAE_PL_CSV = "https://araneae.nmbe.ch/biodiversity/countrylist/export?code=PL&type=csv"


@taxonomy_app.command("collect")
def taxonomy_collect(
    country: str = typer.Option("PL", "--country"),
    config: str = typer.Option("configs/poland.yaml", "--config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    max_records: int = typer.Option(1000, "--max-records"),
    resume: bool = typer.Option(False, "--resume"),
    concurrency: int = typer.Option(4, "--concurrency"),
    input: Optional[str] = typer.Option(None, "--input", help="Local checklist CSV (default: download araneae PL export)"),
    snapshot: str = typer.Option("araneae-09.2026", "--snapshot"),
) -> None:
    """Ingest versioned Polish checklist + taxonomy snapshot (idempotent, resumable)."""
    import csv as _csv

    import httpx as _httpx

    from spider_bench.db import ensure_migrated as _migrated
    from spider_bench.db import get_connection as _connect
    from spider_bench.sources.polish_checklist import ingest_checklist as _ingest_chk
    from spider_bench.sources.world_spider_catalog import ingest_wsc as _ingest_wsc

    cfg = _cfg(config)
    typer.echo(f"taxonomy collect country={country} snapshot={snapshot} dry_run={dry_run}")
    if input:
        raw = Path(input).read_bytes()
    else:
        r = _httpx.get(ARANEAE_PL_CSV, timeout=60, follow_redirects=True)
        r.raise_for_status()
        raw = r.content
    text = raw.decode("utf-8-sig", errors="replace")
    rows = list(_csv.DictReader(text.splitlines()))[:max_records]
    typer.echo(f"fetched {len(rows)} checklist rows")
    if dry_run:
        return
    # raw snapshot to S3 (versioned, never overwritten)
    s3 = s3mod.s3_client(cfg.aws.region)
    date = __import__("datetime").date.today().isoformat()
    key = s3mod.raw_metadata_key(cfg.aws.prefix, "araneae", snapshot, f"poland-{len(rows)}.csv")
    if not resume or not s3mod.key_exists(s3, cfg.aws.bucket, key):
        s3.put_object(Bucket=cfg.aws.bucket, Key=key, Body=raw, ContentType="text/csv")
    conn = _connect(cfg.local_.sqlite_path)
    _migrated(conn)
    wsc_rows = [{"scientific_name": f"{r['Genus']} {r['Species']}", "authorship": r.get("Author"),
                 "rank": "species", "family": r.get("Family"), "genus": r.get("Genus"),
                 "wsc_id": r.get("LSID"), "taxonomic_status": "accepted"} for r in rows if r.get("Genus")]
    typer.echo(f"wsc: {_ingest_wsc(conn, wsc_rows, snapshot_id=snapshot)}")
    chk = [{"original_name": f"{r['Genus']} {r['Species']}", "authorship": r.get("Author"),
            "membership_status": "present",
            "notes": f"araneae species_id={r.get('species_id')} lsid={r.get('LSID')}"} for r in rows if r.get("Genus")]
    typer.echo(f"checklist: {_ingest_chk(conn, chk, source='araneae-poland', version=snapshot, citation='Nentwig et al. Spiders of Europe doi:10.24436/1', retrieval_date=date, raw_s3_uri=f's3://{cfg.aws.bucket}/{key}')}")
    conn.close()


@taxonomy_app.command("reconcile")
def taxonomy_reconcile(
    config: str = typer.Option("configs/poland.yaml", "--config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    out: str = typer.Option("data/work/reports/taxonomy-conflicts.json", "--out"),
) -> None:
    """Reconcile checklist names to the pinned snapshot; write conflict report."""
    from spider_bench.db import ensure_migrated as _migrated
    from spider_bench.db import get_connection as _connect
    from spider_bench.taxonomy.reconcile import conflict_report as _report
    from spider_bench.taxonomy.reconcile import reconcile_all as _reconcile

    cfg = _cfg(config)
    conn = _connect(cfg.local_.sqlite_path)
    _migrated(conn)
    taxa = [{"scientific_name": r[0], "authorship": r[1]}
            for r in conn.execute("SELECT scientific_name, authorship FROM taxa").fetchall()]
    entries = [{"original_name": r[0]} for r in conn.execute("SELECT DISTINCT original_name FROM country_taxa").fetchall()]
    results = _reconcile(entries, taxa)
    report = _report(results)
    typer.echo(f"reconciled {len(results)}: {report.get('counts', {})} needs_review={report.get('needs_review', 0)}")
    if dry_run:
        return
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    typer.echo(f"wrote {out}")
    conn.close()


# ---- discover (real, metadata-only; image bytes never fetched here) ----

def _load_resume(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


@discover_app.command("inaturalist")
def discover_inat(
    country: str = typer.Option("PL", "--country"),
    config: str = typer.Option("configs/poland.yaml", "--config"),
    max_records: int = typer.Option(1000, "--max-records"),
    resume: bool = typer.Option(False, "--resume"),
    concurrency: int = typer.Option(4, "--concurrency"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    rate_limit: float = typer.Option(2.0, "--rate-limit"),
) -> None:
    from spider_bench.db import ensure_migrated as _migrated
    from spider_bench.db import get_connection as _connect
    from spider_bench.sources.inaturalist import (
        discover_observations_sync as _discover,
    )
    from spider_bench.sources.inaturalist import (
        insert_observations_idempotent as _insert,
    )
    from spider_bench.sources.inaturalist import (
        upload_raw_jsonl_to_s3 as _upload,
    )

    cfg = _cfg(config)
    cursor_path = Path(f"data/work/cursors/inaturalist-{country}.json")
    cursor = _load_resume(cursor_path) if resume else {}
    records, nxt = _discover(max_records=max_records, resume=cursor or None, rate_limit=rate_limit, dry_run=dry_run)
    typer.echo(f"inaturalist country={country} fetched={len(records)} dry_run={dry_run}")
    if dry_run:
        return
    s3 = s3mod.s3_client(cfg.aws.region)
    date = __import__("datetime").date.today().isoformat()
    _upload(s3, cfg.aws.bucket, cfg.aws.prefix, "inaturalist", date, records)
    conn = _connect(cfg.local_.sqlite_path)
    _migrated(conn)
    typer.echo(f"inserted: {_insert(conn, records)}")
    conn.close()
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    cursor_path.write_text(json.dumps(nxt), encoding="utf-8")


@discover_app.command("gbif")
def discover_gbif(
    country: str = typer.Option("PL", "--country"),
    config: str = typer.Option("configs/poland.yaml", "--config"),
    max_records: int = typer.Option(1000, "--max-records"),
    resume: bool = typer.Option(False, "--resume"),
    concurrency: int = typer.Option(4, "--concurrency"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    rate_limit: float = typer.Option(2.0, "--rate-limit"),
) -> None:
    from spider_bench.db import ensure_migrated as _migrated
    from spider_bench.db import get_connection as _connect
    from spider_bench.sources.gbif import discover_occurrences_sync as _discover
    from spider_bench.sources.gbif import insert_occurrences_idempotent as _insert
    from spider_bench.sources.gbif import upload_raw_jsonl_to_s3 as _upload

    cfg = _cfg(config)
    cursor_path = Path(f"data/work/cursors/gbif-{country}.json")
    cursor = _load_resume(cursor_path) if resume else {}
    records, nxt = _discover(country_code=country, max_records=max_records,
                             offset=cursor.get("offset", 0) if cursor else 0,
                             rate_limit=rate_limit, dry_run=dry_run)
    typer.echo(f"gbif country={country} fetched={len(records)} dry_run={dry_run}")
    if dry_run:
        return
    s3 = s3mod.s3_client(cfg.aws.region)
    date = __import__("datetime").date.today().isoformat()
    _upload(s3, cfg.aws.bucket, cfg.aws.prefix, "gbif", date, records)
    conn = _connect(cfg.local_.sqlite_path)
    _migrated(conn)
    typer.echo(f"inserted: {_insert(conn, records)}")
    conn.close()
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    cursor_path.write_text(json.dumps(nxt), encoding="utf-8")


@discover_app.command("commons")
def discover_commons(
    country: str = typer.Option("PL", "--country"),
    config: str = typer.Option("configs/poland.yaml", "--config"),
    max_records: int = typer.Option(1000, "--max-records"),
    resume: bool = typer.Option(False, "--resume"),
    concurrency: int = typer.Option(4, "--concurrency"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    rate_limit: float = typer.Option(2.0, "--rate-limit"),
    query: str = typer.Option("spider Poland", "--query"),
) -> None:
    from spider_bench.db import ensure_migrated as _migrated
    from spider_bench.db import get_connection as _connect
    from spider_bench.sources.commons import discover_files_sync as _discover
    from spider_bench.sources.commons import insert_files_idempotent as _insert
    from spider_bench.sources.commons import upload_raw_jsonl_to_s3 as _upload

    cfg = _cfg(config)
    cursor_path = Path(f"data/work/cursors/commons-{country}.json")
    cursor = _load_resume(cursor_path) if resume else {}
    records, nxt = _discover(search=query, max_records=max_records,
                             resume=cursor or None, rate_limit=rate_limit, dry_run=dry_run)
    typer.echo(f"commons query={query!r} fetched={len(records)} dry_run={dry_run}")
    if dry_run:
        return
    s3 = s3mod.s3_client(cfg.aws.region)
    date = __import__("datetime").date.today().isoformat()
    _upload(s3, cfg.aws.bucket, cfg.aws.prefix, "commons", date, records)
    conn = _connect(cfg.local_.sqlite_path)
    _migrated(conn)
    typer.echo(f"inserted: {_insert(conn, records)}")
    conn.close()
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    cursor_path.write_text(json.dumps(nxt), encoding="utf-8")


# ---- audit ----

@audit_app.command("coverage")
def audit_coverage(
    config: str = typer.Option("configs/poland.yaml", "--config"),
    out: str = typer.Option("data/work/reports/coverage.json", "--out"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    upload: bool = typer.Option(True, "--upload/--no-upload"),
) -> None:
    """Per-taxon image coverage: every checklist species flagged has_image true/false.

    has_image=false rows are the explicit no-image pointer required by §2.1
    (stored in the report + uploaded to S3, never silently dropped).
    """
    import httpx as _httpx

    from spider_bench.db import ensure_migrated as _migrated
    from spider_bench.db import get_connection as _connect

    cfg = _cfg(config)
    conn = _connect(cfg.local_.sqlite_path)
    _migrated(conn)
    checklist = [r[0] for r in conn.execute("SELECT DISTINCT original_name FROM country_taxa").fetchall()]
    conn.close()
    typer.echo(f"checklist species: {len(checklist)} dry_run={dry_run}")
    # metadata-only: iNat species_counts (licensed-agnostic) + GBIF still-image facet
    inat_counts: dict[str, int] = {}
    try:
        page = 1
        while True:
            r = _httpx.get("https://api.inaturalist.org/v1/observations/species_counts",
                           params={"place_id": 7800, "taxon_id": 47118, "per_page": 500, "page": page},
                           timeout=30).json()
            res = r.get("results", [])
            if not res:
                break
            for t in res:
                inat_counts[t.get("taxon", {}).get("name", "")] = t.get("count", 0)
            if len(res) < 500:
                break
            page += 1
    except Exception as e:  # noqa: BLE001
        typer.echo(f"inat species_counts failed: {e}")
    gbif_species_with_images = 0
    try:
        r = _httpx.get("https://api.gbif.org/v1/occurrence/search",
                       params={"country": "PL", "order_key": 1496, "media_type": "StillImage",
                               "limit": 0, "facet": "speciesKey", "facetLimit": 2000},
                       timeout=60).json()
        # facet gives keys, not names; resolve via species API in bulk is costly —
        # record count + mark coverage at aggregate level; per-taxon GBIF check
        # happens in media select via occurrence lookup.
        facets = (r.get("facets") or [{}])[0].get("counts", [])
        gbif_species_with_images = len(facets)
        typer.echo(f"gbif PL still-image occurrences={r.get('count')} species={gbif_species_with_images}")
    except Exception as e:  # noqa: BLE001
        typer.echo(f"gbif facet failed: {e}")
    rows = [{"taxon": name,
             "inat_poland_obs": inat_counts.get(name, 0),
             "has_image_candidate": bool(inat_counts.get(name, 0)),
             "image_status": "candidate" if inat_counts.get(name, 0) else "no_image"}
            for name in sorted(checklist)]
    no_image = [r for r in rows if not r["has_image_candidate"]]
    report = {"species_total": len(rows), "with_candidates": len(rows) - len(no_image),
              "no_image": len(no_image), "no_image_taxa": [r["taxon"] for r in no_image], "rows": rows}
    typer.echo(f"with_candidates={report['with_candidates']} no_image={report['no_image']}")
    if dry_run:
        return
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if upload:
        s3 = s3mod.s3_client(cfg.aws.region)
        date = __import__("datetime").date.today().isoformat()
        key = f"{cfg.aws.prefix}work/reports/coverage-{date}.json"
        s3.put_object(Bucket=cfg.aws.bucket, Key=key,
                      Body=json.dumps(report, indent=2).encode(), ContentType="application/json")
        typer.echo(f"uploaded s3://{cfg.aws.bucket}/{key}")
    typer.echo(f"wrote {out}")


@audit_app.command("licenses")
def audit_licenses(
    config: str = typer.Option("configs/poland.yaml", "--config"),
    input: Optional[str] = typer.Option(None, "--input", help="Candidates JSON file"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Classify candidate media licenses against the active profile."""
    from spider_bench.licensing import classify_license, load_profiles

    cfg = _cfg(config)
    profiles = load_profiles()
    profile = cfg.collection.license_profile
    rows: list[dict] = []
    if input:
        rows = json.loads(Path(input).read_text(encoding="utf-8"))
        if isinstance(rows, dict):
            rows = rows.get("candidates", rows.get("media", []))
    from collections import Counter

    counts = Counter(classify_license(str(r.get("license", "")), profile, profiles) for r in rows)
    typer.echo(f"profile={profile} counts={dict(counts)} dry_run={dry_run} n={len(rows)}")


@audit_app.command("taxonomy")
def audit_taxonomy(
    config: str = typer.Option("configs/poland.yaml", "--config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    typer.echo(f"audit taxonomy dry_run={dry_run} (delegated to taxonomy worker).")


# ---- media ----

@media_app.command("select")
def media_select(
    config: str = typer.Option("configs/poland.yaml", "--config"),
    input: str = typer.Option(..., "--input", help="Candidates JSON (list or {candidates:[...]})"),
    out: str = typer.Option("data/work/selected.json", "--out"),
    max_per_taxon: int = typer.Option(50, "--max-per-taxon"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Apply license + dominance caps to discovery candidates."""
    from spider_bench.dataset.select import select_media
    from spider_bench.licensing import load_profiles

    cfg = _cfg(config)
    profiles = load_profiles()
    data = json.loads(Path(input).read_text(encoding="utf-8"))
    candidates = data.get("candidates", data) if isinstance(data, dict) else data
    selected, rejected = select_media(
        candidates, cfg.collection.license_profile, profiles, max_per_taxon=max_per_taxon
    )
    typer.echo(f"selected={len(selected)} rejected={len(rejected)} dry_run={dry_run}")
    if not dry_run:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps({"selected": selected}, indent=2), encoding="utf-8")
        typer.echo(f"wrote {out}")


@media_app.command("collect-one")
def media_collect_one(
    config: str = typer.Option("configs/poland.yaml", "--config"),
    profile: str = typer.Option("research", "--profile"),
    max_species: Optional[int] = typer.Option(None, "--max-species"),
    rate_limit: float = typer.Option(2.0, "--rate-limit"),
    download: bool = typer.Option(True, "--download/--no-download"),
    out: str = typer.Option("data/work/one-per-species.json", "--out"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    scope: str = typer.Option("poland", "--scope", help="poland or worldwide"),
    only_missing: bool = typer.Option(False, "--only-missing",
                                      help="restrict to taxa with no accepted image"),
) -> None:
    """One representative image per checklist species: search candidates, download 1 each.

    Scope poland (default) searches iNaturalist Poland; worldwide drops the
    place filter for species with no Poland photo (observation geography is
    recorded per image). Species with no candidate are recorded as no_image
    in the manifest — never silently dropped.
    """
    from spider_bench.db import ensure_migrated as _migrated
    from spider_bench.db import get_connection as _connect
    from spider_bench.media.collect_one import collect_candidates, record_media_row
    from spider_bench.media.download import DownloadItem, download_selected
    from spider_bench.storage.s3 import public_url as _public_url

    cfg = _cfg(config)
    conn = _connect(cfg.local_.sqlite_path)
    _migrated(conn)
    if only_missing:
        have = {r[0] for r in conn.execute(
            """SELECT DISTINCT o.source_taxon FROM media m
               JOIN observations o ON o.id = m.observation_id
               WHERE m.validation_status='accepted' AND o.source_taxon IS NOT NULL""").fetchall()}
        taxa = [r[0] for r in conn.execute("SELECT DISTINCT original_name FROM country_taxa ORDER BY 1").fetchall()
                if r[0] not in have]
    else:
        taxa = [r[0] for r in conn.execute("SELECT DISTINCT original_name FROM country_taxa ORDER BY 1").fetchall()]
    if max_species:
        taxa = taxa[:max_species]
    if scope not in ("poland", "worldwide"):
        raise typer.BadParameter("--scope must be poland or worldwide")
    place_id = 7800 if scope == "poland" else None
    typer.echo(f"collect-one scope={scope} species={len(taxa)} profile={profile} download={download} dry_run={dry_run}")
    manifest = collect_candidates(taxa, profile=profile, rate_limit=rate_limit, place_id=place_id)
    manifest["scope"] = scope
    typer.echo(f"candidates={manifest['found']} no_image={manifest['missing']}")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    s3 = s3mod.s3_client(cfg.aws.region)
    date = __import__("datetime").date.today().isoformat()
    s3.put_object(Bucket=cfg.aws.bucket, Key=f"{cfg.aws.prefix}work/one-per-species-{scope}-{date}.json",
                  Body=json.dumps(manifest, indent=2).encode(), ContentType="application/json")
    if dry_run or not download:
        conn.close()
        return
    items = [DownloadItem(url=c["url"], license=c["license"], taxon=c["taxon"],
                          source="inaturalist", source_media_id=str(c.get("photo_id")))
             for c in manifest["candidates"]]
    results = download_selected(items, bucket=cfg.aws.bucket, prefix=cfg.aws.prefix,
                                region=cfg.aws.region, s3_client=s3, conn=conn)
    ok = [r for r in results if r.ok]
    typer.echo(f"downloaded={len(ok)}/{len(results)}")
    by_url = {c["url"]: c for c in manifest["candidates"]}
    for r in ok:
        c = by_url.get(r.url, {})
        record_media_row(conn, taxon=c.get("taxon", ""), s3_uri=f"s3://{cfg.aws.bucket}/{r.s3_key}",
                         public_url=_public_url(cfg.aws.bucket, cfg.aws.region, r.s3_key or ""),
                         license=c.get("license", ""), creator=c.get("creator"),
                         attribution=c.get("attribution"), source="inaturalist",
                         source_media_id=str(c.get("photo_id")),
                         observation_id=str(c.get("observation_id", "")),
                         quality_grade="research" if c.get("research_grade") else None,
                         country=c.get("country") or ("PL" if scope == "poland" else None),
                         place_guess=c.get("place_guess"),
                         sha256=r.sha256 or "")
    n = conn.execute("SELECT COUNT(*) FROM media WHERE validation_status='accepted'").fetchone()[0]
    typer.echo(f"media accepted in db: {n}")
    conn.close()


@media_app.command("collect-n")
def media_collect_n(
    config: str = typer.Option("configs/poland.yaml", "--config"),
    profile: str = typer.Option("research", "--profile"),
    per_species: int = typer.Option(10, "--per-species"),
    max_species: Optional[int] = typer.Option(None, "--max-species"),
    rate_limit: float = typer.Option(2.0, "--rate-limit"),
    download: bool = typer.Option(True, "--download/--no-download"),
    out: str = typer.Option("data/work/collect-n.json", "--out"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Top up to N distinct-observation images per species (worldwide, licensed).

    Skips observations already in the DB: reruns only fetch new material.
    Feeds the train/gallery/query splitter.
    """
    from spider_bench.db import ensure_migrated as _migrated
    from spider_bench.db import get_connection as _connect
    from spider_bench.media.collect_one import collect_n_candidates, record_media_row
    from spider_bench.media.download import DownloadItem, download_selected
    from spider_bench.storage.s3 import public_url as _public_url

    cfg = _cfg(config)
    conn = _connect(cfg.local_.sqlite_path)
    _migrated(conn)
    taxa = [r[0] for r in conn.execute("SELECT DISTINCT original_name FROM country_taxa ORDER BY 1").fetchall()]
    if max_species:
        taxa = taxa[:max_species]
    have_obs = {r[0] for r in conn.execute(
        "SELECT DISTINCT source_observation_id FROM observations WHERE source='inaturalist'").fetchall()}
    skip = {int(x) for x in have_obs if str(x).isdigit()}
    typer.echo(f"collect-n species={len(taxa)} per_species={per_species} known_obs={len(skip)} dry_run={dry_run}")
    manifest = collect_n_candidates(taxa, profile=profile, rate_limit=rate_limit,
                                    per_species=per_species, skip_observation_ids=skip)
    typer.echo(f"new_images={manifest['images']}")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    s3 = s3mod.s3_client(cfg.aws.region)
    date = __import__("datetime").date.today().isoformat()
    s3.put_object(Bucket=cfg.aws.bucket, Key=f"{cfg.aws.prefix}work/collect-n-{date}.json",
                  Body=json.dumps({k: v for k, v in manifest.items() if k != "candidates"}).encode(),
                  ContentType="application/json")
    if dry_run or not download:
        conn.close()
        return
    items = [DownloadItem(url=c["url"], license=c["license"], taxon=c["taxon"],
                          source="inaturalist", source_media_id=str(c.get("photo_id")))
             for c in manifest["candidates"]]
    results = download_selected(items, bucket=cfg.aws.bucket, prefix=cfg.aws.prefix,
                                region=cfg.aws.region, s3_client=s3, conn=conn)
    ok = [r for r in results if r.ok]
    by_url = {c["url"]: c for c in manifest["candidates"]}
    for r in ok:
        c = by_url.get(r.url, {})
        record_media_row(conn, taxon=c.get("taxon", ""), s3_uri=f"s3://{cfg.aws.bucket}/{r.s3_key}",
                         public_url=_public_url(cfg.aws.bucket, cfg.aws.region, r.s3_key or ""),
                         license=c.get("license", ""), creator=c.get("creator"),
                         attribution=c.get("attribution"), source="inaturalist",
                         source_media_id=str(c.get("photo_id")),
                         observation_id=str(c.get("observation_id", "")),
                         quality_grade="research" if c.get("research_grade") else None,
                         country=c.get("country"), place_guess=c.get("place_guess"),
                         sha256=r.sha256 or "")
    n = conn.execute("SELECT COUNT(*) FROM media WHERE validation_status='accepted'").fetchone()[0]
    typer.echo(f"downloaded={len(ok)}/{len(results)} media accepted in db: {n}")
    conn.close()


@media_app.command("gap-fill")
def media_gap_fill(
    config: str = typer.Option("configs/poland.yaml", "--config"),
    input: str = typer.Option("data/work/one-per-species.json", "--input"),
    profile: str = typer.Option("research", "--profile"),
    max_species: Optional[int] = typer.Option(None, "--max-species"),
    rate_limit: float = typer.Option(2.0, "--rate-limit"),
    download: bool = typer.Option(True, "--download/--no-download"),
    out: str = typer.Option("data/work/gap-fill.json", "--out"),
    include_sharealike: bool = typer.Option(False, "--include-sharealike"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Commons fallback for species with no iNat candidate (1 image each).

    Ambiguous files always go to a review list. ShareAlike files are
    quarantined unless --include-sharealike (decision recorded in manifest;
    files used unmodified with attribution preserved).
    """
    from spider_bench.db import ensure_migrated as _migrated
    from spider_bench.db import get_connection as _connect
    from spider_bench.media.collect_one import gap_fill_commons, record_media_row
    from spider_bench.media.download import DownloadItem, download_selected
    from spider_bench.storage.s3 import public_url as _public_url

    cfg = _cfg(config)
    data = json.loads(Path(input).read_text(encoding="utf-8"))
    missing = data.get("missing_taxa", [])
    if max_species:
        missing = missing[:max_species]
    typer.echo(f"gap-fill missing={len(missing)} profile={profile} download={download} dry_run={dry_run}")
    manifest = gap_fill_commons(missing, profile=profile, rate_limit=rate_limit,
                                include_sharealike=include_sharealike)
    typer.echo(f"found={manifest['found']} review={manifest['review']} still_missing={manifest['still_missing']}")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    s3 = s3mod.s3_client(cfg.aws.region)
    date = __import__("datetime").date.today().isoformat()
    s3.put_object(Bucket=cfg.aws.bucket, Key=f"{cfg.aws.prefix}work/gap-fill-{date}.json",
                  Body=json.dumps(manifest, indent=2).encode(), ContentType="application/json")
    if dry_run or not download:
        return
    conn = _connect(cfg.local_.sqlite_path)
    _migrated(conn)
    items = [DownloadItem(url=c["url"], license=c["license"], taxon=c["taxon"],
                          source="commons", source_media_id=str(c.get("photo_id")))
             for c in manifest["candidates"]]
    results = download_selected(items, bucket=cfg.aws.bucket, prefix=cfg.aws.prefix,
                                region=cfg.aws.region, s3_client=s3, conn=conn)
    ok = [r for r in results if r.ok]
    typer.echo(f"downloaded={len(ok)}/{len(results)}")
    by_url = {c["url"]: c for c in manifest["candidates"]}
    for r in ok:
        c = by_url.get(r.url, {})
        record_media_row(conn, taxon=c.get("taxon", ""), s3_uri=f"s3://{cfg.aws.bucket}/{r.s3_key}",
                         public_url=_public_url(cfg.aws.bucket, cfg.aws.region, r.s3_key or ""),
                         license=c.get("license", ""), creator=c.get("creator"),
                         attribution=c.get("attribution"), source="commons",
                         source_media_id=str(c.get("photo_id")),
                         observation_id=str(c.get("photo_id")), sha256=r.sha256 or "")
    n = conn.execute("SELECT COUNT(*) FROM media WHERE validation_status='accepted'").fetchone()[0]
    typer.echo(f"media accepted in db: {n}")
    conn.close()


@media_app.command("download")
def media_download(
    config: str = typer.Option("configs/poland.yaml", "--config"),
    input: Optional[str] = typer.Option(None, "--input"),
    resume: bool = typer.Option(True, "--resume"),
    max_records: int = typer.Option(1000, "--max-records"),
    concurrency: int = typer.Option(4, "--concurrency"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    typer.echo(
        f"media download input={input} resume={resume} max_records={max_records} "
        f"concurrency={concurrency} dry_run={dry_run} (media worker module)"
    )


@media_app.command("validate")
def media_validate(
    config: str = typer.Option("configs/poland.yaml", "--config"),
    input: Optional[str] = typer.Option(None, "--input"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    typer.echo(f"media validate input={input} dry_run={dry_run} (media worker module)")


@media_app.command("deduplicate")
def media_deduplicate(
    config: str = typer.Option("configs/poland.yaml", "--config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    typer.echo(f"media deduplicate dry_run={dry_run} (media worker module)")


# ---- danger (owned here) ----

@danger_app.command("evidence")
def danger_evidence(
    input: str = typer.Argument(..., help="Evidence bundle JSON/YAML/CSV"),
    config: str = typer.Option("configs/poland.yaml", "--config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Import + validate bibliographic evidence records and claim linkages."""
    from spider_bench.danger.evidence import import_evidence_file

    sources, claims = import_evidence_file(input)
    typer.echo(f"sources={len(sources)} claims={len(claims)} dry_run={dry_run} from={input}")


@danger_app.command("assessments")
def danger_assessments(
    input: str = typer.Argument(..., help="Assessments JSON/YAML file"),
    config: str = typer.Option("configs/poland.yaml", "--config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Import + validate danger assessments (never defaults missing category)."""
    import yaml as _yaml

    from spider_bench.danger.assessments import import_assessments

    text = Path(input).read_text(encoding="utf-8")
    data = json.loads(text) if Path(input).suffix == ".json" else _yaml.safe_load(text)
    records = data.get("assessments", data) if isinstance(data, dict) else data
    assessments = import_assessments(list(records))
    typer.echo(f"assessments={len(assessments)} dry_run={dry_run} from={input}")


@danger_app.command("audit")
def danger_audit(
    release_dir: str = typer.Option(..., "--release-dir"),
    config: str = typer.Option("configs/poland.yaml", "--config"),
) -> None:
    """Audit danger artifacts inside a built release directory."""
    from spider_bench.dataset.publish import verify_release

    errors = verify_release(release_dir)
    danger_errors = [e for e in errors if e.startswith("danger")]
    typer.echo(f"danger_errors={len(danger_errors)} total_errors={len(errors)}")
    for e in danger_errors:
        typer.echo(f" - {e}")
    if danger_errors:
        raise typer.Exit(1)


# ---- release (owned here) ----

@release_app.command("build")
def release_build(
    version: str = typer.Option(..., "--version"),
    input: str = typer.Option("data/work/release-input.json", "--input"),
    out: str = typer.Option(None, "--out"),
    config: str = typer.Option("configs/poland.yaml", "--config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Build deterministic release artifacts into a temp sibling, verify, stage."""
    from spider_bench.dataset.manifest import build_release

    cfg = _cfg(config)
    dest = out or f"data/releases/polish-spiders/{version}"
    typer.echo(f"release build version={version} dest={dest} dry_run={dry_run}")
    if dry_run:
        return
    p = Path(input)
    payload: dict = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix=f"release-{version}-"))
    build_release(
        taxa=payload.get("taxa", []),
        media=payload.get("media", []),
        attribution=payload.get("attribution", []),
        danger_assessments=payload.get("danger_assessments", []),
        evidence=payload.get("evidence", []),
        out_dir=tmp,
        version=version,
        license_profile=cfg.collection.license_profile,
    )
    from spider_bench.dataset.publish import verify_release

    errors = verify_release(tmp, expected_version=version)
    if errors:
        for e in errors:
            typer.echo(f" - {e}")
        raise typer.Exit(1)
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    if Path(dest).exists():
        typer.echo(f"dest exists: {dest} — refusing to overwrite")
        raise typer.Exit(1)
    tmp.rename(dest)
    typer.echo(f"built {dest}")


@release_app.command("verify")
def release_verify(
    version: str = typer.Option(..., "--version"),
    dir: Optional[str] = typer.Option(None, "--dir"),
    config: str = typer.Option("configs/poland.yaml", "--config"),
    strict_reports: bool = typer.Option(False, "--strict-reports"),
) -> None:
    """Run §11 gates against a built release directory."""
    from spider_bench.dataset.publish import verify_release

    target = dir or f"data/releases/polish-spiders/{version}"
    errors = verify_release(target, expected_version=version, strict_reports=strict_reports)
    if errors:
        for e in errors:
            typer.echo(f" - {e}")
        raise typer.Exit(1)
    typer.echo(f"verify ok: {target}")


@release_app.command("publish")
def release_publish(
    version: str = typer.Option(..., "--version"),
    dir: Optional[str] = typer.Option(None, "--dir"),
    config: str = typer.Option("configs/poland.yaml", "--config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Verify + publish a release to S3 (staging copy, COMPLETE last)."""
    from spider_bench.dataset.publish import publish_release

    cfg = _cfg(config)
    target = dir or f"data/releases/polish-spiders/{version}"
    result = publish_release(
        target, cfg.aws.bucket, cfg.aws.prefix, version,
        region=cfg.aws.region, dry_run=dry_run,
    )
    typer.echo(f"published {result['complete_key']} keys={len(result['keys'])} dry_run={dry_run}")


# ---- benchmark (M0 tasks + M1 adapter/runner/scorer) ----

@benchmark_app.command("build-tasks")
def benchmark_build_tasks(
    version: str = typer.Option("0.4.0", "--version"),
    dir: Optional[str] = typer.Option(None, "--dir"),
    out: str = typer.Option("data/benchmarks/species-id-closed-v1", "--out"),
    imaged_only: bool = typer.Option(True, "--imaged-only/--all-taxa"),
) -> None:
    """Build deterministic closed-set species-ID tasks from a release."""
    import pyarrow.parquet as pq

    from spider_bench.benchmark.tasks import build_tasks, write_tasks

    target = Path(dir) if dir else Path(f"data/releases/polish-spiders/{version}")
    checksums: dict[str, str] = {}
    cpath = target / "checksums.sha256"
    if cpath.exists():
        for line in cpath.read_text(encoding="utf-8").splitlines():
            if line.strip():
                digest, name = line.split(None, 1)
                checksums[name.strip()] = digest

    def rows(name: str) -> list[dict]:
        table = pq.read_table(target / f"{name}.parquet")
        cols = table.column_names
        return [{c: table.column(c)[i].as_py() for c in cols} for i in range(table.num_rows)]

    tasks = build_tasks(rows("taxa"), rows("media"), imaged_only=imaged_only)
    tp = write_tasks(tasks, out, dataset_version=version, dataset_checksums=checksums)
    typer.echo(f"tasks={len(tasks)} wrote={tp}")


@benchmark_app.command("run")
def benchmark_run(
    tasks: Optional[str] = typer.Option(None, "--tasks"),
    model: str = typer.Option("constant-reference", "--model"),
    registry: str = typer.Option("configs/models", "--registry"),
    out: Optional[str] = typer.Option(None, "--out"),
    timeout: float = typer.Option(120.0, "--timeout"),
    resume: bool = typer.Option(True, "--resume/--no-resume"),
    image_source: str = typer.Option("s3", "--image-source", help="s3|none (none passes empty bytes)"),
    max_tasks: Optional[int] = typer.Option(None, "--max-tasks"),
    max_cost: Optional[float] = typer.Option(None, "--max-cost", help="USD ceiling (API models); stops early"),
    run_id: Optional[str] = typer.Option(None, "--run-id"),
    suite: str = typer.Option("species-id-v2", "--suite"),
    concurrency: int = typer.Option(4, "--concurrency", help="parallel model requests"),
    rate_limit: float = typer.Option(0.0, "--rate-limit", help="max requests/sec total (0 = unlimited)"),
    retries: int = typer.Option(3, "--retries", help="retries on timeouts/429/5xx with backoff"),
) -> None:
    """Run a model over tasks. Paths resolve from --suite/--run-id; keys from env only."""
    import datetime as _dt

    from spider_bench.benchmark.adapters import ConstantAdapter, PerfectAdapter
    from spider_bench.benchmark.runner import run_tasks
    from spider_bench.benchmark.tasks import read_tasks

    adapters: dict = {"perfect-reference": PerfectAdapter(),
                      "constant-reference": ConstantAdapter()}
    run_id, out_path = _run_predictions(run_id, out, model)
    manifest_extra: dict = {"suite": suite, "run_id": run_id}
    if model not in adapters:
        from spider_bench.benchmark.api_adapter import OpenAICompatAdapter
        from spider_bench.benchmark.registry import load_registry, resolve_model

        reg = load_registry(registry)
        if model not in reg:
            raise typer.BadParameter(f"unknown model '{model}'; registry has {sorted(reg)}")
        resolved = resolve_model(reg[model])  # raises if key env missing; never logs the key
        adapters[model] = OpenAICompatAdapter(resolved)
        manifest_extra["pricing_usd"] = {
            "per_1k_requests": resolved["price_per_1k_requests"],
            "input_1k_tokens": resolved["price_input_1k_tokens"],
            "output_1k_tokens": resolved["price_output_1k_tokens"]}
    rows = read_tasks(_suite_tasks(suite, tasks))
    if max_tasks:
        rows = rows[:max_tasks]

    def loader(t: dict) -> bytes:
        if image_source == "none":
            return b""
        import httpx

        cache = Path("data/work/image-cache")
        cache.mkdir(parents=True, exist_ok=True)
        p = cache / f"{t['image_sha256']}.bin"
        if p.exists():
            return p.read_bytes()
        url = t.get("image_public_url") or ""
        r = httpx.get(url, timeout=60)
        r.raise_for_status()
        p.write_bytes(r.content)
        return r.content

    adapter = adapters[model]
    total = len(rows)
    step = max(1, total // 50)  # ~50 progress lines per run
    import time as _time

    t_start = _time.time()

    def _progress(done_n: int, total_n: int) -> None:
        if done_n % step == 0 or done_n == total_n:
            el = _time.time() - t_start
            rate = done_n / el if el > 0 else 0.0
            eta = (total_n - done_n) / rate if rate > 0 else 0.0
            cost = ""
            if hasattr(adapter, "estimated_cost"):
                try:
                    cost = f" cost=${adapter.estimated_cost():.4f}"
                except Exception:
                    pass
            typer.echo(f"[{done_n}/{total_n}] {done_n / total_n * 100:.0f}% "
                       f"elapsed={el:.0f}s eta={eta:.0f}s{cost}", err=True)

    typer.echo(f"starting run model={getattr(adapter, 'model_id', model)} tasks={total}", err=True)
    summary = run_tasks(rows, adapter, out_path, loader=loader, timeout_s=timeout,
                        resume=resume, max_cost=max_cost, progress=_progress,
                        max_workers=concurrency, rate_limit=rate_limit, retries=retries)
    manifest = {"model_id": getattr(adapter, "model_id", model), **manifest_extra,
                "tasks": summary["tasks"], "tasks_hash": summary["tasks_hash"],
                "usage": summary.get("usage", {}),
                "estimated_cost_usd": summary.get("estimated_cost_usd"),
                "errors": summary["errors"],
                "finished_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    (Path(out_path).parent / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    typer.echo(json.dumps(summary, indent=2))


@benchmark_app.command("split")
def benchmark_split(
    config: str = typer.Option("configs/poland.yaml", "--config"),
    out: str = typer.Option("data/benchmarks/species-id-v2", "--out"),
    query_per_taxon: int = typer.Option(4, "--query-per-taxon"),
    seed: int = typer.Option(42, "--seed"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Split accepted media into gallery (1/taxon) + observation-disjoint query set."""
    from spider_bench.benchmark.split import split_gallery_query
    from spider_bench.benchmark.tasks import build_tasks
    from spider_bench.db import ensure_migrated as _migrated
    from spider_bench.db import get_connection as _connect

    cfg = _cfg(config)
    conn = _connect(cfg.local_.sqlite_path)
    _migrated(conn)
    cols = ("m.sha256, m.s3_uri, m.public_url, m.source, m.source_media_id,"
            " o.source_taxon AS taxon, o.id AS observation_id, o.country_code AS country,"
            " t.family AS family")
    rows = [dict(zip(("sha256", "s3_uri", "public_url", "source", "source_media_id",
                       "taxon", "observation_id", "country", "family"),
                      r)) for r in conn.execute(
        f"""SELECT {cols} FROM media m JOIN observations o ON o.id=m.observation_id
            LEFT JOIN taxa t ON t.id=o.taxon_id
            WHERE m.validation_status='accepted' ORDER BY o.source_taxon, m.sha256""").fetchall()]
    conn.close()
    taxa = [{"taxon": t, "family": next((r["family"] or "" for r in rows if r["taxon"] == t), "")}
            for t in sorted({r["taxon"] for r in rows if r["taxon"]})]
    parts = split_gallery_query(rows, query_per_taxon=query_per_taxon, seed=seed)
    typer.echo(f"taxa={parts['taxa']} gallery={parts['gallery_n']} query={parts['query_n']} dry_run={dry_run}")
    if dry_run:
        return
    names = sorted({t["taxon"] for t in taxa})
    gallery_tasks = build_tasks(
        [{"taxon": n} for n in names],
        [{**g, "taxon": g["taxon"]} for g in parts["gallery"]], imaged_only=False)
    # query tasks reference query images but score against the gallery taxon set
    query_tasks = []
    for q in sorted(parts["query"], key=lambda r: str(r.get("sha256"))):
        query_tasks.append({
            "task_id": f"species-id-v2:{q['taxon'].replace(' ', '_')}:{q['sha256'][:12]}",
            "task_type": "species-id-closed",
            "image_sha256": q["sha256"], "image_s3_uri": q["s3_uri"],
            "image_public_url": q.get("public_url", ""),
            "prompt": ("Identify the spider species in this photograph. "
                       "Reply with exactly one scientific name from the candidate list."),
            "candidates": names, "correct_taxon": q["taxon"], "synonyms_accepted": [],
            "meta": {"family": q.get("family", ""), "country": q.get("country", ""),
                     "suite": "species-id-v2"}})
    from spider_bench.benchmark.tasks import write_tasks as _wt
    _wt(gallery_tasks, Path(out) / "gallery", dataset_version="db",
        dataset_checksums={"split": f"seed={seed}"})
    _wt(query_tasks, Path(out) / "query", dataset_version="db",
        dataset_checksums={"split": f"seed={seed}"})
    typer.echo(f"wrote {out}/gallery + {out}/query")


@benchmark_app.command("publish")
def benchmark_publish(
    dir: str = typer.Option(..., "--dir"),
    suite: str = typer.Option(..., "--suite"),
    run_id: str = typer.Option(..., "--run-id"),
    config: str = typer.Option("configs/poland.yaml", "--config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Publish a run dir to the private S3 results prefix (COMPLETE last)."""
    from spider_bench.benchmark.publish import publish_results

    cfg = _cfg(config)
    result = publish_results(dir, bucket=cfg.aws.bucket, suite=suite, run_id=run_id,
                             region=cfg.aws.region, dry_run=dry_run)
    typer.echo(f"published {result['complete_key']} keys={len(result['keys'])} dry_run={dry_run}")


@benchmark_app.command("models")
def benchmark_models(
    registry: str = typer.Option("configs/models", "--registry"),
) -> None:
    """List registered models (safe: key presence only, never values)."""
    from spider_bench.benchmark.registry import describe_registry

    for r in describe_registry(registry):
        typer.echo(json.dumps(r, sort_keys=True))


@benchmark_app.command("score")
def benchmark_score(
    tasks: Optional[str] = typer.Option(None, "--tasks"),
    predictions: Optional[str] = typer.Option(None, "--predictions"),
    suite: str = typer.Option("species-id-v2", "--suite"),
    run_id: Optional[str] = typer.Option(None, "--run-id"),
    out: Optional[str] = typer.Option(None, "--out", help="write scores.json here (default: beside predictions)"),
) -> None:
    """Score predictions against tasks (pure, offline). Paths resolve from --suite/--run-id."""
    from spider_bench.benchmark.runner import read_predictions
    from spider_bench.benchmark.scorer import score
    from spider_bench.benchmark.tasks import read_tasks

    tasks_path = _suite_tasks(suite, tasks)
    if predictions:
        pred_path = Path(predictions)
    elif run_id:
        pred_path = Path("data/benchmarks/runs") / run_id / "predictions.jsonl"
    else:
        # latest run dir by mtime
        runs = sorted(Path("data/benchmarks/runs").glob("*/predictions.jsonl"),
                      key=lambda p: p.stat().st_mtime)
        if not runs:
            raise typer.BadParameter("no runs found; pass --predictions or --run-id")
        pred_path = runs[-1]
    scores = score(read_tasks(tasks_path), read_predictions(pred_path))
    typer.echo(f"scored {scores.get('scored')}/{scores.get('tasks')} "
               f"errors={scores.get('errors')} top1={scores.get('top1', 0):.3f}", err=True)
    typer.echo(json.dumps(scores, indent=2))
    out_path = Path(out) if out else pred_path.parent / "scores.json"
    out_path.write_text(json.dumps(scores, indent=2), encoding="utf-8")
    typer.echo(f"wrote {out_path}")


@benchmark_app.command("leaderboard")
def benchmark_leaderboard(
    runs_dir: str = typer.Option("data/benchmarks/runs", "--runs-dir"),
    out: str = typer.Option("data/benchmarks/leaderboard.md", "--out"),
) -> None:
    """Render a static leaderboard from run dirs (each needs scores.json)."""
    from spider_bench.benchmark.leaderboard import collect_runs, render_leaderboard

    runs = collect_runs(runs_dir)
    md, rows = render_leaderboard(runs)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md, encoding="utf-8")
    Path(str(out).replace(".md", ".json")).write_text(json.dumps(rows, indent=2), encoding="utf-8")
    typer.echo(f"runs={len(rows)} wrote={out}")


if __name__ == "__main__":
    app()
