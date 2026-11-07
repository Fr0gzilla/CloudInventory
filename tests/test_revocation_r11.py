import os

import pytest
from flask_jwt_extended import create_access_token

from app import db
from app.models import Account
from test_roles_ev48 import login_api, login_web
from test_accounts_ev48 import admin_client, create


@pytest.fixture
def accounts(orm_app, admin_client):
    admin, csrf = admin_client
    assert create(admin, csrf, username='target', role='opérateur').status_code == 302
    assert create(admin, csrf, username='other').status_code == 302
    target, _ = login_web(orm_app, 'target', 'test-password-123')
    other, _ = login_web(orm_app, 'other', 'test-password-123')
    return admin, csrf, target, other


@pytest.mark.parametrize('action,data', [
    ('password', {'password': 'replacement-password-123'}),
    ('disable', {}),
    ('role', {'role': 'lecteur'}),
])
def test_mutation_revokes_only_target_cookie_and_jwt(orm_app, accounts, action, data):
    admin, csrf, target, other = accounts
    old_token = login_api(orm_app, 'target', 'test-password-123')
    other_token = login_api(orm_app, 'other', 'test-password-123')
    account = Account.query.filter_by(username='target').one()
    assert target.get('/inventory').status_code == 200
    assert orm_app.test_client().get('/api/runs', headers=old_token).status_code == 200
    assert admin.post(f'/accounts/{account.id}', data={'action': action, 'csrf_token': csrf, **data}).status_code == 302
    assert target.get('/inventory').status_code == 302
    assert orm_app.test_client().get('/api/runs', headers=old_token).status_code == 401
    assert other.get('/inventory').status_code == 200
    assert orm_app.test_client().get('/api/runs', headers=other_token).status_code == 200
    assert admin.get('/accounts').status_code == 200


@pytest.mark.parametrize('action,data,password', [
    ('password', {'password': 'replacement-password-123'}, 'replacement-password-123'),
    ('role', {'role': 'lecteur'}, 'test-password-123'),
])
def test_active_account_can_log_in_again_after_mutation(orm_app, accounts, action, data, password):
    admin, csrf, _, _ = accounts
    account = Account.query.filter_by(username='target').one()
    assert admin.post(f'/accounts/{account.id}', data={'action': action, 'csrf_token': csrf, **data}).status_code == 302
    renewed, _ = login_web(orm_app, 'target', password)
    assert renewed.get('/inventory').status_code == 200
    assert orm_app.test_client().get('/api/runs', headers=login_api(orm_app, 'target', password)).status_code == 200


def test_disabled_account_rejects_old_access_and_login_without_affecting_other(orm_app, accounts):
    admin, csrf, target, other = accounts
    account = Account.query.filter_by(username='target').one()
    old_token = login_api(orm_app, 'target', 'test-password-123')
    other_token = login_api(orm_app, 'other', 'test-password-123')
    assert admin.post(f'/accounts/{account.id}', data={'action': 'disable', 'csrf_token': csrf}).status_code == 302
    assert orm_app.test_client().post('/api/login', json={'username': 'target', 'password': 'test-password-123'}).status_code == 401
    assert target.get('/inventory').status_code == 302
    assert orm_app.test_client().get('/api/runs', headers=old_token).status_code == 401
    assert other.get('/inventory').status_code == 200
    assert orm_app.test_client().get('/api/runs', headers=other_token).status_code == 200


@pytest.mark.parametrize('claims', [{}, {'sv': -1}, {'sv': '0'}, {'sv': None}, {'sv': True}, {'sv': 0.0}])
def test_jwt_rejects_missing_stale_or_noninteger_version(orm_app, accounts, claims):
    account = Account.query.filter_by(username='target').one()
    with orm_app.app_context():
        token = create_access_token(identity=str(account.id), additional_claims=claims)
    response = orm_app.test_client().get('/api/runs', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 401


def test_own_password_reset_revokes_admins_prior_access(orm_app, accounts):
    admin, csrf, _, _ = accounts
    previous = login_api(orm_app, 'admin', os.environ['ADMIN_PASSWORD'])
    account = Account.query.filter_by(username='admin').one()
    assert admin.post(f'/accounts/{account.id}', data={'action': 'password', 'csrf_token': csrf,
                       'password': 'replacement-admin-password-123'}).status_code == 302
    assert admin.get('/accounts').status_code == 302
    assert orm_app.test_client().get('/api/runs', headers=previous).status_code == 401
    renewed, _ = login_web(orm_app, 'admin', 'replacement-admin-password-123')
    assert renewed.get('/accounts').status_code == 200
    assert orm_app.test_client().get('/api/runs', headers=login_api(orm_app, 'admin', 'replacement-admin-password-123')).status_code == 200


@pytest.mark.parametrize('action,data', [('disable', {}), ('role', {'role': 'lecteur'})])
def test_last_admin_guard_does_not_revoke_session(orm_app, admin_client, action, data):
    admin, csrf = admin_client
    account = Account.query.filter_by(username='admin').one()
    old_token = login_api(orm_app, 'admin', os.environ['ADMIN_PASSWORD'])
    assert admin.post(f'/accounts/{account.id}', data={'action': action, 'csrf_token': csrf, **data}).status_code == 400
    db.session.refresh(account)
    assert account.role_code == 'admin'
    assert admin.get('/accounts').status_code == 200
    assert orm_app.test_client().get('/api/runs', headers=old_token).status_code == 200
