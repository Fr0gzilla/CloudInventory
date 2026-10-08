"""T011 — C1 canaux/configuration, C2 commit/échecs, C3 UI, C4 HTML sûr.

Tous les transports sont remplacés ; une garde socket interdit le réseau réel.
"""
import json
import re
import secrets
import socket
from contextlib import nullcontext
from datetime import datetime
from html import escape
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from app import notifications
from app.models import Anomaly, Run
from collector.inventory_runner import run_inventory
from collector.mock_netbox import fetch_mock_ipam
from collector.mock_virtualisation import fetch_mock_vms


@pytest.fixture(autouse=True)
def transports(monkeypatch, app):
    calls = SimpleNamespace(smtp=[], messages=[], tls=[], login=[], webhook=[],
                            network=[], smtp_error=False, webhook_error=False,
                            status=204, observe=lambda: None)
    settings = {
        "SMTP_ENABLED": "true", "SMTP_HOST": "smtp.example.invalid",
        "SMTP_PORT": "2525", "SMTP_USE_TLS": "true",
        "SMTP_USERNAME": "dummy-user", "SMTP_PASSWORD": secrets.token_hex(16),
        "SMTP_FROM": "sender@example.invalid", "SMTP_TO": "ops@example.invalid",
        "WEBHOOK_URL": "https://example.invalid/hook",
    }
    for name, value in settings.items():
        app.config.pop(name, None)
        monkeypatch.setenv(name, value)
    calls.password = settings["SMTP_PASSWORD"]

    def no_network(*args, **kwargs):
        calls.network.append(True)
        raise AssertionError("réseau réel interdit")

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.observe()
            calls.smtp.append((host, port, timeout))
            if calls.smtp_error:
                raise OSError("dummy-password")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self, context):
            calls.tls.append(context)

        def login(self, username, password):
            calls.login.append((username, password))

        def send_message(self, message):
            calls.messages.append(message)

    def fake_urlopen(request, timeout):
        calls.observe()
        calls.webhook.append((request, timeout))
        if calls.webhook_error:
            raise OSError("dummy-password")
        return nullcontext(SimpleNamespace(status=calls.status))

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(notifications.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notifications, "urlopen", fake_urlopen)
    yield calls
    assert calls.network == []


def _inventory():
    return run_inventory(collect_vms=fetch_mock_vms, collect_ipam=fetch_mock_ipam)


def _email_html(message):
    return message.get_payload(decode=True).decode(message.get_content_charset())


def test_smtp_uses_environment_and_sends_html(db, transports):
    run = _inventory()
    assert transports.smtp == [("smtp.example.invalid", 2525, 15)]
    assert len(transports.tls) == 1
    assert transports.login == [("dummy-user", transports.password)]
    assert len(transports.messages) == 1
    message = transports.messages[0]
    assert message.get_content_type() == "text/html"
    assert (message["From"], message["To"]) == (
        "sender@example.invalid", "ops@example.invalid")
    assert f"Run #{run.id}" in _email_html(message)


def test_webhook_posts_json_for_successful_run(db, transports):
    run = _inventory()
    assert len(transports.webhook) == 1
    request, timeout = transports.webhook[0]
    payload = json.loads(request.data)
    assert (request.full_url, request.get_method(), request.get_header("Content-type"), timeout) == (
        "https://example.invalid/hook", "POST", "application/json", 10)
    assert (payload["run_id"], payload["status"], payload["anomaly_count"]) == (
        run.id, "SUCCESS", Anomaly.query.filter_by(run_id=run.id).count())


@pytest.mark.parametrize("name,value,channel", [
    ("SMTP_ENABLED", "false", "smtp"), ("SMTP_TO", "", "smtp"),
    ("WEBHOOK_URL", "", "webhook"),
])
def test_unconfigured_channel_does_not_send(db, monkeypatch, transports, name, value, channel):
    monkeypatch.setenv(name, value)
    _inventory()
    assert getattr(transports, channel) == []
    assert getattr(transports, "webhook" if channel == "smtp" else "smtp")


def test_smtp_without_tls_or_credentials(db, monkeypatch, transports):
    monkeypatch.setenv("SMTP_USE_TLS", "false")
    monkeypatch.setenv("SMTP_USERNAME", "")
    monkeypatch.setenv("SMTP_PASSWORD", "")
    _inventory()
    assert (len(transports.messages), transports.tls, transports.login) == (1, [], [])


def test_notifications_only_after_success_commit(db, transports):
    committed = []
    observed = []
    session = db.session()

    def after_commit(session):
        committed.extend(obj.status for obj in session.identity_map.values() if isinstance(obj, Run))

    def observe():
        observed.append(tuple(committed))

    event.listen(session, "after_commit", after_commit)
    transports.observe = observe
    try:
        run = _inventory()
    finally:
        event.remove(session, "after_commit", after_commit)
    assert run.status == "SUCCESS"
    assert observed == [("RUNNING", "SUCCESS"), ("RUNNING", "SUCCESS")]


