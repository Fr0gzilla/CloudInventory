import csv
import io
import os
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest

from app import db
from app.models import Asset, ConsolidatedAsset, Run


class InventoryMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}
        self.scripts = []
        self.cells = []
        self._cell = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids[attrs["id"]] = (tag, attrs)
        if tag == "script":
            self.scripts.append(attrs)
        if tag == "td":
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None:
            self.cells.append("".join(self._cell))
            self._cell = None


@pytest.fixture
def web_client(orm_app):
    client = orm_app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    response = client.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"], "csrf_token": csrf})
    assert response.status_code == 302
    return client


@pytest.fixture
def inventory_rows(web_client):
    run = Run(status="SUCCESS", started_at="2026-01-01T00:00:00Z")
    db.session.add(run)
    db.session.flush()
    for number in range(30):
        name = "<img src=x onerror=alert(1)>" if number == 0 else f"VM {number:02d}"
        asset = Asset(run_id=run.id, vm_id=f"vm-{number:02d}", vm_name=name,
                      type="lxc" if number == 0 else "qemu", node="special" if number == 0 else "common",
                      status="stopped" if number == 0 else "active", tags="red" if number == 0 else "blue")
        db.session.add(asset)
        db.session.flush()
        db.session.add(ConsolidatedAsset(run_id=run.id, asset_id=asset.id,
                                         match_status="NO_MATCH" if number == 0 else "MATCHED_NAME",
                                         status_final=asset.status))
    db.session.commit()
    return web_client


@pytest.mark.parametrize("path", ["/inventory", "/inventory/data", "/inventory/export"])
def test_anonymous_inventory_routes_redirect_to_login(orm_app, path):
    response = orm_app.test_client().get(path)
    target = urlsplit(response.headers["Location"])
    assert response.status_code == 302 and target.path == "/login" and parse_qs(target.query)["next"] == [path]


def test_authenticated_page_exposes_table_filters_pagination_and_csv_link(web_client):
    response = web_client.get("/inventory")
    markup = InventoryMarkup()
    markup.feed(response.get_data(as_text=True))
    assert response.status_code == 200
    assert markup.ids["inventory-rows"][0] == "tbody"
    assert markup.ids["inventory-filters"][0] == "form"
    assert markup.ids["inventory-next"][0] == "button"
    assert markup.ids["inventory-export"][1]["href"] == "/inventory/export"
    assert any(script.get("src") == "/static/inventory.js" for script in markup.scripts)


def test_session_inventory_data_paginates_25_assets(inventory_rows):
    first = inventory_rows.get("/inventory/data?page=1&per_page=25").get_json()
    second = inventory_rows.get("/inventory/data?page=2&per_page=25").get_json()
    assert (first["total"], first["per_page"], len(first["items"]), len(second["items"])) == (30, 25, 25, 5)
    assert {item["vm_id"] for item in first["items"]}.isdisjoint({item["vm_id"] for item in second["items"]})


@pytest.mark.parametrize("query,expected", [
    ("q=VM+02", "vm-02"), ("status=stopped", "vm-00"),
    ("node=special&type=lxc&match=NO_MATCH&tag=red", "vm-00"),
    ("sort=vm_id&order=desc", "vm-29"),
])
def test_session_inventory_uses_existing_filters_search_and_sort(inventory_rows, query, expected):
    response = inventory_rows.get(f"/inventory/data?{query}&per_page=25")
    assert response.status_code == 200
    assert response.get_json()["items"][0]["vm_id"] == expected


def test_session_inventory_rejects_invalid_sort(inventory_rows):
    response = inventory_rows.get("/inventory/data?sort=unknown")
    assert response.status_code == 400


def test_session_csv_export_uses_filters_without_pagination(inventory_rows):
    response = inventory_rows.get("/inventory/export?node=common&sort=vm_id&order=desc")
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
    assert response.status_code == 200
    assert len(rows) == 29 and rows[0]["vm_id"] == "vm-29"
    assert all(row["node"] == "common" for row in rows)


def test_anonymous_api_still_requires_bearer(orm_app):
    response = orm_app.test_client().get("/api/inventory")
    assert response.status_code == 401


def test_hostile_name_is_data_not_markup_in_inventory_response(inventory_rows):
    response = inventory_rows.get("/inventory/data?q=onerror&per_page=25")
    assert response.status_code == 200
    assert response.get_json()["items"][0]["vm_name"] == "<img src=x onerror=alert(1)>"
    page = inventory_rows.get("/inventory")
    markup = InventoryMarkup()
    markup.feed(page.get_data(as_text=True))
    assert markup.cells == [] and "<img src=x onerror=alert(1)>" not in page.get_data(as_text=True)
