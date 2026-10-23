"""Builds the per-SKU feature table: averages, festival-adjusted demand and cover."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from src.engine.weeks import window
from src.ingest.validate import CleanData
from src.rules_config import RulesConfig


def is_festival_run(calendar: pd.DataFrame, run_date: date, rules: RulesConfig) -> bool:
    """True if the run date's month is a festival month in calendar.csv."""
    ym = f"{run_date.year}-{run_date.month:02d}"
    match = calendar.loc[calendar["Year_Month"] == ym, "Is_Festival_Month"]
    if len(match):
        return bool(match.iloc[0])
    return run_date.month in rules.festival.fallback_months


def demand_multiplier(festival: bool, rules: RulesConfig) -> float:
    """Rule 4: festival uplift applied to weekly demand before every cover check."""
    return rules.festival.uplift if (festival and rules.festival.enabled) else 1.0


def build_features(clean: CleanData, rules: RulesConfig) -> pd.DataFrame:
    long_w = window(clean.anchor, rules.windows.long_weeks)
    short_w = long_w[-rules.windows.short_weeks:]
    zero_w = window(clean.anchor, rules.exceptions.zero_sales_lookback_weeks)
    all_w = sorted(set(long_w) | set(zero_w))

    if len(clean.sales):
        pivot = clean.sales.pivot_table(index="SKU_ID", columns="week_start", values="Units_Sold", aggfunc="sum")
    else:
        pivot = pd.DataFrame()
    pivot = pivot.reindex(columns=all_w)
    history = clean.sales.groupby("SKU_ID")["week_start"].nunique()

    master = clean.master.set_index("SKU_ID")
    inv = clean.inventory.set_index("SKU_ID")
    dist = clean.distributors.set_index("Distributor_ID")
    skus = sorted(set(master.index) | set(pivot.index) | set(inv.index))

    df = pd.DataFrame(index=pd.Index(skus, name="sku_id"))
    df["sku_name"] = master["SKU_Name"].reindex(skus)
    df["category"] = master["Category"].reindex(skus)
    df["distributor_id"] = master["Distributor_ID"].reindex(skus)
    df["distributor_name"] = df["distributor_id"].map(dist["Distributor_Name"])
    df["distributor_status"] = df["distributor_id"].map(dist["Status"])
    df["perishable"] = master["Perishability"].reindex(skus).isin(rules.cover.perishable_values)
    df["standard_qty"] = master["Standard_Reorder_Qty"].reindex(skus)
    df["on_hand"] = inv["Units_On_Hand"].reindex(skus)
    df["inventory_as_of"] = inv["As_Of_Date"].reindex(skus)
    df["history_weeks"] = history.reindex(skus).fillna(0).astype(int)

    p = pivot.reindex(skus).astype(float)
    df["avg12"] = p[long_w].mean(axis=1, skipna=True)
    df["avg4"] = p[short_w].mean(axis=1, skipna=True)
    df["has_zero_week"] = (p[zero_w] == 0).any(axis=1)

    festival = is_festival_run(clean.calendar, clean.run_date, rules)
    baseline = df["avg4"] if rules.windows.demand_baseline == "short" else df["avg12"]
    df["weekly_demand"] = baseline * demand_multiplier(festival, rules)
    on_hand = df["on_hand"].astype(float)
    demand = df["weekly_demand"].astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        cover = np.where(demand > 0, on_hand / demand, np.inf)
    df["cover_weeks"] = cover
    df.loc[on_hand.isna() | df["avg12"].isna(), "cover_weeks"] = np.nan

    general, perish = rules.cover.general, rules.cover.perishable
    df["min_cover"] = np.where(df["perishable"], perish.min_weeks, general.min_weeks)
    df["max_cover"] = np.where(df["perishable"], perish.max_weeks, general.max_weeks)
    df.attrs["festival"] = festival
    return df
