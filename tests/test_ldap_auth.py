import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def directory(monkeypatch):
    from app import ldap_auth

    state = {"entries": [SimpleNamespace(entry_dn="uid=alice,dc=example", entry_attributes_as_dict={"memberOf": []})],
             "password": uuid4().hex, "queries": [], "events": [], "offline": False, "tls": True}

    class Server:
        def __init__(self, url, **kwargs):
            state["server_options"] = kwargs
            self.ssl = kwargs["use_ssl"]

    class Connection:
        def __init__(self, server, user=None, password=None, **kwargs):
            self.user = user
            self.password = password
            self.bound = False
            self.entries = []
            state["events"].append(("connect", user))

        def start_tls(self):
            state["events"].append(("tls", self.user))
            return state["tls"]

        def bind(self):
            state["events"].append(("bind", self.user))
            if state["offline"]:
                raise OSError("unreachable")
            self.bound = self.user is None or self.password == state["password"]
            return self.bound

        def search(self, base, query, **kwargs):
            state["queries"].append(query)
            self.entries = state["entries"]
            return bool(self.entries)

        def unbind(self):
            self.bound = False

    monkeypatch.setattr(ldap_auth, "Server", Server)
    monkeypatch.setattr(ldap_auth, "Connection", Connection)
    return state


@pytest.fixture
def ldap_app(monkeypatch, directory):
    for key in ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD"):
        monkeypatch.setenv(key, f"test-{uuid4().hex}")
    monkeypatch.setenv("LDAP_SERVER", "ldap://directory.example")
    monkeypatch.setenv("LDAP_BASE_DN", "dc=example")
    monkeypatch.setenv("LDAP_GROUP_ROLES", '{"cn=ops,dc=example":"opérateur","cn=admins,dc=example":"admin"}')
    from app import create_app
    app = create_app()
    app.config["TEST_DIRECTORY_PASSWORD"] = directory["password"]
    return app


def api_login(app, username="alice", password=None):
    if password is None:
        password = app.config["TEST_DIRECTORY_PASSWORD"]
    return app.test_client().post("/api/login", json={"username": username, "password": password})


def test_ldap_web_login_succeeds(ldap_app):
    client = ldap_app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    response = client.post("/login", data={"username": "alice", "password": ldap_app.config["TEST_DIRECTORY_PASSWORD"], "csrf_token": csrf})
    assert response.status_code == 302


def test_ldap_api_login_returns_jwt(ldap_app):
    assert isinstance(api_login(ldap_app).get_json().get("access_token"), str)


def test_wrong_directory_password_creates_no_account(ldap_app):
    from app.models import Account
    from app import db
    response = api_login(ldap_app, password=uuid4().hex)
    with ldap_app.app_context():
        accounts = db.session.query(Account).filter_by(origin_code="LDAP", username="alice").count()
    assert (response.status_code, accounts) == (401, 0)


@pytest.mark.parametrize("origin,groups,expected", [
    ("LDAP", ["cn=ops,dc=example"], "opérateur"),
    ("AD", ["cn=admins,dc=example"], "admin"),
    ("LDAP", [], "lecteur"),
])
def test_directory_groups_select_role(ldap_app, directory, origin, groups, expected):
    from app.models import Account
    directory["entries"][0].entry_attributes_as_dict["memberOf"] = groups
    ldap_app.config["LDAP_ORIGIN"] = origin
    assert api_login(ldap_app).status_code == 200
    with ldap_app.app_context():
        assert Account.query.filter_by(origin_code=origin, username="alice").one().role_code == expected


@pytest.mark.parametrize("attribute,value", [("userAccountControl", [2]), ("nsAccountLock", ["TRUE"])])
def test_disabled_directory_user_is_rejected(ldap_app, directory, attribute, value):
    directory["entries"][0].entry_attributes_as_dict[attribute] = value
    assert api_login(ldap_app).status_code == 401


def test_ambiguous_search_is_rejected_without_account(ldap_app, directory):
    from app.models import Account
    directory["entries"] *= 2
    assert api_login(ldap_app).status_code == 401
    with ldap_app.app_context():
        assert Account.query.filter_by(origin_code="LDAP", username="alice").count() == 0


def test_filter_escapes_username(ldap_app, directory):
    api_login(ldap_app, username="alice*)(uid=*)")
    assert directory["queries"] == [r"(uid=alice\2a\29\28uid=\2a\29)"]


