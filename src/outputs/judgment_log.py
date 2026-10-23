"""Writes every review decision to docs/judgment_log.md, so the reasoning behind
overrides (Raghav's undocumented logic) is written down over time."""
from __future__ import annotations

import os
from pathlib import Path

from src.settings import ROOT


def judgment_log_markdown(store) -> str:
    lines = ["# Judgment log", "",
             "Every decision event, newest first. Generated automatically on each run. State: current = the",
             "decision in force for that plan; replaced = superseded by a later decision; undone = reversed.", ""]
    events = store.overrides(include_deleted=True)
    current = [o for o in events if o["state"] == "current"]
    changed = [o for o in current if o["decision"] == "CHANGED"]
    lines += [f"- Decision events: {len(events)} (current {len(current)})",
              f"- Current decisions that changed the suggestion: {len(changed)}", ""]
    if not events:
        lines.append("_No decisions recorded yet._")
    else:
        lines += ["| When (UTC) | Plan | SKU | Decision | Suggested | Before | Final | Reason | By | State |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        dash = lambda v: "—" if v is None else v
        for o in events:
            reason = o["reason"].replace("|", "/").replace("\n", " ")
            state = o["state"] + (f" by {o['deleted_by']}" if o["state"] == "undone" else "")
            lines.append(f"| {o['created_at']:%Y-%m-%d %H:%M} | #{o['run_id']} | {o['sku_id']} | {o['decision']} | "
                         f"{dash(o['recommended_qty'])} | {dash(o.get('previous_qty'))} | {dash(o['final_qty'])} | "
                         f"{reason} | {o['username']} | {state} |")
    return "\n".join(lines) + "\n"


def export_judgment_log(store) -> str:
    text = judgment_log_markdown(store)
    path = Path(os.environ.get("JUDGMENT_LOG_PATH") or ROOT / "docs" / "judgment_log.md")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        pass  # read-only filesystem (Vercel): the dashboard serves the same file on demand
    return text
