"""Persistence, overrides, rules versioning, retries and failure flagging."""
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from src.jobs.run_plan import run_plan
from src.retry import RetryError, with_retry
from src.store.models import Override
from src.store.repo import Store
from tests.conftest import RUN_DATE, StaticConnector, no_sleep


@pytest.fixture
def store():
    s = Store()
    s.migrate()
    return s


@pytest.fixture
def done_run(store, raw):
    return run_plan(RUN_DATE, store=store, connector=StaticConnector(raw), sleep=no_sleep)


def test_run_persists_everything(store, done_run):
    assert done_run["status"] == "SUCCESS"
    assert done_run["rules_version"] == 1 and done_run["festival"] is True
    assert done_run["counts"] == {"STOCKOUT_RISK": 5, "HIGH_DEMAND": 0, "OVERSTOCK_RISK": 2, "EXCEPTION": 0,
                                  "OK": 8, "TOTAL": 15, "NEED_REVIEW": 7}
    recs = store.recommendations(done_run["id"])
    assert len(recs) == 15 and all(r["explanation"] for r in recs)
    sku1 = store.recommendation(done_run["id"], "SKU001")
    assert sku1["flags"] == ["HIGH_DEMAND", "STOCKOUT_RISK"] and sku1["recommended_qty"] == 750


def test_rerun_creates_new_version(store, raw, done_run):
    second = run_plan(RUN_DATE, store=store, connector=StaticConnector(raw), sleep=no_sleep)
    assert second["id"] != done_run["id"] and second["seq"] == 2
    assert len(store.recommendations(done_run["id"])) == 15  # first run untouched


def test_override_requires_reason_and_records_user_and_time(store, done_run):
    with pytest.raises(ValueError):
        store.add_override(done_run["id"], "SKU003", "CHANGED", 400, "   ", "raghav")
    oid = store.add_override(done_run["id"], "SKU003", "CHANGED", 400, "Promo next week", "raghav")
    o = store.overrides(done_run["id"])[0]
    assert o["id"] == oid and o["username"] == "raghav" and o["reason"] == "Promo next week"
    assert o["final_qty"] == 400 and o["recommended_qty"] == 300
    assert isinstance(o["created_at"], datetime)


def test_database_rejects_blank_reason_directly(store, done_run):
    with pytest.raises(IntegrityError):
        with store.Session.begin() as s:
            s.add(Override(run_id=done_run["id"], sku_id="SKU001", decision="APPROVED", reason=" ",
                           username="x"))


def test_approve_uses_recommended_qty_and_delete_is_soft(store, done_run):
    oid = store.add_override(done_run["id"], "SKU001", "APPROVED", None, "Agree", "deepa")
    assert store.latest_decisions(done_run["id"])["SKU001"]["final_qty"] == 750
    assert store.delete_override(oid, "deepa")
    assert store.overrides(done_run["id"]) == []
    assert store.overrides(done_run["id"], include_deleted=True)[0]["deleted_by"] == "deepa"


def test_rules_versions(store):
    version, rules = store.current_rules()
    rules.cover.general.min_weeks = 5
    assert store.save_rules(rules, "raghav", "test") == version + 1
    assert store.current_rules()[1].cover.general.min_weeks == 5


def test_explanations_stored(store, done_run):
    rec = store.recommendation(done_run["id"], "SKU006")
    assert rec["explanation_source"] == "template" and "Pause" in rec["explanation"]


# ---- retry and failure flagging (fault injection) ---------------------------
class FlakyConnector(StaticConnector):
    name = "flaky"

    def __init__(self, tables, failures):
        super().__init__(tables)
        self.failures, self.calls = failures, 0

    def fetch(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("SAP export share unreachable")
        return super().fetch()


def test_retry_recovers_after_two_failures(store, raw):
    sleeps = []
    conn = FlakyConnector(raw, failures=2)
    run = run_plan(RUN_DATE, store=store, connector=conn, sleep=sleeps.append)
    assert run["status"] == "SUCCESS" and run["attempts"] == 3 and conn.calls == 3
    assert sleeps == [2.0, 4.0]  # exponential backoff


def test_three_failures_flag_run_and_send_failure_notice(store, raw, isolated_env):
    conn = FlakyConnector(raw, failures=3)
    run = run_plan(RUN_DATE, store=store, connector=conn, sleep=no_sleep)
    assert conn.calls == 3
    assert run["status"] == "FAILED" and run["attempts"] == 3
    assert "ConnectionError" in run["error"] and "Traceback" in run["error"]
    failure = [e for e in store.emails() if e["kind"] == "failure"]
    assert failure and "raghav@example.com" in failure[0]["recipients"]
    assert "deepa@example.com" in failure[0]["recipients"]
    assert list((isolated_env / "var" / "outbox").glob("*-failure.eml"))
    assert store.latest_run() is None  # no successful plan replaced


def test_with_retry_raises_after_three(store):
    calls = []
    with pytest.raises(RetryError) as exc:
        with_retry(lambda: calls.append(1) or 1 / 0, "div", sleep=no_sleep)
    assert len(calls) == 3 and exc.value.attempts == 3


def test_failed_email_falls_back_to_outbox(store, monkeypatch, isolated_env):
    import httpx
    from src.outputs.email import Mailer
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    transport = httpx.MockTransport(lambda req: httpx.Response(500, json={"error": "down"}))
    status = Mailer(store, sleep=no_sleep, transport=transport).send("alert", ["a@example.com"], "s", "<p>x</p>")
    assert status == "FAILED"
    assert "re_test" not in store.emails()[0]["detail"]
    assert list((isolated_env / "var" / "outbox").glob("*-alert.eml"))
