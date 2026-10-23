"""Open purchase orders from Zoho Inventory (stock already on order).

Behind the ZOHO_ENABLED flag. When it is off, or no credentials are set,
fetch_open_pos() returns an empty table with the same columns, so the rest of
the system behaves exactly as before.

Counted as on order: purchase orders issued to the supplier (Zoho status
"issued", "open" or "partially_received") that are not fully received, for the
quantity not yet received. Drafts, received, closed and cancelled orders are
ignored."""
from __future__ import annotations

import pandas as pd

from src.connectors.zoho_api import ZohoClient
from src.settings import settings

OPEN_PO_COLUMNS = ["SKU_ID", "PO_Number", "Units_On_Order", "Expected_Date"]
OPEN_STATUSES = ("issued", "open", "partially_received")


def is_on_order(po: dict) -> bool:
    status = po.get("order_status") or po.get("status")
    return status in OPEN_STATUSES and po.get("received_status") != "received"


def zoho_client(transport=None) -> ZohoClient:
    return ZohoClient(settings.zoho_client_id, settings.zoho_client_secret, settings.zoho_refresh_token,
                      settings.zoho_org_id, settings.zoho_dc, transport=transport)


class ZohoConnector:
    name = "zoho"

    def __init__(self, enabled: bool | None = None, client: ZohoClient | None = None):
        self.enabled = settings.zoho_enabled if enabled is None else enabled
        self.client = client

    @property
    def configured(self) -> bool:
        return self.client is not None or all(
            [settings.zoho_client_id, settings.zoho_client_secret, settings.zoho_refresh_token, settings.zoho_org_id])

    def fetch_open_pos(self) -> pd.DataFrame:
        if not (self.enabled and self.configured):
            return pd.DataFrame(columns=OPEN_PO_COLUMNS)
        zc = self.client or zoho_client()
        skus = {i["item_id"]: i.get("sku") or "" for i in zc.get_all("items", "items")}
        rows = []
        for po in zc.get_all("purchaseorders", "purchaseorders"):
            if not is_on_order(po):
                continue
            detail = zc.request("GET", f"purchaseorders/{po['purchaseorder_id']}")["purchaseorder"]
            for line in detail.get("line_items", []):
                remaining = float(line.get("quantity", 0)) - float(line.get("quantity_received", 0) or 0)
                sku = line.get("sku") or skus.get(line.get("item_id"), "")
                if sku and remaining > 0:
                    rows.append({"SKU_ID": sku, "PO_Number": detail.get("purchaseorder_number", ""),
                                 "Units_On_Order": remaining,
                                 "Expected_Date": detail.get("delivery_date") or ""})
        return pd.DataFrame(rows, columns=OPEN_PO_COLUMNS)
