"""Validates and types the five raw tables.

Problems with a single SKU never stop the run: they become reason codes that send
that SKU to the exception queue. Only structural problems (a missing file or
column) stop the run, because then no SKU can be trusted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from src.connectors.base import DATASETS
from src.engine.weeks import last_complete_week_start, week_label, week_start, window
from src.rules_config import RulesConfig

REQUIRED_COLUMNS = {
    "weekly_sales": ["SKU_ID", "Week", "Units_Sold"],
    "current_inventory": ["SKU_ID", "Location", "Units_On_Hand", "As_Of_Date"],
    "sku_master": ["SKU_ID", "SKU_Name", "Category", "Perishability", "Standard_Reorder_Qty",
                   "Unit_of_Measure", "Distributor_ID"],
    "distributor_master": ["Distributor_ID", "Distributor_Name", "Region", "Status"],
    "calendar": ["Year", "Month", "Year_Month", "Is_Festival_Month"],
}

# Reason codes and their plain-English meaning (shown on the dashboard and workbook).
# {placeholders} are filled from the active rules by reason_text().
REASONS = {
    "NEW_LAUNCH": "Less than {min_history_weeks} weeks of sales history",
    "VOLATILE_DEMAND": "Recent weeks differ from the long-run average by more than {max_deviation_pct}%",
    "ZERO_SALES_WEEK": "A week with zero sales in the last {zero_sales_lookback_weeks} weeks",
    "MISSING_WEEKS": "Sales weeks missing from the data",
    "MISSING_INVENTORY": "No stock figure for this SKU",
    "STALE_INVENTORY": "Stock figure is more than {stale_inventory_days} days old",
    "NEGATIVE_STOCK": "Stock on hand is negative",
    "INACTIVE_DISTRIBUTOR": "Distributor is not active",
    "UNMAPPED_DISTRIBUTOR": "Distributor not found in the distributor list",
    "CONFLICTING_FLAGS": "Overstocked but demand is rising (conflicting signals)",
    "ORPHAN_SKU": "SKU has sales or stock but no master record",
    "DUPLICATE_ROWS": "Duplicate rows in the data",
    "INVALID_VALUE": "A value in the data could not be read",
}


def reason_text(code: str, rules: RulesConfig) -> str:
    """Plain-English text for a reason code, using the active thresholds."""
    ex = rules.exceptions
    return REASONS.get(code, code).format(
        min_history_weeks=ex.min_history_weeks,
        max_deviation_pct=round(ex.max_deviation * 100),
        zero_sales_lookback_weeks=ex.zero_sales_lookback_weeks,
        stale_inventory_days=ex.stale_inventory_days,
    )


class DataValidationError(Exception):
    """Raised when the data is structurally unusable (missing file or column)."""


@dataclass
class CleanData:
    sales: pd.DataFrame            # SKU_ID, Week, week_start, Units_Sold
    inventory: pd.DataFrame        # SKU_ID (one row per SKU), Units_On_Hand, As_Of_Date
    master: pd.DataFrame           # sku_master, one row per SKU
    distributors: pd.DataFrame     # one row per distributor
    calendar: pd.DataFrame
    run_date: date
    anchor: date                   # Monday of the last complete week used
    issues: dict[str, set[str]] = field(default_factory=dict)  # SKU_ID -> reason codes
    warnings: list[str] = field(default_factory=list)

    def add(self, sku: str, code: str) -> None:
        self.issues.setdefault(sku, set()).add(code)


def _strip(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip() for c in df.columns]
    for col in df.columns:
        df[col] = df[col].astype(str).str.strip()
    return df


def _to_bool(series: pd.Series) -> pd.Series:
    return series.str.upper().isin(["TRUE", "1", "YES", "Y"])


def validate(raw: dict[str, pd.DataFrame], run_date: date, rules: RulesConfig) -> CleanData:
    for name in DATASETS:
        if name not in raw:
            raise DataValidationError(f"Missing dataset: {name}")
        missing = [c for c in REQUIRED_COLUMNS[name] if c not in [x.strip() for x in raw[name].columns]]
        if missing:
            raise DataValidationError(f"{name} is missing columns: {', '.join(missing)}")

    tables = {name: _strip(raw[name]) for name in DATASETS}
    issues: dict[str, set[str]] = {}
    warnings: list[str] = []

    def add(sku, code):
        issues.setdefault(sku, set()).add(code)

    # --- SKU master -------------------------------------------------------
    master = tables["sku_master"]
    for sku in master.loc[master.duplicated("SKU_ID", keep=False), "SKU_ID"].unique():
        add(sku, "DUPLICATE_ROWS")
    master = master.drop_duplicates("SKU_ID", keep="first").copy()
    master["Standard_Reorder_Qty"] = pd.to_numeric(master["Standard_Reorder_Qty"], errors="coerce")
    for sku in master.loc[master["Standard_Reorder_Qty"].isna() | (master["Standard_Reorder_Qty"] < 0), "SKU_ID"]:
        add(sku, "INVALID_VALUE")

    # --- Distributors -----------------------------------------------------
    dist = tables["distributor_master"]
    dup_dist = set(dist.loc[dist.duplicated("Distributor_ID", keep=False), "Distributor_ID"])
    dist = dist.drop_duplicates("Distributor_ID", keep="first")
    known_dist = set(dist["Distributor_ID"])
    for _, row in master.iterrows():
        if row["Distributor_ID"] not in known_dist:
            add(row["SKU_ID"], "UNMAPPED_DISTRIBUTOR")
        elif row["Distributor_ID"] in dup_dist:
            add(row["SKU_ID"], "DUPLICATE_ROWS")
    master_skus = set(master["SKU_ID"])

    # --- Weekly sales -----------------------------------------------------
    sales = tables["weekly_sales"].copy()
    sales["week_start"] = sales["Week"].map(week_start)
    sales["Units_Sold"] = pd.to_numeric(sales["Units_Sold"], errors="coerce")
    bad = sales["week_start"].isna() | sales["Units_Sold"].isna() | (sales["Units_Sold"] < 0)
    for sku in sales.loc[bad, "SKU_ID"].unique():
        add(sku, "INVALID_VALUE")
    sales = sales[~bad]
    dups = sales.duplicated(["SKU_ID", "week_start"], keep=False)
    for sku in sales.loc[dups, "SKU_ID"].unique():
        add(sku, "DUPLICATE_ROWS")
    sales = sales.drop_duplicates(["SKU_ID", "week_start"], keep="first")

    # Anchor: last complete week, or the latest week in the data if the export is behind.
    last_complete = last_complete_week_start(run_date)
    available = sales.loc[sales["week_start"] <= last_complete, "week_start"]
    anchor = available.max() if len(available) else last_complete
    if anchor < last_complete:
        warnings.append(
            f"Sales data ends at {week_label(anchor)}, but the last complete week is "
            f"{week_label(last_complete)}. The newest SAP export may not have arrived."
        )
    sales = sales[sales["week_start"] <= anchor]

    # Missing weeks: gaps after a SKU's first sale, inside the lookback window.
    look = set(window(anchor, rules.windows.long_weeks))
    for sku, grp in sales.groupby("SKU_ID"):
        first = grp["week_start"].min()
        expected = {w for w in look if w >= first}
        if expected - set(grp["week_start"]):
            add(sku, "MISSING_WEEKS")

    # --- Inventory --------------------------------------------------------
    inv = tables["current_inventory"].copy()
    inv["Units_On_Hand"] = pd.to_numeric(inv["Units_On_Hand"], errors="coerce")
    inv["As_Of_Date"] = pd.to_datetime(inv["As_Of_Date"], errors="coerce").dt.date
    bad = inv["Units_On_Hand"].isna() | inv["As_Of_Date"].isna()
    for sku in inv.loc[bad, "SKU_ID"].unique():
        add(sku, "INVALID_VALUE")
    inv = inv[~bad]
    for sku in inv.loc[inv.duplicated(["SKU_ID", "Location"], keep=False), "SKU_ID"].unique():
        add(sku, "DUPLICATE_ROWS")
    inv = inv.drop_duplicates(["SKU_ID", "Location"], keep="first")
    # One row per SKU: stock summed across locations; oldest date decides staleness.
    inv = inv.groupby("SKU_ID", as_index=False).agg(Units_On_Hand=("Units_On_Hand", "sum"),
                                                    As_Of_Date=("As_Of_Date", "min"))
    for sku in inv.loc[inv["Units_On_Hand"] < 0, "SKU_ID"]:
        add(sku, "NEGATIVE_STOCK")
    max_age = rules.exceptions.stale_inventory_days
    for _, row in inv.iterrows():
        if (run_date - row["As_Of_Date"]).days > max_age:
            add(row["SKU_ID"], "STALE_INVENTORY")
    for sku in master_skus - set(inv["SKU_ID"]):
        add(sku, "MISSING_INVENTORY")

    # --- Orphans ----------------------------------------------------------
    for sku in (set(sales["SKU_ID"]) | set(inv["SKU_ID"])) - master_skus:
        add(sku, "ORPHAN_SKU")

    # --- Calendar ---------------------------------------------------------
    cal = tables["calendar"].copy()
    cal["Is_Festival_Month"] = _to_bool(cal["Is_Festival_Month"])

    return CleanData(sales=sales, inventory=inv, master=master, distributors=dist, calendar=cal,
                     run_date=run_date, anchor=anchor, issues=issues, warnings=warnings)
