"""Writes every review decision to docs/judgment_log.md, so the reasoning behind
overrides (Raghav's undocumented logic) is written down over time."""
from __future__ import annotations

from src.settings import ROOT


def judgment_log_markdown(store) -> str:
    lines = ["# Judgment log", "",
             "Every decision made on the review page, newest first. Generated automatically on each run.", ""]
    overrides = store.overrides()
    changed = [o for o in overrides if o["decision"] == "CHANGED"]
    lines += [f"- Decisions recorded: {len(overrides)}", f"- Recommendations changed: {len(changed)}", ""]
    if not overrides:
        lines.append("_No decisions recorded yet._")
    else:
        lines += ["| When (UTC) | Run | SKU | Decision | Recommended | Final | Reason | By |",
                  "|---|---|---|---|---|---|---|---|"]
        for o in overrides:
            reason = o["reason"].replace("|", "/").replace("\n", " ")
            lines.append(f"| {o['created_at']:%Y-%m-%d %H:%M} | {o['run_id']} | {o['sku_id']} | {o['decision']} | "
                         f"{'—' if o['recommended_qty'] is None else o['recommended_qty']} | "
                         f"{'—' if o['final_qty'] is None else o['final_qty']} | {reason} | {o['username']} |")
    return "\n".join(lines) + "\n"


def export_judgment_log(store) -> str:
    text = judgment_log_markdown(store)
    try:
        (ROOT / "docs").mkdir(exist_ok=True)
        (ROOT / "docs" / "judgment_log.md").write_text(text, encoding="utf-8")
    except OSError:
        pass  # read-only filesystem (Vercel): the dashboard serves the same file on demand
    return text
