import socket
from html.parser import HTMLParser
from urllib.parse import urlsplit
from uuid import uuid4

import pytest


SECRETS = ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD")


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []
        self.navigation = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "nav":
            self.navigation = True
        self.elements.append((tag, attributes, self.navigation))

    def handle_endtag(self, tag):
        if tag == "nav":
            self.navigation = False


def parse_page(response):
    parser = PageParser()
    parser.feed(response.get_data(as_text=True))
    return parser.elements


def logged_in_client(valid_secrets):
    client = start_app().test_client()
    client.get("/login")
    with client.session_transaction() as session:
        original_token = session["csrf_token"]
    response = client.post("/login", data={"username": "admin", "password": valid_secrets["ADMIN_PASSWORD"],
                                           "csrf_token": original_token})
    assert response.status_code == 302
    return client, original_token


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


def test_web_login_rejects_unicode_username_without_server_error(valid_secrets):
    client = start_app().test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    response = client.post(
        "/login", data={"username": "administrateuré", "password": valid_secrets["ADMIN_PASSWORD"], "csrf_token": csrf}
    )
    assert response.status_code == 401


def test_unicode_web_failures_limit_api_login(valid_secrets):
    client = start_app().test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    for _ in range(5):
        response = client.post(
            "/login", data={"username": "administrateuré", "password": valid_secrets["ADMIN_PASSWORD"], "csrf_token": csrf}
        )
        assert response.status_code == 401
    response = client.post(
        "/api/login", json={"username": "admin", "password": valid_secrets["ADMIN_PASSWORD"]}
    )
    assert (response.status_code, response.get_json()) == (429, {"error": "Too many attempts"})


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
            if rule.endpoint.startswith("web.")} == {
                ("web.health", "/health"), ("web.index", "/"),
                ("web.inventory_page", "/inventory"),
                ("web.inventory_data", "/inventory/data"),
                ("web.inventory_export", "/inventory/export"),
                ("web.runs_page", "/runs"),
                ("web.run_page", "/runs/<int:id>"),
                ("web.asset_page", "/assets/<int:id>"),
            }


def test_unknown_route_returns_404(valid_secrets):
    app = start_app()
    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        token = session["csrf_token"]
    client.post("/login", data={"username": "admin", "password": valid_secrets["ADMIN_PASSWORD"], "csrf_token": token})
    assert client.get("/unregistered-path").status_code == 404


def test_only_expected_business_routes_are_registered(valid_secrets):
    routes = {(rule.rule, rule.endpoint) for rule in start_app().url_map.iter_rules()}
    assert ("/health", "web.health") in routes
    assert all(endpoint in {"web.health", "web.index", "web.inventory_page", "web.inventory_data", "web.inventory_export",
                            "web.runs_page", "web.run_page", "web.asset_page",
                            "static", "login_page.login", "login_page.logout", "api.login", "api.stats",
                            "api.runs", "api.create_run", "api.run_detail", "api.compare_runs",
                            "api.inventory", "api.inventory_export", "api.asset_detail", "api.anomalies"}
               or endpoint.startswith("flasgger.")
               for _, endpoint in routes)


@pytest.mark.parametrize("path,title", [("/login", "Connexion — Cloud Inventory"),
                                         ("/", "Tableau de bord — Cloud Inventory")])
def test_pages_inherit_local_base_template(valid_secrets, path, title):
    client = logged_in_client(valid_secrets)[0] if path == "/" else start_app().test_client()
    response = client.get(path)
    elements = parse_page(response)
    assert response.status_code == 200 and f"<title>{title}</title>" in response.get_data(as_text=True) and {
        attrs.get("href") for tag, attrs, _ in elements if tag == "link"
    } == {"/static/vendor/bootstrap/css/bootstrap.min.css", "/static/vendor/bootstrap-icons/bootstrap-icons.css"}


def test_anonymous_page_has_no_navigation(valid_secrets):
    elements = parse_page(start_app().test_client().get("/login"))
    assert not any(tag == "nav" for tag, _, _ in elements)


def test_authenticated_navigation_has_five_local_links_and_logout(valid_secrets):
    client, _ = logged_in_client(valid_secrets)
    elements = parse_page(client.get("/"))
    assert {attrs.get("href") for tag, attrs, inside in elements if inside and tag == "a"} == {
        "/", "/inventory", "/runs", "/runs/compare", "/anomalies"
    } and any(tag == "form" and inside and attrs.get("action") == "/logout" and attrs.get("method") == "post"
              for tag, attrs, inside in elements)


@pytest.mark.parametrize("path", ["/login", "/"])
def test_pages_have_no_external_resource_or_link_urls(valid_secrets, path):
    client = logged_in_client(valid_secrets)[0] if path == "/" else start_app().test_client()
    elements = parse_page(client.get(path))
    urls = [attrs[key] for tag, attrs, _ in elements for key in ("href", "src", "action") if key in attrs]
    assert urls and all(urlsplit(url).scheme == "" and urlsplit(url).netloc == "" and url.startswith("/")
                        for url in urls)


@pytest.mark.parametrize("path", ["/static/vendor/bootstrap/css/bootstrap.min.css",
                                    "/static/vendor/bootstrap/js/bootstrap.bundle.min.js",
                                    "/static/vendor/bootstrap-icons/bootstrap-icons.css",
                                    "/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2",
                                    "/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff",
                                    "/static/vendor/chart.js/chart.umd.js"])
def test_vendor_assets_are_served_to_anonymous_visitors(valid_secrets, path):
    response = start_app().test_client().get(path)
    assert response.status_code == 200 and len(response.data) > 0


@pytest.mark.parametrize("path", ["/login", "/", "/static/vendor/bootstrap/css/bootstrap.min.css"])
def test_pages_and_assets_preserve_security_headers(valid_secrets, path):
    client = logged_in_client(valid_secrets)[0] if path == "/" else start_app().test_client()
    response = client.get(path)
    assert response.headers.get("X-Content-Type-Options") == "nosniff" and response.headers.get("X-Frame-Options") and (
        "Content-Security-Policy" in response.headers and "https:" not in response.headers["Content-Security-Policy"]
        and "http:" not in response.headers["Content-Security-Policy"])


def test_login_rotates_csrf_token(valid_secrets):
    client, original_token = logged_in_client(valid_secrets)
    with client.session_transaction() as session:
        assert session["csrf_token"] != original_token


@pytest.mark.parametrize("token", [None, "invalid"])
def test_logout_rejects_missing_or_invalid_csrf(valid_secrets, token):
    client, _ = logged_in_client(valid_secrets)
    response = client.post("/logout", data={"csrf_token": token} if token else {})
    assert response.status_code == 400 and client.get("/").status_code == 200


def test_logout_with_csrf_closes_session(valid_secrets):
    client, _ = logged_in_client(valid_secrets)
    with client.session_transaction() as session:
        token = session["csrf_token"]
    response = client.post("/logout", data={"csrf_token": token})
    redirected = client.get("/")
    assert response.status_code == 302 and urlsplit(redirected.headers["Location"]).path == "/login"
