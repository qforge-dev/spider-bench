"""Immutable S3 benchmark suites and disposable, checksum-verified local caches."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import mimetypes
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError

from spider_bench.benchmark.tasks import read_tasks, tasks_hash
from spider_bench.storage.s3 import media_key, public_url

DEFAULT_CACHE = Path("data/work/benchmark-s3-cache")


def client_for(region: str):
    session = boto3.Session()
    options = {"retries": {"mode": "standard", "max_attempts": 4}, "max_pool_connections": 12}
    if session.get_credentials() is None:
        options["signature_version"] = UNSIGNED
    return session.client("s3", region_name=region, config=Config(**options))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        handle.write(data)
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def object_bytes(s3, bucket: str, key: str) -> bytes:
    response = s3.get_object(Bucket=bucket, Key=key)
    try:
        return response["Body"].read()
    finally:
        response["Body"].close()


def cached_object(uri: str, sha256: str, *, cache: Path = DEFAULT_CACHE,
                  s3=None, region: str = "us-east-1", download: bool = True) -> bytes:
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ValueError("invalid asset checksum")
    path = Path(cache) / sha256[:2] / sha256
    if path.exists():
        data = path.read_bytes()
        if digest(data) == sha256:
            return data
    if not download:
        raise ValueError(f"missing or corrupt local asset: {sha256}")
    location = urlparse(uri)
    if location.scheme != "s3" or not location.netloc or not location.path.lstrip("/"):
        raise ValueError("asset must reference an S3 object")
    data = object_bytes(s3 or client_for(region), location.netloc, location.path.lstrip("/"))
    if digest(data) != sha256:
        raise ValueError(f"S3 asset checksum mismatch: {uri}")
    atomic_write(path, data)
    return data


def task_asset(task: dict, kind: str, **kwargs) -> bytes:
    if kind == "image":
        uri, sha = task.get("image_s3_uri"), task["image_sha256"]
        local = task.get("image_local_path")
    elif kind == "source":
        meta = task["meta"]
        uri, sha = meta.get("source_snapshot_s3_uri"), meta["source_snapshot_sha256"]
        local = meta.get("source_snapshot")
    else:
        raise ValueError("unknown task asset")
    if uri:
        return cached_object(uri, sha, **kwargs)
    data = Path(local).read_bytes()
    if digest(data) != sha:
        raise ValueError(f"local {kind} checksum mismatch")
    return data


def suite_prefix(prefix: str, suite: str) -> str:
    if not suite or suite in {".", ".."} or "/" in suite or "\\" in suite:
        raise ValueError("suite must be a single directory name")
    return f"{prefix.rstrip('/')}/benchmarks/{suite}/"


def build_publication(directory: Path, *, bucket: str, prefix: str, region: str,
                      preparation_cache: Path, staging: Path) -> dict:
    """Prepare portable metadata without modifying the local suite or historical runs."""
    from spider_bench.benchmark.prepare import validate_suite

    validation = validate_suite(directory)
    original = json.loads((directory / "manifest.json").read_text())
    suite = original["suite"]
    dest = suite_prefix(prefix, suite)
    if original.get("storage"):
        raise ValueError("suite already uses S3; publish the original preparation directory to retry")
    rows = copy.deepcopy(read_tasks(directory / "tasks.jsonl"))
    artifacts = {}

    def add(name: str, path: Path, key: str | None = None):
        data = path.read_bytes()
        artifacts[name] = {"path": str(path), "key": key or dest + name,
                           "sha256": digest(data), "bytes": len(data)}
        return artifacts[name]

    # Preserve all frozen records and downloaded originals, including excluded rows.
    for folder in ("images", "observations"):
        for path in sorted((preparation_cache / folder).glob("*")):
            if path.is_file():
                key = media_key(prefix, path.stem, "jpg") if folder == "images" and path.suffix == ".jpg" else None
                add(f"preparation/{folder}/{path.name}", path, key)
    for row in rows:
        meta = row["meta"]
        image = add(f"preparation/images/{row['image_sha256']}.jpg", Path(row.pop("image_local_path")),
                    media_key(prefix, row["image_sha256"], "jpg"))
        source = add(f"preparation/observations/{meta['observation_id']}.json", Path(meta.pop("source_snapshot")))
        raw = artifacts[f"preparation/images/inat-{meta['photo_id']}.source"]
        if image["sha256"] != row["image_sha256"] or source["sha256"] != meta["source_snapshot_sha256"] or raw["sha256"] != meta["source_image_sha256"]:
            raise ValueError("preparation asset changed")
        row["image_s3_uri"] = f"s3://{bucket}/{image['key']}"
        row["image_public_url"] = public_url(bucket, region, image["key"])
        meta["source_snapshot_s3_uri"] = f"s3://{bucket}/{source['key']}"
        meta["source_image_s3_uri"] = f"s3://{bucket}/{raw['key']}"
    source_tasks = add("source-tasks.jsonl", Path(original["source_tasks"]))
    if tasks_hash(read_tasks(Path(original["source_tasks"]))) != original["source_tasks_hash"]:
        raise ValueError("source task pool changed")
    add("exclusions.jsonl", directory / "exclusions.jsonl")
    manifest = copy.deepcopy(original)
    manifest.pop("source_tasks")
    manifest["source_tasks_s3_uri"] = f"s3://{bucket}/{source_tasks['key']}"
    manifest["prepublication_tasks_hash"] = original["tasks_hash"]
    manifest["tasks_hash"] = tasks_hash(rows)
    manifest["storage"] = {"kind": "s3", "bucket": bucket, "prefix": dest, "region": region}
    validation["tasks_hash"] = manifest["tasks_hash"]
    staging.mkdir(parents=True, exist_ok=True)
    for name, data in {
        "tasks.jsonl": "".join(json.dumps(t, sort_keys=True) + "\n" for t in rows).encode(),
        "manifest.json": json_bytes(manifest),
        "validation.json": json_bytes(validation),
    }.items():
        atomic_write(staging / name, data)
        add(name, staging / name)
    inventory = {"suite": suite, "tasks_hash": manifest["tasks_hash"], "format_version": 1,
                 "files": {name: {k: v for k, v in item.items() if k != "path"}
                           for name, item in sorted(artifacts.items())}}
    inventory_data = json_bytes(inventory)
    atomic_write(staging / "inventory.json", inventory_data)
    add("inventory.json", staging / "inventory.json")
    return {"artifacts": artifacts, "inventory": inventory,
            "complete": {"format_version": 1, "inventory_sha256": digest(inventory_data),
                         "tasks_hash": manifest["tasks_hash"]}, "prefix": dest}


def publish_suite(plan: dict, *, bucket: str, s3, progress=print) -> dict:
    """Conditional immutable writes; checksum inventory and COMPLETE are written last."""
    def absent(exc):
        return exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}

    def put(key, data, sha):
        try:
            head = s3.head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")
        except ClientError as exc:
            if not absent(exc):
                raise
        else:
            expected = base64.b64encode(bytes.fromhex(sha)).decode()
            if head.get("ContentLength") == len(data) and head.get("ChecksumSHA256") == expected:
                return
            if digest(object_bytes(s3, bucket, key)) == sha:
                return
            raise ValueError(f"refusing to overwrite different S3 object: {key}")
        result = s3.put_object(Bucket=bucket, Key=key, Body=data, IfNoneMatch="*",
                              ChecksumSHA256=base64.b64encode(bytes.fromhex(sha)).decode(),
                              ContentType=mimetypes.guess_type(key)[0] or "application/octet-stream",
                              Metadata={"sha256": sha})
        if result.get("ChecksumSHA256") != base64.b64encode(bytes.fromhex(sha)).decode():
            raise ValueError(f"S3 did not confirm the uploaded checksum: {key}")

    marker_key = plan["prefix"] + "COMPLETE"
    try:
        existing = json.loads(object_bytes(s3, bucket, marker_key))
    except ClientError as exc:
        if not absent(exc):
            raise
    else:
        if existing != plan["complete"]:
            raise ValueError("a different completed suite already exists; use a new suite name")
        return {"complete_uri": f"s3://{bucket}/{marker_key}", "already_published": True}

    def upload(item):
        data = Path(item["path"]).read_bytes()
        if digest(data) != item["sha256"]:
            raise ValueError("local publication file changed")
        put(item["key"], data, item["sha256"])

    items = [v for k, v in plan["artifacts"].items() if k != "inventory.json"]
    with ThreadPoolExecutor(max_workers=10) as pool:
        for i, _ in enumerate(pool.map(upload, items), 1):
            if i % 250 == 0 or i == len(items):
                progress(f"S3 verified {i}/{len(items)} objects")
    upload(plan["artifacts"]["inventory.json"])
    data = json_bytes(plan["complete"])
    put(marker_key, data, digest(data))
    return {"complete_uri": f"s3://{bucket}/{marker_key}", "files": len(items) + 2,
            "tasks_hash": plan["complete"]["tasks_hash"]}


def restore_suite(directory: Path, *, suite: str, bucket: str, prefix: str,
                  region: str = "us-east-1", s3=None, cache: Path = DEFAULT_CACHE,
                  archive: bool = False, progress=print) -> dict:
    """Restore portable suite files and verified assets from a completed S3 publication."""
    s3 = s3 or client_for(region)
    dest = suite_prefix(prefix, suite)
    complete_data = object_bytes(s3, bucket, dest + "COMPLETE")
    complete = json.loads(complete_data)
    inventory_data = object_bytes(s3, bucket, dest + "inventory.json")
    if digest(inventory_data) != complete["inventory_sha256"]:
        raise ValueError("S3 inventory checksum mismatch")
    inventory = json.loads(inventory_data)
    if inventory["suite"] != suite or inventory["tasks_hash"] != complete["tasks_hash"]:
        raise ValueError("S3 suite identity mismatch")
    files = inventory["files"]
    restored = {}
    for name in ("tasks.jsonl", "manifest.json", "exclusions.jsonl", "validation.json", "source-tasks.jsonl"):
        entry = files[name]
        data = cached_object(f"s3://{bucket}/{entry['key']}", entry["sha256"], cache=cache, s3=s3)
        if (directory / name).exists() and (directory / name).read_bytes() != data:
            raise ValueError(f"local suite differs from S3: {directory / name}; restore to a new directory")
        restored[name] = data
    manifest = json.loads(restored["manifest.json"])
    rows = [json.loads(line) for line in restored["tasks.jsonl"].splitlines() if line.strip()]
    if manifest["suite"] != suite or tasks_hash(rows) != complete["tasks_hash"] or manifest["tasks_hash"] != complete["tasks_hash"]:
        raise ValueError("S3 tasks/manifest mismatch")
    wanted = {}
    for row in rows:
        wanted[row["image_s3_uri"]] = row["image_sha256"]
        wanted[row["meta"]["source_snapshot_s3_uri"]] = row["meta"]["source_snapshot_sha256"]
    if archive:
        wanted.update({f"s3://{bucket}/{v['key']}": v["sha256"] for v in files.values()})

    def fetch(item):
        uri, sha = item
        cached_object(uri, sha, cache=cache, s3=s3)

    with ThreadPoolExecutor(max_workers=10) as pool:
        for i, _ in enumerate(pool.map(fetch, wanted.items()), 1):
            if i % 500 == 0 or i == len(wanted):
                progress(f"S3 cache verified {i}/{len(wanted)} assets")
    # Only install the suite metadata after all required assets have been verified.
    for name, data in restored.items():
        atomic_write(directory / name, data)
    atomic_write(directory / "inventory.json", inventory_data)
    atomic_write(directory / "COMPLETE", complete_data)
    return {"suite": suite, "tasks": len(rows), "tasks_hash": manifest["tasks_hash"],
            "assets": len(wanted), "s3_uri": f"s3://{bucket}/{dest}"}
