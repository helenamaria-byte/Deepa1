"""Every exception reason code, perishable precedence and flag combinations.
Each test changes the real sample data in one specific way."""
import pandas as pd
import pytest

from src.engine.pipeline import run_engine
from src.rules_config import load_default_rules
from tests.conftest import RUN_DATE


def run(raw):
    return run_engine(raw, RUN_DATE, load_default_rules()).recommendations.set_index("sku_id")


def sales_set(raw, sku, weeks_values):
    s = raw["weekly_sales"]
    for week, value in weeks_values.items():
        s.loc[(s.SKU_ID == sku) & (s.Week == week), "Units_Sold"] = str(value)


def assert_exception(res, sku, code):
    assert res.loc[sku, "status"] == "EXCEPTION", res.loc[sku].to_dict()
    assert code in res.loc[sku, "exception_codes"]
    assert res.loc[sku, "recommended_qty"] is None or pd.isna(res.loc[sku, "recommended_qty"])


def test_new_launch(raw):
    s = raw["weekly_sales"]
    raw["weekly_sales"] = s[~((s.SKU_ID == "SKU005") & (s.Week < "2026-W35"))]
    assert_exception(run(raw), "SKU005", "NEW_LAUNCH")


def test_volatile_demand(raw):
    sales_set(raw, "SKU008", {f"2026-W{w}": 300 for w in range(39, 43)})
    assert_exception(run(raw), "SKU008", "VOLATILE_DEMAND")


def test_zero_sales_week(raw):
    sales_set(raw, "SKU009", {"2026-W35": 0})
    assert_exception(run(raw), "SKU009", "ZERO_SALES_WEEK")


def test_missing_weeks(raw):
    s = raw["weekly_sales"]
    raw["weekly_sales"] = s[~((s.SKU_ID == "SKU010") & (s.Week == "2026-W36"))]
    assert_exception(run(raw), "SKU010", "MISSING_WEEKS")


def test_missing_inventory(raw):
    inv = raw["current_inventory"]
    raw["current_inventory"] = inv[inv.SKU_ID != "SKU012"]
    assert_exception(run(raw), "SKU012", "MISSING_INVENTORY")


def test_stale_inventory(raw):
    raw["current_inventory"].loc[raw["current_inventory"].SKU_ID == "SKU014", "As_Of_Date"] = "2026-10-01"
    assert_exception(run(raw), "SKU014", "STALE_INVENTORY")


def test_negative_stock(raw):
    raw["current_inventory"].loc[raw["current_inventory"].SKU_ID == "SKU015", "Units_On_Hand"] = "-5"
    assert_exception(run(raw), "SKU015", "NEGATIVE_STOCK")


def test_inactive_distributor(raw):
    raw["distributor_master"].loc[raw["distributor_master"].Distributor_ID == "DIST004", "Status"] = "Inactive"
    res = run(raw)
    for sku in ("SKU010", "SKU011", "SKU012"):
        assert_exception(res, sku, "INACTIVE_DISTRIBUTOR")


def test_unmapped_distributor(raw):
    raw["sku_master"].loc[raw["sku_master"].SKU_ID == "SKU002", "Distributor_ID"] = "DIST999"
    assert_exception(run(raw), "SKU002", "UNMAPPED_DISTRIBUTOR")


def test_conflicting_flags(raw):
    # SKU006 is overstocked; lift its last 4 weeks by ~30% (under the 40% volatility limit).
    sales_set(raw, "SKU006", {**{f"2026-W{w}": 40 for w in range(31, 39)},
                              **{f"2026-W{w}": 56 for w in range(39, 43)}})
    res = run(raw)
    assert set(res.loc["SKU006", "flags"]) == {"HIGH_DEMAND", "OVERSTOCK_RISK"}
    assert_exception(res, "SKU006", "CONFLICTING_FLAGS")


def test_orphan_sku(raw):
    extra = pd.DataFrame([{"SKU_ID": "SKU999", "Week": f"2026-W{w}", "Units_Sold": "10"} for w in range(27, 43)])
    raw["weekly_sales"] = pd.concat([raw["weekly_sales"], extra], ignore_index=True)
    assert_exception(run(raw), "SKU999", "ORPHAN_SKU")


def test_duplicate_rows(raw):
    s = raw["weekly_sales"]
    raw["weekly_sales"] = pd.concat([s, s[(s.SKU_ID == "SKU003") & (s.Week == "2026-W40")]], ignore_index=True)
    assert_exception(run(raw), "SKU003", "DUPLICATE_ROWS")


def test_invalid_value(raw):
    raw["weekly_sales"].loc[(raw["weekly_sales"].SKU_ID == "SKU005") & (raw["weekly_sales"].Week == "2026-W30"),
                            "Units_Sold"] = "abc"
    assert_exception(run(raw), "SKU005", "INVALID_VALUE")


def test_every_reason_code_has_a_test():
    from src.ingest.validate import REASONS
    names = {n for n in globals() if n.startswith("test_")}
    for code in REASONS:
        assert f"test_{code.lower()}" in names, code


def test_clean_skus_unaffected_by_other_exceptions(raw):
    raw["current_inventory"].loc[raw["current_inventory"].SKU_ID == "SKU015", "Units_On_Hand"] = "-5"
    res = run(raw)
    assert res.loc["SKU001", "recommended_qty"] == 750
    assert (res["status"] == "EXCEPTION").sum() == 1


def test_perishable_window_overrides_general(raw):
    """SKU007 has 4.08 weeks: fine under the general 6-14 window (would be a stockout),
    but over the perishable max of 4, so it must be OVERSTOCK_RISK."""
    res = run(raw)
    assert res.loc["SKU007", "status"] == "OVERSTOCK_RISK"
    raw["sku_master"].loc[raw["sku_master"].SKU_ID == "SKU007", "Perishability"] = "Low"
    res_low = run(raw)
    assert res_low.loc["SKU007", "status"] == "STOCKOUT_RISK"
    assert res_low.loc["SKU007", "recommended_qty"] == 400


def test_high_demand_alone(raw):
    # SKU008: raise recent sales 35% so HIGH_DEMAND fires while cover stays in range.
    sales_set(raw, "SKU008", {f"2026-W{w}": 100 for w in range(31, 39)})
    sales_set(raw, "SKU008", {f"2026-W{w}": 135 for w in range(39, 43)})
    res = run(raw)
    assert res.loc["SKU008", "flags"] == ["HIGH_DEMAND"]
    assert res.loc["SKU008", "status"] == "HIGH_DEMAND"
    assert res.loc["SKU008", "recommended_qty"] == 450


def test_thresholds_come_from_config(raw):
    rules = load_default_rules()
    rules.cover.general.max_weeks = 25  # SKU006 (22.2 weeks) is no longer overstocked
    res = run_engine(raw, RUN_DATE, rules).recommendations.set_index("sku_id")
    assert res.loc["SKU006", "status"] == "OK"


def test_sales_data_behind_warning(raw):
    from datetime import date
    result = run_engine(raw, date(2026, 10, 27), load_default_rules())
    assert result.data_week == "2026-W42"
    assert any("W43" in w for w in result.warnings)


@pytest.mark.parametrize("missing", ["Units_Sold", "Status"])
def test_missing_column_stops_run(raw, missing):
    from src.ingest.validate import DataValidationError
    for name, df in raw.items():
        if missing in df.columns:
            raw[name] = df.drop(columns=[missing])
    with pytest.raises(DataValidationError):
        run(raw)
