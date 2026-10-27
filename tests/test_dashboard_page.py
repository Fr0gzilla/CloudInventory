import os
from html.parser import HTMLParser

import pytest

from app import db
from app.models import Anomaly, Asset, ConsolidatedAsset, Run


class DashboardMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}
        self.scripts = []
        self.cards = {}
        self._heading = None
        self._count = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids[attrs["id"]] = (tag, attrs)
        if tag == "script":
            self.scripts.append(attrs.get("src"))
        if tag == "h2" and "card-title" in attrs.get("class", ""):
            self._heading = []
        if tag == "p" and "fs-3" in attrs.get("class", ""):
            self._count = []

    def handle_data(self, data):
        if isinstance(self._heading, list):
            self._heading.append(data)
        if self._count is not None:
            self._count.append(data)

    def handle_endtag(self, tag):
        if tag == "h2" and self._heading is not None:
            self._heading = "".join(self._heading)
        if tag == "p" and self._count is not None:
            self.cards[self._heading] = "".join(self._count)
            self._heading = None
            self._count = None


@pytest.fixture
def client(orm_app):
    browser = orm_app.test_client()
    browser.get("/login")
    with browser.session_transaction() as session:
        token = session["csrf_token"]
    result = browser.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"], "csrf_token": token})
    assert result.status_code == 302
    return browser


def markup(response):
    parser = DashboardMarkup()
    parser.feed(response.get_data(as_text=True))
    return parser


@pytest.mark.parametrize("method,path", [("get", "/"), ("get", "/dashboard/data"), ("post", "/dashboard/run")])
def test_anonymous_dashboard_requires_session(orm_app, method, path):
    response = getattr(orm_app.test_client(), method)(path)
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")


def test_empty_dashboard_json_with_tables(client):
    response = client.get("/dashboard/data")
    assert response.get_json() == {"stats": {"run": None, "total_assets": 0, "matches": dict.fromkeys(("MATCHED_NAME", "MATCHED_FQDN", "MATCHED_IP", "NO_MATCH"), 0), "anomalies": {}}, "runs": []}


def test_empty_dashboard_without_tables_still_renders(monkeypatch):
    for key in ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD"):
        monkeypatch.setenv(key, f"test-{key}")
    from app import create_app
    browser = create_app().test_client()
    browser.get("/login")
    with browser.session_transaction() as session:
        token = session["csrf_token"]
    browser.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"], "csrf_token": token})
    assert browser.get("/").status_code == 200
    assert browser.get("/dashboard/data").get_json()["runs"] == []


def test_dashboard_has_three_canvases_and_local_library(client):
    page = markup(client.get("/"))
    assert {name: page.ids[name][0] for name in ("dashboard-matches", "dashboard-anomalies", "dashboard-history")} == dict.fromkeys(("dashboard-matches", "dashboard-anomalies", "dashboard-history"), "canvas")
    assert "/static/vendor/chart.js/chart.umd.js" in page.scripts
    assert "/static/dashboard.js" in page.scripts


def test_server_cards_display_latest_counts_without_javascript(client):
    run = Run(status="SUCCESS", started_at="2026-01-01T00:00:00Z")
    db.session.add(run)
    db.session.flush()
    asset = Asset(run_id=run.id, vm_id="hostile", vm_name="<img src=x onerror=alert(1)>")
    db.session.add(asset)
    db.session.flush()
    db.session.add_all([ConsolidatedAsset(run_id=run.id, asset_id=asset.id, match_status="MATCHED_IP"), Anomaly(run_id=run.id, code="MATCHED_IP", description="unsafe", created_at="2026-01-01")])
    db.session.commit()
    response = client.get("/")
    page = markup(response)
    assert page.cards["VM"] == "1"
    assert page.cards["Correspondance par IP"] == "1"
    assert page.cards["Anomalies"] == "1"
    assert "<img src=x onerror=alert(1)>" not in response.get_data(as_text=True)


def test_dashboard_json_isolates_latest_run_and_returns_api_history(client):
    old = Run(status="SUCCESS", started_at="2026-01-01T00:00:00Z", matched_name_count=9)
    latest = Run(status="SUCCESS", started_at="2026-01-02T00:00:00Z", matched_ip_count=1)
    db.session.add(old)
    db.session.flush()
    db.session.add(latest)
    db.session.flush()
    for run, code in ((old, "NO_MATCH"), (latest, "MATCHED_IP")):
        asset = Asset(run_id=run.id, vm_id=f"vm-{run.id}")
        db.session.add(asset)
        db.session.flush()
        db.session.add(ConsolidatedAsset(run_id=run.id, asset_id=asset.id, match_status=code))
        db.session.add(Anomaly(run_id=run.id, code=code, description="sample", created_at="2026-01-01"))
    db.session.commit()
    data = client.get("/dashboard/data").get_json()
    assert data["stats"]["run"]["id"] == latest.id
    assert data["stats"]["matches"] == {"MATCHED_NAME": 0, "MATCHED_FQDN": 0, "MATCHED_IP": 1, "NO_MATCH": 0}
    assert data["stats"]["anomalies"] == {"MATCHED_IP": 1}
    assert data["stats"]["total_assets"] == 1
    assert [run["id"] for run in data["runs"]] == [latest.id, old.id]


def test_dashboard_history_supplies_ten_newest_runs(client):
    for day in range(1, 13):
        db.session.add(Run(status="SUCCESS", started_at=f"2026-01-{day:02d}T00:00:00Z", matched_name_count=day))
        db.session.flush()
    db.session.commit()
    data = client.get("/dashboard/data").get_json()
    assert [run["matched_name_count"] for run in data["runs"][:10]] == list(range(12, 2, -1))


def test_api_still_requires_jwt_despite_web_session(client):
    assert client.get("/api/stats").status_code == 401
    assert client.get("/api/runs").status_code == 401


def test_dashboard_run_requires_csrf_and_delegates_to_api(client, orm_app, monkeypatch):
    response = client.post("/dashboard/run")
    assert response.status_code == 400
    with client.session_transaction() as session:
        token = session["csrf_token"]
    from flask import jsonify
    calls = []

    def create_run():
        calls.append(True)
        return jsonify(id=57, status="SUCCESS"), 201

    monkeypatch.setitem(orm_app.view_functions, "api.create_run", create_run)
    response = client.post("/dashboard/run", data={"csrf_token": token})
    assert (response.status_code, response.get_json(), calls) == (201, {"id": 57, "status": "SUCCESS"}, [True])
