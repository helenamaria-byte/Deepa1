"""Builds the per-SKU feature table: averages, festival-adjusted demand and cover."""
from __future__ import annotations

from datetime import date, timedelta

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
    multiplier = demand_multiplier(festival, rules)
    df["baseline_demand"] = df["avg4"] if rules.windows.demand_baseline == "short" else df["avg12"]
    df["demand_multiplier"] = multiplier
    df["weekly_demand"] = df["baseline_demand"] * multiplier  # the uplift is applied here, once
    on_hand = df["on_hand"].astype(float)
    demand = df["weekly_demand"].astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        df["cover_on_hand"] = np.where(demand > 0, on_hand / demand, np.inf)

    # Open purchase orders: count each one only if it arrives before stock on hand runs out.
    counted, excluded, incoming = {}, {}, {}
    oo = rules.open_orders
    for sku, grp in (clean.open_pos.groupby("SKU_ID") if len(clean.open_pos) else []):
        if sku not in df.index:
            continue
        cover_hand = df.at[sku, "cover_on_hand"]
        runs_out = (clean.run_date + timedelta(days=float(cover_hand) * 7)
                    if pd.notna(cover_hand) and np.isfinite(cover_hand) else None)
        lines = []
        for po in grp.itertuples():
            units, expected = float(po.Units_On_Order), po.Expected_Date
            if not oo.include_in_cover:
                ok, why = False, "Stock on order is switched off in the rules"
            elif not oo.count_only_if_arrives_before_stockout:
                ok, why = True, "Counted"
            elif expected is None:
                ok, why = False, "No expected delivery date, so it is not counted"
            elif runs_out is not None and expected > runs_out:
                ok, why = False, f"Arrives {expected:%d %b}, after stock on hand runs out on {runs_out:%d %b}"
            else:
                ok, why = True, (f"Arrives {expected:%d %b}, before stock on hand runs out"
                                 + (f" on {runs_out:%d %b}" if runs_out else ""))
            lines.append({"po": str(po.PO_Number), "units": units,
                          "expected": expected.isoformat() if expected else None, "counted": ok, "note": why})
            (counted if ok else excluded)[sku] = (counted if ok else excluded).get(sku, 0.0) + units
        incoming[sku] = lines
    df["on_order"] = pd.Series(counted, dtype=float).reindex(df.index).fillna(0.0)
    df["on_order_excluded"] = pd.Series(excluded, dtype=float).reindex(df.index).fillna(0.0)
    df["incoming"] = pd.Series({s: incoming.get(s, []) for s in df.index}, dtype=object)
    with np.errstate(divide="ignore", invalid="ignore"):
        df["cover_weeks"] = np.where(demand > 0, (on_hand + df["on_order"]) / demand, np.inf)
    df.loc[on_hand.isna() | df["avg12"].isna(), ["cover_weeks", "cover_on_hand"]] = np.nan

    general, perish = rules.cover.general, rules.cover.perishable
    df["min_cover"] = np.where(df["perishable"], perish.min_weeks, general.min_weeks)
    df["max_cover"] = np.where(df["perishable"], perish.max_weeks, general.max_weeks)
    df.attrs["festival"] = festival
    return df
