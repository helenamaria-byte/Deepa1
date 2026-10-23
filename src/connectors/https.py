"""Fetches the five exports over HTTPS from `<base_url>/<dataset>.csv`.
DATA_SOURCE_CREDENTIALS, if set, is sent as a bearer token."""
from __future__ import annotations

import httpx
import pandas as pd

from src.connectors.base import DATASETS, Connector, read_csv_bytes


class HttpsConnector(Connector):
    name = "https"

    def __init__(self, base_url: str, token: str = "", transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.transport = transport

    def fetch(self) -> dict[str, pd.DataFrame]:
        out = {}
        with httpx.Client(headers=self.headers, transport=self.transport, timeout=60) as client:
            for name in DATASETS:
                resp = client.get(f"{self.base_url}/{name}.csv")
                resp.raise_for_status()
                out[name] = read_csv_bytes(resp.content)
        return out
