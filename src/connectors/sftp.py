"""Reads the five exports from `sftp://user@host[:port]/folder`.
Uses paramiko (installed only when this source is configured); the password
comes from DATA_SOURCE_CREDENTIALS."""
from __future__ import annotations

import io
from urllib.parse import urlparse

import pandas as pd

from src.connectors.base import DATASETS, Connector, read_csv_bytes


class SftpConnector(Connector):
    name = "sftp"

    def __init__(self, url: str, credentials: str = ""):
        p = urlparse(url)
        self.host, self.port, self.user = p.hostname, p.port or 22, p.username
        self.folder = p.path or "/"
        self.password = credentials

    def fetch(self) -> dict[str, pd.DataFrame]:
        import paramiko  # optional dependency

        transport = paramiko.Transport((self.host, self.port))
        try:
            transport.connect(username=self.user, password=self.password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            out = {}
            for name in DATASETS:
                buf = io.BytesIO()
                sftp.getfo(f"{self.folder.rstrip('/')}/{name}.csv", buf)
                out[name] = read_csv_bytes(buf.getvalue())
            return out
        finally:
            transport.close()
