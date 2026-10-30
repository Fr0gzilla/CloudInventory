import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import (Account, Anomaly, AnomalyException, Asset, Audit,
                        ConsolidatedAsset, IpamRecord, Run, SnapshotObservation)


def bearer(client, username="admin", password=None):
    response = client.post("/api/login", json={"username": username,
                                                  "password": password or os.environ["ADMIN_PASSWORD"]})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.get_json()['access_token']}"}


def scenario(code="NO_MATCH", address="192.0.2.42", prefix="192.0.2.0/24", *,
             source="netbox", state="ouverte", linked=False, consolidate=True):
    run = Run(status="SUCCESS", started_at="2026-01-01T00:00:00+00:00")
    db.session.add(run)
    db.session.flush()
    asset = Asset(run_id=run.id, vm_id="vm-proposal", ip_reported=address)
    observation = IpamRecord(run_id=run.id, ip="192.0.2.1" if ":" not in address else "2001:db8::1",
                             network_prefix=prefix, ipam_source=source)
    db.session.add_all([asset, observation])
    db.session.flush()
    if consolidate:
        db.session.add(ConsolidatedAsset(run_id=run.id, asset_id=asset.id,
                                        match_status="NO_MATCH"))
    anomaly = Anomaly(run_id=run.id, asset_id=asset.id,
                      ipam_record_id=observation.id if linked else None,
                      code=code, description="observation", state=state,
                      tracking_key=json.dumps([code, "vm-proposal"]),
                      created_at="2026-01-01T00:00:00+00:00")
    db.session.add(anomaly)
    db.session.commit()
    return anomaly, asset, observation


@pytest.fixture
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access during dry-run")

    import requests
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    return forbidden


@pytest.mark.parametrize("address,prefix,expected", [
    ("192.0.2.42", "192.0.2.0/24", "192.0.2.42/24"),
    ("2001:db8::42", "2001:db8::/64", "2001:db8::42/64"),
])
def test_no_match_proposes_exact_netbox_creation_without_network(orm_app, offline, address, prefix, expected):
    client = orm_app.test_client()
    headers = bearer(client)
    anomaly, _, _ = scenario(address=address, prefix=prefix)
    response = client.post(f"/api/anomalies/{anomaly.id}/proposal", headers=headers)
    assert response.status_code == 200
    assert response.get_json() == {
        "simulation": True, "status": "proposition", "method": "POST",
        "url": "/api/ipam/ip-addresses/", "body": {"address": expected},
        "explanation": "Adresse absente des observations IPAM du run ; préfixe observé dans NetBox",
        "current_value": None, "reason": None,
    }


@pytest.mark.parametrize("code", ["HOSTNAME_MISMATCH", "STATUS_MISMATCH", "NO_MATCH", "RENAMED",
                                  "IP_OUT_OF_PREFIX", "VM_IP_CONFLICT", "DNS_MISSING_PTR", "PTR_MISMATCH"])
def test_codes_without_authentic_netbox_target_refuse_invented_patch(orm_app, offline, code):
    client = orm_app.test_client()
    headers = bearer(client)
    anomaly, _, observation = scenario(code=code, linked=True)
    response = client.post(f"/api/anomalies/{anomaly.id}/proposal", headers=headers)
    data = response.get_json()
    assert response.status_code == 200
    assert (data["simulation"], data["status"], data["method"], data["url"], data["body"]) == (
        True, "aucune proposition", None, None, None)
    assert str(observation.id) not in str(data["url"])
    assert data["reason"]


@pytest.mark.parametrize("state", ["acquittée", "résolue"])
def test_closed_anomaly_has_no_proposal(orm_app, offline, state):
    client = orm_app.test_client()
    headers = bearer(client)
    anomaly, _, _ = scenario(state=state)
    data = client.post(f"/api/anomalies/{anomaly.id}/proposal", headers=headers).get_json()
    assert (data["status"], data["body"], data["reason"]) == (
        "aucune proposition", None, "Anomalie non ouverte")


def test_active_exception_prevents_proposal(orm_app, offline):
    client = orm_app.test_client()
    headers = bearer(client)
    anomaly, _, _ = scenario()
    db.session.add(AnomalyException(code="NO_MATCH", target="vm-proposal", author="admin",
                                    justification="maintenance",
                                    expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat()))
    db.session.commit()
    data = client.post(f"/api/anomalies/{anomaly.id}/proposal", headers=headers).get_json()
    assert (data["status"], data["body"], data["reason"]) == (
        "aucune proposition", None, "Anomalie couverte par une exception active")


