import pytest

from test_auth import auth_app, submit, token_for


def api(client, username, password, address="192.0.2.10", headers=None):
    return client.post("/api/login", json={"username": username, "password": password},
                       headers=headers or {}, environ_overrides={"REMOTE_ADDR": address})


def test_other_names_from_independent_clients_do_not_lock_admin(auth_app):
    app, credentials = auth_app
    for _ in range(5):
        assert api(app.test_client(), "someone-else", "incorrect").status_code == 401
    response = api(app.test_client(), "admin", credentials["ADMIN_PASSWORD"])
    assert response.status_code == 200 and response.get_json()["access_token"]


def test_admin_delay_rejects_immediate_success_then_admits_after_clock(auth_app, monkeypatch):
    import app.auth as auth

    clock = [100000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
    app, credentials = auth_app
    for _ in range(5):
        assert api(app.test_client(), "admin", "incorrect").status_code == 401
    blocked = api(app.test_client(), "admin", credentials["ADMIN_PASSWORD"])
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
    clock[0] += int(blocked.headers["Retry-After"]) + 1
    assert api(app.test_client(), "admin", credentials["ADMIN_PASSWORD"]).status_code == 200


def test_admin_delays_grow_and_remain_bounded_after_long_sequence(auth_app, monkeypatch):
    import app.auth as auth

    clock = [100000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
    app, _ = auth_app
    for _ in range(5):
        assert api(app.test_client(), "admin", "incorrect").status_code == 401
    delays = []
    for _ in range(12):
        blocked = api(app.test_client(), "admin", "incorrect")
        assert blocked.status_code == 429
        delay = int(blocked.headers["Retry-After"])
        delays.append(delay)
        clock[0] += delay + 1
        assert api(app.test_client(), "admin", "incorrect").status_code == 401
    assert delays == [30, 60, 120, 240, 480] + [900] * 7


def test_distinct_address_unaffected_by_spoofed_forwarding_headers(auth_app):
    app, credentials = auth_app
    for number in range(5):
        headers = {"X-Forwarded-For": f"198.51.100.{number + 1}",
                   "X-Real-IP": f"198.51.100.{number + 1}",
                   "Forwarded": f"for=198.51.100.{number + 1}"}
        assert api(app.test_client(), "admin", "incorrect", headers=headers).status_code == 401
    assert api(app.test_client(), "admin", credentials["ADMIN_PASSWORD"],
               headers={"X-Forwarded-For": "198.51.100.99"}).status_code == 429
    assert api(app.test_client(), "admin", credentials["ADMIN_PASSWORD"],
               address="192.0.2.11").status_code == 200


def test_thousand_distinct_names_are_globally_bounded_per_address(auth_app):
    app, credentials = auth_app
    statuses = [api(app.test_client(), f"stranger-{number}", "incorrect").status_code for number in range(1000)]
    assert statuses[:20] == [401] * 20 and statuses[20:] == [429] * 980
    assert api(app.test_client(), "admin", credentials["ADMIN_PASSWORD"]).status_code == 200


@pytest.mark.parametrize("channel", ["web", "api"])
def test_web_and_api_admin_rejections_include_retry_after(auth_app, channel):
    app, credentials = auth_app
    for _ in range(5):
        assert api(app.test_client(), "admin", "incorrect").status_code == 401
    client = app.test_client()
    response = (submit(client, credentials["ADMIN_PASSWORD"], token_for(client),
                       environ_overrides={"REMOTE_ADDR": "192.0.2.10"}) if channel == "web"
                else api(client, "admin", credentials["ADMIN_PASSWORD"]))
    assert response.status_code == 429 and int(response.headers["Retry-After"]) > 0
