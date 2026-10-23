"""Turns flags into a status, a plain-English action and an order quantity."""
from __future__ import annotations

import math

from src.engine.rules import HIGH_DEMAND, OVERSTOCK_RISK, STOCKOUT_RISK
from src.rules_config import RulesConfig

ACTIONS = {
    STOCKOUT_RISK: "Reorder now",
    OVERSTOCK_RISK: "Pause next order",
    HIGH_DEMAND: "Reorder (demand rising)",
    "OK": "No action",
    "EXCEPTION": "Needs your review",
}


def size(flags: list[str], exception_codes: list[str], standard_qty: float,
         rules: RulesConfig) -> tuple[str, int | None]:
    """Return (status, recommended_qty). Exceptions never get a quantity.
    STOCKOUT_RISK + HIGH_DEMAND -> multiplier x standard; STOCKOUT_RISK alone ->
    standard; OVERSTOCK_RISK -> 0 (pause); HIGH_DEMAND alone -> multiplier x standard."""
    if exception_codes:
        return "EXCEPTION", None
    multiplier = rules.high_demand.reorder_multiplier if HIGH_DEMAND in flags else 1.0
    if STOCKOUT_RISK in flags:
        return STOCKOUT_RISK, math.ceil(round(standard_qty * multiplier, 6))
    if OVERSTOCK_RISK in flags:
        return OVERSTOCK_RISK, 0
    if HIGH_DEMAND in flags:
        return HIGH_DEMAND, math.ceil(round(standard_qty * multiplier, 6))
    return "OK", None
