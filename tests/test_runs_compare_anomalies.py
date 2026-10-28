import os
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest

from app import db
from app.models import Anomaly, Asset, ConsolidatedAsset, Run


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sections = {}
        self.rows = []
        self.links = []
        self.alerts = []
        self._section = None
        self._row = None
        self._cell = None
        self._alert = None
        self._nav = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "nav":
            self._nav = True
        if tag == "a" and self._nav:
            self.links.append(attrs.get("href"))
        if tag == "section":
            self._section = []
            self.sections[attrs.get("aria-label")] = self._section
        if tag == "tr":
            self._row = []
        if tag in ("td", "th") and self._row is not None:
            self._cell = []
        if attrs.get("role") == "alert":
            self._alert = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._alert is not None:
            self._alert.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        if tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        if tag == "nav":
            self._nav = False
        if tag == "p" and self._alert is not None:
            self.alerts.append("".join(self._alert).strip())
            self._alert = None


def page(response):
    parsed = Page()
    parsed.feed(response.get_data(as_text=True))
    return parsed


@pytest.fixture
def client(orm_app):
    client = orm_app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    response = client.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"], "csrf_token": csrf})
    assert response.status_code == 302
    return client


def run():
    item = Run(status="SUCCESS", started_at="2026-01-01T00:00:00Z")
    db.session.add(item)
    db.session.commit()
    return item


def asset(current, vm_id, name, ip):
    item = Asset(run_id=current.id, vm_id=vm_id, vm_name=name)
    db.session.add(item)
    db.session.flush()
    db.session.add(ConsolidatedAsset(run_id=current.id, asset_id=item.id, ip_final=ip, match_status="MATCHED_NAME"))
    db.session.commit()


def anomaly(current, code, description):
    db.session.add(Anomaly(run_id=current.id, code=code, description=description, created_at="2026-01-01"))
    db.session.commit()


@pytest.mark.parametrize("path", ["/runs/compare", "/anomalies"])
def test_pages_refuse_anonymous_session(orm_app, path):
    response = orm_app.test_client().get(path)
    target = urlsplit(response.headers["Location"])
    assert (response.status_code, target.path, parse_qs(target.query)["next"]) == (302, "/login", [path])


@pytest.mark.parametrize("path", ["/runs/compare", "/anomalies"])
def test_jwt_alone_does_not_grant_web_session(orm_app, path):
    client = orm_app.test_client()
    token = client.post("/api/login", json={"username": "admin", "password": os.environ["ADMIN_PASSWORD"]}).get_json()["access_token"]
    assert client.get(path, headers={"Authorization": f"Bearer {token}"}).status_code == 302


def test_comparison_identifies_appeared_disappeared_and_changed_assets(client):
    old, new = run(), run()
    asset(old, "gone", "deleted", "10.0.0.1")
    asset(new, "new", "created", "10.0.0.2")
    asset(old, "same", "modified", "10.0.0.3")
    asset(new, "same", "modified", "10.0.0.4")
    response = client.get(f"/runs/compare?from={old.id}&to={new.id}")
    rows = page(response).rows
    assert response.status_code == 200
    assert [row for row in rows if row and row[0] in ("gone", "new", "same")] == [
        ["new", "created", "10.0.0.2", "—", "—", "MATCHED_NAME"],
        ["gone", "deleted", "10.0.0.1", "—", "—", "MATCHED_NAME"],
        ["same", "ip_final", "10.0.0.3 / — / — / MATCHED_NAME", "10.0.0.4 / — / — / MATCHED_NAME"],
    ]


@pytest.mark.parametrize("old_count,new_count,expected", [(1, 2, "+1"), (2, 1, "-1"), (1, 1, "+0")])
def test_comparison_anomaly_delta_uses_destination_minus_source(client, old_count, new_count, expected):
    old, new = run(), run()
    for _ in range(old_count):
        anomaly(old, "NO_MATCH", "before")
    for _ in range(new_count):
        anomaly(new, "MATCHED_IP", "after")
    response = client.get(f"/runs/compare?from={old.id}&to={new.id}")
    assert f"(écart : {expected})" in response.get_data(as_text=True).split("<h2>Écart d'anomalies</h2><p>")[1].split("</p>")[0]


@pytest.mark.parametrize("query", ["from=999999&to=1", "from=1&to=999999"])
def test_comparison_unknown_run_reports_error_without_results(client, query):
    run()
    response = client.get(f"/runs/compare?{query}")
    assert response.status_code == 200 and page(response).alerts and "Assets ajoutés" not in response.get_data(as_text=True)


@pytest.mark.parametrize("query", ["from=1", "from=1&to=1", "from=abc&to=2", "from=0&to=2", "from=1&from=2&to=3", "from=1&to=2&extra=3"])
def test_comparison_invalid_parameters_show_error_without_results(client, query):
    response = client.get(f"/runs/compare?{query}")
    assert response.status_code == 200 and page(response).alerts and "Assets ajoutés" not in response.get_data(as_text=True)


def test_comparison_empty_run_selection_and_empty_results(client):
    assert "Aucun run disponible." in client.get("/runs/compare").get_data(as_text=True)
    first = run()
    assert "Deux runs sont nécessaires" in client.get("/runs/compare").get_data(as_text=True)
    second = run()
    response = client.get(f"/runs/compare?from={first.id}&to={second.id}")
    assert all(text in response.get_data(as_text=True) for text in ("Aucun asset ajouté.", "Aucun asset supprimé.", "Aucun asset modifié."))


