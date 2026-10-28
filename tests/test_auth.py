from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest


@pytest.fixture
def auth_app(monkeypatch):
    values = {key: f"test-{uuid4().hex}" for key in ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD")}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    from app import create_app

    return create_app(), values


def token_for(client):
    client.get("/login")
    with client.session_transaction() as session:
        return session["csrf_token"]


def submit(client, password, token, username="admin", next_url=None, **kwargs):
    return client.post("/login", query_string={"next": next_url} if next_url else None,
                       data={"username": username, "password": password, "csrf_token": token}, **kwargs)


def test_correct_login_grants_health_access(auth_app):
    app, values = auth_app
    client = app.test_client()
    response = submit(client, values["ADMIN_PASSWORD"], token_for(client))
    assert response.status_code == 302 and client.get("/health").get_json() == {"status": "ok"}


def test_incorrect_login_does_not_grant_access(auth_app):
    app, _ = auth_app
    client = app.test_client()
    response = submit(client, "incorrect", token_for(client))
    assert response.status_code == 401 and client.get("/health").status_code == 302


def test_only_configured_admin_can_log_in(auth_app):
    app, values = auth_app
    client = app.test_client()
    response = submit(client, values["ADMIN_PASSWORD"], token_for(client), username="other")
    assert response.status_code == 401


def test_password_is_not_kept_in_plaintext_config(auth_app):
    app, values = auth_app
    assert "ADMIN_PASSWORD" not in app.config and values["ADMIN_PASSWORD"] not in repr(app.config)


@pytest.mark.parametrize("path", ["/health", "/apidocs/"])
def test_anonymous_web_routes_redirect_to_login(auth_app, path):
    app, _ = auth_app
    response = app.test_client().get(path)
    location = urlsplit(response.headers["Location"])
    assert response.status_code == 302 and location.path == "/login" and parse_qs(location.query)["next"] == [path]


@pytest.mark.parametrize("token", [None, "invalid"])
def test_login_rejects_missing_or_invalid_csrf(auth_app, token):
    app, values = auth_app
    client = app.test_client()
    token_for(client)
    response = submit(client, values["ADMIN_PASSWORD"], token)
    assert response.status_code == 400 and client.get("/health").status_code == 302


def test_internal_next_redirects_after_login(auth_app):
    app, values = auth_app
    client = app.test_client()
    response = submit(client, values["ADMIN_PASSWORD"], token_for(client), next_url="/health?view=full")
    assert response.headers["Location"] == "/health?view=full"


@pytest.mark.parametrize("destination", ["https://evil.example/path", "//evil.example/path", "/\\evil.example", "/%2fevil.example", "/health#fragment"])
def test_external_or_ambiguous_next_is_rejected(auth_app, destination):
    app, values = auth_app
    client = app.test_client()
    response = submit(client, values["ADMIN_PASSWORD"], token_for(client), next_url=destination)
    assert response.headers["Location"] == "/health"


def test_five_failed_logins_block_next_attempt(auth_app):
    app, values = auth_app
    client = app.test_client()
    token = token_for(client)
    for _ in range(5):
        assert submit(client, "incorrect", token).status_code == 401
    assert submit(client, values["ADMIN_PASSWORD"], token).status_code == 429


def test_login_limit_expires_after_fifteen_minutes(auth_app, monkeypatch):
    app, values = auth_app
    import app.auth as auth

    clock = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock[0])
    client = app.test_client()
    token = token_for(client)
    for _ in range(5):
        submit(client, "incorrect", token)
    clock[0] += 901
    assert submit(client, values["ADMIN_PASSWORD"], token).status_code == 302


@pytest.mark.parametrize("kind", ["success", "error", "redirect"])
def test_security_headers_on_every_response_kind(auth_app, kind):
    app, _ = auth_app
    client = app.test_client()
    response = {"success": lambda: client.get("/login"),
                "error": lambda: client.post("/login"),
                "redirect": lambda: client.get("/health")}[kind]()
    assert (response.headers["Content-Security-Policy"], response.headers["X-Frame-Options"],
            response.headers["X-Content-Type-Options"]) == (
                "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'", "DENY", "nosniff")


def test_session_cookie_is_signed_http_only_secure_and_samesite(auth_app):
    app, values = auth_app
    client = app.test_client()
    response = submit(client, values["ADMIN_PASSWORD"], token_for(client))
    cookie = SimpleCookie()
    cookie.load(response.headers["Set-Cookie"])
    session_cookie = cookie[app.config["SESSION_COOKIE_NAME"]]
    assert (bool(session_cookie["httponly"]), bool(session_cookie["secure"]), session_cookie["samesite"],
            "." in session_cookie.value, values["ADMIN_PASSWORD"] not in session_cookie.value) == (True, True, "Lax", True, True)


def test_session_cookie_secure_defaults_to_true_without_environment_variable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    for key in ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD"):
        monkeypatch.setenv(key, f"test-{uuid4().hex}")
    from app import create_app

    app = create_app(database_uri="sqlite:///:memory:")

    assert app.config["SESSION_COOKIE_SECURE"] is True


def test_authenticated_web_post_requires_csrf(auth_app):
    app, values = auth_app
    from flask import Blueprint

    form = Blueprint("test_form", __name__)
    form.add_url_rule("/test-form", "submit", lambda: "accepted", methods=["POST"])
    app.register_blueprint(form)
    client = app.test_client()
    token = token_for(client)
    submit(client, values["ADMIN_PASSWORD"], token)
    assert client.post("/test-form").status_code == 400


def test_authenticated_web_post_accepts_valid_csrf(auth_app):
    app, values = auth_app
    from flask import Blueprint

    form = Blueprint("test_form", __name__)
    form.add_url_rule("/test-form", "submit", lambda: "accepted", methods=["POST"])
    app.register_blueprint(form)
    client = app.test_client()
    token = token_for(client)
    submit(client, values["ADMIN_PASSWORD"], token)
    token = token_for(client)
    assert client.post("/test-form", data={"csrf_token": token}).data == b"accepted"
