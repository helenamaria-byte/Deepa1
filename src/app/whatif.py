"""What if the rules changed? Re-applies the engine's own rule and sizing functions
to the current plan's stored numbers with different thresholds. Nothing is saved.

Held as in the plan: the sales averages, stock on hand, which purchase orders were
counted, and data-quality exception codes. Recomputed: weekly demand (uplift), weeks
of stock, the flags, conflicting signals, the status and the suggested quantity."""
from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd

from src.engine.rules import HIGH_DEMAND, OVERSTOCK_RISK, compute_flags
from src.engine.sizing import size
from src.rules_config import RulesConfig

# Form field -> (rules path, kind). Percent fields are entered as whole percentages.
PARAMS = {
    "uplift_pct": (("festival", "uplift"), "pct_over_1"),
    "hd_pct": (("high_demand", "ratio"), "pct_over_1"),
    "gen_min": (("cover", "general", "min_weeks"), "num"),
    "gen_max": (("cover", "general", "max_weeks"), "num"),
    "per_min": (("cover", "perishable", "min_weeks"), "num"),
    "per_max": (("cover", "perishable", "max_weeks"), "num"),
}
STATUSES = ["STOCKOUT_RISK", "HIGH_DEMAND", "OVERSTOCK_RISK", "EXCEPTION", "OK"]


def current_params(rules: RulesConfig) -> dict:
    out = {}
    for name, (path, kind) in PARAMS.items():
        v = rules.model_dump()
        for p in path:
            v = v[p]
        out[name] = round((v - 1) * 100, 2) if kind == "pct_over_1" else v
    return out


def rules_with(rules: RulesConfig, params: dict) -> RulesConfig:
    data = rules.model_dump()
    for name, value in params.items():
        path, kind = PARAMS[name]
        target = data
        for p in path[:-1]:
            target = target[p]
        target[path[-1]] = 1 + value / 100 if kind == "pct_over_1" else value
    return RulesConfig.model_validate(data)  # raises on impossible values (e.g. min >= max)


def evaluate(recs: list[dict], festival: bool, rules: RulesConfig) -> pd.DataFrame:
    """Status and quantity for each SKU under `rules`, using the engine's functions."""
    df = pd.DataFrame(recs).set_index("sku_id")
    uplift = rules.festival.uplift if (festival and rules.festival.enabled) else 1.0
    demand = df["avg12"].astype(float) * uplift
    stock = df["on_hand"].astype(float) + df["on_order"].fillna(0).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        cover = np.where(demand > 0, stock / demand, np.inf)
    feats = pd.DataFrame({
        "avg4": df["avg4"].astype(float), "avg12": df["avg12"].astype(float), "cover_weeks": cover,
        "min_cover": np.where(df["perishable"], rules.cover.perishable.min_weeks, rules.cover.general.min_weeks),
        "max_cover": np.where(df["perishable"], rules.cover.perishable.max_weeks, rules.cover.general.max_weeks),
    }, index=df.index)
    feats.loc[df["on_hand"].isna() | df["avg12"].isna(), "cover_weeks"] = np.nan
    flags = compute_flags(feats, rules)
    out = []
    for sku in df.index:
        codes = [c for c in df.at[sku, "exception_codes"] if c != "CONFLICTING_FLAGS"]
        if HIGH_DEMAND in flags[sku] and OVERSTOCK_RISK in flags[sku]:
            codes.append("CONFLICTING_FLAGS")
        status, qty = size(flags[sku], codes, df.at[sku, "standard_qty"], rules)
        c = feats.at[sku, "cover_weeks"]
        out.append({"sku_id": sku, "status": status, "qty": qty,
                    "cover": round(float(c), 2) if pd.notna(c) and np.isfinite(c) else None})
    result = pd.DataFrame(out).set_index("sku_id")
    # Keep "no quantity" as None (pandas would turn it into NaN, which never equals itself).
    result["qty"] = pd.Series({o["sku_id"]: o["qty"] for o in out}, dtype=object)
    result["cover"] = pd.Series({o["sku_id"]: o["cover"] for o in out}, dtype=object)
    return result


def what_if(recs: list[dict], festival: bool, base: RulesConfig, params: dict) -> dict:
    new_rules = rules_with(base, params)
    before, after = evaluate(recs, festival, base), evaluate(recs, festival, new_rules)
    names = {r["sku_id"]: r["sku_name"] for r in recs}
    changed = [{"sku_id": s, "sku_name": names[s], "before": before.at[s, "status"], "after": after.at[s, "status"],
                "qty_before": before.at[s, "qty"], "qty_after": after.at[s, "qty"],
                "cover_before": before.at[s, "cover"], "cover_after": after.at[s, "cover"]}
               for s in before.index
               if (before.at[s, "status"], before.at[s, "qty"]) != (after.at[s, "status"], after.at[s, "qty"])]
    cb, ca = Counter(before["status"]), Counter(after["status"])
    return {"counts": [(s, cb.get(s, 0), ca.get(s, 0)) for s in STATUSES], "changed": changed}
