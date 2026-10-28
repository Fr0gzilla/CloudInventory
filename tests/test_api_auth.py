import re
from uuid import uuid4

import pytest


@pytest.fixture
def api_app(monkeypatch):
    credentials = {key: f"test-{uuid4().hex}" for key in ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD")}
    for key, value in credentials.items():
        monkeypatch.setenv(key, value)
    from app import create_app

    app = create_app()
    app.add_url_rule("/api/probe", "probe", lambda: {"result": "protected"}, methods=["GET", "POST"])
    return app, credentials


def login(client, credentials):
    return client.post("/api/login", json={"username": "admin", "password": credentials["ADMIN_PASSWORD"]})


def test_api_login_returns_access_token(api_app):
    app, credentials = api_app
    response = login(app.test_client(), credentials)
    assert response.status_code == 200 and isinstance(response.get_json().get("access_token"), str)


def test_api_login_rejects_wrong_password(api_app):
    app, _ = api_app
    response = app.test_client().post("/api/login", json={"username": "admin", "password": "incorrect"})
    assert response.status_code == 401 and response.get_json() == {"error": "Invalid credentials"}


@pytest.mark.parametrize("username", ["administrateuré", "\ud800"], ids=["unicode", "isolated-surrogate"])
def test_api_login_rejects_unicode_username_without_server_error(api_app, username):
    app, credentials = api_app
    response = app.test_client().post(
        "/api/login", json={"username": username, "password": credentials["ADMIN_PASSWORD"]}
    )
    assert (response.status_code, response.get_json()) == (401, {"error": "Invalid credentials"})


def test_unicode_api_failures_do_not_limit_admin_web_login(api_app):
    app, credentials = api_app
    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    for _ in range(5):
        response = client.post(
            "/api/login", json={"username": "administrateuré", "password": credentials["ADMIN_PASSWORD"]}
        )
        assert (response.status_code, response.get_json()) == (401, {"error": "Invalid credentials"})
    response = client.post(
        "/login", data={"username": "admin", "password": credentials["ADMIN_PASSWORD"], "csrf_token": csrf}
    )
    assert response.status_code == 302


@pytest.mark.parametrize("body", [None, [], {"username": "admin"}, {"username": 3, "password": "wrong"}])
def test_api_login_rejects_invalid_json(api_app, body):
    app, _ = api_app
    response = app.test_client().post("/api/login", json=body)
    assert response.status_code == 400 and response.get_json() == {"error": "Invalid credentials"}


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_api_probe_requires_bearer_without_web_redirect(api_app, method):
    app, _ = api_app
    response = app.test_client().open("/api/probe", method=method)
    assert response.status_code == 401 and response.get_json() == {"error": "Unauthorized"} and "Location" not in response.headers


def test_unknown_api_route_requires_bearer(api_app):
    app, _ = api_app
    response = app.test_client().get("/api/not-registered")
    assert response.status_code == 401 and response.get_json() == {"error": "Unauthorized"}


def test_api_probe_rejects_invalid_token(api_app):
    app, _ = api_app
    response = app.test_client().get("/api/probe", headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401 and response.get_json() == {"error": "Unauthorized"}


def test_api_probe_accepts_valid_token_for_post_without_csrf(api_app):
    app, credentials = api_app
    client = app.test_client()
    token = login(client, credentials).get_json()["access_token"]
    response = client.post("/api/probe", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200 and response.get_json() == {"result": "protected"}


def test_api_probe_rejects_expired_token(api_app):
    app, credentials = api_app
    from datetime import timedelta
    from flask_jwt_extended import create_access_token

    with app.app_context():
        token = create_access_token(identity="admin", expires_delta=timedelta(seconds=-1))
    response = app.test_client().get("/api/probe", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401 and response.get_json() == {"error": "Unauthorized"}


def test_web_session_does_not_replace_api_bearer(api_app):
    app, credentials = api_app
    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    client.post("/login", data={"username": "admin", "password": credentials["ADMIN_PASSWORD"], "csrf_token": csrf})
    response = client.get("/api/probe")
    assert response.status_code == 401 and response.get_json() == {"error": "Unauthorized"}


def test_api_login_does_not_require_web_csrf_with_session(api_app):
    app, credentials = api_app
    client = app.test_client()
    client.get("/login")
    assert login(client, credentials).status_code == 200


def test_web_failures_limit_api_login(api_app):
    app, credentials = api_app
    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    for _ in range(5):
        client.post("/login", data={"username": "admin", "password": "incorrect", "csrf_token": csrf})
    response = login(client, credentials)
    assert response.status_code == 429 and response.get_json() == {"error": "Too many attempts"}


def test_api_failures_limit_web_login(api_app):
    app, credentials = api_app
    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    for _ in range(5):
        client.post("/api/login", json={"username": "admin", "password": "incorrect"})
    response = client.post("/login", data={"username": "admin", "password": credentials["ADMIN_PASSWORD"], "csrf_token": csrf})
    assert response.status_code == 429


def test_login_limits_are_isolated_between_factories(api_app, monkeypatch):
    app, credentials = api_app
    client = app.test_client()
    for _ in range(5):
        client.post("/api/login", json={"username": "admin", "password": "incorrect"})
    from app import create_app

    other = create_app()
    assert login(other.test_client(), credentials).status_code == 200


def test_swagger_spec_describes_login_and_bearer(api_app):
    app, credentials = api_app
    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    client.post("/login", data={"username": "admin", "password": credentials["ADMIN_PASSWORD"], "csrf_token": csrf})
    response = client.get("/apispec_1.json")
    spec = response.get_json()
    assert response.status_code == 200 and "post" in spec["paths"]["/api/login"] and any(
        value.get("type") == "apiKey" and value.get("name") == "Authorization"
        for value in spec["securityDefinitions"].values()
    )


def test_apidocs_served_to_authenticated_web_user_with_scoped_csp(api_app):
    app, credentials = api_app
    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    client.post("/login", data={"username": "admin", "password": credentials["ADMIN_PASSWORD"], "csrf_token": csrf})
    response = client.get("/apidocs/")
    csp = response.headers["Content-Security-Policy"]
    second = client.get("/apidocs/")
    assert response.status_code == second.status_code == 200
    assert b"swagger" in response.data.lower()
    for page in (response, second):
        policy = page.headers["Content-Security-Policy"]
        assert "unsafe-inline" not in policy
        script_nonce = re.search(r"script-src [^;]*'nonce-([A-Za-z0-9_-]+)'", policy)
        style_nonce = re.search(r"style-src [^;]*'nonce-([A-Za-z0-9_-]+)'", policy)
        assert script_nonce and style_nonce and script_nonce.group(1) == style_nonce.group(1)
        html = page.get_data(as_text=True)
        for tag in ("script", "style"):
            inline = re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}>", html, re.DOTALL | re.IGNORECASE)
            assert inline
            for match in re.finditer(rf"<{tag}\b([^>]*)>(.*?)</{tag}>", html, re.DOTALL | re.IGNORECASE):
                if tag == "script" and re.search(r"\bsrc=", match.group(1)):
                    continue
                assert f'nonce="{script_nonce.group(1)}"' in match.group(1)
        assert not re.search(r"\s(?:style|onload)=", html)
    assert csp != second.headers["Content-Security-Policy"]
    redirect = client.get("/oauth2-redirect.html")
    assert redirect.status_code == 200
    assert "unsafe-inline" not in redirect.headers["Content-Security-Policy"]
    assert "nonce=" in redirect.get_data(as_text=True)
    assert " onload=" not in redirect.get_data(as_text=True)
    assert client.get("/health").headers["Content-Security-Policy"] == (
        "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
