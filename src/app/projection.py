"""Week-by-week projected stock for one SKU, from what the plan recorded.

Start: stock on hand on its as-of date. Each week: add open purchase orders due
that week (by their Zoho expected date; overdue ones in week 1), then subtract
that week's demand (the 12-week average, with the festival uplift in festival
months). The minimum and maximum lines are the SKU's cover window times that
week's demand. A projection, not a forecast: it assumes sales continue at the
recent average. New orders are not added: supplier lead time is not in the data."""
from __future__ import annotations

from datetime import date, timedelta

from src.engine.weeks import week_label
from src.rules_config import RulesConfig

HORIZON_WEEKS = 13


def project_stock(rec: dict, run_date: date, rules: RulesConfig, weeks: int = HORIZON_WEEKS) -> dict | None:
    calc = rec.get("calc") or {}
    base = calc.get("baseline_demand")
    if rec.get("on_hand") is None or not base:
        return None
    start = rec.get("inventory_as_of") or run_date
    undated = [p for p in calc.get("incoming", []) if not p.get("expected")]
    by_week: dict[int, list[dict]] = {}
    for p in calc.get("incoming", []):
        if p.get("expected"):
            due = date.fromisoformat(p["expected"])
            by_week.setdefault(max((due - start).days // 7, 0), []).append(p)  # overdue -> week 1
    stock = float(rec["on_hand"])
    rows = []
    for k in range(weeks):
        wk_start = start + timedelta(days=7 * k)
        fest = rules.festival.enabled and wk_start.month in rules.festival.fallback_months
        demand = base * (rules.festival.uplift if fest else 1.0)
        arriving = by_week.get(k, [])
        arrivals = sum(p["units"] for p in arriving)
        stock = stock + arrivals - demand
        runs_out = stock < 0
        stock = max(stock, 0.0)
        rows.append({
            "week": week_label(wk_start - timedelta(days=wk_start.weekday())), "starts": wk_start,
            "demand": demand, "festival": fest, "arrivals": arrivals,
            "arriving": [f"{p['po']} +{p['units']:,.0f}" for p in arriving],
            "stock": stock, "min": rec["min_cover"] * demand, "max": rec["max_cover"] * demand,
            "runs_out": runs_out,
        })
    return {"rows": rows, "notes": _notes(rec, rows, undated)}


def _notes(rec: dict, rows: list[dict], undated: list[dict]) -> list[str]:
    notes = []
    below = next((r for r in rows if r["stock"] < r["min"]), None)
    out = next((r for r in rows if r["runs_out"]), None)
    if out:
        notes.append(f"Runs out in the week of {out['starts']:%d %b} if nothing more arrives.")
    if below and (not out or below is not out):
        notes.append(f"Falls below the {rec['min_cover']:g}-week minimum ({below['min']:,.0f} units) "
                     f"in the week of {below['starts']:%d %b}.")
    if not below:
        notes.append(f"Stays at or above the {rec['min_cover']:g}-week minimum for all {len(rows)} weeks.")
    for r in rows:
        for a in r["arriving"]:
            notes.append(f"{a} units arrive in the week of {r['starts']:%d %b}.")
    above = [r for r in rows if r["stock"] > r["max"]]
    if above:
        notes.append(f"Above the {rec['max_cover']:g}-week maximum for {len(above)} of {len(rows)} weeks.")
    for p in undated:
        notes.append(f"{p['po']} ({p['units']:,.0f} units) has no delivery date, so it is not in the chart.")
    if rec.get("recommended_qty"):
        notes.append(f"The suggested order ({rec['recommended_qty']:,} units) is not in the chart: supplier lead "
                     "time is not in the data, so its arrival week is unknown.")
    return notes
