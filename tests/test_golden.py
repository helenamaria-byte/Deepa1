"""Golden test: the engine must reproduce the expected flags for the provided
sample data (run date 2026-10-22, a festival month), and agree with an
independent, deliberately simple pandas calculation written only for this test."""
from datetime import date

import pandas as pd
import pytest

from src.connectors.csv_folder import CsvFolderConnector
from src.engine.pipeline import run_engine
from src.rules_config import load_default_rules
from src.settings import ROOT

RUN_DATE = date(2026, 10, 22)

EXPECTED = {
    "SKU001": ("STOCKOUT_RISK", 3.35, 750),
    "SKU003": ("STOCKOUT_RISK", 4.36, 300),
    "SKU004": ("STOCKOUT_RISK", 1.80, 150),
    "SKU011": ("STOCKOUT_RISK", 1.80, 500),
    "SKU013": ("STOCKOUT_RISK", 3.17, 120),
    "SKU006": ("OVERSTOCK_RISK", 22.2, 0),
    "SKU007": ("OVERSTOCK_RISK", 4.08, 0),
}
OK_SKUS = ["SKU002", "SKU005", "SKU008", "SKU009", "SKU010", "SKU012", "SKU014", "SKU015"]


@pytest.fixture(scope="module")
def data():
    return CsvFolderConnector(ROOT / "data").fetch()


@pytest.fixture(scope="module")
def result(data):
    return run_engine(data, RUN_DATE, load_default_rules()).recommendations.set_index("sku_id")


def test_golden_flags_and_quantities(result):
    for sku, (status, cover, qty) in EXPECTED.items():
        row = result.loc[sku]
        assert row["status"] == status, sku
        assert row["cover_weeks"] == pytest.approx(cover, abs=0.01 if cover < 10 else 0.05), sku
        assert row["recommended_qty"] == qty, sku
    assert "HIGH_DEMAND" in result.loc["SKU001", "flags"]
    for sku in OK_SKUS:
        assert result.loc[sku, "status"] == "OK", sku
    assert (result["status"] == "EXCEPTION").sum() == 0


def test_perishable_skus_use_perishable_window(result):
    assert bool(result.loc["SKU007", "perishable"])
    assert result.loc["SKU007", "max_cover"] == 4
    assert result.loc["SKU003", "min_cover"] == 6


def _independent(data, festival_uplift):
    """Independent recomputation using plain string filters and groupby."""
    sales = data["weekly_sales"].astype({"Units_Sold": int})
    last12 = [f"2026-W{w}" for w in range(31, 43)]
    last4 = [f"2026-W{w}" for w in range(39, 43)]
    avg12 = sales[sales.Week.isin(last12)].groupby("SKU_ID").Units_Sold.mean()
    avg4 = sales[sales.Week.isin(last4)].groupby("SKU_ID").Units_Sold.mean()
    inv = data["current_inventory"].set_index("SKU_ID").Units_On_Hand.astype(float)
    master = data["sku_master"].set_index("SKU_ID")
    out = {}
    for sku in master.index:
        demand = avg12[sku] * festival_uplift
        cover = inv[sku] / demand
        lo, hi = (3, 4) if master.loc[sku, "Perishability"] == "High" else (6, 14)
        flags = []
        if avg4[sku] > 1.2 * avg12[sku]:
            flags.append("HIGH_DEMAND")
        if cover < lo:
            flags.append("STOCKOUT_RISK")
        if cover > hi:
            flags.append("OVERSTOCK_RISK")
        out[sku] = (round(cover, 2), sorted(flags))
    return out


def test_matches_independent_pandas_calculation(data, result):
    expected = _independent(data, 1.25)
    for sku, (cover, flags) in expected.items():
        assert result.loc[sku, "cover_weeks"] == pytest.approx(cover, abs=0.005), sku
        assert sorted(result.loc[sku, "flags"]) == flags, sku


def test_uplift_flip_in_non_festival_month(data):
    """Same data, but October marked as a non-festival month: SKU002 (14.33 weeks)
    must flip to OVERSTOCK_RISK. Proves the uplift is wired into the thresholds."""
    cal = data["calendar"].copy()
    cal.loc[cal.Year_Month == "2026-10", "Is_Festival_Month"] = False
    no_festival = {**data, "calendar": cal}
    res = run_engine(no_festival, RUN_DATE, load_default_rules()).recommendations.set_index("sku_id")
    assert res.loc["SKU002", "cover_weeks"] == pytest.approx(14.33, abs=0.01)
    assert res.loc["SKU002", "status"] == "OVERSTOCK_RISK"
    assert res.loc["SKU002", "recommended_qty"] == 0
    indep = _independent(data, 1.0)
    for sku, (cover, flags) in indep.items():
        assert sorted(res.loc[sku, "flags"]) == flags, sku


def test_uplift_flip_via_config_switch(data):
    rules = load_default_rules()
    rules.festival.enabled = False
    res = run_engine(data, RUN_DATE, rules).recommendations.set_index("sku_id")
    assert res.loc["SKU002", "status"] == "OVERSTOCK_RISK"
