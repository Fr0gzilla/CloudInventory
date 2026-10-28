import os
from datetime import datetime

import pytest

from app import db
from app.models import Anomaly, Run


@pytest.fixture
def api(orm_app):
    client = orm_app.test_client()
    token = client.post("/api/login", json={
        "username": "admin", "password": os.environ["ADMIN_PASSWORD"]
    }).get_json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def anomaly(*, code="NO_MATCH", severity="info", state="ouverte", key=None):
    run = Run(status="SUCCESS", started_at="2026-01-01T00:00:00+00:00")
    db.session.add(run)
    db.session.flush()
    row = Anomaly(run_id=run.id, code=code, severity=severity, state=state,
                  tracking_key=key, description="observation", created_at="2026-01-01T00:00:00+00:00")
    db.session.add(row)
    db.session.commit()
    return row


def test_combined_severity_and_state_filters_select_only_matching_rows(api):
    client, headers = api
    selected = anomaly(code="RENAMED", severity="critique", state="acquittée")
    anomaly(code="NO_MATCH", severity="critique", state="ouverte")
    anomaly(code="MATCHED_IP", severity="mineure", state="acquittée")
    response = client.get(f"/api/anomalies?run_id={selected.run_id}&severity=critique&state=acquitt%C3%A9e", headers=headers)
    assert [item["id"] for item in response.get_json()["items"]] == [selected.id]


@pytest.mark.parametrize("query", ["severity=unknown", "state=unknown", "severity=info&severity=majeure",
                                         "state=ouverte&state=résolue"])
def test_invalid_severity_or_state_filter_is_rejected(api, query):
    client, headers = api
    response = client.get("/api/anomalies?" + query, headers=headers)
    assert (response.status_code, response.get_json()) == (400, {"error": "Invalid anomaly filter"})


@pytest.mark.parametrize("state", ["ouverte", "acquittée", "résolue"])
def test_anomaly_list_exposes_lifecycle_state(api, state):
    client, headers = api
    row = anomaly(state=state)
    item = client.get(f"/api/anomalies?run_id={row.run_id}", headers=headers).get_json()["items"][0]
    assert item["state"] == state


def test_acknowledge_then_resolve_dates_and_measures_duration(api):
    client, headers = api
    row = anomaly(key='["NO_MATCH", "vm-1"]')
    acknowledged = client.post(f"/api/anomalies/{row.id}/acknowledge", headers=headers)
    resolved = client.post(f"/api/anomalies/{row.id}/resolve", headers=headers)
    data = resolved.get_json()
    assert (acknowledged.get_json()["state"], acknowledged.get_json()["resolution_seconds"],
            data["state"], data["resolution_seconds"] ==
            (datetime.fromisoformat(data["state_changed_at"]) - datetime.fromisoformat(row.created_at)).total_seconds()) == (
                "acquittée", None, "résolue", True)


@pytest.mark.parametrize("action,state", [("resolve", "ouverte"), ("acknowledge", "acquittée"),
                                            ("resolve", "résolue")])
def test_forbidden_lifecycle_transition_returns_conflict(api, action, state):
    client, headers = api
    row = anomaly(state=state)
    response = client.post(f"/api/anomalies/{row.id}/{action}", headers=headers)
    assert (response.status_code, response.get_json()) == (409, {"error": "Invalid anomaly transition"})


@pytest.mark.parametrize("action", ["acknowledge", "resolve"])
def test_transition_requires_bearer_even_with_web_session(api, action):
    client, _ = api
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    client.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"],
                                "csrf_token": csrf})
    row = anomaly()
    response = client.post(f"/api/anomalies/{row.id}/{action}")
    assert (response.status_code, response.get_json()) == (401, {"error": "Unauthorized"})


@pytest.mark.parametrize("action", ["acknowledge", "resolve"])
def test_transition_rejects_missing_anomaly(api, action):
    client, headers = api
    response = client.post(f"/api/anomalies/999999/{action}", headers=headers)
    assert (response.status_code, response.get_json()) == (404, {"error": "Anomaly not found"})


@pytest.mark.parametrize("action", ["acknowledge", "resolve"])
def test_transition_rejects_body_and_query(api, action):
    client, headers = api
    row = anomaly(state="acquittée" if action == "resolve" else "ouverte")
    for suffix, payload in (("?unexpected=1", {}), ("", {"json": {"state": "résolue"}})):
        response = client.post(f"/api/anomalies/{row.id}/{action}{suffix}", headers=headers, **payload)
        assert (response.status_code, response.get_json()) == (400, {"error": "Invalid anomaly transition"})


def test_old_tracking_observation_cannot_be_changed(api):
    client, headers = api
    old = anomaly(key='["NO_MATCH", "vm-1"]')
    anomaly(key=old.tracking_key)
    response = client.post(f"/api/anomalies/{old.id}/acknowledge", headers=headers)
    assert (response.status_code, response.get_json()) == (409, {"error": "Invalid anomaly transition"})
