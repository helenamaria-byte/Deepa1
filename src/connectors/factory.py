"""Picks the connector from DATA_SOURCE_URL. Blank means the local ./data folder."""
from __future__ import annotations

from src.connectors.base import Connector
from src.connectors.csv_folder import CsvFolderConnector
from src.settings import ROOT, settings


def make_connector(url: str | None = None, credentials: str | None = None) -> Connector:
    url = settings.data_source_url if url is None else url
    credentials = settings.data_source_credentials if credentials is None else credentials
    if not url:
        return CsvFolderConnector(ROOT / "data")
    if url.startswith("file://"):
        return CsvFolderConnector(url[len("file://"):])
    if url.startswith(("http://", "https://")):
        from src.connectors.https import HttpsConnector
        return HttpsConnector(url, credentials)
    if url.startswith("s3://"):
        from src.connectors.s3 import S3Connector
        return S3Connector(url, credentials)
    if url.startswith("sftp://"):
        from src.connectors.sftp import SftpConnector
        return SftpConnector(url, credentials)
    return CsvFolderConnector(url)
