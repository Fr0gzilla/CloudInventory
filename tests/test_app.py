import socket
from uuid import uuid4

import pytest


SECRETS = ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD")


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in (*SECRETS, "USE_MOCK_VIRT", "USE_MOCK_IPAM"):
        monkeypatch.delenv(key, raising=False)

    def no_network(*args, **kwargs):
        raise AssertionError("outbound network access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)


@pytest.fixture
def valid_secrets(monkeypatch):
    values = {key: f"test-{uuid4().hex}" for key in SECRETS}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def start_app():
    from app import create_app

    return create_app()


def test_smoke(valid_secrets):
    app = start_app()
    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        token = session["csrf_token"]
    client.post("/login", data={"username": "admin", "password": valid_secrets["ADMIN_PASSWORD"], "csrf_token": token})
    response = client.get("/health")
    assert (response.status_code, response.get_json()) == (200, {"status": "ok"})


@pytest.mark.parametrize("key", SECRETS)
@pytest.mark.parametrize("invalid", (None, "", "change-me"), ids=("absent", "empty", "placeholder"))
def test_rejects_each_invalid_secret(monkeypatch, valid_secrets, key, invalid):
    if invalid is None:
        monkeypatch.delenv(key)
    else:
        monkeypatch.setenv(key, invalid)
    with pytest.raises(ValueError) as error:
        start_app()
    assert str(error.value) == key


def test_accepts_secrets_without_dotenv(valid_secrets):
    app = start_app()
    assert all(app.config[key] == value for key, value in valid_secrets.items() if key != "ADMIN_PASSWORD")
    assert "ADMIN_PASSWORD" not in app.config


def test_loads_secrets_from_dotenv(tmp_path):
    values = {key: f"test-{uuid4().hex}" for key in SECRETS}
    (tmp_path / ".env").write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8"
    )
    app = start_app()
    assert all(app.config[key] == value for key, value in values.items() if key != "ADMIN_PASSWORD")
    assert "ADMIN_PASSWORD" not in app.config


def test_environment_takes_priority_over_dotenv(valid_secrets, tmp_path):
    (tmp_path / ".env").write_text(
        "".join(f"{key}=change-me\n" for key in SECRETS)
        + "USE_MOCK_VIRT=false\nUSE_MOCK_IPAM=false\n",
        encoding="utf-8",
    )
    app = start_app()
    assert all(app.config[key] == value for key, value in valid_secrets.items() if key != "ADMIN_PASSWORD")
    assert "ADMIN_PASSWORD" not in app.config


def test_mock_flags_default_to_true(valid_secrets):
    app = start_app()
    assert (app.config["USE_MOCK_VIRT"], app.config["USE_MOCK_IPAM"]) == (True, True)


@pytest.mark.parametrize("flag", ("USE_MOCK_VIRT", "USE_MOCK_IPAM"))
def test_mock_flag_can_be_disabled_explicitly(monkeypatch, valid_secrets, flag):
    monkeypatch.setenv(flag, "false")
    assert start_app().config[flag] is False


def test_initializes_extensions(valid_secrets):
    app = start_app()
    from app import db, jwt, login_manager, swagger

    assert (
        app.extensions.get("sqlalchemy") is db,
        app.extensions.get("flask-jwt-extended") is jwt,
        app.login_manager is login_manager,
        swagger.app is app,
    ) == (True, True, True, True)


def test_registers_blueprints_at_expected_prefixes(valid_secrets):
    app = start_app()
    assert {"web", "api", "auth"} <= set(app.blueprints)
    assert {(rule.endpoint, rule.rule) for rule in app.url_map.iter_rules()
            if rule.endpoint.startswith("web.")} == {("web.health", "/health")}


def test_unknown_route_returns_404(valid_secrets):
    app = start_app()
    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        token = session["csrf_token"]
    client.post("/login", data={"username": "admin", "password": valid_secrets["ADMIN_PASSWORD"], "csrf_token": token})
    assert client.get("/unregistered-path").status_code == 404


def test_no_business_routes_are_registered(valid_secrets):
    routes = {(rule.rule, rule.endpoint) for rule in start_app().url_map.iter_rules()}
    assert ("/health", "web.health") in routes
    assert all(endpoint in {"web.health", "static", "login_page.login"} or endpoint.startswith("flasgger.")
               for _, endpoint in routes)
