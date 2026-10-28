from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from test_auth import auth_app


@pytest.mark.parametrize("channel", ["web", "api"])
def test_simultaneous_requests_from_one_source_admit_at_most_five(auth_app, channel):
    app, _ = auth_app
    participants = 12
    gate = Barrier(participants, timeout=15)

    def attempt(_):
        client = app.test_client()
        if channel == "web":
            client.get("/login")
            with client.session_transaction() as session:
                csrf = session["csrf_token"]
        gate.wait(timeout=15)
        if channel == "web":
            response = client.post("/login", data={"username": "admin", "password": "incorrect", "csrf_token": csrf},
                                   environ_overrides={"REMOTE_ADDR": "192.0.2.10"})
        else:
            response = client.post("/api/login", json={"username": "admin", "password": "incorrect"},
                                   environ_overrides={"REMOTE_ADDR": "192.0.2.10"})
        return response.status_code

    with ThreadPoolExecutor(max_workers=participants) as pool:
        statuses = list(pool.map(attempt, range(participants), timeout=30))
    assert sorted(statuses) == [401] * 5 + [429] * (participants - 5)


def test_failed_attempts_from_other_source_do_not_block_valid_admin(auth_app):
    app, credentials = auth_app
    attacker = app.test_client()
    rejected = "incorrect"
    for _ in range(5):
        response = attacker.post("/api/login", json={"username": "admin", "password": rejected},
                                 environ_overrides={"REMOTE_ADDR": "192.0.2.10"})
        assert response.status_code == 401
    assert attacker.post("/api/login", json={"username": "admin", "password": rejected},
                         environ_overrides={"REMOTE_ADDR": "192.0.2.10"}).status_code == 429
    admin = app.test_client()
    response = admin.post("/api/login", json={"username": "admin", "password": credentials["ADMIN_PASSWORD"]},
                          environ_overrides={"REMOTE_ADDR": "192.0.2.11"})
    assert response.status_code == 200 and response.get_json()["access_token"]


def test_spoofed_client_headers_cannot_reset_source_limit(auth_app):
    app, _ = auth_app
    client = app.test_client()
    statuses = []
    for number in range(7):
        response = client.post("/api/login", json={"username": "admin", "password": "incorrect"},
                               headers={"X-Forwarded-For": f"198.51.100.{number + 1}",
                                        "X-Real-IP": f"203.0.113.{number + 1}",
                                        "Forwarded": f"for=198.51.100.{number + 1}"},
                               environ_overrides={"REMOTE_ADDR": "192.0.2.10"})
        statuses.append(response.status_code)
    assert statuses == [401] * 5 + [429] * 2