def test_anomalies_default_to_latest_run_and_render_rows(client):
    old, latest = run(), run()
    anomaly(old, "NO_MATCH", "old only")
    anomaly(latest, "MATCHED_IP", "latest only")
    response = client.get("/anomalies")
    assert response.status_code == 200
    assert page(response).rows[1:] == [["MATCHED_IP", "latest only", "—", "—", "2026-01-01"]]


def test_anomalies_run_and_code_filters_isolate_rows(client):
    old, latest = run(), run()
    anomaly(old, "NO_MATCH", "old matching code")
    anomaly(latest, "NO_MATCH", "latest matching code")
    anomaly(latest, "MATCHED_IP", "latest other code")
    response = client.get(f"/anomalies?run_id={latest.id}&code=NO_MATCH")
    assert page(response).rows[1:] == [["NO_MATCH", "latest matching code", "—", "—", "2026-01-01"]]


def test_anomalies_unknown_run_reports_error(client):
    response = client.get("/anomalies?run_id=999999")
    assert response.status_code == 200 and page(response).alerts and page(response).rows == []


@pytest.mark.parametrize("query", ["run_id=0", "run_id=abc", "run_id=1&run_id=2", "code=BAD", "code=NO_MATCH&code=MATCHED_IP", "unknown=1"])
def test_anomalies_invalid_filters_report_error(client, query):
    response = client.get(f"/anomalies?{query}")
    assert response.status_code == 200 and page(response).alerts and page(response).rows == []


def test_anomalies_empty_run_and_empty_filter_state(client):
    assert "Aucun run disponible." in client.get("/anomalies").get_data(as_text=True)
    current = run()
    response = client.get(f"/anomalies?run_id={current.id}&code=NO_MATCH")
    assert "Aucune anomalie pour ce filtre." in response.get_data(as_text=True) and page(response).rows == []


def test_hostile_description_is_escaped_in_anomaly_cell(client):
    current = run()
    hostile = "<img src=x onerror=alert(1)>"
    anomaly(current, "NO_MATCH", hostile)
    response = client.get(f"/anomalies?run_id={current.id}")
    assert page(response).rows[1][1] == hostile and hostile not in response.get_data(as_text=True)


def test_hostile_asset_name_is_escaped_in_comparison_cell(client):
    old, new = run(), run()
    hostile = "<img src=x onerror=alert(1)>"
    asset(new, "new", hostile, "10.0.0.2")
    response = client.get(f"/runs/compare?from={old.id}&to={new.id}")
    assert any(row[:2] == ["new", hostile] for row in page(response).rows) and hostile not in response.get_data(as_text=True)


@pytest.mark.parametrize("path", ["/runs/compare", "/anomalies"])
def test_navigation_links_reach_both_pages(client, path):
    assert path in page(client.get("/")).links and client.get(path).status_code == 200


@pytest.mark.parametrize("path", ["/runs/compare", "/anomalies"])
def test_pages_keep_csp_and_no_inline_scripts(client, path):
    response = client.get(path)
    assert response.headers.get("Content-Security-Policy") and "<script>" not in response.get_data(as_text=True)


class RunSelectors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.options = {"from": [], "to": []}
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select":
            self.current = attrs.get("name")
        elif tag == "option" and self.current in self.options:
            self.options[self.current].append((attrs.get("value"), "selected" in attrs))

    def handle_endtag(self, tag):
        if tag == "select":
            self.current = None


def selectors(response):
    parsed = RunSelectors()
    parsed.feed(response.get_data(as_text=True))
    return parsed.options


def test_comparison_selectors_list_every_run_beyond_first_api_page(client):
    for _ in range(102):
        run()
    ids = {str(item.id) for item in db.session.query(Run.id).all()}
    options = selectors(client.get("/runs/compare"))
    assert {name: {value for value, _ in entries if value} for name, entries in options.items()} == {
        "from": ids, "to": ids,
    }


def test_comparison_selected_runs_remain_selected_and_are_compared(client):
    first, second, third = run(), run(), run()
    asset(first, "removed", "before", "10.0.0.1")
    asset(third, "added", "after", "10.0.0.3")
    response = client.get(f"/runs/compare?from={first.id}&to={third.id}")
    options = selectors(response)
    assert ({value for value, selected in options["from"] if selected},
            {value for value, selected in options["to"] if selected},
            [row[0] for row in page(response).rows if row and row[0] in ("removed", "added")]) == (
                {str(first.id)}, {str(third.id)}, ["added", "removed"])


@pytest.mark.parametrize("query,expected", [
    ("from=1&to=1", "Sélection invalide : choisissez deux runs distincts."),
    ("from=1&to=999999", "Run inconnu : choisissez un run disponible."),
])
def test_comparison_invalid_selection_has_explicit_alert(client, query, expected):
    run()
    response = client.get(f"/runs/compare?{query}")
    assert page(response).alerts == [expected]


def test_comparison_run_listing_and_validation_use_api_not_run_orm(client, monkeypatch):
    from flask import jsonify

    first = run()
    calls = []

    def listed_runs():
        calls.append(True)
        return jsonify(items=[{"id": first.id, "started_at": first.started_at},
                              {"id": 999999, "started_at": "2026-01-01"}], total=2)

    monkeypatch.setitem(client.application.view_functions, "api.runs", listed_runs)
    response = client.get(f"/runs/compare?from={first.id}&to=888888")
    assert calls and {value for value, _ in selectors(response)["to"]} == {"", str(first.id), "999999"} and page(response).alerts == ["Run inconnu : choisissez un run disponible."]
