"""Zoho Inventory open purchase orders: API parsing, effect on cover, failure handling
and the database migration. Zoho itself is simulated with an httpx mock."""
import json

import httpx
import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect, text

from src.connectors.zoho import OPEN_PO_COLUMNS, ZohoConnector
from src.connectors.zoho_api import ZohoClient
from src.engine.pipeline import run_engine
from src.jobs.run_plan import run_plan
from src.rules_config import load_default_rules
from src.store.repo import Store
from tests.conftest import RUN_DATE, StaticConnector, no_sleep

POS = {
    "po1": {"purchaseorder_number": "PO-1", "order_status": "issued", "received_status": "to_be_received",
            "delivery_date": "2026-10-29", "lines": [("i1", 1000, 0)]},
    "po2": {"purchaseorder_number": "PO-2", "order_status": "issued", "received_status": "partially_received",
            "delivery_date": "2026-10-26", "lines": [("i4", 200, 50)]},           # 150 still to come
    "po3": {"purchaseorder_number": "PO-3", "order_status": "draft", "received_status": "to_be_received",
            "delivery_date": "2026-11-01", "lines": [("i3", 300, 0)]},            # draft: ignored
    "po4": {"purchaseorder_number": "PO-4", "order_status": "closed", "received_status": "received",
            "delivery_date": "2026-10-01", "lines": [("i13", 500, 500)]},         # received: ignored
}
ITEMS = {"i1": "SKU001", "i3": "SKU003", "i4": "SKU004", "i13": "SKU013"}


def zoho_mock(calls=None, fail=False):
    def handler(request: httpx.Request):
        if calls is not None:
            calls.append(request.url.path)
        if fail:
            return httpx.Response(503, json={"code": 1, "message": "Service unavailable"})
        path = request.url.path
        if path.endswith("/oauth/v2/token"):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["authorization"] == "Zoho-oauthtoken tok"
        assert request.url.params["organization_id"] == "org1"
        if path.endswith("/items"):
            return httpx.Response(200, json={"code": 0, "items": [{"item_id": k, "sku": v} for k, v in ITEMS.items()],
                                             "page_context": {"has_more_page": False}})
        if path.endswith("/purchaseorders"):
            rows = [{"purchaseorder_id": k, **{f: v[f] for f in ("purchaseorder_number", "order_status",
                                                                  "received_status")}} for k, v in POS.items()]
            return httpx.Response(200, json={"code": 0, "purchaseorders": rows, "page_context": {"has_more_page": False}})
        po_id = path.rsplit("/", 1)[-1]
        po = POS[po_id]
        lines = [{"item_id": i, "quantity": q, "quantity_received": r} for i, q, r in po["lines"]]
        return httpx.Response(200, json={"code": 0, "purchaseorder": {
            "purchaseorder_number": po["purchaseorder_number"], "delivery_date": po["delivery_date"],
            "line_items": lines}})
    return httpx.MockTransport(handler)


def connector(**kw):
    client = ZohoClient("cid", "secret", "refresh", "org1", "in", transport=zoho_mock(**kw))
    return ZohoConnector(enabled=True, client=client)


def test_reads_only_open_quantities():
    pos = connector().fetch_open_pos()
    assert list(pos.columns) == OPEN_PO_COLUMNS
    got = {r.SKU_ID: (r.PO_Number, r.Units_On_Order) for r in pos.itertuples()}
    assert got == {"SKU001": ("PO-1", 1000), "SKU004": ("PO-2", 150)}


def test_disabled_or_unconfigured_returns_empty_without_calls():
    calls = []
    client = ZohoClient("cid", "secret", "refresh", "org1", transport=zoho_mock(calls))
    assert ZohoConnector(enabled=False, client=client).fetch_open_pos().empty
    assert ZohoConnector(enabled=True).fetch_open_pos().empty  # no credentials in the test env
    assert calls == []


