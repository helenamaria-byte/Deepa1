"""Today absorbs the old Recommendations tab: filters, filtered Approve all, older plans."""
import re

import pytest
from fastapi.testclient import TestClient

from src.app.main import app, get_store
from src.jobs.run_plan import run_plan
from tests.conftest import RUN_DATE, StaticConnector, no_sleep

ITEM = re.compile(r'class="item [^"]*" id="(SKU\d+)"')


@pytest.fixture
def client(raw):
    run_plan(RUN_DATE, store=get_store(), connector=StaticConnector(raw), sleep=no_sleep, send_email=False)
    c = TestClient(app)
    c.post("/me", data={"who": "Raghav"})
    return c


def test_menu_has_no_recommendations_tab_and_old_links_forward(client):
    home = client.get("/").text
    nav = home[home.index("<nav>"):home.index("</nav>")]
    assert "Recommendations" not in nav
    r = client.get("/recommendations?distributor=Meridian+Trade+Co.", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?distributor=Meridian+Trade+Co."
    r = client.get("/review", follow_redirects=False)  # link used in the alert email
    assert r.headers["location"] == "/?show=todo"


def test_distributor_filter_and_filtered_approve_all(client):
    page = client.get("/?distributor=Meridian+Trade+Co.").text
    items = ITEM.findall(page)
    assert sorted(items) == ["SKU010", "SKU011", "SKU012"] and "Showing 3 of 15 SKUs" in page
    assert 'value="SKU011,SKU010,SKU012"' in page or "skus" in page
    r = client.post("/approve-all", data={"run_id": "1", "skus": ",".join(items),
                                          "next": "/?distributor=Meridian+Trade+Co."})
    assert "Approved 3 suggestions." in r.text
    assert sorted(get_store().latest_decisions(1)) == ["SKU010", "SKU011", "SKU012"]
    # Status line still counts the whole plan.
    assert "3 of 15 done" in client.get("/").text


def test_category_and_waiting_filters(client):
    snacks = ITEM.findall(client.get("/?category=Snack").text)
    assert sorted(snacks) == ["SKU002", "SKU005", "SKU008", "SKU014"]
    client.post("/sku/1/SKU002", data={"decision": "APPROVED", "next": "/"})
    waiting = ITEM.findall(client.get("/?category=Snack&show=todo").text)
    assert sorted(waiting) == ["SKU005", "SKU008", "SKU014"]


def test_decision_returns_to_the_same_filtered_view(client):
    r = client.post("/sku/1/SKU005", data={"decision": "APPROVED", "next": "/?category=Snack#SKU005"},
                    follow_redirects=False)
    assert r.headers["location"].startswith("/?category=Snack&msg=") and r.headers["location"].endswith("#SKU005")


def test_older_plan_is_viewable(client, raw):
    run_plan(RUN_DATE, store=get_store(), connector=StaticConnector(raw), sleep=no_sleep, send_email=False)
    assert "older plan" not in client.get("/").text
    old = client.get("/?run=1").text
    assert "You are looking at an older plan (#1" in old and "Go to the current plan #2" in old
    assert 'href="/?run=1"' in client.get("/decisions").text or "#1" in client.get("/decisions").text
