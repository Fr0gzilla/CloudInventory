import json
import os
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

import pytest

from app import create_app, db
from app.models import Anomaly, AnomalyException, Asset, IpamRecord, Run
from collector.anomalies import network_match_anomalies, ptr_anomalies, vm_ip_conflicts
from collector.inventory_runner import run_inventory
from collector.matching import consolidate_asset
from collector.mock_ipam import MockIpamSource
from collector.mock_proxmox import MockProxmoxSource


def test_ip_out_of_prefix_and_inside_prefix():
    asset = MockProxmoxSource().collect()[0]
    ipam = MockIpamSource().collect()[0]
    ipam.update(ip=asset["ip_reported"], dns_name=asset["vm_name"], network_prefix="192.0.2.0/24")
    assert [row["code"] for row in network_match_anomalies(consolidate_asset(asset, [ipam]))] == ["IP_OUT_OF_PREFIX"]
    ipam["network_prefix"] = "10.0.0.0/8"
    assert network_match_anomalies(consolidate_asset(asset, [ipam])) == []


@pytest.mark.parametrize("prefix", [None, "invalid-prefix"])
def test_missing_or_invalid_prefix_is_unknown_not_outside(prefix):
    asset = MockProxmoxSource().collect()[0]
    ipam = MockIpamSource().collect()[0]
    ipam.update(ip=asset["ip_reported"], dns_name=asset["vm_name"], network_prefix=prefix)
    assert network_match_anomalies(consolidate_asset(asset, [ipam])) == []


def test_distinct_vms_share_ip_but_same_identity_does_not():
    first = {"vm_id": "1", "ip_reported": "192.0.2.3"}
    second = {"vm_id": "2", "ip_reported": "192.0.2.3"}
    assert [row["asset"]["vm_id"] for row in vm_ip_conflicts([first, second])] == ["1", "2"]
    assert vm_ip_conflicts([first, {**second, "vm_id": "1"}]) == []


def test_ipam_duplicate_alone_does_not_create_vm_conflict():
    assert vm_ip_conflicts([{"vm_id": "1", "ip_reported": "192.0.2.3"}]) == []


def test_observed_missing_ptr_differs_from_unknown_ptr():
    observed = {"ip": "192.0.2.3", "dns_name": "host.example", "ptr_name": ""}
    assert [row["code"] for row in ptr_anomalies([observed])] == ["DNS_MISSING_PTR"]
    assert ptr_anomalies([{**observed, "ptr_name": None}]) == []
    assert ptr_anomalies([{"ip": "192.0.2.3", "dns_name": "host.example"}]) == []


def test_ptr_mismatch_and_normalized_matching_name():
    observed = {"ip": "192.0.2.3", "dns_name": "Host.Example.", "ptr_name": "other.example."}
    assert [row["code"] for row in ptr_anomalies([observed])] == ["PTR_MISMATCH"]
    assert ptr_anomalies([{**observed, "ptr_name": "HOST.EXAMPLE"}]) == []


def test_network_codes_persist_with_correct_run_and_targets(orm_app):
    previous = run_inventory(ipam_source=MockIpamSource("network"))
    run = run_inventory(ipam_source=MockIpamSource("network"))
    expected = {"IP_OUT_OF_PREFIX", "VM_IP_CONFLICT", "DNS_MISSING_PTR", "PTR_MISMATCH"}
    anomalies = db.session.query(Anomaly).filter_by(run_id=run.id).all()
    assets = {row.id for row in db.session.query(Asset).filter_by(run_id=run.id)}
    ipam = {row.id for row in db.session.query(IpamRecord).filter_by(run_id=run.id)}
    assert {row.code for row in anomalies} >= expected
    assert all(row.severity is None and row.run_id == run.id and
               (row.asset_id is None or row.asset_id in assets) and
               (row.ipam_record_id is None or row.ipam_record_id in ipam)
               for row in anomalies if row.code in expected)
    assert {row.code for row in db.session.query(Anomaly).filter_by(run_id=previous.id)} >= expected


NETWORK_CODES = ("IP_OUT_OF_PREFIX", "VM_IP_CONFLICT", "DNS_MISSING_PTR", "PTR_MISMATCH")


class AnomalyTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.in_body = False
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tbody" and not self.in_body:
            self.in_body = True
        elif tag == "tr" and self.in_body:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "tbody" and self.in_body:
            self.in_body = False


def login_clients(app):
    client = app.test_client()
    token = client.post("/api/login", json={"username": "admin",
                                             "password": os.environ["ADMIN_PASSWORD"]}).get_json()["access_token"]
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    assert client.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"],
                                       "csrf_token": csrf}).status_code == 302
    return client, {"Authorization": f"Bearer {token}"}


def network_row(run_id, code):
    return Anomaly.query.filter_by(run_id=run_id, code=code).order_by(Anomaly.id).first()


@pytest.mark.parametrize("code", NETWORK_CODES)
def test_network_exception_covers_exact_tracking_key_via_api(orm_app, code):
    run = run_inventory(ipam_source=MockIpamSource("network"))
    row = network_row(run.id, code)
    assert row is not None and row.tracking_key
    client, headers = login_clients(orm_app)
    target = json.loads(row.tracking_key)[1]
    data = {"code": code, "target": target, "justification": "maintenance",
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()}
    wrong = client.post("/api/anomaly-exceptions", json={**data, "target": "missing-target"}, headers=headers)
    assert (wrong.status_code, wrong.get_json()) == (400, {"error": "Invalid exception"})
    created = client.post("/api/anomaly-exceptions", json=data, headers=headers)
    assert created.status_code == 201
    exception = AnomalyException.query.filter_by(code=code, target=target).one()
    filtered = client.get(f"/api/anomalies?run_id={run.id}&code={code}", headers=headers)
    selected = next(item for item in filtered.get_json()["items"] if item["id"] == row.id)
    assert (selected["covered"], selected["exception_id"], selected["target"],
            db.session.get(Anomaly, row.id).state) == (True, exception.id, target, "ouverte")


@pytest.mark.parametrize("code", NETWORK_CODES)
def test_network_code_filters_api_and_html_rows(orm_app, code):
    old = run_inventory(ipam_source=MockIpamSource("network"))
    run = run_inventory(ipam_source=MockIpamSource("network"))
    client, headers = login_clients(orm_app)
    response = client.get(f"/api/anomalies?run_id={run.id}&code={code}", headers=headers)
    assert response.status_code == 200
    items = response.get_json()["items"]
    assert items and all(item["code"] == code and item["run_id"] == run.id for item in items)
    assert network_row(old.id, code) is not None
    page = client.get(f"/anomalies?run_id={run.id}&code={code}")
    assert page.status_code == 200
    table = AnomalyTable()
    table.feed(page.get_data(as_text=True))
    assert table.rows and all(row[0].startswith(code) and
                              row[0][len(code):] in {str(item["target"]) for item in items}
                              for row in table.rows)
    assert len(table.rows) == len(items)
    invalid = client.get(f"/api/anomalies?run_id={run.id}&code=INVALID", headers=headers)
    assert (invalid.status_code, invalid.get_json()) == (400, {"error": "Invalid anomaly filter"})


def test_async_network_run_persists_all_codes_without_dns_network(monkeypatch, tmp_path):
    import collector.async_runner as async_runner

    for key in ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD"):
        monkeypatch.setenv(key, f"test-{key.lower()}")
    monkeypatch.setenv("EXPORT_DIRECTORY", str(tmp_path / "exports"))
    app = create_app(database_uri=f"sqlite:///{tmp_path / 'network.sqlite3'}")
    original = async_runner._run_inventory
    threads = []
    start = async_runner.threading.Thread.start

    def capture(thread):
        threads.append(thread)
        start(thread)

    def simulated(*args, **kwargs):
        return original(*args, ipam_source=MockIpamSource("network"), **kwargs)

    monkeypatch.setattr(async_runner.threading.Thread, "start", capture)
    monkeypatch.setattr(async_runner, "_run_inventory", simulated)
    with app.app_context():
        run_id = async_runner.start_inventory()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    with app.app_context():
        assert db.session.get(Run, run_id).status == "SUCCESS"
        assert {row.code for row in Anomaly.query.filter_by(run_id=run_id)} >= set(NETWORK_CODES)
