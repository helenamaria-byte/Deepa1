"""ISO-week helpers. Weeks are labelled like 2026-W42 (Monday to Sunday)."""
from __future__ import annotations

import re
from datetime import date, timedelta

WEEK_RE = re.compile(r"^(\d{4})-W(\d{1,2})$")


def week_start(label: str) -> date | None:
    """Monday of an ISO week label, or None if the label is invalid."""
    m = WEEK_RE.match(label.strip())
    if not m:
        return None
    try:
        return date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    except ValueError:
        return None


def week_label(monday: date) -> str:
    year, week, _ = monday.isocalendar()
    return f"{year}-W{week:02d}"


def last_complete_week_start(run_date: date) -> date:
    """Monday of the latest week whose Sunday falls before the run date."""
    day_before = run_date - timedelta(days=1)
    sunday = day_before - timedelta(days=(day_before.weekday() + 1) % 7)
    return sunday - timedelta(days=6)


def window(end_monday: date, weeks: int) -> list[date]:
    """The `weeks` Mondays ending at end_monday, oldest first."""
    return [end_monday - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
