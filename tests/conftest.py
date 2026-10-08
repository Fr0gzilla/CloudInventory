"""Fixtures pytest — une app Flask et une base SQLite :memory: éphémère par test.

Chaque test reçoit une application fraîche (donc un engine et une base mémoire
dédiés) : aucune donnée n'est partagée, aucun test ne dépend d'un autre ni de
l'ordre d'exécution.
"""
import pathlib
import sys
from datetime import datetime

import pytest

# Racine du projet dans sys.path : `import app` marche aussi hors `python -m`.
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.models import Asset, IpamRecord, Run  # noqa: E402  (après sys.path)

# Secrets de test — valeurs factices, seulement pour satisfaire Config.validate().
_TEST_ENV = {
    "SECRET_KEY": "test-secret-key",
    "JWT_SECRET_KEY": "test-jwt-secret-key",
    "ADMIN_PASSWORD": "test-admin-password",
    "DATABASE_URL": "sqlite:///:memory:",
    "APP_ENV": "test",
}

NOW = datetime(2026, 1, 1, 12, 0, 0)


@pytest.fixture()
def app(monkeypatch):
    """Factory de test : secrets posés, base SQLite mémoire, tables créées."""
    for name, value in _TEST_ENV.items():
        monkeypatch.setenv(name, value)

    from app import create_app
    from app.config import Config
    from app.extensions import db

    class TestConfig(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

    application = create_app(TestConfig)
    try:
        yield application
    finally:
        with application.app_context():
            db.session.remove()
            db.drop_all()


@pytest.fixture()
def db(app):
    """Session SQLAlchemy dans le contexte de l'application de test."""
    from app.extensions import db as extensions_db

    with app.app_context():
        yield extensions_db
        extensions_db.session.remove()


@pytest.fixture()
def run(db):
    """Run RUNNING persistant (RG17), requis par les FK asset et anomaly."""
    run = Run(status="RUNNING", start_date=NOW)
    db.session.add(run)
    db.session.commit()
    return run


@pytest.fixture()
def asset(db, run):
    """Asset persistant, requis par les FK consolidated_asset et anomaly."""
    asset = Asset(vm_id="vm-001", vm_name="web-a500", consolidated_run_id=run.id)
    db.session.add(asset)
    db.session.commit()
    return asset


@pytest.fixture()
def ipam_record(db):
    """Enregistrement IPAM persistant, clé métier ip+dns_name (RG18)."""
    record = IpamRecord(ip="10.0.0.1", dns_name="host-a500")
    db.session.add(record)
    db.session.commit()
    return record
