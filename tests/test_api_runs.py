import os

import pytest

from app import db
from app.models import Anomaly, Asset, ConsolidatedAsset, Run


@pytest.fixture
def authorized(orm_app, monkeypatch):
    client = orm_app.test_client()
    token = client.post("/api/login", json={"username": "admin", "password": os.environ["ADMIN_PASSWORD"]}).get_json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def run(status="SUCCESS"):
    item = Run(status=status, started_at="2026-01-01T00:00:00Z", matched_name_count=0,
               matched_fqdn_count=0, matched_ip_count=0, no_match_count=0)
    db.session.add(item)
    db.session.commit()
    return item


def snapshot(item, vm_id, ip, match="MATCHED_IP"):
    asset = Asset(run_id=item.id, vm_id=vm_id, vm_name=vm_id)
    db.session.add(asset)
    db.session.flush()
    db.session.add(ConsolidatedAsset(run_id=item.id, asset_id=asset.id, ip_final=ip,
                                     match_status=match))
    db.session.commit()


def test_stats_empty(authorized):
    client, headers = authorized
    response = client.get("/api/stats", headers=headers)
    assert response.get_json() == {"run": None, "total_assets": 0, "matches": {
        "MATCHED_NAME": 0, "MATCHED_FQDN": 0, "MATCHED_IP": 0, "NO_MATCH": 0}, "anomalies": {}}


def test_stats_latest_run_counts_only_latest(authorized):
    client, headers = authorized
    old = run()
    snapshot(old, "old", "10.0.0.1")
    latest = run()
    snapshot(latest, "new", "10.0.0.2", "NO_MATCH")
    db.session.add(Anomaly(run_id=latest.id, code="NO_MATCH", severity="info", description="unmatched", created_at="2026-01-01"))
    db.session.commit()
    data = client.get("/api/stats", headers=headers).get_json()
    assert (data["run"]["id"], data["total_assets"], data["matches"]["NO_MATCH"],
            data["anomalies"]["NO_MATCH"]) == (latest.id, 1, 1, 1)


def test_runs_paginated_newest_first(authorized):
    client, headers = authorized
    ids = [run().id for _ in range(3)]
    data = client.get("/api/runs?page=2&per_page=1", headers=headers).get_json()
    assert (data["items"][0]["id"], data["total"], data["page"], data["per_page"]) == (ids[1], 3, 2, 1)


@pytest.mark.parametrize("query", ["page=0", "page=abc", "per_page=101", "per_page=-1"])
def test_runs_reject_invalid_pagination(authorized, query):
    client, headers = authorized
    response = client.get(f"/api/runs?{query}", headers=headers)
    assert (response.status_code, response.get_json()) == (400, {"error": "Invalid pagination"})


def test_run_detail_contains_persisted_assets_and_anomalies(authorized):
    client, headers = authorized
    item = run()
    snapshot(item, "vm-1", "10.0.0.1")
    db.session.add(Anomaly(run_id=item.id, code="MATCHED_IP", severity="info", description="ip match", created_at="2026-01-01"))
    db.session.commit()
    data = client.get(f"/api/runs/{item.id}", headers=headers).get_json()
    assert (data["id"], data["assets"][0]["vm_id"], data["assets"][0]["ip_final"],
            data["anomalies"][0]["code"]) == (item.id, "vm-1", "10.0.0.1", "MATCHED_IP")


def test_unknown_run_returns_json_404(authorized):
    client, headers = authorized
    response = client.get("/api/runs/999", headers=headers)
    assert (response.status_code, response.get_json()) == (404, {"error": "Run not found"})


def test_create_run_executes_real_pipeline(authorized, monkeypatch):
    from collector.mock_ipam import MockIpamSource
    from collector.mock_proxmox import MockProxmoxSource
    import collector.inventory_runner as pipeline

    monkeypatch.setattr(pipeline, "MockProxmoxSource", MockProxmoxSource)
    monkeypatch.setattr(pipeline, "MockIpamSource", MockIpamSource)
    client, headers = authorized
    from threading import Event
    import collector.async_runner as async_runner

    original = async_runner._run_inventory
    entered, release = Event(), Event()
    threads = []
    original_start = async_runner.threading.Thread.start

    def capture_start(thread):
        threads.append(thread)
        original_start(thread)

    def blocked(*args, **kwargs):
        def after_step(step):
            if step == 1:
                entered.set()
                assert release.wait(10)
        return original(*args, after_step=after_step, **kwargs)

    monkeypatch.setattr(async_runner, "_run_inventory", blocked)
    monkeypatch.setattr(async_runner.threading.Thread, "start", capture_start)
    try:
        response = client.post("/api/runs", headers=headers)
        data = response.get_json()
        assert response.status_code == 202
        assert entered.wait(10)
        assert client.get(data["status_url"]).status_code == 302
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
    db.session.expire_all()
    assert (db.session.get(Run, data["id"]).status,
            Asset.query.filter_by(run_id=data["id"]).count()) == ("SUCCESS", len(MockProxmoxSource().collect()))


def test_compare_persisted_appearances_disappearances_and_changes(authorized):
    client, headers = authorized
    before, after = run(), run()
    snapshot(before, "gone", "10.0.0.1")
    snapshot(before, "same", "10.0.0.2")
    snapshot(after, "new", "10.0.0.3")
    snapshot(after, "same", "10.0.0.4")
    data = client.get(f"/api/runs/compare?from={before.id}&to={after.id}", headers=headers).get_json()
    assert (data["from"], data["to"], [x["vm_id"] for x in data["appeared"]],
            [x["vm_id"] for x in data["disappeared"]], data["changed"][0]["fields"],
            data["changed"][0]["before"]["ip_final"], data["changed"][0]["after"]["ip_final"]) == (
                before.id, after.id, ["new"], ["gone"], ["ip_final"], "10.0.0.2", "10.0.0.4")


@pytest.mark.parametrize("query", ["", "from=1", "to=2", "from=1&to=1", "from=1&to=999",
                                        "from=abc&to=2", "from=-1&to=2", "from=1.0&to=2"])
def test_compare_rejects_invalid_run_ids(authorized, query):
    client, headers = authorized
    run()
    run()
    response = client.get(f"/api/runs/compare?{query}", headers=headers)
    assert (response.status_code, response.get_json()) == (400, {"error": "Invalid comparison"})


@pytest.mark.parametrize("method,path", [("GET", "/api/stats"), ("GET", "/api/runs"),
                                         ("POST", "/api/runs"), ("GET", "/api/runs/1"),
                                         ("GET", "/api/runs/compare?from=1&to=2")])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"}])
def test_each_run_operation_rejects_missing_or_invalid_token(orm_app, method, path, headers):
    response = orm_app.test_client().open(path, method=method, headers=headers)
    assert (response.status_code, response.get_json()) == (401, {"error": "Unauthorized"})


def test_swagger_documents_run_operations_and_security(authorized):
    client, _ = authorized
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    client.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"], "csrf_token": csrf})
    paths = client.get("/apispec_1.json").get_json()["paths"]
    assert all(paths[path][method]["security"] == [{"Bearer": []}]
               for path, method in [("/api/stats", "get"), ("/api/runs", "get"),
                                    ("/api/runs", "post"), ("/api/runs/{id}", "get"),
                                    ("/api/runs/compare", "get")])
