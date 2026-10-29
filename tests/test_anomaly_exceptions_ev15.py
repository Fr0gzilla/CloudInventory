import json
import os
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

import pytest

from app import db
from app.anomaly_exceptions import anomaly_coverage
from app.models import Anomaly, AnomalyException, Run
from collector.inventory_runner import run_inventory


class Source:
    def __init__(self, records):
        self.records = records

    def collect(self):
        return self.records


def unmatched_run():
    return run_inventory(Source([{"vm_id": "vm-1", "vm_name": "unmatched",
                                  "type": "qemu"}]), Source([]))


def navigation_badge(client):
    class NavigationParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_link = False
            self.in_badge = False
            self.badge = None

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == "a" and attributes.get("href") == "/anomalies":
                self.in_link = True
            elif self.in_link and tag == "span" and "badge" in attributes.get("class", "").split():
                self.in_badge = True

        def handle_data(self, data):
            if self.in_badge:
                self.badge = (self.badge or "") + data

        def handle_endtag(self, tag):
            if tag == "span":
                self.in_badge = False
            elif tag == "a" and self.in_link:
                self.in_link = False

    parser = NavigationParser()

    response = client.get("/")
    assert response.status_code == 200
    parser.feed(response.get_data(as_text=True))
    return parser.badge.strip() if parser.badge is not None else None


@pytest.fixture
def scenario(orm_app):
    client = orm_app.test_client()
    run = Run(status="SUCCESS", started_at="2026-01-01T00:00:00+00:00")
    db.session.add(run)
    db.session.flush()
    row = Anomaly(run_id=run.id, code="NO_MATCH", severity="info", state="ouverte",
                  tracking_key=json.dumps(["NO_MATCH", "vm-1"]), description="observation",
                  created_at="2026-01-01T00:00:00+00:00")
    db.session.add(row)
    db.session.commit()
    return client, row


def payload(**overrides):
    data = {"code": "NO_MATCH", "target": "vm-1", "justification": "maintenance",
            "expires_at": (datetime.now(UTC) + timedelta(days=2)).isoformat()}
    data.update(overrides)
    return data


def bearer(client):
    token = client.post("/api/login", json={"username": "admin",
                                              "password": os.environ["ADMIN_PASSWORD"]}).get_json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def web_login(client):
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    client.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"],
                                 "csrf_token": csrf})
    with client.session_transaction() as session:
        return session["csrf_token"]


def test_api_creation_lists_server_author_and_preserves_anomaly_state(scenario):
    client, row = scenario
    headers = bearer(client)
    response = client.post("/api/anomaly-exceptions", json=payload(), headers=headers)
    assert response.status_code == 201
    assert response.get_json()["author"] == "admin"
    assert client.get("/api/anomaly-exceptions", headers=headers).get_json()["items"][0]["target"] == "vm-1"
    assert db.session.get(Anomaly, row.id).state == "ouverte"


@pytest.mark.parametrize("changes", [
    {"code": "UNKNOWN"}, {"target": ""}, {"target": "vm-2"},
    {"justification": "  "}, {"expires_at": "2030-01-01T00:00:00"},
    {"expires_at": "2020-01-01T00:00:00+00:00"}, {"author": "forged"},
])
def test_api_rejects_invalid_exception_fields(scenario, changes):
    client, _ = scenario
    response = client.post("/api/anomaly-exceptions", json=payload(**changes), headers=bearer(client))
    assert (response.status_code, response.get_json()) == (400, {"error": "Invalid exception"})


def test_active_coverage_matches_exact_code_and_target_without_changing_state(scenario):
    _, row = scenario
    now = datetime.now(UTC)
    matching = AnomalyException(code="NO_MATCH", target="vm-1", justification="maintenance",
                                expires_at=(now + timedelta(hours=1)).isoformat(), author="admin")
    other = AnomalyException(code="MATCHED_IP", target="vm-1", justification="other",
                             expires_at=(now + timedelta(hours=1)).isoformat(), author="admin")
    db.session.add(matching)
    db.session.flush()
    db.session.add(other)
    db.session.commit()
    assert anomaly_coverage([row], now=now) == {row.id: matching.id}
    assert db.session.get(Anomaly, row.id).state == "ouverte"
    assert anomaly_coverage([row], now=now + timedelta(hours=1)) == {row.id: None}


def test_ambiguous_tracking_group_is_never_covered(scenario):
    _, row = scenario
    duplicate = Anomaly(run_id=row.run_id, code=row.code, severity="info", state="ouverte",
                        tracking_key=row.tracking_key, description="second",
                        created_at=row.created_at)
    db.session.add(duplicate)
    db.session.flush()
    db.session.add(AnomalyException(code="NO_MATCH", target="vm-1",
                        justification="maintenance", expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
                        author="admin"))
    db.session.commit()
    assert anomaly_coverage([row, duplicate]) == {row.id: None, duplicate.id: None}


def test_web_acknowledge_then_resolve_and_reject_invalid_transition(scenario):
    client, row = scenario
    csrf = web_login(client)
    path = f"/anomalies/{row.id}"
    client.post(path + "/acknowledge", data={"csrf_token": csrf})
    assert db.session.get(Anomaly, row.id).state == "acquittée"
    client.post(path + "/resolve", data={"csrf_token": csrf})
    assert db.session.get(Anomaly, row.id).state == "résolue"
    client.post(path + "/acknowledge", data={"csrf_token": csrf})
    assert db.session.get(Anomaly, row.id).state == "résolue"


