"""T035 — C12/C13 budgets partagés ; C16 Swagger ; C18 clés en octets."""
import json
import multiprocessing
import re
import secrets
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest


def _post(client, endpoint, username="unknown", password=None, ip="192.0.2.1"):
    data = {"username": username, "password": password if password is not None else secrets.token_urlsafe(12)}
    options = {"environ_overrides": {"REMOTE_ADDR": ip}}
    if endpoint == "/login":
        page = client.get("/login")
        token = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text)
        assert token is not None
        data["csrf_token"] = token.group(1)
        return client.post(endpoint, data=data, **options)
    return client.post(endpoint, json=data, **options)


def _identity(dimension, index):
    username = "unknown" if dimension == "account" else f"unknown-{index}"
    ip = "192.0.2.1" if dimension == "ip" else f"192.0.2.{index + 1}"
    return username, ip


@pytest.fixture()
def limited_app(app, tmp_path):
    app.config.update(
        RATE_LIMIT_STORE_PATH=str(tmp_path / "budgets.json"),
        RATE_LIMIT_IP_MAX=100,
        RATE_LIMIT_ACCOUNT_MAX=100,
        RATE_LIMIT_GLOBAL_MAX=100,
        RATE_LIMIT_WINDOW_SECONDS=60,
        RATE_LIMIT_MAX_ENTRIES=100,
    )
    return app


@pytest.fixture()
def hash_spies(monkeypatch):
    import app.api as api
    import app.auth as auth

    spies = []
    for module in (auth, api):
        spy = Mock(wraps=module.check_password_hash)
        monkeypatch.setattr(module, "check_password_hash", spy)
        spies.append(spy)
    return spies


@pytest.mark.parametrize("dimension", ["ip", "account", "global"])
@pytest.mark.parametrize("first", ["/login", "/api/login"])
def test_web_and_api_share_each_budget_before_hash(limited_app, hash_spies, dimension, first):
    limited_app.config[f"RATE_LIMIT_{dimension.upper()}_MAX"] = 2
    client = limited_app.test_client()
    second = "/api/login" if first == "/login" else "/login"
    for index, endpoint in enumerate((first, second)):
        username, ip = _identity(dimension, index)
        response = _post(client, endpoint, username, ip=ip)
        assert response.status_code == (200 if endpoint == "/login" else 401)
    assert sum(spy.call_count for spy in hash_spies) == 2
    for index, endpoint in enumerate((first, second), start=2):
        username, ip = _identity(dimension, index)
        response = _post(client, endpoint, username, ip=ip)
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) >= 1
    assert sum(spy.call_count for spy in hash_spies) == 2


def _worker(application, barrier, results, worker_id, dimension):
    client = application.test_client()
    endpoint = "/login" if worker_id % 2 == 0 else "/api/login"
    statuses = []
    for attempt in range(3):
        username, ip = _identity(dimension, worker_id * 3 + attempt)
        barrier.wait(timeout=20)
        response = _post(client, endpoint, username, ip=ip)
        statuses.append((response.status_code, response.headers.get("Retry-After")))
    results.put((endpoint, statuses))


@pytest.mark.parametrize("dimension", ["ip", "account", "global"])
def test_four_processes_consume_each_budget_atomically(limited_app, dimension):
    limited_app.config[f"RATE_LIMIT_{dimension.upper()}_MAX"] = 5
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(4)
    results = context.Queue()
    processes = [context.Process(target=_worker, args=(
        limited_app, barrier, results, index, dimension,
    )) for index in range(4)]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=40)
        assert [process.exitcode for process in processes] == [0, 0, 0, 0]
        batches = [results.get(timeout=5) for _ in processes]
        accepted = 0
        rejected = 0
        for endpoint, statuses in batches:
            for status, retry_after in statuses:
                assert status in (200 if endpoint == "/login" else 401, 429)
                if status == 429:
                    assert int(retry_after) >= 1
                    rejected += 1
                else:
                    accepted += 1
        assert (accepted, rejected) == (5, 7)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()
        results.join_thread()


@pytest.mark.parametrize("endpoint", ["/login", "/api/login"])
def test_expiration_restores_login(limited_app, monkeypatch, endpoint):
    clock = SimpleNamespace(time=lambda: 1000)
    monkeypatch.setattr("app.auth.time", clock)
    limited_app.config["RATE_LIMIT_GLOBAL_MAX"] = 1
    client = limited_app.test_client()
    assert _post(client, endpoint).status_code == (200 if endpoint == "/login" else 401)
    assert _post(client, endpoint).status_code == 429
    clock.time = lambda: 1061
    response = _post(client, endpoint, limited_app.config["ADMIN_USERNAME"], "test-admin-password")
    assert response.status_code == (302 if endpoint == "/login" else 200)


