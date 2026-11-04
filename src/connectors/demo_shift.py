"""Demo mode: the case's sample files with their dates moved forward.

The 5 sample CSVs end at 2026-W42 with stock dated 19 Oct 2026, so after a couple
of weeks every plan is (correctly) flagged as stale. In demo mode the sales weeks
are shifted by whole weeks so the newest week is the last complete week before
`today`, and stock dates move by the same number of days. Quantities, patterns
and the calendar are unchanged. Turned on with DATA_SOURCE_URL=demo:shift and
always labelled as demo data; never use it for real planning."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.connectors.base import Connector
from src.connectors.csv_folder import CsvFolderConnector
from src.engine.weeks import last_complete_week_start, week_label, week_start

IST = timezone(timedelta(hours=5, minutes=30))


class DemoShiftConnector(Connector):
    name = "demo_shift"

    def __init__(self, folder: Path | str, today: date | None = None):
        self.folder = Path(folder)
        self.today = today
        self.shift_weeks = 0

    def fetch(self) -> dict[str, pd.DataFrame]:
        data = CsvFolderConnector(self.folder).fetch()
        today = self.today or datetime.now(IST).date()
        starts = data["weekly_sales"]["Week"].map(week_start).dropna()
        if starts.empty:
            return data
        shift = (last_complete_week_start(today) - starts.max()).days // 7
        self.shift_weeks = max(shift, 0)
        if self.shift_weeks:
            delta = timedelta(weeks=self.shift_weeks)
            sales = data["weekly_sales"].copy()
            sales["Week"] = sales["Week"].map(lambda w: week_label(week_start(w) + delta) if week_start(w) else w)
            inv = data["current_inventory"].copy()
            inv["As_Of_Date"] = (pd.to_datetime(inv["As_Of_Date"], errors="coerce") + delta).dt.strftime("%Y-%m-%d")
            data = {**data, "weekly_sales": sales, "current_inventory": inv}
        return data
