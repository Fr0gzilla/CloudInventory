import os
import sqlite3

import pytest
from sqlalchemy.exc import OperationalError

from app import create_app, db
from app.models import Run


@pytest.fixture(autouse=True)
def test_secrets(monkeypatch):
    for name in ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD"):
        monkeypatch.setenv(name, f"test-{name.lower()}")


def test_fresh_file_initializes_tables_and_authenticated_data_routes(monkeypatch, tmp_path):
    path = tmp_path / "fresh.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    app = create_app()

    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"run", "asset", "ipam_record", "consolidated_asset", "anomaly"} <= tables

    client = app.test_client()
    token = client.post("/api/login", json={"username": "admin", "password": os.environ["ADMIN_PASSWORD"]}).get_json()["access_token"]
    assert client.get("/api/inventory", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_data_survives_second_factory_with_same_file(monkeypatch, tmp_path):
    uri = f"sqlite:///{tmp_path / 'persistent.sqlite3'}"
    monkeypatch.setenv("DATABASE_URL", uri)
    first = create_app()
    with first.app_context():
        db.session.add(Run(status="SUCCESS", started_at="2026-01-01T00:00:00Z"))
        db.session.commit()
    second = create_app()
    with second.app_context():
        assert db.session.query(Run).count() == 1


def test_unavailable_database_fails_explicitly(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'missing' / 'db.sqlite3'}")
    with pytest.raises(OperationalError):
        create_app()


def test_missing_tables_raise_instead_of_returning_empty_data(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'lost.sqlite3'}")
    app = create_app()
    with app.app_context():
        db.session.execute(db.text("DROP TABLE run"))
        db.session.commit()
        with pytest.raises(OperationalError):
            db.session.query(Run).all()


@pytest.mark.parametrize("missing", ["SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD"])
def test_each_secret_remains_required(monkeypatch, missing):
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(ValueError, match=missing):
        create_app(database_uri="sqlite:///:memory:")


def test_explicit_memory_database_overrides_file_environment(monkeypatch, tmp_path):
    path = tmp_path / "environment.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    app = create_app(database_uri="sqlite:///:memory:")
    with app.app_context():
        assert db.session.query(Run).count() == 0
    assert not path.exists()
