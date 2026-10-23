"""Runs validation, features, rules, exceptions and sizing in one call."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from src.engine.exceptions import compute_exception_codes
from src.engine.features import build_features
from src.engine.rules import compute_flags
from src.engine.sizing import ACTIONS, size
from src.engine.weeks import week_label
from src.ingest.validate import validate
from src.rules_config import RulesConfig


@dataclass
class EngineResult:
    recommendations: pd.DataFrame
    festival: bool
    data_week: str
    warnings: list[str] = field(default_factory=list)


def _r(value, decimals):
    return None if value is None or pd.isna(value) else round(float(value), decimals)


def _text(value) -> str:
    return "" if value is None or pd.isna(value) else str(value)


def run_engine(raw: dict[str, pd.DataFrame], run_date: date, rules: RulesConfig) -> EngineResult:
    clean = validate(raw, run_date, rules)
    feats = build_features(clean, rules)
    flags = compute_flags(feats, rules)
    codes = compute_exception_codes(feats, flags, clean, rules)

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
        })
    recs = pd.DataFrame(rows)
    if len(recs):  # keep quantities as Python ints / None, not floats / NaN
        recs["recommended_qty"] = pd.Series([r["recommended_qty"] for r in rows], dtype=object)
    return EngineResult(recommendations=recs, festival=bool(feats.attrs["festival"]),
                        data_week=week_label(clean.anchor), warnings=clean.warnings)
