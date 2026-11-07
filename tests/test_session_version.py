import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import Account
from test_auth import submit, token_for


def browsers(orm_app):
    db.session.add(Account(origin_code="local", role_code="lecteur", username="reader", local_password_hash=generate_password_hash("reader-test-password")))
    db.session.commit()
    reader = orm_app.test_client()
    admin = orm_app.test_client()
    assert submit(reader, "reader-test-password", token_for(reader), username="reader").status_code == 302
    import os

    assert submit(admin, os.environ["ADMIN_PASSWORD"], token_for(admin)).status_code == 302
    return reader, admin


@pytest.mark.parametrize("actor", ["reader", "admin"])
def test_logout_revokes_only_its_account_cookie(orm_app, actor):
    reader, admin = browsers(orm_app)
    departing, remaining = (reader, admin) if actor == "reader" else (admin, reader)
    with departing.session_transaction() as session:
        old_cookie = orm_app.session_interface.get_signing_serializer(orm_app).dumps(dict(session))
        csrf = session["csrf_token"]
    assert departing.post("/logout", data={"csrf_token": csrf}).status_code == 302
    replay = orm_app.test_client()
    replay.set_cookie(orm_app.config["SESSION_COOKIE_NAME"], old_cookie)
    assert replay.get("/health").status_code == 302
    assert remaining.get("/health").status_code == 200


def test_cookie_without_session_version_is_rejected(orm_app):
    _, admin = browsers(orm_app)
    with admin.session_transaction() as session:
        session.pop("sv", None)
    response = admin.get("/health")
    assert response.status_code == 302 and response.location.startswith("/login")


@pytest.mark.parametrize("token", [None, "invalid"])
def test_logout_requires_csrf_without_revoking_session(orm_app, token):
    reader, admin = browsers(orm_app)
    assert reader.post("/logout", data={"csrf_token": token}).status_code == 400
    assert reader.get("/health").status_code == 200
    assert admin.get("/health").status_code == 200
