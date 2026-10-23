"""Decides which SKUs need human judgment. Each reason has a code."""
from __future__ import annotations

import pandas as pd

from src.engine.rules import HIGH_DEMAND, OVERSTOCK_RISK
from src.ingest.validate import CleanData
from src.rules_config import RulesConfig


def compute_exception_codes(features: pd.DataFrame, flags: pd.Series, clean: CleanData,
                            rules: RulesConfig) -> pd.Series:
    ex = rules.exceptions
    out = {}
    for sku, row in features.iterrows():
        codes = set(clean.issues.get(sku, set()))
        if row["history_weeks"] < ex.min_history_weeks:
            codes.add("NEW_LAUNCH")
        avg12, avg4 = row["avg12"], row["avg4"]
        if pd.notna(avg12) and avg12 > 0 and pd.notna(avg4) and abs(avg4 / avg12 - 1) > ex.max_deviation:
            codes.add("VOLATILE_DEMAND")
        if bool(row["has_zero_week"]):
            codes.add("ZERO_SALES_WEEK")
        if pd.notna(row["distributor_status"]) and row["distributor_status"] != ex.active_status:
            codes.add("INACTIVE_DISTRIBUTOR")
        if HIGH_DEMAND in flags[sku] and OVERSTOCK_RISK in flags[sku]:
            codes.add("CONFLICTING_FLAGS")
        out[sku] = sorted(codes)
    return pd.Series(out, dtype=object).reindex(features.index)
