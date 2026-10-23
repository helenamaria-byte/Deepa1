"""Reads the five exports from a local folder (default: ./data)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.connectors.base import DATASETS, Connector, read_csv_bytes


class CsvFolderConnector(Connector):
    name = "csv_folder"

    def __init__(self, folder: Path | str):
        self.folder = Path(folder)

    def fetch(self) -> dict[str, pd.DataFrame]:
        out = {}
        for name in DATASETS:
            path = self.folder / f"{name}.csv"
            if not path.exists():
                raise FileNotFoundError(f"Missing export file: {path.name}")
            out[name] = read_csv_bytes(path.read_bytes())
        return out
