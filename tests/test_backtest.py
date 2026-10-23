"""Backtest replay and the scheduler/secret checks."""
import json
import subprocess
import sys

import yaml

from src.jobs.backtest import REPLAY_WEEKS, build_report, replay, run_date_for
from src.rules_config import load_default_rules
from src.settings import ROOT


def test_replay_uses_only_sales_available_at_each_point(raw):
    results = replay(raw, load_default_rules())
    assert list(results) == REPLAY_WEEKS
    sku1 = [results[w].loc["SKU001"] for w in REPLAY_WEEKS]
    assert [r["avg4"] for r in sku1] == [207.5, 225.0, 242.75, 261.0]
    assert ["HIGH_DEMAND" in r["flags"] for r in sku1] == [False, False, False, True]
    assert all(r["status"] == "STOCKOUT_RISK" for r in sku1)


def test_report_has_required_sections():
    report = build_report()
    for heading in ("Flags week by week", "SKU001 demand jump", "Sensitivity", "cannot validate"):
        assert heading in report


def test_w42_replay_matches_golden_run_date():
    assert str(run_date_for("2026-W42")) == "2026-10-22"


def test_vercel_cron_is_0600_ist_mon_thu():
    cfg = json.loads((ROOT / "vercel.json").read_text())
    crons = {c["path"]: c["schedule"] for c in cfg["crons"]}
    assert crons["/api/cron/run"] == "30 0 * * 1,4"   # 00:30 UTC = 06:00 IST
    assert crons["/api/cron/weekly"] == "30 0 * * 5"  # Friday summary


def test_github_workflows_are_valid_yaml():
    for name in ("ci.yml", "run_plan.yml"):
        wf = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text())
        assert "jobs" in wf
    run_plan = yaml.safe_load((ROOT / ".github" / "workflows" / "run_plan.yml").read_text())
    assert run_plan[True]["schedule"][0]["cron"] == "30 0 * * 1,4"  # YAML reads "on" as True


def test_secret_scan_passes():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "secret_scan.py")], cwd=ROOT,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout
