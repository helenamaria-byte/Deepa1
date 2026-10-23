"""Zoho Books open purchase orders (phase 2).

Behind the ZOHO_ENABLED flag. Until Zoho credentials are supplied this is a stub
that returns an empty open-PO table with the final column layout, so the rest
of the system can already consume it."""
from __future__ import annotations

import pandas as pd

OPEN_PO_COLUMNS = ["SKU_ID", "PO_Number", "Units_On_Order", "Expected_Date"]


class ZohoConnector:
    name = "zoho"

    def __init__(self, enabled: bool):
        self.enabled = enabled

    def fetch_open_pos(self) -> pd.DataFrame:
        return pd.DataFrame(columns=OPEN_PO_COLUMNS)
