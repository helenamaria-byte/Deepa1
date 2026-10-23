"""Analytics charts, the front-page status line, and the next-plan time."""
import json
import re
from datetime import datetime

from fastapi.testclient import TestClient

from src.app.charts import sales_chart, stock_overview
from src.app.main import app, get_store
from src.engine.pipeline import HISTORY_WEEKS, run_engine
from src.jobs.run_plan import IST, RUN_WEEKDAYS, next_scheduled_run, run_plan
from src.rules_config import load_default_rules
from src.settings import ROOT
from tests.conftest import RUN_DATE, StaticConnector, no_sleep


def make_run(raw):
    return run_plan(RUN_DATE, store=get_store(), connector=StaticConnector(raw), sleep=no_sleep, send_email=False)


def test_sales_history_is_stored_with_each_run(raw):
    run = make_run(raw)
    hist = get_store().recommendation(run["id"], "SKU001")["sales_history"]
    assert len(hist) == HISTORY_WEEKS and hist[0] == ["2026-W27", 194] and hist[-1] == ["2026-W42", 259]


def test_missing_week_shows_as_gap(raw):
    s = raw["weekly_sales"]
    raw["weekly_sales"] = s[~((s.SKU_ID == "SKU010") & (s.Week == "2026-W36"))]
    rec = run_engine(raw, RUN_DATE, load_default_rules()).recommendations.set_index("sku_id").loc["SKU010"]
    assert ["2026-W36", None] in rec["sales_history"]


def test_stock_overview_one_row_per_sku_lowest_first(raw):
    recs = run_engine(raw, RUN_DATE, load_default_rules()).recommendations.to_dict("records")
    svg = str(stock_overview(recs))
    ids = re.findall(r'font-weight="600">(SKU\d+)<', svg)
    assert len(ids) == 15 and ids[0] in ("SKU004", "SKU011") and ids[-1] == "SKU006"
    assert "SKU001 Freshroots Mango Drink 200ml: 3.35 weeks of stock; target 6-14 weeks; Reorder now" in svg


def test_stock_overview_splits_on_order():
    r = {"sku_id": "SKU001", "sku_name": "Mango", "status": "HIGH_DEMAND", "cover_weeks": 7.07, "min_cover": 6.0,
         "max_cover": 14.0, "on_order": 1000.0, "on_hand": 900.0, "weekly_demand": 268.65}
    svg = str(stock_overview([r]))
    assert "3.35 on hand + 3.72 on order" in svg and svg.count("<path") == 2


def test_sales_chart_has_hover_targets_and_labelled_averages():
    hist = [[f"2026-W{w}", u] for w, u in zip(range(27, 43), [194, 188, 204, 186, 201, 195, 185, 200, 185,
                                                               197, 186, 186, 261, 267, 257, 259])]
    svg = str(sales_chart(hist, 261.0, 214.92, 4, 12))
    assert svg.count("data-tip=") == 16 and "2026-W39: 261 units sold" in svg
    assert "4-wk avg 261" in svg and "12-wk avg 215" in svg
    assert sales_chart(hist[:1], None, None, 4, 12) == ""


def test_front_page_has_no_charts_and_analytics_has_both(raw):
    run = make_run(raw)
    c = TestClient(app)
    home = c.get("/").text
    assert 'stock-chart' not in home and 'sales-chart' not in home and 'class="gauge"' not in home
    sku_page = c.get(f"/sku/{run['id']}/SKU001").text
    assert "sales-chart" not in sku_page and "/analytics?sku=SKU001" in sku_page
    page = c.get("/analytics?sku=SKU001").text
    assert 'class="stock-chart"' in page and 'class="sales-chart"' in page
    assert "SKUs in this plan" in page and "Show as a table" in page
    assert '<option value="SKU001" selected>' in page


def test_status_line_says_what_is_done_and_what_is_due(raw):
    make_run(raw)
    c = TestClient(app)
    home = c.get("/").text
    assert "0 decisions made</b> for the plan of Thu 22 Oct" in home
    assert "7 decisions left</span>," in home and "when the next plan runs" in home
    c.post("/sku/1/SKU001", data={"decision": "APPROVED", "who": "Raghav"})
    home = c.get("/").text
    assert "1 decision made</b>" in home and "6 decisions left" in home


def test_next_plan_time_matches_the_schedule():
    # Fri 23 Oct 2026 21:00 IST -> Mon 26 Oct 06:00 IST; Mon 05:59 -> same Mon 06:00; Mon 06:00 -> Thu.
    assert next_scheduled_run(datetime(2026, 10, 23, 21, 0, tzinfo=IST)) == datetime(2026, 10, 26, 6, 0, tzinfo=IST)
    assert next_scheduled_run(datetime(2026, 10, 26, 5, 59, tzinfo=IST)) == datetime(2026, 10, 26, 6, 0, tzinfo=IST)
    assert next_scheduled_run(datetime(2026, 10, 26, 6, 0, tzinfo=IST)) == datetime(2026, 10, 29, 6, 0, tzinfo=IST)
    cron = next(c["schedule"] for c in json.loads((ROOT / "vercel.json").read_text())["crons"]
                if c["path"] == "/api/cron/run")
    assert cron == "30 0 * * 1,4" and RUN_WEEKDAYS == (0, 3)  # 00:30 UTC Mon/Thu = 06:00 IST
