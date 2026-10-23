"""The two dashboard charts: data stored per run, rendered SVG, and page wiring."""
import re

from fastapi.testclient import TestClient

from src.app.charts import cover_gauge, sales_chart
from src.app.main import app, get_store
from src.engine.pipeline import HISTORY_WEEKS, run_engine
from src.jobs.run_plan import run_plan
from src.rules_config import load_default_rules
from tests.conftest import RUN_DATE, StaticConnector, no_sleep


def test_sales_history_is_stored_with_each_run(raw):
    run = run_plan(RUN_DATE, store=get_store(), connector=StaticConnector(raw), sleep=no_sleep, send_email=False)
    hist = get_store().recommendation(run["id"], "SKU001")["sales_history"]
    assert len(hist) == HISTORY_WEEKS and hist[0] == ["2026-W27", 194] and hist[-1] == ["2026-W42", 259]


def test_missing_week_shows_as_gap(raw):
    s = raw["weekly_sales"]
    raw["weekly_sales"] = s[~((s.SKU_ID == "SKU010") & (s.Week == "2026-W36"))]
    rec = run_engine(raw, RUN_DATE, load_default_rules()).recommendations.set_index("sku_id").loc["SKU010"]
    assert ["2026-W36", None] in rec["sales_history"]


def test_cover_gauge_marks_range_and_on_order():
    r = {"cover_weeks": 7.07, "min_cover": 6.0, "max_cover": 14.0, "on_order": 1000.0, "on_hand": 900.0,
         "weekly_demand": 268.65}
    svg = str(cover_gauge(r))
    assert svg.startswith("<svg") and "Target range 6 to 14 weeks" in svg
    assert "3.35 from stock on hand" in svg and svg.count("<path") == 2  # on hand + on order
    assert cover_gauge({**r, "cover_weeks": None}) == ""


def test_sales_chart_has_hover_targets_and_labelled_averages():
    hist = [[f"2026-W{w}", u] for w, u in zip(range(27, 43), [194, 188, 204, 186, 201, 195, 185, 200, 185,
                                                               197, 186, 186, 261, 267, 257, 259])]
    svg = str(sales_chart(hist, 261.0, 214.92, 4, 12))
    assert svg.count("data-tip=") == 16 and "2026-W39: 261 units sold" in svg
    assert "4-wk avg 261" in svg and "12-wk avg 215" in svg
    assert 'stroke-width="2"' in svg and not re.search(r"stroke-dasharray", svg)
    assert sales_chart(hist[:1], None, None, 4, 12) == ""


def test_pages_show_the_charts(raw):
    run = run_plan(RUN_DATE, store=get_store(), connector=StaticConnector(raw), sleep=no_sleep, send_email=False)
    c = TestClient(app)
    home = c.get("/").text
    assert home.count('class="gauge"') == 7  # one per SKU needing a decision; OK SKUs are folded away
    sku = c.get(f"/sku/{run['id']}/SKU001").text
    assert 'class="sales-chart"' in sku and "Show as a table" in sku and "2026-W39" in sku