def test_tls_verification_and_starttls_precede_bind(ldap_app, directory):
    api_login(ldap_app)
    assert directory["server_options"]["tls"].validate == 2
    assert directory["events"].index(("tls", None)) < directory["events"].index(("bind", None))


def test_failed_starttls_prevents_bind(ldap_app, directory):
    directory["tls"] = False
    assert api_login(ldap_app).status_code == 401
    assert not any(event == "bind" for event, _ in directory["events"])


def test_empty_password_is_rejected_before_directory_connection(ldap_app, directory):
    assert api_login(ldap_app, password="").status_code == 400
    assert directory["events"] == []


def test_directory_password_never_persisted_or_logged(ldap_app, caplog):
    from app.models import Account
    with caplog.at_level(logging.INFO):
        assert api_login(ldap_app).status_code == 200
    with ldap_app.app_context():
        account = Account.query.filter_by(origin_code="LDAP", username="alice").one()
        assert account.local_password_hash is None
    assert ldap_app.config["TEST_DIRECTORY_PASSWORD"] not in caplog.text


def test_unreachable_directory_does_not_create_account(ldap_app, directory):
    from app.models import Account
    directory["offline"] = True
    assert api_login(ldap_app).status_code == 401
    with ldap_app.app_context():
        assert Account.query.filter_by(origin_code="LDAP", username="alice").count() == 0


def test_unreachable_directory_falls_back_to_local_account(ldap_app, directory):
    from app import db
    from app.models import Account
    from werkzeug.security import generate_password_hash

    local_password = uuid4().hex
    with ldap_app.app_context():
        db.session.add(Account(origin_code="local", username="alice", role_code="lecteur",
                               local_password_hash=generate_password_hash(local_password)))
        db.session.commit()
    directory["offline"] = True
    response = api_login(ldap_app, password=local_password)
    assert response.status_code == 200


def test_disabled_local_account_cannot_use_directory_fallback(ldap_app, directory):
    from app import db
    from app.models import Account

    local_password = uuid4().hex
    with ldap_app.app_context():
        db.session.add(Account(origin_code="local", username="alice", role_code="lecteur",
                               local_password_hash="disabled:" + local_password))
        db.session.commit()
    directory["offline"] = True
    assert api_login(ldap_app, password=local_password).status_code == 401


def test_local_and_directory_identities_are_distinct(ldap_app):
    from app import db
    from app.models import Account
    from werkzeug.security import generate_password_hash

    local_password = uuid4().hex
    with ldap_app.app_context():
        db.session.add(Account(origin_code="local", username="alice", role_code="lecteur",
                               local_password_hash=generate_password_hash(local_password)))
        db.session.commit()
    assert api_login(ldap_app).status_code == 200
    with ldap_app.app_context():
        accounts = Account.query.filter_by(username="alice").all()
        assert {account.origin_code for account in accounts} == {"local", "LDAP"}


@pytest.mark.parametrize("group,expected", [("cn=ops,dc=example", 201),
                                             ("cn=admins,dc=example", 201)])
def test_directory_operator_and_admin_can_create_anomaly_exception(ldap_app, directory, group, expected):
    from app import db
    from app.models import Anomaly, Run
    from datetime import UTC, datetime, timedelta

    directory["entries"][0].entry_attributes_as_dict["memberOf"] = [group]
    with ldap_app.app_context():
        run = Run(status="SUCCESS", started_at="2026-01-01T00:00:00+00:00")
        db.session.add(run)
        db.session.flush()
        db.session.add(Anomaly(run_id=run.id, code="NO_MATCH", severity="info", state="ouverte",
                               tracking_key='["NO_MATCH", "vm-1"]', description="observation",
                               created_at="2026-01-01T00:00:00+00:00"))
        db.session.commit()
    client = ldap_app.test_client()
    login = client.post("/api/login", json={"username": "alice", "password": ldap_app.config["TEST_DIRECTORY_PASSWORD"]})
    assert login.status_code == 200
    response = client.post("/api/anomaly-exceptions", json={
        "code": "NO_MATCH", "target": "vm-1", "justification": "maintenance",
        "expires_at": (datetime.now(UTC) + timedelta(days=2)).isoformat()},
        headers={"Authorization": f"Bearer {login.get_json()['access_token']}"})
    assert response.status_code == expected
