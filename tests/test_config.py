"""C2 — Refus de démarrage sans secrets (RG30, docs/modele/regles.md).

create_app() doit lever RuntimeError si SECRET_KEY, JWT_SECRET_KEY ou
ADMIN_PASSWORD est absent ou conserve la valeur d'exemple `change-me`, et
démarrer normalement quand les trois sont renseignés.

Aucun secret réel ici : seules des valeurs factices sont injectées dans
l'environnement via monkeypatch, restauré automatiquement après chaque test.
"""
import pytest

from app import create_app

# Valeurs factices >= 32 octets pour SECRET_KEY/JWT_SECRET_KEY — jamais de secret réel dans les tests (C4).
_VALID = {
    "SECRET_KEY": "dummy-secret-key-for-tests-32b!!",
    "JWT_SECRET_KEY": "dummy-jwt-key-for-tests-32bytes!!",
    "ADMIN_PASSWORD": "dummy-admin-password",
    "DATABASE_URL": "sqlite:///:memory:",
}
_DEFAULT = "change-me"


@pytest.fixture()
def factory(monkeypatch):
    """create_app isolé : un .env local ne doit jamais combler les variables retirées."""
    import app as app_package

    monkeypatch.setattr(app_package, "load_dotenv", lambda: None)
    return create_app


def _set_valid_env(monkeypatch, **overrides):
    for name, value in {**_VALID, **overrides}.items():
        monkeypatch.setenv(name, value)


def _unset(monkeypatch, *names):
    for name in names:
        monkeypatch.delenv(name, raising=False)


def test_missing_secret_key_refuses_startup(factory, monkeypatch):
    """Sans SECRET_KEY, create_app lève une erreur citant la variable."""
    _set_valid_env(monkeypatch)
    _unset(monkeypatch, "SECRET_KEY")

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        factory()


def test_missing_jwt_secret_key_refuses_startup(factory, monkeypatch):
    """Sans JWT_SECRET_KEY, create_app lève une erreur citant la variable."""
    _set_valid_env(monkeypatch)
    _unset(monkeypatch, "JWT_SECRET_KEY")

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        factory()


def test_missing_admin_password_refuses_startup(factory, monkeypatch):
    """Sans ADMIN_PASSWORD, create_app lève une erreur citant la variable."""
    _set_valid_env(monkeypatch)
    _unset(monkeypatch, "ADMIN_PASSWORD")

    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        factory()


def test_default_secret_key_refuses_startup(factory, monkeypatch):
    """SECRET_KEY=`change-me` (valeur d'exemple) est refusée."""
    _set_valid_env(monkeypatch, SECRET_KEY=_DEFAULT)

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        factory()


def test_default_jwt_secret_key_refuses_startup(factory, monkeypatch):
    """JWT_SECRET_KEY=`change-me` (valeur d'exemple) est refusée."""
    _set_valid_env(monkeypatch, JWT_SECRET_KEY=_DEFAULT)

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        factory()


def test_default_admin_password_refuses_startup(factory, monkeypatch):
    """ADMIN_PASSWORD=`change-me` (valeur d'exemple) est refusée."""
    _set_valid_env(monkeypatch, ADMIN_PASSWORD=_DEFAULT)

    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        factory()


def test_valid_secrets_start_app(factory, monkeypatch):
    """Avec les trois secrets renseignés, create_app renvoie une app opérationnelle."""
    _set_valid_env(monkeypatch)

    flask_app = factory()

    assert flask_app.config["SECRET_KEY"] == _VALID["SECRET_KEY"]
