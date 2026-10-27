import os
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest

from app import db
from app.models import Anomaly, Asset, ConsolidatedAsset, Run


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.definitions = {}
        self.tables = []
        self.links = []
        self.term = None
        self.label = None
        self.value = None
        self.row = None
        self.cell = None
        self.table = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs).get("href"))
        if tag == "table":
            self.table = []
            self.tables.append(self.table)
        if tag == "tr" and self.table is not None:
            self.row = []
        if tag in ("td", "th") and self.row is not None:
            self.cell = []
        if tag == "dt":
            self.term = []
        if tag == "dd":
            self.label = self.term
            self.value = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)
        if isinstance(self.term, list):
            self.term.append(data)
        if self.value is not None:
            self.value.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.table.append(self.row)
            self.row = None
        if tag == "dt" and self.term is not None:
            self.term = "".join(self.term).strip()
        if tag == "dd" and self.value is not None:
            self.definitions[self.label] = "".join(self.value).strip()
            self.term = self.value = None


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
    assert client.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"], "csrf_token": csrf}).status_code == 302
    return client


def observation(run, vm_id="vm-17", **fields):
    asset = Asset(run_id=run.id, vm_id=vm_id, **fields)
    db.session.add(asset)
    db.session.flush()
    return asset


def new_run():
    run = Run(status="SUCCESS", started_at="2026-01-01T00:00:00Z")
    db.session.add(run)
    db.session.flush()
    return run


def test_anonymous_asset_redirects_to_login(orm_app):
    response = orm_app.test_client().get("/assets/17")
    target = urlsplit(response.headers["Location"])
    assert (response.status_code, target.path, parse_qs(target.query)["next"]) == (302, "/login", ["/assets/17"])


def test_web_session_does_not_authorize_jwt_asset_api(client):
    assert client.get("/api/assets/17").status_code == 401


def test_asset_details_render_metrics_and_inventory_link(client):
    asset = observation(new_run(), vm_name="server-17", type="qemu", node="node-7", cpu=2.5, ram=4096, disk=80, uptime=3600)
    db.session.add(ConsolidatedAsset(run_id=asset.run_id, asset_id=asset.id, match_status="MATCHED_NAME", ip_final="192.0.2.17", dns_final="server.example", status_final="running"))
    db.session.commit()
    response = client.get(f"/assets/{asset.id}")
    parsed = page(response)
    assert response.status_code == 200
    assert {key: parsed.definitions[key] for key in ("Nom", "Type", "Nœud", "CPU", "RAM", "Disque", "Disponibilité", "IP finale", "DNS final", "Statut", "Correspondance")} == {"Nom": "server-17", "Type": "qemu", "Nœud": "node-7", "CPU": "2.5", "RAM": "4096.0", "Disque": "80.0", "Disponibilité": "3600.0", "IP finale": "192.0.2.17", "DNS final": "server.example", "Statut": "running", "Correspondance": "MATCHED_NAME"}
    assert "/inventory" in parsed.links


def test_history_uses_only_observations_in_last_30_runs_in_descending_order(client):
    old = observation(new_run(), vm_name="outside-window")
    observed = []
    for i in range(30):
        run = new_run()
        if i % 3 != 0:
            observed.append(observation(run, vm_name=f"name-{i}"))
    db.session.commit()
    response = client.get(f"/assets/{observed[-1].id}")
    assert response.status_code == 200
    assert [row[:2] for row in page(response).tables[0][1:]] == [[str(asset.run_id), asset.vm_name] for asset in reversed(observed)]
    assert old.run_id not in [asset.run_id for asset in observed]


def test_anomalies_belong_only_to_observed_asset(client):
    first = observation(new_run())
    other = observation(db.session.get(Run, first.run_id), vm_id="other")
    later = observation(new_run())
    for asset, code, description, date in ((first, "NO_MATCH", "first only", "2026-01-01"), (later, "MATCHED_IP", "later only", "2026-01-02"), (other, "NO_MATCH", "unrelated", "2026-01-03")):
        db.session.add(Anomaly(run_id=asset.run_id, asset_id=asset.id, code=code, description=description, created_at=date))
        db.session.flush()
    db.session.commit()
    assert [row[2] for row in page(client.get(f"/assets/{later.id}")).tables[1][1:]] == ["later only", "first only"]


def test_unknown_asset_returns_404(client):
    assert client.get("/assets/999999").status_code == 404


def test_hostile_asset_name_is_escaped_in_its_field(client):
    hostile = '<img src=x onerror=alert(1)>'
    asset = observation(new_run(), vm_name=hostile)
    db.session.commit()
    response = client.get(f"/assets/{asset.id}")
    assert page(response).definitions["Nom"] == hostile
    assert hostile not in response.get_data(as_text=True)
