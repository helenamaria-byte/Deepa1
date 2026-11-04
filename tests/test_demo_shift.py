"""Demo mode keeps the sample data current by moving its dates, nothing else."""
from datetime import date

import pandas as pd

from src.connectors.csv_folder import CsvFolderConnector
from src.connectors.demo_shift import DemoShiftConnector
from src.connectors.factory import make_connector
from src.engine.pipeline import run_engine
from src.jobs.run_plan import source_label
from src.rules_config import load_default_rules
from src.settings import ROOT
from tests.conftest import RUN_DATE

TODAY = date(2026, 11, 4)  # Wed: last complete week is W44 (26 Oct - 1 Nov)


def test_dates_move_forward_quantities_do_not():
    original = CsvFolderConnector(ROOT / "data").fetch()
    conn = DemoShiftConnector(ROOT / "data", today=TODAY)
    shifted = conn.fetch()
    assert conn.shift_weeks == 2
    assert shifted["weekly_sales"]["Week"].iloc[0] == "2026-W29" and shifted["weekly_sales"]["Week"].iloc[-1] == "2026-W44"
    assert set(shifted["current_inventory"]["As_Of_Date"]) == {"2026-11-02"}
    pd.testing.assert_series_equal(shifted["weekly_sales"]["Units_Sold"], original["weekly_sales"]["Units_Sold"])
    pd.testing.assert_frame_equal(shifted["sku_master"], original["sku_master"])
    pd.testing.assert_frame_equal(shifted["calendar"], original["calendar"])


def test_demo_plan_is_current_and_matches_the_case():
    rules = load_default_rules()
    golden = run_engine(CsvFolderConnector(ROOT / "data").fetch(), RUN_DATE, rules)
    demo = run_engine(DemoShiftConnector(ROOT / "data", today=TODAY).fetch(), TODAY, rules)
    assert demo.warnings == [] and demo.data_week == "2026-W44"
    g = golden.recommendations.set_index("sku_id")
    d = demo.recommendations.set_index("sku_id")
    assert (d["status"] == g["status"]).all() and (d["cover_weeks"] == g["cover_weeks"]).all()
    assert (d["status"] != "EXCEPTION").all()


def test_no_shift_when_data_is_already_current():
    conn = DemoShiftConnector(ROOT / "data", today=RUN_DATE)
    conn.fetch()
    assert conn.shift_weeks == 0


def test_demo_mode_is_selected_by_config_and_labelled():
    conn = make_connector("demo:shift")
    assert conn.name == "demo_shift"
    conn.today = TODAY
    conn.fetch()
    label = source_label(conn)
    assert label.startswith("Demo data") and "2 weeks" in label and "not a live SAP export" in label


def test_overdue_purchase_order_is_labelled():
    raw = CsvFolderConnector(ROOT / "data").fetch()
    raw["open_purchase_orders"] = pd.DataFrame([{"SKU_ID": "SKU001", "PO_Number": "PO-OLD",
                                                 "Units_On_Order": "1000", "Expected_Date": "2026-10-15"}])
    po = run_engine(raw, RUN_DATE, load_default_rules()).recommendations.set_index("sku_id").loc["SKU001", "calc"]
    assert po["incoming"][0]["counted"] is True and "overdue" in po["incoming"][0]["note"]
