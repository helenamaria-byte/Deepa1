"""Excel workbook per run: Recommendations, Exceptions, Override log, Rules, Run info.

The Override log sheet doubles as an import form: rows with a Decision and Reason
but no Timestamp are new decisions, read back by read_override_sheet()."""
from __future__ import annotations

import io

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from src.ingest.validate import REASONS, reason_text
from src.rules_config import RulesConfig

SHEETS = ["Recommendations", "Exceptions", "Override log", "Rules", "Run info"]
OVERRIDE_HEADERS = ["SKU_ID", "SKU_Name", "Recommended_Qty", "Decision (Approve/Change)", "Your_Qty",
                    "Reason", "User", "Timestamp"]
HEADER_FILL = PatternFill("solid", fgColor="1F5130")
HEADER_FONT = Font(bold=True, color="FFFFFF")


def _sheet(wb: Workbook, title: str, headers: list[str], rows: list[list]) -> None:
    ws = wb.create_sheet(title)
    ws.append(headers)
    for cell in ws[1]:
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for row in rows:
        ws.append(row)
    for i, h in enumerate(headers, start=1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows if r[i - 1] is not None] or [10])
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 10), 70)
    ws.freeze_panes = "A2"


def build_workbook(run: dict, recs: list[dict], overrides: list[dict], rules: RulesConfig,
                   rules_version: int) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    actionable = [r for r in recs if r["status"] != "EXCEPTION"]
    exceptions = [r for r in recs if r["status"] == "EXCEPTION"]

    _sheet(wb, "Recommendations",
           ["SKU_ID", "SKU_Name", "Category", "Distributor", "Status", "Action", "Recommended_Qty",
            "Cover_Weeks", "Min_Cover", "Max_Cover", "Units_On_Hand", "Units_On_Order", "Avg_4wk", "Avg_12wk",
            "Weekly_Demand_Used", "Perishable", "Reason"],
           [[r["sku_id"], r["sku_name"], r["category"], r["distributor_name"], r["status"], r["action"],
             r["recommended_qty"], r["cover_weeks"], r["min_cover"], r["max_cover"], r["on_hand"],
             r.get("on_order") or 0, r["avg4"], r["avg12"], r["weekly_demand"],
             "Yes" if r["perishable"] else "No", r["explanation"]]
            for r in actionable])

    _sheet(wb, "Exceptions",
           ["SKU_ID", "SKU_Name", "Distributor", "Reason_Codes", "What_It_Means", "Cover_Weeks",
            "Units_On_Hand", "Avg_4wk", "Avg_12wk"],
           [[r["sku_id"], r["sku_name"], r["distributor_name"], ", ".join(r["exception_codes"]),
             "; ".join(reason_text(c, rules) for c in r["exception_codes"]), r["cover_weeks"], r["on_hand"],
             r["avg4"], r["avg12"]] for r in exceptions])

    decided = {o["sku_id"] for o in overrides}
    rows = [[o["sku_id"], next((r["sku_name"] for r in recs if r["sku_id"] == o["sku_id"]), ""),
             o["recommended_qty"], "Approve" if o["decision"] == "APPROVED" else "Change", o["final_qty"],
             o["reason"], o["username"], o["created_at"].strftime("%Y-%m-%d %H:%M UTC")] for o in overrides]
    # Blank rows for SKUs still awaiting a decision, ready to fill in and upload.
    rows += [[r["sku_id"], r["sku_name"], r["recommended_qty"], None, None, None, None, None]
             for r in recs if r["status"] != "OK" and r["sku_id"] not in decided]
    _sheet(wb, "Override log", OVERRIDE_HEADERS, rows)

    _sheet(wb, "Rules", ["Rule", "Setting"], [
        ["Rules version", rules_version],
        ["1. High demand", f"avg of last {rules.windows.short_weeks} weeks > {rules.high_demand.ratio} x "
                           f"avg of last {rules.windows.long_weeks} weeks -> reorder "
                           f"{rules.high_demand.reorder_multiplier} x standard quantity "
                           "(festival uplift cancels out in this ratio)"],
        ["2. Stockout risk", f"cover < {rules.cover.general.min_weeks} weeks -> reorder standard quantity now"],
        ["3. Overstock risk", f"cover > {rules.cover.general.max_weeks} weeks -> pause next order"],
        ["4. Festival uplift", f"weekly demand x {rules.festival.uplift} in festival months "
                               f"({'on' if rules.festival.enabled else 'off'})"],
        ["5. Perishable SKUs", f"cover window {rules.cover.perishable.min_weeks}-"
                               f"{rules.cover.perishable.max_weeks} weeks replaces rules 2 and 3"],
        ["Both stockout and high demand", "reorder the high-demand quantity"],
        ["Overstock and high demand", "conflict -> exception queue"],
        ["Cover", "(units on hand" + (" + units on open purchase orders" if rules.open_orders.include_in_cover
                                       else "") + ") / weekly demand (avg of last 12 weeks, with festival uplift)"],
        *[[f"Exception: {code}", reason_text(code, rules)] for code in sorted(REASONS)],
    ])

    counts = run.get("counts", {})
    _sheet(wb, "Run info", ["Field", "Value"], [
        ["Run ID", run["id"]], ["Run date", str(run["run_date"])], ["Run number that day", run["seq"]],
        ["Status", run["status"]], ["Trigger", run["trigger"]], ["Data up to week", run["data_week"]],
        ["Festival month", "Yes" if run["festival"] else "No"], ["Rules version", run["rules_version"]],
        ["Data source", run["connector"]], *[[f"Count: {k}", v] for k, v in counts.items()],
        *[["Warning", w] for w in run.get("warnings", [])],
    ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def read_override_sheet(raw: bytes) -> list[dict]:
    """New decisions from an uploaded workbook's Override log sheet."""
    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    if "Override log" not in wb.sheetnames:
        raise ValueError("The file has no 'Override log' sheet.")
    rows = list(wb["Override log"].iter_rows(values_only=True))
    if not rows or [str(h) for h in rows[0][:len(OVERRIDE_HEADERS)]] != OVERRIDE_HEADERS:
        raise ValueError("The 'Override log' sheet does not have the expected columns.")
    out = []
    for row in rows[1:]:
        sku, _, _, decision, qty, reason, _, ts = (list(row) + [None] * 8)[:8]
        if not sku or not decision or ts:
            continue
        decision = str(decision).strip().lower()
        out.append({
            "sku_id": str(sku).strip(),
            "decision": "APPROVED" if decision.startswith("approve") else "CHANGED",
            "final_qty": int(qty) if qty not in (None, "") else None,
            "reason": str(reason or "").strip(),
        })
    return out
