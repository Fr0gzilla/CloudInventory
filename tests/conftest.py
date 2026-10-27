from uuid import uuid4

import pytest


@pytest.fixture
def orm_app(monkeypatch, tmp_path):
    for key in ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD"):
        monkeypatch.setenv(key, f"test-only-{uuid4().hex}")
    monkeypatch.setenv("EXPORT_DIRECTORY", str(tmp_path / "exports"))
    from app import create_app, db

    app = create_app()
    with app.app_context():
        db.create_all()
        try:
            yield app
        finally:
            db.session.remove()
            db.drop_all()
