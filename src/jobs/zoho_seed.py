"""Loads the demo data into Zoho Inventory: the distributors as vendors, the SKUs
as items, and a few purchase orders. Safe to run again (existing records are reused).

    python -m src.jobs.zoho_seed
"""
from __future__ import annotations

from datetime import date, timedelta

from src.connectors.csv_folder import CsvFolderConnector
from src.connectors.zoho import zoho_client
from src.settings import ROOT

# (SKU, units, days until delivery, status) - "open" POs count as on order; the draft does not.
DEMO_POS = [
    ("SKU001", 1000, 6, "open"),     # big order already placed: SKU001 should stop asking to reorder
    ("SKU004", 150, 3, "open"),      # perishable: brings cover inside its 3-4 week window
    ("SKU013", 60, 9, "open"),       # partial cover: still below the 6-week minimum
    ("SKU003", 300, 12, "draft"),    # draft, not sent to the supplier: must be ignored
]


def seed() -> dict:
    zc = zoho_client()
    data = CsvFolderConnector(ROOT / "data").fetch()
    master, dists = data["sku_master"], data["distributor_master"]

    vendors = {v["contact_name"]: v["contact_id"]
               for v in zc.get_all("contacts", "contacts", {"contact_type": "vendor"})}
    for _, d in dists.iterrows():
        if d["Distributor_Name"] not in vendors:
            v = zc.request("POST", "contacts", json={
                "contact_name": d["Distributor_Name"], "contact_type": "vendor",
                "company_name": d["Distributor_Name"],
                "notes": f"Freshroots distributor {d['Distributor_ID']} ({d['Region']})"})["contact"]
            vendors[d["Distributor_Name"]] = v["contact_id"]

    items = {i.get("sku"): i["item_id"] for i in zc.get_all("items", "items")}
    for _, s in master.iterrows():
        if s["SKU_ID"] not in items:
            it = zc.request("POST", "items", json={
                "name": s["SKU_Name"], "sku": s["SKU_ID"], "unit": "pcs", "item_type": "inventory",
                "product_type": "goods", "rate": 100, "purchase_rate": 60,
                "description": f"{s['Category']} · perishability {s['Perishability']}"})["item"]
            items[s["SKU_ID"]] = it["item_id"]

    existing = {(p.get("reference_number") or "") for p in zc.get_all("purchaseorders", "purchaseorders")}
    created = []
    dist_name = dists.set_index("Distributor_ID")["Distributor_Name"]
    for sku, qty, days, status in DEMO_POS:
        ref = f"DEMO-{sku}"
        if ref in existing:
            continue
        row = master.set_index("SKU_ID").loc[sku]
        po = zc.request("POST", "purchaseorders", json={
            "vendor_id": vendors[dist_name[row["Distributor_ID"]]], "reference_number": ref,
            "date": date.today().isoformat(), "delivery_date": (date.today() + timedelta(days=days)).isoformat(),
            "line_items": [{"item_id": items[sku], "quantity": qty, "rate": 60}]})["purchaseorder"]
        if status == "open":
            zc.request("POST", f"purchaseorders/{po['purchaseorder_id']}/status/open")
        created.append((po["purchaseorder_number"], sku, qty, status))
    return {"vendors": len(vendors), "items": len(items), "created_pos": created}


if __name__ == "__main__":
    print(seed())
