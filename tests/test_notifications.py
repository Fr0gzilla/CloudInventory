import json
import os
import socket
from html.parser import HTMLParser

import pytest

from app import db
from app.models import Anomaly, Run
from app import notifications
from collector.inventory_runner import run_inventory


class Alerts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.badges = []
        self.banners = []
        self._target = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "span" and "badge" in attrs.get("class", "").split():
            self._target = self.badges
            self._text = []
        elif tag == "div" and attrs.get("role") == "alert":
            self._target = self.banners
            self._text = []

    def handle_data(self, data):
        if self._target is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if self._target is not None and tag in ("span", "div"):
            self._target.append("".join(self._text).strip())
            self._target = None


@pytest.fixture
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network access forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(notifications.smtplib, "SMTP_SSL", forbidden)
    monkeypatch.setattr(notifications, "urlopen", forbidden)
    for key in ("SMTP_ENABLED", "WEBHOOK_ENABLED", "USE_MOCK_SMTP", "USE_MOCK_WEBHOOK"):
        monkeypatch.delenv(key, raising=False)


def logged_client(app):
    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"], "csrf_token": token})
    assert response.status_code == 302
    return client


def alerts(response):
    parsed = Alerts()
    parsed.feed(response.get_data(as_text=True))
    return parsed


def persisted_run(status="SUCCESS", count=0):
    run = Run(status=status, started_at="2026-01-01T00:00:00", finished_at="2026-01-01T00:01:00")
    db.session.add(run)
    db.session.flush()
    for _ in range(count):
        db.session.add(Anomaly(run_id=run.id, code="NO_MATCH", description="test",
                               created_at="2026-01-01T00:01:00"))
        db.session.flush()
    db.session.commit()
    return run.id


def test_mock_channels_receive_successful_run_anomalies(orm_app, offline, monkeypatch):
    sent_mail, sent_webhooks = [], []
    monkeypatch.setenv("SMTP_ENABLED", "true")
    monkeypatch.setenv("WEBHOOK_ENABLED", "true")
    monkeypatch.setattr(notifications, "_mock_smtp", sent_mail.append)
    monkeypatch.setattr(notifications, "_mock_webhook", sent_webhooks.append)

    run = run_inventory()
    count = db.session.query(Anomaly).filter_by(run_id=run.id).count()

    assert count > 0
    assert len(sent_mail) == 1
    assert str(count) in sent_mail[0].get_content()
    assert sent_webhooks == [{"run_id": run.id, "status": "SUCCESS",
                              "anomaly_count": count, "finished_at": run.finished_at}]


def test_zero_anomalies_never_call_transports(offline, monkeypatch):
    monkeypatch.setenv("SMTP_ENABLED", "true")
    monkeypatch.setenv("WEBHOOK_ENABLED", "true")
    monkeypatch.setattr(notifications, "_mock_smtp", lambda _: pytest.fail("unexpected mail"))
    monkeypatch.setattr(notifications, "_mock_webhook", lambda _: pytest.fail("unexpected webhook"))
    notifications.notify_anomalies(9, 0, "2026-01-01")


def test_disabled_channels_never_call_transports(offline, monkeypatch):
    monkeypatch.setattr(notifications, "_mock_smtp", lambda _: pytest.fail("unexpected mail"))
    monkeypatch.setattr(notifications, "_mock_webhook", lambda _: pytest.fail("unexpected webhook"))
    notifications.notify_anomalies(9, 2, "2026-01-01")


def test_real_smtp_uses_stubbed_client(offline, monkeypatch):
    sent = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self, username, password):
            sent.append((username, password))

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setenv("SMTP_ENABLED", "true")
    monkeypatch.setenv("USE_MOCK_SMTP", "false")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "from@example.test")
    monkeypatch.setenv("SMTP_TO", "to@example.test")
    monkeypatch.setenv("SMTP_USERNAME", "test-user")
    monkeypatch.setenv("SMTP_PASSWORD", "synthetic-password")
    monkeypatch.setattr(notifications.smtplib, "SMTP_SSL", lambda *a, **kw: Client())

    notifications.notify_anomalies(7, 3, "2026-01-01")

    assert sent[0] == ("test-user", "synthetic-password")
    assert sent[1]["To"] == "to@example.test"
    assert "3 anomalies" in sent[1].get_content()


def test_real_webhook_posts_json_via_stubbed_http(offline, monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b"ok"

    def stub(request, timeout):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setenv("WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("USE_MOCK_WEBHOOK", "false")
    monkeypatch.setenv("WEBHOOK_URL", "https://example.test/notify")
    monkeypatch.setattr(notifications, "urlopen", stub)

    notifications.notify_anomalies(7, 3, "2026-01-01")

    request, timeout = requests[0]
    assert len(requests) == 1
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {"run_id": 7, "status": "SUCCESS",
                                        "anomaly_count": 3, "finished_at": "2026-01-01"}
    assert timeout > 0


@pytest.mark.parametrize("broken", ["smtp", "webhook"])
def test_delivery_failure_does_not_change_committed_run_or_other_channel(
        orm_app, offline, monkeypatch, caplog, broken):
    token = "synthetic-sensitive-marker"
    sent = []

    def fail(_):
        raise RuntimeError(token)

    monkeypatch.setenv("SMTP_ENABLED", "true")
    monkeypatch.setenv("WEBHOOK_ENABLED", "true")
    monkeypatch.setattr(notifications, "_mock_smtp", fail if broken == "smtp" else sent.append)
    monkeypatch.setattr(notifications, "_mock_webhook", fail if broken == "webhook" else sent.append)

    run = run_inventory()

    assert db.session.get(Run, run.id).status == "SUCCESS"
    assert len(sent) == 1
    assert token not in caplog.text


def test_failed_run_does_not_notify(orm_app, offline, monkeypatch):
    sent = []
    monkeypatch.setenv("SMTP_ENABLED", "true")
    monkeypatch.setenv("WEBHOOK_ENABLED", "true")
    monkeypatch.setattr(notifications, "_mock_smtp", sent.append)
    monkeypatch.setattr(notifications, "_mock_webhook", sent.append)

    class BrokenSource:
        def collect(self):
            raise RuntimeError("synthetic collection failure")

    run = run_inventory(virtualization_source=BrokenSource())

    assert db.session.get(Run, run.id).status == "FAIL"
    assert sent == []


def test_badge_and_banner_show_latest_successful_run_only(orm_app):
    persisted_run(count=4)
    latest = persisted_run(count=2)
    persisted_run(status="FAIL", count=7)

    parsed = alerts(logged_client(orm_app).get("/"))

    assert parsed.badges == ["2"]
    assert len(parsed.banners) == 1
    assert "2 anomalie(s)" in parsed.banners[0]
    assert f'/runs/{latest}' in logged_client(orm_app).get("/").get_data(as_text=True)


@pytest.mark.parametrize("counts", [[], [0]])
def test_no_badge_or_banner_without_successful_anomalies(orm_app, counts):
    for count in counts:
        persisted_run(count=count)

    parsed = alerts(logged_client(orm_app).get("/"))

    assert parsed.badges == []
    assert parsed.banners == []


def test_anomaly_alerts_not_exposed_to_unauthenticated_clients(orm_app):
    persisted_run(count=3)
    response = orm_app.test_client().get("/")

    parsed = alerts(response)
    assert parsed.badges == []
    assert parsed.banners == []
