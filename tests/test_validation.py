"""Data validation catches missing weeks, negative stock, unmapped SKUs,
duplicates and stale inventory (tested directly on validate())."""
import pandas as pd
import pytest

from src.ingest.validate import DataValidationError, validate
from src.rules_config import load_default_rules
from tests.conftest import RUN_DATE

RULES = load_default_rules()


def test_clean_sample_has_no_issues(raw):
    clean = validate(raw, RUN_DATE, RULES)
    assert clean.issues == {}
    assert clean.warnings == []


def test_catches_missing_weeks(raw):
    s = raw["weekly_sales"]
    raw["weekly_sales"] = s[~((s.SKU_ID == "SKU001") & s.Week.isin(["2026-W33", "2026-W34"]))]
    assert "MISSING_WEEKS" in validate(raw, RUN_DATE, RULES).issues["SKU001"]


def test_catches_negative_stock(raw):
    raw["current_inventory"].loc[0, "Units_On_Hand"] = "-10"
    assert "NEGATIVE_STOCK" in validate(raw, RUN_DATE, RULES).issues["SKU001"]


def test_catches_unmapped_skus(raw):
    inv = raw["current_inventory"]
    raw["current_inventory"] = pd.concat([inv, pd.DataFrame([{"SKU_ID": "SKU777", "Location": "Pune",
                                                              "Units_On_Hand": "5", "As_Of_Date": "2026-10-19"}])])
    assert "ORPHAN_SKU" in validate(raw, RUN_DATE, RULES).issues["SKU777"]


def test_catches_duplicates_in_each_table(raw):
    raw["sku_master"] = pd.concat([raw["sku_master"], raw["sku_master"].iloc[[1]]])
    raw["current_inventory"] = pd.concat([raw["current_inventory"], raw["current_inventory"].iloc[[2]]])
    s = raw["weekly_sales"]
    raw["weekly_sales"] = pd.concat([s, s.iloc[[100]]])
    issues = validate(raw, RUN_DATE, RULES).issues
    for sku in ("SKU002", "SKU003", s.iloc[100]["SKU_ID"]):
        assert "DUPLICATE_ROWS" in issues[sku]


def test_catches_stale_inventory(raw):
    raw["current_inventory"]["As_Of_Date"] = "2026-10-11"  # 11 days before the run
    issues = validate(raw, RUN_DATE, RULES).issues
    assert all("STALE_INVENTORY" in issues[s] for s in raw["sku_master"].SKU_ID)


def test_stale_threshold_is_configurable(raw):
    raw["current_inventory"]["As_Of_Date"] = "2026-10-11"
    rules = load_default_rules()
    rules.exceptions.stale_inventory_days = 15
    assert validate(raw, RUN_DATE, rules).issues == {}


def test_multiple_locations_are_summed(raw):
    inv = raw["current_inventory"]
    extra = inv[inv.SKU_ID == "SKU002"].assign(Location="Mumbai DC", Units_On_Hand="100")
    raw["current_inventory"] = pd.concat([inv, extra])
    clean = validate(raw, RUN_DATE, RULES)
    assert clean.inventory.set_index("SKU_ID").loc["SKU002", "Units_On_Hand"] == 1300


def test_missing_file_raises(raw):
    del raw["calendar"]
    with pytest.raises(DataValidationError):
        validate(raw, RUN_DATE, RULES)
