from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from werkzeug.security import generate_password_hash


@pytest.fixture
def credentials(monkeypatch):
    values = {key: f"test-{uuid4().hex}" for key in ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD")}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def test_password_definition_rejects_less_than_twelve_characters(credentials):
    from app import create_app
    from app.auth import configure_auth

    app = create_app(database_uri="sqlite:///:memory:")
    with pytest.raises(ValueError, match="ADMIN_PASSWORD must be at least 12 characters"):
        configure_auth(app, "admin", "elevenchars")


def test_exactly_twelve_characters_are_accepted_at_startup(monkeypatch, credentials):
    from app import create_app

    monkeypatch.setenv("ADMIN_PASSWORD", "twelvechars!")
    app = create_app(database_uri="sqlite:///:memory:")
    assert app.test_client().post("/api/login", json={"username": "admin", "password": "twelvechars!"}).status_code == 200


@pytest.mark.parametrize("password", ["elevenchars", "", "change-me"])
def test_short_admin_password_is_rejected_at_startup(monkeypatch, credentials, password):
    from app import create_app

    monkeypatch.setenv("ADMIN_PASSWORD", password)
    with pytest.raises(ValueError, match="ADMIN_PASSWORD"):
        create_app(database_uri="sqlite:///:memory:")


@pytest.mark.parametrize("key", ["SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD"])
@pytest.mark.parametrize("value", [None, "", "change-me"])
def test_missing_empty_or_placeholder_secrets_remain_rejected(monkeypatch, credentials, key, value):
    from app import create_app

    if value is None:
        monkeypatch.delenv(key)
    else:
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=key):
        create_app(database_uri="sqlite:///:memory:")


def test_new_password_hash_is_verified_by_argon2(monkeypatch, credentials):
    import app.auth as auth
    from app import create_app

    hashes = []
    original = auth.PasswordHasher

    class RecordingHasher(original):
        def hash(self, password, **kwargs):
            hashed = super().hash(password, **kwargs)
            hashes.append(hashed)
            return hashed

    monkeypatch.setattr(auth, "PasswordHasher", RecordingHasher)
    create_app(database_uri="sqlite:///:memory:")
    assert len(hashes) == 1 and PasswordHasher().verify(hashes[0], credentials["ADMIN_PASSWORD"])


@pytest.mark.parametrize("password,expected", [("legacy-short", 200), ("wrong", 401)])
def test_legacy_werkzeug_hash_migrates_only_after_success(monkeypatch, credentials, password, expected):
    import app.auth as auth
    from app import create_app

    legacy = generate_password_hash("legacy-short")
    created = []
    original = auth.check_password_hash
    hasher = auth.PasswordHasher

    class RecordingHasher(hasher):
        def hash(self, candidate, **kwargs):
            result = super().hash(candidate, **kwargs)
            created.append(result)
            return result

    def check_legacy(stored, candidate):
        assert stored == legacy
        return original(stored, candidate)

    monkeypatch.setattr(auth, "PasswordHasher", RecordingHasher)
    app = create_app(database_uri="sqlite:///:memory:")
    created.clear()
    monkeypatch.setattr(auth, "check_password_hash", check_legacy)
    function = app.view_functions["login_page.login"]
    authenticate = next((cell.cell_contents for cell in function.__closure__ or ()
                         if callable(cell.cell_contents) and cell.cell_contents.__name__ == "authenticate"), None)
    assert authenticate is not None
    hash_cell = next(cell for name, cell in zip(authenticate.__code__.co_freevars, authenticate.__closure__)
                     if name == "password_hash")
    hash_cell.cell_contents = legacy
    response = app.test_client().post("/api/login", json={"username": "admin", "password": password})
    assert response.status_code == expected
    if expected == 200:
        assert len(created) == 1 and PasswordHasher().verify(hash_cell.cell_contents, "legacy-short")
        assert hash_cell.cell_contents != legacy
    else:
        assert created == [] and hash_cell.cell_contents == legacy