def test_web_exception_creation_and_deletion_immediately_change_visible_coverage(scenario):
    client, row = scenario
    csrf = web_login(client)
    response = client.post("/anomaly-exceptions", data={**payload(), "csrf_token": csrf})
    assert response.status_code == 302
    created = AnomalyException.query.one()
    assert anomaly_coverage([row])[row.id] == created.id
    client.post(f"/anomaly-exceptions/{created.id}/delete", data={"csrf_token": csrf})
    assert anomaly_coverage([row])[row.id] is None


@pytest.mark.parametrize("path", ["/anomalies/1/acknowledge", "/anomalies/1/resolve",
                                    "/anomaly-exceptions", "/anomaly-exceptions/1/delete"])
def test_web_mutations_require_session_and_valid_csrf(scenario, path):
    client, row = scenario
    path = path.replace("/1/", f"/{row.id}/")
    assert client.post(path, data=payload()).status_code in (302, 401, 403)
    web_login(client)
    assert client.post(path, data={**payload(), "csrf_token": "invalid"}).status_code == 400
    assert db.session.get(Anomaly, row.id).state == "ouverte"
    assert AnomalyException.query.count() == 0


@pytest.mark.parametrize("method,path", [("get", "/api/anomaly-exceptions"),
                                          ("post", "/api/anomaly-exceptions"),
                                          ("delete", "/api/anomaly-exceptions/1")])
def test_exception_api_rejects_anonymous_requests(scenario, method, path):
    client, _ = scenario
    response = getattr(client, method)(path, json=payload() if method == "post" else None)
    assert response.status_code == 401


def test_api_admin_deletes_exception_and_restores_coverage(scenario):
    client, row = scenario
    headers = bearer(client)
    created = client.post("/api/anomaly-exceptions", json=payload(), headers=headers).get_json()
    assert client.delete(f"/api/anomaly-exceptions/{created['id']}", headers=headers).status_code in (200, 204)
    assert anomaly_coverage([row])[row.id] is None


def test_page_shows_coverage_without_hiding_anomaly_and_restores_at_expiration(scenario, monkeypatch):
    client, row = scenario
    web_login(client)
    now = datetime.now(UTC)
    exception = AnomalyException(code="NO_MATCH", target="vm-1", justification="maintenance",
                                 expires_at=(now + timedelta(hours=1)).isoformat(), author="admin")
    db.session.add(exception)
    db.session.commit()
    current = client.get("/anomalies").get_data(as_text=True)
    assert f"couverte par exception #{exception.id}" in current.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    monkeypatch.setattr("app.anomaly_exceptions.datetime", type("Clock", (), {
        "now": staticmethod(lambda tz: now + timedelta(hours=2)),
        "fromisoformat": staticmethod(datetime.fromisoformat),
    }))
    expired = client.get("/anomalies").get_data(as_text=True)
    assert "ouverte ; non couverte" in expired.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    assert db.session.get(Anomaly, row.id).state == "ouverte"


def test_page_escapes_exception_justification(scenario):
    client, _ = scenario
    csrf = web_login(client)
    client.post("/anomaly-exceptions", data={**payload(justification="<script>alert(1)</script>"),
                                            "csrf_token": csrf})
    table = client.get("/anomalies").get_data(as_text=True).split('id="exceptions-title"', 1)[1]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in table and "<script>alert(1)</script>" not in table


def test_active_exception_sends_zero_anomalies_to_notification_after_real_run(orm_app, monkeypatch):
    sent = []
    monkeypatch.setattr("collector.inventory_runner.notify_anomalies",
                        lambda run_id, count, finished_at: sent.append((run_id, count)))
    first = unmatched_run()
    assert first.status == "SUCCESS"
    assert sent == [(first.id, 1)]
    db.session.add(AnomalyException(code="NO_MATCH", target="vm-1", justification="maintenance",
                                    expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
                                    author="admin"))
    db.session.commit()
    second = unmatched_run()
    assert second.status == "SUCCESS"
    assert sent[-1] == (second.id, 0)


def test_expired_exception_restores_real_run_notification(orm_app, monkeypatch):
    sent = []
    monkeypatch.setattr("collector.inventory_runner.notify_anomalies",
                        lambda run_id, count, finished_at: sent.append((run_id, count)))
    from collector import inventory_runner

    clock = [datetime.now(UTC)]
    monkeypatch.setattr(inventory_runner, "_now", lambda: clock[0].isoformat())
    exception = AnomalyException(code="NO_MATCH", target="vm-1", justification="maintenance",
                                  expires_at=(clock[0] + timedelta(days=1)).isoformat(), author="admin")
    db.session.add(exception)
    db.session.commit()
    covered = unmatched_run()
    assert covered.status == "SUCCESS", covered.error_message
    assert sent[-1] == (covered.id, 0)
    clock[0] += timedelta(days=2)
    db.session.rollback()
    restored = unmatched_run()
    assert restored.status == "SUCCESS", restored.error_message
    assert sent[-1] == (restored.id, 1)


def test_navigation_badge_disappears_for_active_exception_and_returns_after_expiration(orm_app):
    client = orm_app.test_client()
    web_login(client)
    db.session.rollback()
    run = unmatched_run()
    assert run.status == "SUCCESS"
    assert navigation_badge(client) == "1"
    exception = AnomalyException(code="NO_MATCH", target="vm-1", justification="maintenance",
                                 expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(), author="admin")
    db.session.add(exception)
    db.session.commit()
    assert navigation_badge(client) is None
    exception.expires_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    db.session.commit()
    assert navigation_badge(client) == "1"
