"""The Connector interface. Every data source returns the same five tables, so
switching from the sample CSVs to real SAP exports is a configuration change."""
from __future__ import annotations

import io
from abc import ABC, abstractmethod

import pandas as pd

DATASETS = ("weekly_sales", "current_inventory", "sku_master", "distributor_master", "calendar")


class Connector(ABC):
    """A source of the five SAP export tables."""

    name = "base"

    @abstractmethod
    def fetch(self) -> dict[str, pd.DataFrame]:
        """Return {dataset name: DataFrame} for every name in DATASETS."""


def read_csv_bytes(raw: bytes) -> pd.DataFrame:
    """Parse CSV bytes the same way for every connector (all columns as text;
    typing happens in validation so bad values are reported, not crashed on)."""
    return pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
