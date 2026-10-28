import os
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest

from app import db
from app.models import Anomaly, Run


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.rows = []
        self.sections = {}
        self._row = None
        self._cell = None
        self._section = None
        self._definition = None
        self.definitions = {}
        self._term = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.links.append(attrs.get("href"))
        if tag == "section":
            self._section = attrs.get("aria-label")
            self.sections[self._section] = []
        if tag == "tr":
            self._row = []
        if tag in ("td", "th") and self._row is not None:
            self._cell = []
        if tag == "dt" and self._term is None:
            self._term = []
        if tag == "dd":
            self._definition = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if isinstance(self._term, list):
            self._term.append(data)
        if self._definition is not None:
            self._definition.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        if tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            if self._section:
                self.sections[self._section].append(self._row)
            self._row = None
        if tag == "dt" and self._term is not None:
            self._term = "".join(self._term).strip()
        if tag == "dd" and self._definition is not None:
            self.definitions[self._term] = "".join(self._definition).strip()
            self._term = None
            self._definition = None
        if tag == "section":
            self._section = None


def markup(response):
    parsed = Markup()
    parsed.feed(response.get_data(as_text=True))
    return parsed


@pytest.fixture
def web_client(orm_app):
    client = orm_app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    response = client.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"], "csrf_token": csrf})
    assert response.status_code == 302
    return client


def add_run(status="SUCCESS", **fields):
    item = Run(status=status, started_at="2026-01-01T00:00:00Z", **fields)
    db.session.add(item)
    db.session.commit()
    return item


@pytest.mark.parametrize("path", ["/runs", "/runs/1"])
def test_anonymous_runs_pages_redirect_to_login(orm_app, path):
    response = orm_app.test_client().get(path)
    target = urlsplit(response.headers["Location"])
    assert (response.status_code, target.path, parse_qs(target.query)["next"]) == (302, "/login", [path])


def test_empty_runs_list_shows_empty_state(web_client):
    response = web_client.get("/runs")
    assert response.status_code == 200
    assert "Aucun run sur cette page." in response.get_data(as_text=True)
    assert markup(response).rows == []


def test_runs_list_links_to_newest_first(web_client):
    older = add_run(status="FAIL")
    newer = add_run(status="SUCCESS")
    response = web_client.get("/runs")
    parsed = markup(response)
    assert response.status_code == 200
    assert parsed.rows[1:] == [[f"Run {newer.id}", "SUCCESS", "2026-01-01T00:00:00Z", "—"],
                               [f"Run {older.id}", "FAIL", "2026-01-01T00:00:00Z", "—"]]
    assert f"/runs/{newer.id}" in parsed.links and f"/runs/{older.id}" in parsed.links


def test_runs_pagination_links_and_rows(web_client):
    ids = [add_run().id for _ in range(26)]
    first = web_client.get("/runs")
    second = web_client.get("/runs?page=2")
    assert len(markup(first).rows) == 21
    assert markup(second).rows[1][0] == f"Run {ids[5]}"
    assert "/runs?page=2" in markup(first).links
    assert "/runs?page=1" in markup(second).links


def test_run_detail_contains_own_metadata_counters_and_anomalies(web_client):
    other = add_run()
    current = add_run(status="FAIL", finished_at="2026-01-01T01:00:00Z", error_message="collection failed",
                      matched_name_count=2, matched_fqdn_count=3, matched_ip_count=4, no_match_count=5)
    db.session.add(Anomaly(run_id=other.id, code="NO_MATCH", severity="info", description="other run only", created_at="2026-01-01"))
    db.session.commit()
    db.session.add(Anomaly(run_id=current.id, code="MATCHED_IP", severity="info", description="current only", created_at="2026-01-02"))
    db.session.commit()
    response = web_client.get(f"/runs/{current.id}")
    parsed = markup(response)
    assert response.status_code == 200
    assert {key: parsed.definitions[key] for key in ("Statut", "Début", "Fin", "Erreur", "Par nom", "Par FQDN", "Par IP", "Sans correspondance")} == {
        "Statut": "FAIL", "Début": "2026-01-01T00:00:00Z", "Fin": "2026-01-01T01:00:00Z",
        "Erreur": "collection failed", "Par nom": "2", "Par FQDN": "3", "Par IP": "4", "Sans correspondance": "5"}
    assert parsed.sections["Anomalies du run"][1:] == [["MATCHED_IP", "current only", "—", "—", "2026-01-02"]]


def test_unknown_run_returns_404(web_client):
    assert web_client.get("/runs/999999").status_code == 404


def test_hostile_anomaly_description_is_escaped(web_client):
    current = add_run()
    db.session.add(Anomaly(run_id=current.id, code="NO_MATCH", severity="info", description="<img src=x onerror=alert(1)>", created_at="2026-01-01"))
    db.session.commit()
    response = web_client.get(f"/runs/{current.id}")
    assert markup(response).sections["Anomalies du run"][1][1] == "<img src=x onerror=alert(1)>"
    assert "<img src=x onerror=alert(1)>" not in response.get_data(as_text=True)


def test_runs_navigation_link_reaches_list(web_client):
    home = markup(web_client.get("/"))
    assert "/runs" in home.links
    assert web_client.get("/runs").status_code == 200


def test_web_session_does_not_authorize_jwt_runs_api(web_client):
    assert web_client.get("/api/runs").status_code == 401
