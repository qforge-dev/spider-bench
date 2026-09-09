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

app.add_typer(taxonomy_app, name="taxonomy")
app.add_typer(discover_app, name="discover")
app.add_typer(audit_app, name="audit")
app.add_typer(media_app, name="media")
app.add_typer(danger_app, name="danger")
app.add_typer(release_app, name="release")


def _cfg(config: str):
    return load_country_config(config)


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


# ---- taxonomy (owned by taxonomy worker; thin stubs) ----

@taxonomy_app.command("collect")
def taxonomy_collect(
    country: str = typer.Option("PL", "--country"),
    config: str = typer.Option("configs/poland.yaml", "--config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    max_records: int = typer.Option(1000, "--max-records"),
    resume: bool = typer.Option(False, "--resume"),
    concurrency: int = typer.Option(4, "--concurrency"),
) -> None:
    """Ingest versioned checklist inputs (delegates to taxonomy worker module)."""
    typer.echo(
        f"taxonomy collect country={country} dry_run={dry_run} "
        f"max_records={max_records} resume={resume} concurrency={concurrency}"
    )
    if dry_run:
        return
    try:
        from spider_bench.taxonomy import reconcile as _  # noqa: F401
    except ImportError:
        typer.echo("taxonomy module not yet implemented by taxonomy worker; recorded intent only.")


@taxonomy_app.command("reconcile")
def taxonomy_reconcile(
    config: str = typer.Option("configs/poland.yaml", "--config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    typer.echo(f"taxonomy reconcile dry_run={dry_run}")
    if dry_run:
        return
    try:
        from spider_bench.taxonomy import reconcile  # noqa

        typer.echo("taxonomy reconcile delegated.")
    except ImportError:
        typer.echo("taxonomy module not yet implemented; recorded intent only.")


# ---- discover (sources worker stubs) ----

def _discover(source: str, country: str, max_records: int, resume: bool,
              concurrency: int, dry_run: bool) -> None:
    typer.echo(
        f"discover {source} country={country} max_records={max_records} "
        f"resume={resume} concurrency={concurrency} dry_run={dry_run}"
    )
    if dry_run:
        return
    typer.echo(f"{source} discovery delegated to sources worker module (metadata-only first).")


@discover_app.command("inaturalist")
def discover_inat(
    country: str = typer.Option("PL", "--country"),
    config: str = typer.Option("configs/poland.yaml", "--config"),
    max_records: int = typer.Option(1000, "--max-records"),
    resume: bool = typer.Option(False, "--resume"),
    concurrency: int = typer.Option(4, "--concurrency"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    _discover("inaturalist", country, max_records, resume, concurrency, dry_run)


@discover_app.command("gbif")
def discover_gbif(
    country: str = typer.Option("PL", "--country"),
    config: str = typer.Option("configs/poland.yaml", "--config"),
    max_records: int = typer.Option(1000, "--max-records"),
    resume: bool = typer.Option(False, "--resume"),
    concurrency: int = typer.Option(4, "--concurrency"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    _discover("gbif", country, max_records, resume, concurrency, dry_run)


@discover_app.command("commons")
def discover_commons(
    country: str = typer.Option("PL", "--country"),
    config: str = typer.Option("configs/poland.yaml", "--config"),
    max_records: int = typer.Option(1000, "--max-records"),
    resume: bool = typer.Option(False, "--resume"),
    concurrency: int = typer.Option(4, "--concurrency"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    _discover("commons", country, max_records, resume, concurrency, dry_run)


# ---- audit ----

@audit_app.command("coverage")
def audit_coverage(
    config: str = typer.Option("configs/poland.yaml", "--config"),
    out: str = typer.Option("data/work/reports/coverage.json", "--out"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    typer.echo(f"audit coverage out={out} dry_run={dry_run}")
    if dry_run:
        return
    typer.echo("coverage audit delegated (taxonomy+discovery stats).")


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


if __name__ == "__main__":
    app()