def test_failed_collection_does_not_notify(db, transports):
    def fail():
        raise RuntimeError("source indisponible")

    run = run_inventory(collect_vms=fail, collect_ipam=lambda: [])
    assert (run.status, transports.smtp, transports.webhook) == ("FAIL", [], [])


@pytest.mark.parametrize("status", ["RUNNING", "FAIL"])
def test_non_success_run_does_not_notify(db, run, transports, status):
    run.status = status
    db.session.add(Anomaly(run_id=run.id, code="NO_MATCH", detected_at=datetime(2026, 1, 1)))
    db.session.commit()
    notifications.notify_run(run)
    assert (transports.smtp, transports.webhook) == ([], [])


def test_success_without_anomalies_does_not_notify(db, transports):
    run = run_inventory(collect_vms=lambda: [], collect_ipam=lambda: [])
    assert (run.status, transports.smtp, transports.webhook) == ("SUCCESS", [], [])


@pytest.mark.parametrize("channel", ["smtp", "webhook"])
def test_transport_errors_are_logged_without_affecting_run(db, transports, caplog, channel):
    setattr(transports, channel + "_error", True)
    run = _inventory()
    db.session.expire_all()
    assert (db.session.get(Run, run.id).status, run.error_message) == ("SUCCESS", None)
    assert Anomaly.query.filter_by(run_id=run.id).count() > 0
    assert transports.smtp and transports.webhook
    assert any(channel in record.message.lower() for record in caplog.records)
    assert "dummy-password" not in caplog.text


@pytest.mark.parametrize("name,value,channel", [
    ("SMTP_PORT", "invalid", "smtp"),
    ("WEBHOOK_URL", "file:///tmp/not-a-webhook", "webhook"),
])
def test_invalid_channel_configuration_is_logged(db, monkeypatch, transports, caplog, name, value, channel):
    monkeypatch.setenv(name, value)
    run = _inventory()
    assert run.status == "SUCCESS"
    assert getattr(transports, channel) == []
    assert any(channel in record.message.lower() for record in caplog.records)


def test_webhook_http_error_is_logged_without_affecting_run(db, transports, caplog):
    transports.status = 503
    run = _inventory()
    assert run.status == "SUCCESS"
    assert any("webhook" in record.message.lower() for record in caplog.records)


@pytest.mark.parametrize("field", ["vm_name", "dns_name", "description", "code"])
def test_email_escapes_each_source_value(app, field):
    payload = '<b>x</b>' if field == "vm_name" else '<img src=x onerror="alert(1)"> & \'x\''
    run = SimpleNamespace(id=1, status="SUCCESS", end_date=datetime(2026, 1, 1),
                          matched_name_count=0, matched_fqdn_count=0,
                          matched_ip_count=0, no_match_count=1)
    anomaly = SimpleNamespace(code="NO_MATCH", description="missing")
    asset = SimpleNamespace(vm_name="vm")
    ipam = SimpleNamespace(dns_name="vm.example.invalid")
    target = asset if field == "vm_name" else ipam if field == "dns_name" else anomaly
    setattr(target, field, payload)
    body = notifications.render_email(run, [(anomaly, asset, ipam)])
    assert escape(payload, quote=True) in body
    assert payload not in body


def test_email_escapes_ipam_only_anomalies(app):
    run = SimpleNamespace(id=1, status="SUCCESS", end_date=None,
                          matched_name_count=0, matched_fqdn_count=0,
                          matched_ip_count=0, no_match_count=0)
    anomaly = SimpleNamespace(code="DUPLICATE_DNS", description="<b>duplicate</b>")
    ipam = SimpleNamespace(dns_name="<b>dns</b>")
    body = notifications.render_email(run, [(anomaly, None, ipam)])
    assert "&lt;b&gt;dns&lt;/b&gt;" in body
    assert "&lt;b&gt;duplicate&lt;/b&gt;" in body


def _authenticated_client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "admin"
        session["_fresh"] = True
    return client


def test_badge_and_banner_show_latest_anomaly_count(app):
    with app.app_context():
        run = _inventory()
        count = Anomaly.query.filter_by(run_id=run.id).count()
    response = _authenticated_client(app).get("/inventory")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert re.search(r'badge rounded-pill bg-danger[^>]*>' + str(count) + r'<', html)
    assert re.search(r'<div class="[^"]*ci-alert-anomalies\b', html)
    assert f"<strong>{count} anomalies</strong>" in html


def test_empty_latest_run_hides_previous_notifications(app):
    with app.app_context():
        _inventory()
        run_inventory(collect_vms=lambda: [], collect_ipam=lambda: [])
    response = _authenticated_client(app).get("/inventory")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert not re.search(r'<div class="[^"]*ci-alert-anomalies\b', html)
    assert "badge rounded-pill bg-danger" not in html


def test_anonymous_page_does_not_disclose_notifications(app):
    with app.app_context():
        _inventory()
    response = app.test_client().get("/login")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert not re.search(r'<div class="[^"]*ci-alert-anomalies\b', html)
    assert "badge rounded-pill bg-danger" not in html
