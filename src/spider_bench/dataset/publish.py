"""S3 release publishing.

Flow: local staging dir (built by manifest.build_release) -> verify gates ->
upload to temp S3 staging prefix -> copy to ``poland/releases/<version>/`` ->
write ``COMPLETE`` last. Never mutate a completed release.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from botocore.exceptions import ClientError

from spider_bench.dataset.manifest import RELEASE_ARTIFACTS, sha256_of_file
from spider_bench.storage import s3 as s3mod

REQUIRED_REPORTS = (
    "reports/coverage.json",
    "reports/review-status.json",
)


def _read_parquet_rows(path: Path) -> list[dict[str, Any]]:
    table = pq.read_table(path)
    cols = table.column_names
    return [
        {c: table.column(c)[i].as_py() for c in cols} for i in range(table.num_rows)
    ]


def _fail(errors: list[str], msg: str) -> None:
    errors.append(msg)


def verify_release(
    directory: str | Path,
    expected_version: str | None = None,
    strict_reports: bool = False,
) -> list[str]:
    """Enforce release gates (§11). Returns list of error strings (empty = pass).

    Fails when:
    - included image lacks provenance / creator / accepted license;
    - referenced local media file missing or checksum differs;
    - taxon absent from pinned taxonomy snapshot (assessments/media);
    - duplicate records presented as distinct canonical images;
    - danger assessment has invalid category, missing geography/review state,
      or unsupported evidence reference;
    - missing danger evidence represented as none_known;
    - manifests/reports do not match recorded checksums;
    - release.json version mismatch.
    """
    directory = Path(directory)
    errors: list[str] = []

    for name in RELEASE_ARTIFACTS:
        if not (directory / name).exists():
            _fail(errors, f"missing release artifact: {name}")

    release_path = directory / "release.json"
    release: dict[str, Any] = {}
    if release_path.exists():
        try:
            release = json.loads(release_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            _fail(errors, f"release.json unparseable: {e}")
    if expected_version and release.get("version") != expected_version:
        _fail(
            errors,
            f"release.json version {release.get('version')!r} != expected {expected_version!r}",
        )

    # checksums gate: every recorded checksum must match.
    checksums_path = directory / "checksums.sha256"
    if checksums_path.exists():
        for line in checksums_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                digest, name = line.split(None, 1)
            except ValueError:
                _fail(errors, f"malformed checksums.sha256 line: {line!r}")
                continue
            target = directory / name.strip()
            if not target.exists():
                _fail(errors, f"checksummed file missing: {name.strip()}")
            elif sha256_of_file(target) != digest:
                _fail(errors, f"checksum mismatch: {name.strip()}")

    def load(name: str) -> list[dict[str, Any]]:
        p = directory / name
        if not p.exists():
            return []
        try:
            return _read_parquet_rows(p)
        except Exception as e:  # noqa: BLE001
            _fail(errors, f"{name} unreadable: {e}")
            return []

    taxa = load("taxa.parquet")
    media = load("media.parquet")
    attribution = load("attribution.parquet")
    danger = load("danger_assessments.parquet")
    evidence = load("evidence.parquet")

    taxa_names = {str(r.get("taxon", "")) for r in taxa}
    evidence_ids = {str(r.get("id", "")) for r in evidence}
    # Map evidence id -> has locator (doi/pmid/isbn/url column non-empty).
    evidence_has_locator = {
        str(r.get("id", "")): any(str(r.get(k, "") or "").strip() for k in ("doi", "pmid", "isbn", "url"))
        for r in evidence
    }
    # Which taxa have any supporting evidence claim? evidence rows carry taxon.
    taxa_with_evidence = {str(r.get("taxon", "")) for r in evidence if str(r.get("taxon", "") or "").strip()}

    # Media gates.
    seen_sha: set[str] = set()
    for m in media:
        sha = str(m.get("sha256", "") or "")
        if not sha:
            _fail(errors, "media row missing sha256")
            continue
        if sha in seen_sha:
            _fail(errors, f"duplicate media sha256 presented as distinct: {sha[:16]}…")
        seen_sha.add(sha)
        for field in ("s3_uri", "creator", "license", "source_record", "taxon"):
            if not str(m.get(field, "") or "").strip():
                _fail(errors, f"media {sha[:12]}… lacks {field} (gate §11)")
        taxon = str(m.get("taxon", "") or "")
        if taxon and taxa and taxon not in taxa_names:
            _fail(errors, f"media {sha[:12]}… taxon {taxon!r} absent from taxonomy snapshot")
        # Local byte verification when a local path is referenced.
        local_path = str(m.get("local_path", "") or "")
        expected = str(m.get("sha256", "") or "")
        if local_path:
            lp = Path(local_path)
            if not lp.is_absolute():
                lp = directory / lp
            if not lp.exists():
                _fail(errors, f"media {sha[:12]}… referenced file missing: {local_path}")
            elif sha256_of_file(lp) != expected:
                _fail(errors, f"media {sha[:12]}… checksum differs: {local_path}")

    # Attribution gate: every media sha needs creator+license+attribution text.
    attr_by_sha = {str(r.get("sha256", "")): r for r in attribution}
    for m in media:
        sha = str(m.get("sha256", "") or "")
        a = attr_by_sha.get(sha)
        if a is None:
            _fail(errors, f"media {sha[:12]}… missing attribution row")
        elif not str(a.get("attribution", "") or "").strip():
            _fail(errors, f"media {sha[:12]}… attribution text empty")

    # Danger gates.
    valid_categories = {"none_known", "minor_local_effects", "medically_significant", "uncertain"}
    for d in danger:
        taxon = str(d.get("taxon", "") or "")
        cat = str(d.get("category", "") or "")
        if cat not in valid_categories:
            _fail(errors, f"danger {taxon!r}: invalid category {cat!r}")
        for field in ("geographic_scope", "rationale", "reviewer", "review_date", "status"):
            if not str(d.get(field, "") or "").strip():
                _fail(errors, f"danger {taxon!r}: missing {field}")
        refs = d.get("evidence_ids") or d.get("evidence_refs") or []
        if isinstance(refs, str):
            refs = [r.strip() for r in refs.split(",") if r.strip()]
        if not refs:
            _fail(errors, f"danger {taxon!r}: missing evidence references")
        for ref in refs:
            if str(ref) not in evidence_ids:
                _fail(errors, f"danger {taxon!r}: unsupported evidence reference {ref!r}")
            elif not evidence_has_locator.get(str(ref), False):
                _fail(errors, f"danger {taxon!r}: evidence {ref!r} has no DOI/PMID/ISBN/URL")
        if taxa and taxon and taxon not in taxa_names:
            _fail(errors, f"danger {taxon!r}: taxon absent from taxonomy snapshot")
        if cat == "none_known" and taxon not in taxa_with_evidence and not refs:
            _fail(errors, f"danger {taxon!r}: missing evidence represented as none_known")
        # none_known with zero linked evidence rows is suspicious even if refs listed
        # but unresolvable — flag when refs resolve to other taxa only.
        if cat == "none_known":
            linked_taxa = set()
            for r in evidence:
                if str(r.get("id", "")) in {str(x) for x in refs}:
                    linked_taxa.add(str(r.get("taxon", "")))
            if linked_taxa and taxon not in linked_taxa and taxon not in taxa_with_evidence:
                _fail(
                    errors,
                    f"danger {taxon!r}: none_known but evidence rows reference other taxa "
                    f"({sorted(linked_taxa)}) — missing evidence must be 'uncertain'",
                )

    # Reports gate (advisory unless strict).
    for rep in REQUIRED_REPORTS:
        if not (directory / rep).exists() and strict_reports:
            _fail(errors, f"missing required report: {rep}")

    return errors


def _upload_dir(s3, bucket: str, local_dir: Path, dest_prefix: str, dry_run: bool) -> list[str]:
    keys: list[str] = []
    for path in sorted(local_dir.rglob("*")):
        if not path.is_file():
            continue
        key = dest_prefix + str(path.relative_to(local_dir)).replace("\\", "/")
        keys.append(key)
        if not dry_run:
            s3.upload_file(str(path), bucket, key)
    return keys


def publish_release(
    directory: str | Path,
    bucket: str,
    prefix: str,
    version: str,
    region: str = "us-east-1",
    dry_run: bool = False,
    strict_reports: bool = False,
    s3=None,
) -> dict[str, Any]:
    """Verify then publish a release to S3.

    Writes objects to a temp staging prefix, copies to
    ``<prefix>releases/<version>/``, writes ``COMPLETE`` last.
    Refuses to mutate a completed release (COMPLETE already present).
    Returns ``{"keys": [...], "complete_key": ...}``.
    """
    import boto3

    directory = Path(directory)
    errors = verify_release(directory, expected_version=version, strict_reports=strict_reports)
    if errors:
        raise ValueError(f"release verification failed ({len(errors)}):\n- " + "\n- ".join(errors))

    s3 = s3 if s3 is not None else boto3.client("s3", region_name=region)
    base = prefix.rstrip("/") + "/"
    final_prefix = f"{base}releases/{version}/"
    complete_key = final_prefix + "COMPLETE"

    if s3mod.key_exists(s3, bucket, complete_key):
        raise ValueError(
            f"refusing to mutate completed release s3://{bucket}/{complete_key} "
            "(material change requires a new version, §11)"
        )

    staging_prefix = f"{base}releases/.staging-{version}-tmp/"
    keys = _upload_dir(s3, bucket, directory, staging_prefix, dry_run)

    final_keys: list[str] = []
    if not dry_run:
        # Copy staging -> final.
        paginator = s3.get_paginator("list_objects_v2")
        staged: list[str] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=staging_prefix):
            for obj in page.get("Contents", []):
                staged.append(obj["Key"])
        if not staged and not keys:
            raise ValueError(f"staging upload produced no objects under {staging_prefix}")
        staged = staged or keys  # moto-less fakes may skip list; fall back to planned keys
        for sk in sorted(staged):
            name = sk[len(staging_prefix):]
            dk = final_prefix + name
            if dk.endswith("COMPLETE"):
                continue
            s3.copy_object(
                Bucket=bucket, Key=dk, CopySource={"Bucket": bucket, "Key": sk}
            )
            final_keys.append(dk)
        # Cleanup staging (keep it tidy; best-effort).
        for sk in staged:
            try:
                s3.delete_object(Bucket=bucket, Key=sk)
            except ClientError:
                pass
        # COMPLETE last, containing version + sha of checksums file.
        body = f"{version}\nsha256:{sha256_of_file(directory / 'checksums.sha256')}\n".encode()
        if dry_run is False:
            import io

            s3.upload_fileobj(io.BytesIO(body), bucket, complete_key)
    else:
        final_keys = [final_prefix + k[len(staging_prefix):] for k in keys]

    return {"keys": sorted(final_keys), "complete_key": complete_key, "dry_run": dry_run}
