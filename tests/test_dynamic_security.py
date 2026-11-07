import sqlite3

import pytest
from alembic import command
from sqlalchemy import create_engine

from app import db
from app.models import Account
from test_accounts_ev48 import admin_client, create
from test_migrations import migration_config
from test_roles_ev48 import login_api, login_web


@pytest.fixture
def accounts(orm_app, admin_client):
    admin, csrf = admin_client
    assert create(admin, csrf, username='target', role='opérateur').status_code == 302
    assert create(admin, csrf, username='other').status_code == 302
    target, _ = login_web(orm_app, 'target', 'test-password-123')
    other, _ = login_web(orm_app, 'other', 'test-password-123')
    return admin, csrf, target, other


def test_web_logout_revokes_cookie_and_jwt_but_not_other_account(orm_app, accounts):
    admin, csrf, target, other = accounts
    target_token = login_api(orm_app, 'target', 'test-password-123')
    other_token = login_api(orm_app, 'other', 'test-password-123')
    with target.session_transaction() as session:
        old_cookie = orm_app.session_interface.get_signing_serializer(orm_app).dumps(dict(session))
        target_csrf = session['csrf_token']
    assert target.post('/logout', data={'csrf_token': target_csrf}).status_code == 302
    replay = orm_app.test_client()
    replay.set_cookie(orm_app.config['SESSION_COOKIE_NAME'], old_cookie)
    assert replay.get('/inventory').status_code == 302
    assert orm_app.test_client().get('/api/runs', headers=target_token).status_code == 401
    assert other.get('/inventory').status_code == 200
    assert orm_app.test_client().get('/api/runs', headers=other_token).status_code == 200
    renewed, _ = login_web(orm_app, 'target', 'test-password-123')
    assert renewed.get('/inventory').status_code == 200
    assert orm_app.test_client().get('/api/runs', headers=login_api(orm_app, 'target', 'test-password-123')).status_code == 200
    assert admin.get('/accounts').status_code == 200


@pytest.mark.parametrize('action,data', [
    ('logout', {}),
    ('password', {'password': 'replacement-password-123'}),
    ('disable', {}),
    ('role', {'role': 'lecteur'}),
])
def test_failed_commit_keeps_account_and_prior_access(orm_app, accounts, monkeypatch, action, data):
    admin, csrf, target, other = accounts
    token = login_api(orm_app, 'target', 'test-password-123')
    other_token = login_api(orm_app, 'other', 'test-password-123')
    account = Account.query.filter_by(username='target').one()
    account_id = account.id
    old_state = (account.session_version, account.role_code, account.local_password_hash)
    original_commit = db.session.commit

    def failed_commit():
        db.session.rollback()
        raise sqlite3.IntegrityError('test-only pre-commit failure')

    monkeypatch.setattr(db.session, 'commit', failed_commit)
    if action == 'logout':
        with target.session_transaction() as session:
            target_csrf = session['csrf_token']
        response = target.post('/logout', data={'csrf_token': target_csrf})
    else:
        response = admin.post(f'/accounts/{account_id}', data={'action': action, 'csrf_token': csrf, **data})
    monkeypatch.setattr(db.session, 'commit', original_commit)
    assert response.status_code >= 500
    db.session.remove()
    with db.engine.connect() as connection:
        persisted = connection.exec_driver_sql(
            'SELECT session_version, role_code, local_password_hash FROM account WHERE id = ?',
            (account_id,),
        ).one()
    assert tuple(persisted) == old_state
    assert target.get('/inventory').status_code == 200
    assert orm_app.test_client().get('/api/runs', headers=token).status_code == 200
    assert other.get('/inventory').status_code == 200
    assert orm_app.test_client().get('/api/runs', headers=other_token).status_code == 200


def test_file_copy_of_populated_v30_migrates_without_losing_accounts_or_audit(tmp_path):
    original = tmp_path / 'populated.sqlite3'
    backup = tmp_path / 'deployed-copy.sqlite3'
    engine = create_engine(f'sqlite:///{original}')
    try:
        with engine.begin() as connection:
            command.upgrade(migration_config(connection), '0004_model_v30')
            connection.exec_driver_sql("INSERT INTO auth_origin (origin_code) VALUES ('local')")
            connection.exec_driver_sql("INSERT INTO role (role_code) VALUES ('admin')")
            connection.exec_driver_sql("INSERT INTO account (id, origin_code, role_code, username, local_password_hash) VALUES (41, 'local', 'admin', 'existing', 'hash')")
            connection.exec_driver_sql("INSERT INTO audit (id, account_id, actor_identity, action, created_at, target) VALUES (42, 41, 'existing', 'login', '2026-01-01', 'account')")
    finally:
        engine.dispose()
    with sqlite3.connect(original) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
    copied = create_engine(f'sqlite:///{backup}')
    try:
        with copied.begin() as connection:
            command.upgrade(migration_config(connection), 'head')
            assert connection.exec_driver_sql('SELECT id, username, session_version FROM account WHERE id=41').one() == (41, 'existing', 0)
            assert connection.exec_driver_sql('SELECT id, account_id, action FROM audit WHERE id=42').one() == (42, 41, 'login')
            assert connection.exec_driver_sql('PRAGMA foreign_key_check').all() == []
            assert connection.exec_driver_sql('PRAGMA integrity_check').one() == ('ok',)
        with sqlite3.connect(original) as untouched:
            assert 'session_version' not in [column[1] for column in untouched.execute('PRAGMA table_info(account)')]
    finally:
        copied.dispose()
