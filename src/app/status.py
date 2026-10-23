"""Is this plan safe to act on? Built only from what the run recorded (runs.data_info,
warnings, recommendations) and today's date; nothing is assumed or invented."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from src.rules_config import RulesConfig

IST = timezone(timedelta(hours=5, minutes=30))
DATA_EXCEPTIONS = {"MISSING_INVENTORY", "STALE_INVENTORY", "NEGATIVE_STOCK", "MISSING_WEEKS", "ORPHAN_SKU",
                   "DUPLICATE_ROWS", "INVALID_VALUE"}


def _ist(ts: str | datetime | None) -> datetime | None:
    if not ts:
        return None
    dt = datetime.fromisoformat(ts) if isinstance(ts, str) else ts
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(IST)


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%a %d %b, %I:%M %p").replace(" 0", " ") if dt else "not recorded"


def plan_status(run: dict, recs: list[dict], rules: RulesConfig, now: datetime,
                last_scheduled: datetime) -> dict:
    """level: "ok" (safe to act on), "caution" (check the warnings) or "unsafe"."""
    info = run.get("data_info") or {}
    today = now.astimezone(IST).date()
    warnings = list(run.get("warnings") or [])
    level = "ok"

    def raise_to(new):
        nonlocal level
        order = ["ok", "caution", "unsafe"]
        level = max(level, new, key=order.index)

    items = [("Plan", f"#{run['id']}, made {_fmt(_ist(run.get('started_at')))} for {run['run_date']:%a %d %b}")]
    if not info:
        items.append(("Data details", "Not recorded for this plan (made before the status panel existed)"))
        raise_to("caution")
    else:
        items.append(("Source data read", f"{_fmt(_ist(info.get('fetched_at')))} · {info.get('source', '')}"))
        first, last = info.get("sales_first_week"), info.get("sales_last_week")
        items.append(("Sales data covers", f"{first} to {last}" if first else "no sales data"))
        inv_min = info.get("inventory_as_of_min")
        if inv_min:
            age = (today - date.fromisoformat(inv_min)).days
            stock_text = f"as of {date.fromisoformat(inv_min):%d %b} ({age} days old)"
            if info.get("inventory_as_of_max") and info["inventory_as_of_max"] != inv_min:
                stock_text = (f"{date.fromisoformat(inv_min):%d %b} to "
                              f"{date.fromisoformat(info['inventory_as_of_max']):%d %b} (oldest {age} days old)")
            items.append(("Stock figures", stock_text))
            if age > rules.exceptions.stale_inventory_days:
                warnings.append(f"Stock figures are {age} days old, more than the {rules.exceptions.stale_inventory_days}"
                                "-day limit. Do not act on this plan until a new stock export arrives.")
                raise_to("unsafe")
        else:
            items.append(("Stock figures", "missing"))
            raise_to("unsafe")
        zoho = info.get("zoho", "off")
        po_text = {"read": f"{info.get('open_po_lines', 0)} open order lines read from Zoho at run time, "
                           f"{info.get('open_po_lines_counted', 0)} counted in weeks of stock",
                   "unavailable": "Zoho could not be reached; stock on order is not counted",
                   "not configured": "Zoho is switched on but has no login; stock on order is not counted",
                   "off": "Not connected; stock on order is not counted"}.get(zoho, zoho)
        items.append(("Purchase orders", po_text))
        if zoho in ("unavailable", "not configured"):
            raise_to("caution")
    review = [r for r in recs if r["status"] == "EXCEPTION"]
    data_review = [r for r in review if DATA_EXCEPTIONS & set(r["exception_codes"])]
    processed = len(recs)
    in_master = info.get("skus_in_master")
    sku_text = f"{processed} processed" + (f" ({in_master} in the SKU master)" if in_master is not None else "")
    sku_text += f" · {len(review)} sent for your review"
    items.append(("SKUs", sku_text))
    if data_review:
        warnings.append(f"{len(data_review)} SKUs have missing or unreliable data and are waiting for your review.")
        raise_to("caution" if len(data_review) <= processed / 4 else "unsafe")
    if any("may not have arrived" in w for w in run.get("warnings") or []):
        raise_to("unsafe")
    elif run.get("warnings"):
        raise_to("caution")
    started = _ist(run.get("started_at"))
    # Vercel starts the scheduled job within the hour, so allow an hour before warning.
    if started and started < last_scheduled and now > last_scheduled + timedelta(hours=1):
        warnings.append(f"A newer plan was due on {_fmt(last_scheduled)} but has not been made. "
                        "This plan may be out of date.")
        raise_to("caution")
    label = {"ok": "Safe to act on", "caution": "Check the warnings before acting",
             "unsafe": "Not reliable: inputs are missing or out of date"}[level]
    return {"level": level, "label": label, "items": items, "warnings": warnings,
            "demo": "demo data" in (info.get("source") or "")}