def test_open_orders_count_in_cover(raw):
    raw["open_purchase_orders"] = connector().fetch_open_pos()
    res = run_engine(raw, RUN_DATE, load_default_rules()).recommendations.set_index("sku_id")
    # SKU001: (900 + 1000) / 268.65 = 7.07 weeks -> no longer a stockout
    assert res.loc["SKU001", "on_order"] == 1000
    assert res.loc["SKU001", "cover_weeks"] == pytest.approx(7.07, abs=0.01)
    assert res.loc["SKU001", "status"] == "HIGH_DEMAND"  # demand is still rising: reorder 750, not urgent
    # SKU004 (perishable): (180 + 150) / 100.21 = 3.29 weeks -> inside the 3-4 window
    assert res.loc["SKU004", "status"] == "OK"
    # Draft PO for SKU003 is ignored
    assert res.loc["SKU003", "on_order"] == 0 and res.loc["SKU003", "status"] == "STOCKOUT_RISK"


def test_switch_off_restores_golden_result(raw):
    raw["open_purchase_orders"] = connector().fetch_open_pos()
    rules = load_default_rules()
    rules.open_orders.include_in_cover = False
    res = run_engine(raw, RUN_DATE, rules).recommendations.set_index("sku_id")
    assert res.loc["SKU001", "cover_weeks"] == 3.35 and res.loc["SKU001", "recommended_qty"] == 750
    assert res.loc["SKU004", "status"] == "STOCKOUT_RISK"


def test_po_for_unknown_sku_is_reported(raw):
    raw["open_purchase_orders"] = pd.DataFrame([{"SKU_ID": "SKU999", "PO_Number": "X", "Units_On_Order": 5,
                                                 "Expected_Date": ""}])
    result = run_engine(raw, RUN_DATE, load_default_rules())
    assert any("SKU999" in w for w in result.warnings)


def test_run_plan_uses_zoho_and_explains_it(raw):
    store = Store()
    run = run_plan(RUN_DATE, store=store, connector=StaticConnector(raw), sleep=no_sleep, zoho=connector())
    assert run["status"] == "SUCCESS" and run["connector"] == "static+zoho"
    rec = store.recommendation(run["id"], "SKU001")
    assert rec["on_order"] == 1000 and "1000 units already on order" in rec["explanation"]


def test_zoho_outage_does_not_stop_the_plan(raw):
    calls = []
    store = Store()
    run = run_plan(RUN_DATE, store=store, connector=StaticConnector(raw), sleep=no_sleep,
                   zoho=connector(calls=calls, fail=True))
    assert run["status"] == "SUCCESS" and run["connector"] == "static"
    assert any("could not be read from Zoho" in w for w in run["warnings"])
    assert sum(p.endswith("/oauth/v2/token") for p in calls) == 3  # retried three times
    assert store.recommendation(run["id"], "SKU001")["recommended_qty"] == 750  # falls back to stock on hand


def test_workbook_and_dashboard_show_on_order(raw):
    import io

    from fastapi.testclient import TestClient
    from openpyxl import load_workbook

    from src.app.main import app, get_store
    store = get_store()
    run = run_plan(RUN_DATE, store=store, connector=StaticConnector(raw), sleep=no_sleep, zoho=connector())
    c = TestClient(app)
    assert "+1,000 on order" in c.get("/recommendations").text
    wb = load_workbook(io.BytesIO(c.get(f"/runs/{run['id']}/workbook").content))
    header = [x.value for x in wb["Recommendations"][1]]
    assert "Units_On_Order" in header


def test_migration_adds_on_order_to_old_database(tmp_path):
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    eng = create_engine(url)
    with eng.begin() as conn:  # a v1 recommendations table, without on_order
        conn.execute(text("CREATE TABLE recommendations (id INTEGER PRIMARY KEY, run_id INTEGER, sku_id TEXT, "
                          "min_cover FLOAT, max_cover FLOAT, status TEXT, action TEXT)"))
    Store(url).migrate()
    assert "on_order" in {c["name"] for c in inspect(eng).get_columns("recommendations")}
    Store(url).migrate()  # running again is harmless