@pytest.mark.parametrize("change", ["missing_prefix", "ambiguous_prefix", "mixed_source",
                                   "missing_consolidation", "matching_ip", "duplicate_asset", "invalid_ip"])
def test_unproven_no_match_refuses_creation(orm_app, offline, change):
    client = orm_app.test_client()
    headers = bearer(client)
    anomaly, asset, observation = scenario(consolidate=change != "missing_consolidation")
    if change == "missing_prefix":
        observation.network_prefix = None
    elif change == "ambiguous_prefix":
        db.session.add(IpamRecord(run_id=anomaly.run_id, ip="192.0.2.2", ipam_source="netbox",
                                  network_prefix="192.0.2.0/25"))
    elif change == "mixed_source":
        db.session.add(IpamRecord(run_id=anomaly.run_id, ip="192.0.2.2", ipam_source="efficientip"))
    elif change == "matching_ip":
        observation.ip = asset.ip_reported
    elif change == "duplicate_asset":
        db.session.add(Asset(run_id=anomaly.run_id, vm_id="vm-other", ip_reported=asset.ip_reported))
    elif change == "invalid_ip":
        asset.ip_reported = "not-an-ip"
    db.session.commit()
    data = client.post(f"/api/anomalies/{anomaly.id}/proposal", headers=headers).get_json()
    assert (data["status"], data["method"], data["body"]) == ("aucune proposition", None, None)
    assert data["reason"]


@pytest.mark.parametrize("role,expected", [("admin", 200), ("opérateur", 200), ("lecteur", 403),
                                            ("anonymous", 401)])
def test_proposal_role_permissions(orm_app, offline, role, expected):
    client = orm_app.test_client()
    anomaly, _, _ = scenario()
    headers = {}
    if role != "anonymous":
        if role != "admin":
            db.session.add(Account(origin_code="local", username=role, role_code=role,
                                   local_password_hash=generate_password_hash("test-role-password")))
            db.session.commit()
        headers = bearer(client, role, "test-role-password" if role != "admin" else None)
    response = client.post(f"/api/anomalies/{anomaly.id}/proposal", headers=headers)
    assert response.status_code == expected


def test_request_audits_without_mutating_observations_or_leaking_token(orm_app, offline, caplog):
    client = orm_app.test_client()
    headers = bearer(client)
    anomaly, asset, observation = scenario()
    db.session.add(SnapshotObservation(run_id=anomaly.run_id, asset_id=asset.id,
                                       snapshot_id="snapshot-stable"))
    db.session.commit()
    before = (anomaly.state, asset.ip_reported, observation.ip, observation.network_prefix,
              [(row.id, row.snapshot_id) for row in SnapshotObservation.query.all()])
    audit_count = Audit.query.count()
    response = client.post(f"/api/anomalies/{anomaly.id}/proposal", headers=headers)
    db.session.expire_all()
    after = (anomaly.state, asset.ip_reported, observation.ip, observation.network_prefix,
             [(row.id, row.snapshot_id) for row in SnapshotObservation.query.all()])
    traces = Audit.query.order_by(Audit.id).all()[audit_count:]
    assert response.status_code == 200
    assert before == after
    assert [(row.action, row.target) for row in traces] == [
        ("proposition NetBox demandée", f"anomalie:{anomaly.id}")]
    token = headers["Authorization"].split(" ", 1)[1]
    assert token not in response.get_data(as_text=True)
    assert all(token not in str((row.action, row.target, row.actor_identity)) for row in traces)
    assert token not in caplog.text


@pytest.mark.parametrize("suffix,kwargs", [("?unexpected=1", {}), ("", {"json": {"action": "apply"}}),
                                           ("", {"data": "x"})])
def test_proposal_rejects_body_and_query(orm_app, offline, suffix, kwargs):
    client = orm_app.test_client()
    headers = bearer(client)
    anomaly, _, _ = scenario()
    response = client.post(f"/api/anomalies/{anomaly.id}/proposal{suffix}", headers=headers, **kwargs)
    assert (response.status_code, response.get_json()) == (400, {"error": "Invalid anomaly proposal"})


def test_missing_anomaly_returns_not_found(orm_app, offline):
    client = orm_app.test_client()
    response = client.post("/api/anomalies/999999/proposal", headers=bearer(client))
    assert (response.status_code, response.get_json()) == (404, {"error": "Anomaly not found"})
