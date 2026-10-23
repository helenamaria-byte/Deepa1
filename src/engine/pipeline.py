"""Runs validation, features, rules, exceptions and sizing in one call."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.engine.exceptions import compute_exception_codes
from src.engine.features import build_features
from src.engine.rules import compute_flags
from src.engine.sizing import ACTIONS, size
from src.engine.weeks import week_label, window
from src.ingest.validate import validate
from src.rules_config import RulesConfig

HISTORY_WEEKS = 16  # weeks of sales kept per SKU for the dashboard chart


@dataclass
class EngineResult:
    recommendations: pd.DataFrame
    festival: bool
    data_week: str
    warnings: list[str] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)


def _r(value, decimals):
    return None if value is None or pd.isna(value) else round(float(value), decimals)


def _text(value) -> str:
    return "" if value is None or pd.isna(value) else str(value)


def _finite(v) -> bool:
    return v is not None and pd.notna(v) and np.isfinite(v)


def calculation(f: pd.Series, status: str, qty: int | None, flags: list[str], run_date: date,
                rules: RulesConfig) -> dict:
    """Every number behind one recommendation, for the "Calculation details" view.
    Also checks where stock ends up after the suggested order (a warning only: the
    documented rules decide the quantity)."""
    d = rules.rounding.cover_decimals
    demand, on_hand = f["weekly_demand"], f["on_hand"]
    cover_hand = f["cover_on_hand"]
    runs_out = (run_date + timedelta(days=float(cover_hand) * 7)) if _finite(cover_hand) else None
    std = None if pd.isna(f["standard_qty"]) else float(f["standard_qty"])
    if qty is None:
        formula = "No automatic quantity" if status == "EXCEPTION" else "No order needed"
    elif qty == 0:
        formula = "Pause the next order (0 units)"
    elif "HIGH_DEMAND" in flags:
        formula = f"{rules.high_demand.reorder_multiplier:g} × standard order {std:,.0f} = {qty:,} units"
    else:
        formula = f"Standard order quantity = {qty:,} units"
    after = None
    checks = []
    if qty and _finite(demand) and demand > 0 and pd.notna(on_hand):
        after = round((on_hand + f["on_order"] + qty) / demand, d)
        if after < f["min_cover"]:
            checks.append(f"Even after this order, stock covers {after:g} weeks, still below the "
                          f"{f['min_cover']:g}-week minimum.")
        elif after > f["max_cover"]:
            checks.append(f"This order would take stock to {after:g} weeks, above the "
                          f"{f['max_cover']:g}-week maximum.")
        else:
            checks.append(f"After this order arrives, stock covers {after:g} weeks, inside the "
                          f"{f['min_cover']:g} to {f['max_cover']:g}-week range.")
    if f["on_order_excluded"]:
        checks.append(f"{f['on_order_excluded']:,.0f} units on order are not counted because they "
                      "arrive too late or have no delivery date (see the order list).")
    return {
        "baseline_demand": _r(f["baseline_demand"], 2),
        "baseline_label": (f"average of the last {rules.windows.short_weeks} weeks"
                           if rules.windows.demand_baseline == "short"
                           else f"average of the last {rules.windows.long_weeks} weeks"),
        "multiplier": float(f["demand_multiplier"]),
        "weekly_demand": _r(demand, 2),
        "cover_on_hand": _r(cover_hand, d) if _finite(cover_hand) else None,
        "runs_out_on": runs_out.isoformat() if runs_out else None,
        "runs_out_label": runs_out.strftime("%d %b").lstrip("0") if runs_out else None,
        "incoming": list(f["incoming"]),
        "on_order_counted": _r(f["on_order"], 2),
        "on_order_excluded": _r(f["on_order_excluded"], 2),
        "standard_qty": std,
        "qty_formula": formula,
        "cover_after_order": after,
        "checks": checks,
        "lead_time": "Not in the data: the plan cannot check whether a new order arrives before stock runs out.",
    }


def run_engine(raw: dict[str, pd.DataFrame], run_date: date, rules: RulesConfig) -> EngineResult:
    clean = validate(raw, run_date, rules)
    feats = build_features(clean, rules)
    flags = compute_flags(feats, rules)
    codes = compute_exception_codes(feats, flags, clean, rules)

    # Weekly sales behind each decision, for the dashboard chart (display only, not a rule).
    hist_w = window(clean.anchor, HISTORY_WEEKS)
    hist = (clean.sales[clean.sales["week_start"] >= hist_w[0]]
            .pivot_table(index="SKU_ID", columns="week_start", values="Units_Sold", aggfunc="sum")
            .reindex(columns=hist_w)) if len(clean.sales) else pd.DataFrame(columns=hist_w)

    rows = []
    decimals = rules.rounding.cover_decimals
    for sku, f in feats.iterrows():
        status, qty = size(flags[sku], codes[sku], f["standard_qty"], rules)
        cover = f["cover_weeks"]
        rows.append({
            "sku_id": sku,
            "sku_name": _text(f["sku_name"]) or "(no master record)",
            "category": _text(f["category"]),
            "distributor_id": _text(f["distributor_id"]),
            "distributor_name": _text(f["distributor_name"]),
            "perishable": bool(f["perishable"]),
            "standard_qty": _r(f["standard_qty"], 2),
            "on_hand": _r(f["on_hand"], 2),
            "on_order": _r(f["on_order"], 2),
            "inventory_as_of": f["inventory_as_of"] if pd.notna(f["inventory_as_of"]) else None,
            "history_weeks": int(f["history_weeks"]),
            "avg4": _r(f["avg4"], 2),
            "avg12": _r(f["avg12"], 2),
            "weekly_demand": _r(f["weekly_demand"], 2),
            "cover_weeks": _r(cover, decimals) if pd.notna(cover) and np.isfinite(cover) else None,
            "min_cover": float(f["min_cover"]),
            "max_cover": float(f["max_cover"]),
            "flags": list(flags[sku]),
            "exception_codes": list(codes[sku]),
            "status": status,
            "action": ACTIONS[status],
            "recommended_qty": qty,
            "sales_history": [[week_label(w), None if sku not in hist.index or pd.isna(hist.at[sku, w])
                               else int(hist.at[sku, w])] for w in hist_w],
            "calc": calculation(f, status, qty, flags[sku], clean.run_date, rules),
        })
    recs = pd.DataFrame(rows)
    if len(recs):  # keep quantities as Python ints / None, not floats / NaN
        recs["recommended_qty"] = pd.Series([r["recommended_qty"] for r in rows], dtype=object)
    inv_dates = clean.inventory["As_Of_Date"]
    coverage = {  # what this run actually read, for the plan-status panel
        "sales_first_week": week_label(clean.sales["week_start"].min()) if len(clean.sales) else None,
        "sales_last_week": week_label(clean.anchor),
        "inventory_as_of_min": inv_dates.min().isoformat() if len(inv_dates) else None,
        "inventory_as_of_max": inv_dates.max().isoformat() if len(inv_dates) else None,
        "skus_in_master": int(len(clean.master)),
        "skus_processed": int(len(recs)),
        "open_po_lines": int(len(clean.open_pos)),
        "open_po_lines_counted": int(sum(x["counted"] for r in rows for x in r["calc"]["incoming"])),
    }
    return EngineResult(recommendations=recs, festival=bool(feats.attrs["festival"]),
                        data_week=week_label(clean.anchor), warnings=clean.warnings, coverage=coverage)
