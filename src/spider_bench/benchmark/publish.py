"""Results publisher: run dir -> private S3 results prefix, COMPLETE last."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def publish_results(local_dir: str | Path, *, bucket: str, suite: str, run_id: str,
                    region: str = "us-east-1", dry_run: bool = False,
                    s3=None) -> dict[str, Any]:
    """Upload predictions + manifest to benchmarks/<suite>/<run_id>/. Never mutates completed runs."""
    import boto3

    s3 = s3 or boto3.client("s3", region_name=region)
    local_dir = Path(local_dir)
    dest = f"benchmarks/{suite}/{run_id}/"
    if not dry_run:
        try:
            s3.head_object(Bucket=bucket, Key=dest + "COMPLETE")
            raise ValueError(f"run already published (COMPLETE present): s3://{bucket}/{dest}")
        except Exception as e:
            if "already published" in str(e):
                raise
    keys = []
    for path in sorted(local_dir.rglob("*")):
        if not path.is_file():
            continue
        key = dest + str(path.relative_to(local_dir)).replace("\\", "/")
        keys.append(key)
        if not dry_run:
            s3.upload_file(str(path), bucket, key)
    if not dry_run:
        s3.put_object(Bucket=bucket, Key=dest + "COMPLETE", Body=b"complete")
    return {"keys": keys, "complete_key": dest + "COMPLETE", "dry_run": dry_run}
