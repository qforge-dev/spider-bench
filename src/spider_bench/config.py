"""Typed config loading for spider-bench (S3-native)."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class AwsConfig(BaseModel):
    region: str = "us-east-1"
    bucket: str = "spiders-dataset-088543363904"
    prefix: str = "poland/"
    public_base_url: str = ""


class LocalConfig(BaseModel):
    work_dir: str = "data/work"
    sqlite_path: str = "data/work/spider-bench.sqlite"


class CollectionConfig(BaseModel):
    license_profile: str = "research"
    max_records: int = 1000
    concurrency: int = 4
    rate_limit_per_sec: float = 2.0


class CountryConfig(BaseModel):
    country: str = "PL"
    country_prefix: str = "poland"
    aws: AwsConfig = Field(default_factory=AwsConfig)
    local_: LocalConfig = Field(default_factory=LocalConfig, alias="local")
    collection: CollectionConfig = Field(default_factory=CollectionConfig)
    sources: dict = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


def load_country_config(path: str | Path) -> CountryConfig:
    data = yaml.safe_load(Path(path).read_text())
    return CountryConfig.model_validate(data)
