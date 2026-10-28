import logging
from http.cookies import SimpleCookie

import pytest

from test_auth import auth_app, submit, token_for


def copied_cookie(app, response):
    cookie = SimpleCookie()
    cookie.load(response.headers["Set-Cookie"])
    return cookie[app.config["SESSION_COOKIE_NAME"]].value


def test_cookie_remains_valid_during_normal_navigation(auth_app):
    app, credentials = auth_app
    client = app.test_client()
    assert submit(client, credentials["ADMIN_PASSWORD"], token_for(client)).status_code == 302
    assert client.get("/health").get_json() == {"status": "ok"}


def test_logout_revokes_every_copy_of_session_cookie(auth_app):
    app, credentials = auth_app
    client = app.test_client()
    cookie = copied_cookie(app, submit(client, credentials["ADMIN_PASSWORD"], token_for(client)))
    other = app.test_client()
    other.set_cookie(app.config["SESSION_COOKIE_NAME"], cookie)
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    assert client.post("/logout", data={"csrf_token": csrf}).status_code == 302
    for browser in (client, other):
        response = browser.get("/health")
        assert response.status_code == 302 and response.location.startswith("/login")


def test_copied_cookie_is_rejected_after_factory_recreation(auth_app):
    app, credentials = auth_app
    client = app.test_client()
    cookie = copied_cookie(app, submit(client, credentials["ADMIN_PASSWORD"], token_for(client)))
    from app import create_app

    new_app = create_app()
    new_client = new_app.test_client()
    new_client.set_cookie(new_app.config["SESSION_COOKIE_NAME"], cookie)
    response = new_client.get("/health")
    assert response.status_code == 302 and response.location.startswith("/login")


@pytest.mark.parametrize("elapsed,visits", [(8 * 3600, tuple(range(900, 8 * 3600, 900))), (1800, ())], ids=["absolute-eight-hours-despite-navigation", "idle-thirty-minutes"])
def test_session_expires_at_absolute_or_idle_deadline(auth_app, monkeypatch, elapsed, visits):
    import app.auth as auth

    clock = [100000.0]
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])
    app, credentials = auth_app
    client = app.test_client()
    assert submit(client, credentials["ADMIN_PASSWORD"], token_for(client)).status_code == 302
    for visit in visits:
        clock[0] = 100000.0 + visit
        assert client.get("/health").status_code == 200
    clock[0] = 100000.0 + elapsed
    response = client.get("/health")
    assert response.status_code == 302 and response.location.startswith("/login")


@pytest.mark.parametrize("channel", ["web", "api"])
def test_auth_events_have_one_issue_per_request_without_credentials(auth_app, caplog, channel):
    app, credentials = auth_app
    client = app.test_client()
    caplog.set_level(logging.INFO, logger="security.auth")
    raw_user = "unrecognized-unique-user"
    raw_password = "incorrect" + "-unique-credential"

    def attempt(username, password):
        if channel == "web":
            return submit(client, password, csrf, username=username)
        return client.post("/api/login", json={"username": username, "password": password})

    csrf = token_for(client) if channel == "web" else None
    expected = [("invalid", 401)] * 5 + [("limited", 429)]
    for issue, code in expected:
        before = len(caplog.records)
        response = attempt(raw_user, raw_password)
        events = [record for record in caplog.records[before:] if record.name == "security.auth"]
        assert (response.status_code, len(events), f"result={issue}" in events[0].getMessage(),
                f"channel={channel}" in events[0].getMessage()) == (code, 1, True, True)
    text = " ".join(record.getMessage() for record in caplog.records if record.name == "security.auth")
    assert all(value not in text for value in (raw_user, raw_password, credentials["ADMIN_PASSWORD"], csrf or "secret-not-present"))


@pytest.mark.parametrize("channel", ["web", "api"])
def test_success_and_web_logout_emit_single_safe_event(auth_app, caplog, channel):
    app, credentials = auth_app
    client = app.test_client()
    caplog.set_level(logging.INFO, logger="security.auth")
    if channel == "web":
        csrf = token_for(client)
        response = submit(client, credentials["ADMIN_PASSWORD"], csrf)
        expected_status = 302
    else:
        response = client.post("/api/login", json={"username": "admin", "password": credentials["ADMIN_PASSWORD"]})
        expected_status = 200
    events = [record.getMessage() for record in caplog.records if record.name == "security.auth"]
    assert (response.status_code, len(events), "result=success" in events[0], f"channel={channel}" in events[0]) == (expected_status, 1, True, True)
    if channel == "api":
        jwt = response.get_json()["access_token"]
        assert client.get("/api/runs", headers={"Authorization": f"Bearer {jwt}"}).status_code != 401
    else:
        jwt = copied_cookie(app, response)
        with client.session_transaction() as session:
            logout_csrf = session["csrf_token"]
        assert client.post("/logout", data={"csrf_token": logout_csrf}).status_code == 302
        events = [record.getMessage() for record in caplog.records if record.name == "security.auth"]
        assert len(events) == 2 and "result=logout" in events[1] and "channel=web" in events[1]
    assert all(secret not in " ".join(events) for secret in (credentials["ADMIN_PASSWORD"], jwt))
