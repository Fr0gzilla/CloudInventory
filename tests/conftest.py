from uuid import uuid4

import pytest


@pytest.fixture(autouse=True)
def isolated_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")


@pytest.fixture
def orm_app(monkeypatch, tmp_path):
    for key in ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD"):
        monkeypatch.setenv(key, f"test-only-{uuid4().hex}")
    monkeypatch.setenv("EXPORT_DIRECTORY", str(tmp_path / "exports"))
    from app import create_app, db

    app = create_app(database_uri="sqlite:///:memory:")
    with app.app_context():
        try:
            yield app
        finally:
            db.session.remove()
