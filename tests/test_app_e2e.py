"""End-to-end over HTTP: login, every page, override round trip, Excel upload,
settings versioning, cron protection."""
import io
import re

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from src.app.main import app, get_store
from src.jobs.bootstrap import bootstrap
from src.jobs.run_plan import run_plan
from tests.conftest import RUN_DATE, StaticConnector, no_sleep


@pytest.fixture
def client(raw, monkeypatch):
    monkeypatch.setenv("DASHBOARD_USERS", "raghav@example.com:pw-raghav")
    store = get_store()
    bootstrap(store)
    run_plan(RUN_DATE, store=store, connector=StaticConnector(raw), sleep=no_sleep)
    c = TestClient(app)
    r = c.post("/login", data={"username": "raghav@example.com", "password": "pw-raghav"}, follow_redirects=False)
    assert r.status_code == 303
    return c


def test_pages_require_login():
    c = TestClient(app)
    for path in ["/", "/recommendations", "/exceptions", "/decisions", "/runs", "/settings"]:
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), path


def test_wrong_password_rejected(client):
    c = TestClient(app)
    r = c.post("/login", data={"username": "raghav@example.com", "password": "nope"})
    assert "password don" in r.text and "t match" in r.text
    assert "fr_session" not in r.cookies


def test_every_page_loads(client):
    for path in ["/", "/recommendations", "/recommendations?show=todo", "/recommendations?status=STOCKOUT_RISK",
                 "/exceptions", "/decisions", "/runs", "/settings", "/sku/1/SKU001", "/judgment-log.md", "/health"]:
        r = client.get(path)
        assert r.status_code == 200, path
    home = client.get("/").text
    assert "7 of 7 SKUs are waiting for your review" in home
    rec = client.get("/recommendations?status=STOCKOUT_RISK").text
    assert "SKU001" in rec and "750" in rec and "SKU006" not in rec


def test_override_round_trip_and_delete(client):
    r = client.post("/sku/1/SKU003", data={"decision": "CHANGED", "final_qty": "420", "reason": ""})
    assert "required" in r.text  # reason is mandatory
    r = client.post("/sku/1/SKU003", data={"decision": "CHANGED", "final_qty": "420",
                                            "reason": "HTTP test override"})
    assert "Saved your decision for SKU003" in r.text
    decisions = client.get("/decisions").text
    assert "HTTP test override" in decisions and "raghav@example.com" in decisions
    oid = re.search(r"/decisions/(\d+)/delete", decisions).group(1)
    r = client.post(f"/decisions/{oid}/delete")
    assert "Decision removed." in r.text
    assert get_store().overrides(1) == []


def test_approve_all(client):
    r = client.post("/recommendations/approve-all", data={"run_id": "1", "reason": "Reviewed with procurement"})
    assert "Approved 7 suggestions." in r.text
    assert "All 7 SKUs are reviewed" in client.get("/").text


def test_excel_download_and_upload(client):
    r = client.get("/runs/1/workbook")
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb["Override log"]
    row = next(x for x in ws.iter_rows(min_row=2) if x[0].value == "SKU013")
    row[3].value, row[5].value = "Approve", "Looks right"
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/decisions/upload", files={"file": ("plan.xlsx", buf.getvalue())})
    assert "Imported 1 decisions." in r.text
    assert get_store().latest_decisions(1)["SKU013"]["final_qty"] == 120


def test_settings_save_creates_version(client):
    page = client.get("/settings").text
    form = dict(re.findall(r'name="(\w+)" value="([^"]+)"', page))
    form["festival_enabled"] = "on"
    form["gen_min"] = "5"
    form["note"] = "test"
    r = client.post("/settings", data=form)
    assert "Saved as version 2" in r.text
    assert get_store().current_rules()[1].cover.general.min_weeks == 5


def test_settings_rejects_bad_values(client):
    page = client.get("/settings").text
    form = dict(re.findall(r'name="(\w+)" value="([^"]+)"', page))
    form["gen_min"], form["gen_max"] = "20", "10"
    assert "Not saved" in client.post("/settings", data=form).text


def test_run_now_button(client):
    r = client.post("/runs/new")
    assert r.status_code == 200
    assert len(get_store().list_runs()) == 2


def test_cron_requires_secret(client):
    assert TestClient(app).get("/api/cron/run").status_code == 401
    assert TestClient(app).get("/api/cron/run", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = TestClient(app).get("/api/cron/weekly", headers={"Authorization": "Bearer test-cron"})
    assert r.status_code == 200 and r.json()["weekly_summary"] == "OUTBOX"
