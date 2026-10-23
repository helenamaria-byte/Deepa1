"""Reads the five exports from `s3://bucket/prefix/<dataset>.csv`.
Uses boto3 (installed only when this source is configured). Credentials come
from DATA_SOURCE_CREDENTIALS as `ACCESS_KEY_ID:SECRET_ACCESS_KEY`, or from the
standard AWS environment."""
from __future__ import annotations

from urllib.parse import urlparse

import pandas as pd

from src.connectors.base import DATASETS, Connector, read_csv_bytes


class S3Connector(Connector):
    name = "s3"

    def __init__(self, url: str, credentials: str = ""):
        parsed = urlparse(url)
        self.bucket, self.prefix = parsed.netloc, parsed.path.strip("/")
        self.credentials = credentials

    def fetch(self) -> dict[str, pd.DataFrame]:
        import boto3  # optional dependency

        kwargs = {}
        if ":" in self.credentials:
            key_id, secret = self.credentials.split(":", 1)
            kwargs = {"aws_access_key_id": key_id, "aws_secret_access_key": secret}
        s3 = boto3.client("s3", **kwargs)
        out = {}
        for name in DATASETS:
            key = f"{self.prefix}/{name}.csv" if self.prefix else f"{name}.csv"
            out[name] = read_csv_bytes(s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())
        return out
