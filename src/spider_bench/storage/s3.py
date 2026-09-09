"""S3 helpers. All image bytes + raw metadata + releases live on S3.

Key layout per country prefix, e.g. prefix=poland/:
  poland/raw-metadata/<source>/<snapshot>/<file>
  poland/media/sha256/<aa>/<bb>/<sha256>.<ext>
  poland/releases/<version>/<artifact>
  poland/work/logs/... (optional)
"""
from __future__ import annotations

import boto3
from botocore.exceptions import ClientError


def s3_client(region: str):
    return boto3.client("s3", region_name=region)


def media_key(prefix: str, sha256: str, ext: str) -> str:
    p = prefix.rstrip("/") + "/"
    return f"{p}media/sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}.{ext.lstrip('.')}"


def raw_metadata_key(prefix: str, source: str, snapshot: str, filename: str) -> str:
    p = prefix.rstrip("/") + "/"
    return f"{p}raw-metadata/{source}/{snapshot}/{filename}"


def release_key(prefix: str, version: str, filename: str) -> str:
    p = prefix.rstrip("/") + "/"
    return f"{p}releases/{version}/{filename}"


def public_url(bucket: str, region: str, key: str) -> str:
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"


def key_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
