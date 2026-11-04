"""Projected stock per SKU and the rule what-if on the Analytics page."""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.app.main import app, get_store
from src.app.projection import project_stock
from src.app.whatif import evaluate, what_if
from src.jobs.run_plan import run_plan
from src.rules_config import load_default_rules
from tests.conftest import RUN_DATE, StaticConnector, no_sleep

RULES = load_default_rules()


@pytest.fixture
def plan(raw):
    raw["open_purchase_orders"] = pd.DataFrame([
        {"SKU_ID": "SKU001", "PO_Number": "PO-1", "Units_On_Order": "1000", "Expected_Date": "2026-10-29"},
        {"SKU_ID": "SKU013", "PO_Number": "PO-3", "Units_On_Order": "60", "Expected_Date": ""}])
    store = get_store()
    run = run_plan(RUN_DATE, store=store, connector=StaticConnector(raw), sleep=no_sleep, send_email=False)
    return store, run


def test_projection_follows_demand_and_orders(plan):
    store, run = plan
    rec = store.recommendation(run["id"], "SKU001")
    p = project_stock(rec, run["run_date"], RULES)
    rows = p["rows"]
    assert len(rows) == 13 and rows[0]["starts"].isoformat() == "2026-10-19"   # from the stock date
    assert rows[0]["demand"] == pytest.approx(214.92 * 1.25, abs=0.01)           # October: festival uplift
    assert rows[0]["stock"] == pytest.approx(900 - 268.65, abs=0.1)
    assert rows[1]["arrivals"] == 1000 and rows[1]["arriving"] == ["PO-1 +1,000"]  # 29 Oct is in week 2
    dec = next(r for r in rows if r["starts"].month == 12)
    assert dec["festival"] is False and dec["demand"] == pytest.approx(214.92, abs=0.01)
    assert rows[0]["min"] == pytest.approx(6 * rows[0]["demand"])
    assert any("lead time is not in the data" in n for n in p["notes"])


def test_projection_reports_run_out_and_undated_orders(plan):
    store, run = plan
    p = project_stock(store.recommendation(run["id"], "SKU013"), run["run_date"], RULES)
    assert any(r["runs_out"] for r in p["rows"]) and p["notes"][0].startswith("Runs out in the week of")
    assert any("has no delivery date" in n for n in p["notes"])
    assert all(r["stock"] >= 0 for r in p["rows"])


def test_whatif_reproduces_the_plan_then_shows_changes(plan):
    store, run = plan
    recs = store.recommendations(run["id"])
    base = evaluate(recs, run["festival"], RULES)
    for r in recs:
        assert (base.at[r["sku_id"], "status"], base.at[r["sku_id"], "qty"]) == (r["status"], r["recommended_qty"])
    res = what_if(recs, run["festival"], RULES, {"uplift_pct": 0})
    changed = {c["sku_id"]: (c["before"], c["after"]) for c in res["changed"]}
    assert changed["SKU002"] == ("OK", "OVERSTOCK_RISK")  # the case's uplift on/off check
    assert dict((s, (a, b)) for s, a, b in res["counts"])["OVERSTOCK_RISK"][1] > 2


def test_whatif_rejects_impossible_values(plan):
    store, run = plan
    with pytest.raises(Exception):
        what_if(store.recommendations(run["id"]), run["festival"], RULES, {"gen_min": 20, "gen_max": 10})


def test_analytics_page_shows_projection_and_whatif(plan):
    store, run = plan
    c = TestClient(app)
    page = c.get("/analytics?sku=SKU001").text
    assert 'class="projection-chart"' in page and "Projected stock, next 13 weeks" in page
    assert "PO-1 +1,000 units arrive" in page and "What if the rules changed?" in page
    page = c.get("/analytics?sku=SKU001&uplift_pct=0").text
    assert "SKUs that would change" in page and "SKU002" in page
    bad = c.get("/analytics?gen_min=20&gen_max=10").text
    assert "don&#39;t work" in bad or "don't work" in bad
    assert store.decision_progress(run["id"])["decided"] == 0  # what-if saves nothing
    assert len(store.rules_history()) == 1
    home = c.get("/").text
    assert "projection-chart" not in home and "What if" not in home  # charts stay off Today
