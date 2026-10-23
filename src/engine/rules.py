"""Raghav's rules 1-3 and 5 (rule 4, the festival uplift, is applied to demand in
features.py). Produces the list of flags per SKU."""
from __future__ import annotations

import pandas as pd

from src.rules_config import RulesConfig

HIGH_DEMAND, STOCKOUT_RISK, OVERSTOCK_RISK = "HIGH_DEMAND", "STOCKOUT_RISK", "OVERSTOCK_RISK"


def compute_flags(features: pd.DataFrame, rules: RulesConfig) -> pd.Series:
    """Rule 1: avg4 > ratio x avg12. The uplift multiplies both sides, so it cancels
    out and is not applied here.
    Rules 2/3: cover below the min / above the max of the SKU's cover window.
    Rule 5: perishable SKUs use the perishable window (chosen in features.py)."""
    # Compare at 6 decimals so a value exactly on a threshold is not flagged by float noise
    # (e.g. 1.2 x 83.333... must equal 100, not 99.99999999).
    exact = lambda s: s.astype(float).round(6)
    high = exact(features["avg4"]) > exact(rules.high_demand.ratio * features["avg12"])
    stockout = exact(features["cover_weeks"]) < features["min_cover"]
    overstock = exact(features["cover_weeks"]) > features["max_cover"]

    def row_flags(sku):
        flags = []
        if high[sku]:
            flags.append(HIGH_DEMAND)
        if stockout[sku]:
            flags.append(STOCKOUT_RISK)
        if overstock[sku]:
            flags.append(OVERSTOCK_RISK)
        return flags

    return pd.Series([row_flags(s) for s in features.index], index=features.index, dtype=object)
