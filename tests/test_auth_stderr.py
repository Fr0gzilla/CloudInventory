import importlib
import re
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie

import pytest

from test_auth import auth_app, submit, token_for


@pytest.mark.parametrize("channel", ["web", "api"])
def test_security_events_reach_stderr_once_with_utc_date_and_no_secrets(auth_app, capfd, request, channel):
    import app.auth as auth

    app, credentials = auth_app
    original_handlers = auth.security_log.handlers[:]

    def restore_handlers():
        for handler in auth.security_log.handlers[:]:
            auth.security_log.removeHandler(handler)
            handler.close()
        for handler in original_handlers:
            auth.security_log.addHandler(handler)

    request.addfinalizer(restore_handlers)
    for handler in original_handlers:
        auth.security_log.removeHandler(handler)
    importlib.reload(auth)
    client = app.test_client()
    csrf = token_for(client)
    capfd.readouterr()
    username = "distinct-unknown-account"
    password = "distinct-incorrect-password"

    def attempt(client, username, password):
        if channel == "web":
            return submit(client, password, token_for(client), username=username)
        return client.post("/api/login", json={"username": username, "password": password})

    before = datetime.now(UTC) - timedelta(seconds=1)
    for _ in range(5):
        assert attempt(client, username, password).status_code == 401
    assert attempt(client, username, password).status_code == 429
    success = attempt(client, "admin", credentials["ADMIN_PASSWORD"])
    assert success.status_code == (302 if channel == "web" else 200)
    after = datetime.now(UTC)
    cookies = SimpleCookie()
    cookies.load(success.headers.get("Set-Cookie", ""))
    secrets = [username, password, credentials["ADMIN_PASSWORD"], csrf]
    secrets.extend(cookie.value for cookie in cookies.values())
    if channel == "api":
        secrets.append(success.get_json()["access_token"])
    text = capfd.readouterr().err
    lines = [line for line in text.splitlines() if "security.auth" in line]
    assert len(lines) == 7
    assert [re.search(r"result=(\w+)", line).group(1) for line in lines] == ["invalid"] * 5 + ["limited", "success"]
    for line in lines:
        assert re.fullmatch(
            rf"\d{{4}}-\d\d-\d\dT\d\d:\d\d:\d\dZ (WARNING|INFO) security\.auth "
            rf"auth result=(invalid|limited|success) channel={channel} ip=127\.0\.0\.1", line
        )
        timestamp = datetime.strptime(line.split()[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        assert before <= timestamp <= after
    assert all(value not in text for value in secrets)
    importlib.reload(auth)
    next_client = app.test_client()
    assert attempt(next_client, "admin", credentials["ADMIN_PASSWORD"]).status_code == (302 if channel == "web" else 200)
    assert len([line for line in capfd.readouterr().err.splitlines() if "security.auth" in line]) == 1
