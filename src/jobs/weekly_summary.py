"""Friday metrics summary for Deepa.

    python -m src.jobs.weekly_summary
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from src.jobs.run_plan import today_ist
from src.outputs.email import Mailer, render
from src.settings import settings
from src.store.repo import Store


def weekly_metrics(store: Store, end: date) -> dict:
    start = end - timedelta(days=7)
    runs = [r for r in store.list_runs(200) if start < r["run_date"] <= end]
    latest = next((r for r in runs if r["status"] == "SUCCESS"), None) or store.latest_run()
    since = datetime.combine(start, datetime.min.time())
    decisions = [o for o in store.overrides() if o["created_at"] >= since]
    counts = latest["counts"] if latest else {}
    total = counts.get("TOTAL", 0)
    return {
        "runs": len(runs),
        "failed": sum(r["status"] == "FAILED" for r in runs),
        "skus": total,
        "flagged": counts.get("NEED_REVIEW", 0) - counts.get("EXCEPTION", 0),
        "exceptions": counts.get("EXCEPTION", 0),
        "exception_pct": round(100 * counts.get("EXCEPTION", 0) / total) if total else 0,
        "by_status": {k: v for k, v in counts.items() if k not in ("TOTAL", "NEED_REVIEW")},
        "decisions": len(decisions),
        "approved": sum(o["decision"] == "APPROVED" for o in decisions),
        "changed": sum(o["decision"] == "CHANGED" for o in decisions),
        "reasons": [o for o in decisions if o["decision"] == "CHANGED"][:10],
        "start": start, "end": end,
    }


def send_weekly_summary(store: Store | None = None, mailer: Mailer | None = None,
                        end: date | None = None) -> str:
    store = store or Store()
    store.migrate()
    mailer = mailer or Mailer(store)
    end = end or today_ist()
    m = weekly_metrics(store, end)
    html = render("weekly_summary.html", m=m, start=m["start"], end=m["end"])
    return mailer.send("weekly_summary", settings.recipients["deepa"],
                       f"Freshroots planning: weekly summary to {end}", html)


if __name__ == "__main__":
    print(send_weekly_summary())
