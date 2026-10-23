"""The single entry point for a planning run.

    python -m src.jobs.run_plan                 # run for today (India time)
    python -m src.jobs.run_plan --date 2026-10-22

Steps: fetch data (3 retries) -> validate -> rules -> explanations -> store
(3 retries) -> workbook -> alert email (3 retries) -> judgment log.
Any failure marks the run FAILED with the stack trace and sends a failure notice.
"""
from __future__ import annotations

import argparse
import logging
import time
import traceback
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from src.ai.explain import Explainer, build_facts
from src.connectors.base import Connector
from src.connectors.factory import make_connector
from src.engine.pipeline import run_engine
from src.outputs.email import Mailer, render
from src.outputs.judgment_log import export_judgment_log
from src.outputs.workbook import build_workbook
from src.retry import with_retry
from src.settings import settings
from src.store.models import utcnow
from src.store.repo import Store

log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
STATUS_ORDER = ["STOCKOUT_RISK", "HIGH_DEMAND", "OVERSTOCK_RISK", "EXCEPTION", "OK"]


def today_ist() -> date:
    return datetime.now(IST).date()


def _scrub(text: str) -> str:
    for secret in (settings.anthropic_api_key, settings.resend_api_key, settings.smtp_password,
                   settings.data_source_credentials):
        if secret:
            text = text.replace(secret, "***")
    db = settings.database_url
    if "@" in db:
        text = text.replace(db.split("@")[0], "***")
    return text


def run_plan(run_date: date | None = None, trigger: str = "manual", store: Store | None = None,
             connector: Connector | None = None, mailer: Mailer | None = None,
             explainer: Explainer | None = None, sleep=time.sleep, send_email: bool = True) -> dict:
    run_date = run_date or today_ist()
    store = store or Store()
    with_retry(store.migrate, "database:migrate", sleep=sleep)
    connector = connector or make_connector()
    mailer = mailer or Mailer(store, sleep=sleep)
    run_id = with_retry(lambda: store.start_run(run_date, trigger), "database:start_run", sleep=sleep)
    attempts = {"n": 0}

    def count_attempt(n):
        attempts["n"] = n

    try:
        rules_version, rules = store.current_rules()
        raw = with_retry(connector.fetch, f"connector:{connector.name}", sleep=sleep, on_attempt=count_attempt)
        result = run_engine(raw, run_date, rules)

        explainer = explainer or Explainer(settings.anthropic_api_key, settings.anthropic_model,
                                           cache=store.ai_cache())
        rows = result.recommendations.to_dict("records")
        for row in rows:
            row["explanation"], row["explanation_source"] = explainer.explain(
                build_facts(row, result.festival, rules))

        with_retry(lambda: store.save_recommendations(run_id, rows), "database:save", sleep=sleep)
        by_status = Counter(r["status"] for r in rows)
        counts = {s: by_status.get(s, 0) for s in STATUS_ORDER}
        counts["TOTAL"] = len(rows)
        counts["NEED_REVIEW"] = len(rows) - by_status.get("OK", 0)
        with_retry(lambda: store.update_run(
            run_id, status="SUCCESS", finished_at=utcnow(), rules_version=rules_version,
            festival=result.festival, data_week=result.data_week, connector=connector.name,
            attempts=attempts["n"], warnings=result.warnings, counts=counts), "database:finish", sleep=sleep)

        run = store.get_run(run_id)
        if send_email:
            send_alert(store, mailer, run, rules, rules_version)
        export_judgment_log(store)
        log.info("Run %s finished: %s", run_id, counts)
        return run
    except Exception as exc:  # noqa: BLE001 - every failure must be recorded
        error = _scrub(traceback.format_exc())
        log.error("Run %s failed: %s", run_id, type(exc).__name__)
        try:
            with_retry(lambda: store.update_run(run_id, status="FAILED", finished_at=utcnow(),
                                                attempts=attempts["n"], error=error),
                       "database:fail", sleep=sleep)
        except Exception:  # noqa: BLE001
            log.exception("Could not record failure in the database")
        if send_email:
            send_failure(mailer, run_id, run_date, attempts["n"], error)
        return store.get_run(run_id) or {"id": run_id, "status": "FAILED", "error": error}


def send_alert(store: Store, mailer: Mailer, run: dict, rules, rules_version: int) -> str:
    recs = store.recommendations(run["id"])
    need = [r for r in recs if r["status"] != "OK"]
    need.sort(key=lambda r: STATUS_ORDER.index(r["status"]))
    workbook = build_workbook(run, recs, store.overrides(run["id"]), rules, rules_version)
    html = render("alert.html", run=run, counts=run["counts"], need_review=len(need), rows=need,
                  uplift_pct=round((rules.festival.uplift - 1) * 100))
    to = settings.recipients["raghav"] + settings.recipients["procurement"]
    return mailer.send("alert", to, f"{len(need)} SKUs need review: plan for {run['run_date']}", html,
                       attachments=[(f"freshroots_plan_{run['run_date']}_run{run['id']}.xlsx", workbook)],
                       run_id=run["id"])


def send_failure(mailer: Mailer, run_id: int, run_date: date, attempts: int, error: str) -> str:
    html = render("failure.html", run_id=run_id, run_date=run_date, attempts=max(attempts, 1),
                  error=error.strip().splitlines()[-1] if error.strip() else "Unknown error")
    to = settings.recipients["raghav"] + settings.recipients["deepa"]
    return mailer.send("failure", to, f"Planning run FAILED for {run_date}", html, run_id=run_id)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Run the Freshroots demand plan")
    parser.add_argument("--date", help="Run date YYYY-MM-DD (default: today, India time)")
    parser.add_argument("--trigger", default="manual")
    parser.add_argument("--no-email", action="store_true")
    args = parser.parse_args()
    run_date = date.fromisoformat(args.date) if args.date else None
    run = run_plan(run_date, trigger=args.trigger, send_email=not args.no_email)
    print(f"Run {run['id']}: {run['status']} {run.get('counts', '')}")
    raise SystemExit(0 if run["status"] == "SUCCESS" else 1)


if __name__ == "__main__":
    main()
