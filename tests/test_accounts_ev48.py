import os

import pytest

from app import db
from app.models import Account


@pytest.fixture
def admin_client(orm_app):
    client = orm_app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        token = session['csrf_token']
    response = client.post('/login', data={
        'username': 'admin', 'password': os.environ['ADMIN_PASSWORD'],
        'csrf_token': token,
    })
    assert response.status_code == 302
    with client.session_transaction() as session:
        token = session['csrf_token']
    return client, token


def create(client, token, username='reader', role='lecteur', password='test-password-123'):
    return client.post('/accounts', data={'username': username, 'role': role,
                                          'password': password, 'csrf_token': token})


def test_creation_persists_role_and_argon2_hash_without_plaintext(orm_app, admin_client):
    client, token = admin_client
    assert create(client, token).status_code == 302
    account = Account.query.filter_by(username='reader').one()
    assert account.role_code == 'lecteur'
    assert account.local_password_hash.startswith('$argon2')
    assert 'test-password-123' not in account.local_password_hash


@pytest.mark.parametrize('data', [
    {'username': '', 'role': 'lecteur', 'password': 'test-password-123'},
    {'username': 'reader', 'role': 'unknown', 'password': 'test-password-123'},
    {'username': 'reader', 'role': 'lecteur', 'password': 'short'},
])
def test_creation_rejects_invalid_account(orm_app, admin_client, data):
    client, token = admin_client
    assert client.post('/accounts', data={**data, 'csrf_token': token}).status_code == 400


def test_duplicate_account_is_rejected(orm_app, admin_client):
    client, token = admin_client
    create(client, token)
    assert create(client, token).status_code == 400


@pytest.mark.parametrize('action,data', [('disable', {}), ('role', {'role': 'lecteur'})])
def test_last_active_admin_cannot_be_removed(orm_app, admin_client, action, data):
    client, token = admin_client
    admin = Account.query.filter_by(username='admin').one()
    response = client.post(f'/accounts/{admin.id}', data={'csrf_token': token, 'action': action, **data})
    db.session.refresh(admin)
    assert response.status_code == 400
    assert admin.role_code == 'admin' and not admin.local_password_hash.startswith('disabled:')


def test_account_mutation_requires_csrf(orm_app, admin_client):
    client, token = admin_client
    create(client, token)
    account = Account.query.filter_by(username='reader').one()
    assert client.post(f'/accounts/{account.id}', data={'action': 'disable'}).status_code == 400


def test_reader_cannot_access_account_administration(orm_app, admin_client):
    admin, token = admin_client
    create(admin, token)
    reader = orm_app.test_client()
    reader.get('/login')
    with reader.session_transaction() as session:
        reader_token = session['csrf_token']
    assert reader.post('/login', data={'username': 'reader', 'password': 'test-password-123',
                                       'csrf_token': reader_token}).status_code == 302
    with reader.session_transaction() as session:
        reader_token = session['csrf_token']
    assert reader.get('/accounts').status_code == 403
    assert reader.post('/accounts', data={'csrf_token': reader_token, 'username': 'intruder',
                                          'password': 'test-password-123', 'role': 'admin'}).status_code == 403


def test_disabled_account_loses_existing_session(orm_app, admin_client):
    admin, token = admin_client
    create(admin, token)
    reader = orm_app.test_client()
    reader.get('/login')
    with reader.session_transaction() as session:
        reader_token = session['csrf_token']
    reader.post('/login', data={'username': 'reader', 'password': 'test-password-123',
                                'csrf_token': reader_token})
    account = Account.query.filter_by(username='reader').one()
    assert reader.get('/inventory').status_code == 200
    assert admin.post(f'/accounts/{account.id}', data={'action': 'disable', 'csrf_token': token}).status_code == 302
    assert reader.get('/inventory').status_code == 302


def test_recreating_app_preserves_single_admin_password_and_history(orm_app, admin_client, tmp_path, monkeypatch):
    from app import create_app
    from app.models import Audit

    database = tmp_path / 'persistent.sqlite3'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{database}')
    first = create_app()
    with first.app_context():
        account = Account.query.filter_by(username='admin').one()
        original_hash = account.local_password_hash
        original_id = account.id
        audit = Audit(actor_identity='admin', action='test', target='migration', created_at='2026-01-01T00:00:00Z')
        db.session.add(audit)
        db.session.commit()
        audit_id = audit.id
    second = create_app()
    with second.app_context():
        accounts = Account.query.filter_by(username='admin').all()
        assert len(accounts) == 1
        assert (accounts[0].id, accounts[0].local_password_hash) == (original_id, original_hash)
        assert db.session.get(Audit, audit_id) is not None
    assert second.test_client().post('/api/login', json={
        'username': 'admin', 'password': os.environ['ADMIN_PASSWORD'],
    }).status_code == 200