def test_unknown_accounts_have_bounded_cardinality_and_expired_entries_are_evicted(limited_app, monkeypatch):
    clock = SimpleNamespace(time=lambda: 1000)
    monkeypatch.setattr("app.auth.time", clock)
    limited_app.config["RATE_LIMIT_MAX_ENTRIES"] = 5
    client = limited_app.test_client()
    statuses = [_post(client, "/api/login", f"unknown-{index}").status_code for index in range(20)]
    assert statuses[:3] == [401, 401, 401]
    assert statuses[3:] == [429] * 17
    store = Path(limited_app.config["RATE_LIMIT_STORE_PATH"])
    assert len(json.loads(store.read_text())) <= 5
    clock.time = lambda: 1061
    assert _post(client, "/api/login", "new-account").status_code == 401
    assert len(json.loads(store.read_text())) == 3


@pytest.mark.parametrize("endpoint", ["/login", "/api/login"])
@pytest.mark.parametrize("failure", ["unavailable", "corrupt"])
def test_storage_failure_refuses_login_before_hash(limited_app, hash_spies, tmp_path, endpoint, failure):
    store = tmp_path / "broken"
    if failure == "unavailable":
        store.mkdir()
        limited_app.config["RATE_LIMIT_STORE_PATH"] = str(store / "missing" / "budgets.json")
    else:
        store.write_text("not-json")
        limited_app.config["RATE_LIMIT_STORE_PATH"] = str(store)
    response = _post(limited_app.test_client(), endpoint,
                     limited_app.config["ADMIN_USERNAME"], "test-admin-password")
    assert response.status_code == 503
    assert sum(spy.call_count for spy in hash_spies) == 0


@pytest.mark.parametrize("endpoint", ["/login", "/api/login"])
def test_excessive_body_is_rejected_before_hash(limited_app, hash_spies, endpoint):
    response = _post(limited_app.test_client(), endpoint, password="x" * 9000)
    assert response.status_code == 413
    assert sum(spy.call_count for spy in hash_spies) == 0


@pytest.mark.parametrize("endpoint", ["/login", "/api/login"])
@pytest.mark.parametrize("field,value", [("username", "x" * 257), ("password", "x" * 4097)])
def test_excessive_fields_are_rejected_before_hash(limited_app, hash_spies, endpoint, field, value):
    response = _post(limited_app.test_client(), endpoint, **{field: value})
    assert response.status_code == 400
    assert sum(spy.call_count for spy in hash_spies) == 0


def test_untrusted_forwarded_ip_does_not_bypass_ip_budget(limited_app):
    limited_app.config["RATE_LIMIT_IP_MAX"] = 1
    client = limited_app.test_client()
    assert _post(client, "/api/login").status_code == 401
    response = client.post("/api/login", json={"username": "another", "password": "incorrect"},
                           environ_overrides={"REMOTE_ADDR": "192.0.2.1"},
                           headers={"X-Forwarded-For": "198.51.100.1"})
    assert response.status_code == 429


@pytest.mark.parametrize("path", ["/apidocs", "/apispec_1.json"])
def test_swagger_ui_and_json_refuse_anonymous_users(app, path):
    response = app.test_client().get(path)
    if path == "/apidocs":
        assert response.status_code == 302
        assert urlsplit(response.location).path == "/login"
    else:
        assert response.status_code == 401
        assert "paths" not in response.get_json()


@pytest.mark.parametrize("path", ["/apidocs", "/apispec_1.json"])
def test_swagger_ui_and_json_accept_authenticated_users(app, path):
    client = app.test_client()
    username = app.config["ADMIN_USERNAME"]
    headers = {}
    if path == "/apidocs":
        assert _post(client, "/login", username, "test-admin-password").status_code == 302
    else:
        login = _post(client, "/api/login", username, "test-admin-password")
        assert login.status_code == 200
        headers["Authorization"] = "Bearer " + login.get_json()["access_token"]
    response = client.get(path, headers=headers)
    assert response.status_code == 200
    if path == "/apidocs":
        assert "swagger" in response.text.lower()
    else:
        assert "/api/login" in response.get_json()["paths"]


@pytest.mark.parametrize("key", ["SECRET_KEY", "JWT_SECRET_KEY"])
@pytest.mark.parametrize("value", ["x" * 31, "é" * 15])
def test_each_short_key_prevents_startup(app, monkeypatch, key, value):
    from app import create_app

    monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match=key):
        create_app()


@pytest.mark.parametrize("key", ["SECRET_KEY", "JWT_SECRET_KEY"])
@pytest.mark.parametrize("unicode_key", [False, True])
def test_each_exactly_32_byte_key_allows_startup(app, monkeypatch, key, unicode_key):
    from app import create_app

    value = "é" * 16 if unicode_key else secrets.token_hex(16)
    assert len(value.encode("utf-8")) == 32
    monkeypatch.setenv(key, value)
    application = create_app()
    assert application.config[key] == value
