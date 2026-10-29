import os
import json
from datetime import UTC, datetime, timedelta

import pytest

from app import db
from app.models import Account, Anomaly, AnomalyException, Run, RunSchedule

from test_accounts_ev48 import admin_client as admin_client_fixture, create


def login_web(app, username, password):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf_token']
    assert client.post('/login', data={'username': username, 'password': password,
                                       'csrf_token': csrf}).status_code == 302
    with client.session_transaction() as session:
        return client, session['csrf_token']


def login_api(app, username, password):
    response = app.test_client().post('/api/login', json={'username': username, 'password': password})
    assert response.status_code == 200
    return {'Authorization': f"Bearer {response.json['access_token']}"}


@pytest.fixture
def roles(orm_app, admin_client_fixture):
    admin, csrf = admin_client_fixture
    for username, role in [('reader', 'lecteur'), ('operator', 'opérateur')]:
        assert create(admin, csrf, username=username, role=role).status_code == 302
    return {'admin': (admin, csrf),
            'lecteur': login_web(orm_app, 'reader', 'test-password-123'),
            'opérateur': login_web(orm_app, 'operator', 'test-password-123')}


@pytest.mark.parametrize('role,method,path,allowed', [
    ('lecteur', 'get', '/inventory', 200),
    ('opérateur', 'get', '/inventory', 200),
    ('admin', 'get', '/inventory', 200),
    ('lecteur', 'get', '/accounts', 403),
    ('opérateur', 'get', '/accounts', 403),
    ('admin', 'get', '/accounts', 200),
    ('lecteur', 'get', '/metrics', 403),
    ('opérateur', 'get', '/metrics', 403),
    ('admin', 'get', '/metrics', 200),
    ('lecteur', 'get', '/scheduling', 403),
    ('opérateur', 'get', '/scheduling', 403),
    ('admin', 'get', '/scheduling', 200),
    ('lecteur', 'get', '/inventory/export', 200),
    ('opérateur', 'get', '/inventory/export', 200),
    ('admin', 'get', '/inventory/export', 200),
    ('lecteur', 'post', '/dashboard/run', 403),
    ('opérateur', 'post', '/dashboard/run', 202),
    ('admin', 'post', '/dashboard/run', 202),
    ('lecteur', 'post', '/accounts/1', 403),
    ('opérateur', 'post', '/accounts/1', 403),
    ('lecteur', 'put', '/accounts', 403),
    ('opérateur', 'patch', '/accounts', 403),
    ('admin', 'put', '/accounts', 403),
    ('lecteur', 'post', '/accounts', 403),
    ('opérateur', 'post', '/accounts', 403),
])
def test_web_role_matrix(roles, role, method, path, allowed):
    client, csrf = roles[role]
    response = getattr(client, method)(path, data={'csrf_token': csrf})
    assert response.status_code == allowed


@pytest.mark.parametrize('role,expected', [('lecteur', 403), ('opérateur', 403), ('admin', 302)])
def test_schedule_creation_requires_admin(roles, role, expected):
    client, csrf = roles[role]
    response = client.post('/scheduling', data={'csrf_token': csrf, 'frequency': 'quotidien', 'enabled': '0'})
    assert response.status_code == expected
    assert RunSchedule.query.count() == (1 if role == 'admin' else 0)


@pytest.fixture
def anomaly_row(roles):
    run = Run(status='SUCCESS', started_at='2026-01-01T00:00:00+00:00')
    db.session.add(run)
    db.session.flush()
    row = Anomaly(run_id=run.id, code='NO_MATCH', severity='info', state='ouverte',
                  tracking_key=json.dumps(['NO_MATCH', 'vm-1']), description='observation',
                  created_at='2026-01-01T00:00:00+00:00')
    db.session.add(row)
    db.session.commit()
    return row


def exception_payload():
    return {'code': 'NO_MATCH', 'target': 'vm-1', 'justification': 'maintenance',
            'expires_at': (datetime.now(UTC) + timedelta(days=2)).isoformat()}


@pytest.mark.parametrize('role,expected', [('lecteur', 403), ('opérateur', 302), ('admin', 302)])
def test_web_acknowledgement_permissions(roles, anomaly_row, role, expected):
    client, csrf = roles[role]
    response = client.post(f'/anomalies/{anomaly_row.id}/acknowledge', data={'csrf_token': csrf})
    db.session.refresh(anomaly_row)
    assert (response.status_code, anomaly_row.state) == (expected, 'ouverte' if role == 'lecteur' else 'acquittée')


@pytest.mark.parametrize('role,expected', [('lecteur', 403), ('opérateur', 200), ('admin', 200)])
def test_api_acknowledgement_permissions(orm_app, roles, anomaly_row, role, expected):
    headers = role_headers(orm_app, role)
    response = orm_app.test_client().post(f'/api/anomalies/{anomaly_row.id}/acknowledge', headers=headers)
    db.session.refresh(anomaly_row)
    assert (response.status_code, anomaly_row.state) == (expected, 'ouverte' if role == 'lecteur' else 'acquittée')


@pytest.mark.parametrize('role,expected', [('lecteur', 403), ('opérateur', 302), ('admin', 302)])
def test_web_exception_creation_permissions(roles, anomaly_row, role, expected):
    client, csrf = roles[role]
    response = client.post('/anomaly-exceptions', data={**exception_payload(), 'csrf_token': csrf})
    assert (response.status_code, AnomalyException.query.count()) == (expected, 0 if role == 'lecteur' else 1)


@pytest.mark.parametrize('role,expected', [('lecteur', 403), ('opérateur', 302), ('admin', 302)])
def test_web_exception_deletion_permissions(roles, anomaly_row, role, expected):
    admin, csrf = roles['admin']
    admin.post('/anomaly-exceptions', data={**exception_payload(), 'csrf_token': csrf})
    row = AnomalyException.query.one()
    client, token = roles[role]
    response = client.post(f'/anomaly-exceptions/{row.id}/delete', data={'csrf_token': token})
    assert (response.status_code, AnomalyException.query.count()) == (expected, 1 if role == 'lecteur' else 0)


def role_headers(app, role):
    username = {'lecteur': 'reader', 'opérateur': 'operator', 'admin': 'admin'}[role]
    password = os.environ['ADMIN_PASSWORD'] if role == 'admin' else 'test-password-123'
    return login_api(app, username, password)


@pytest.mark.parametrize('role,expected', [('lecteur', 403), ('opérateur', 201), ('admin', 201)])
def test_api_exception_creation_permissions(orm_app, roles, anomaly_row, role, expected):
    response = orm_app.test_client().post('/api/anomaly-exceptions', json=exception_payload(),
                                          headers=role_headers(orm_app, role))
    assert (response.status_code, AnomalyException.query.count()) == (expected, 0 if role == 'lecteur' else 1)


@pytest.mark.parametrize('role,expected', [('lecteur', 403), ('opérateur', 204), ('admin', 204)])
def test_api_exception_deletion_permissions(orm_app, roles, anomaly_row, role, expected):
    admin, csrf = roles['admin']
    admin.post('/anomaly-exceptions', data={**exception_payload(), 'csrf_token': csrf})
    row = AnomalyException.query.one()
    response = orm_app.test_client().delete(f'/api/anomaly-exceptions/{row.id}',
                                             headers=role_headers(orm_app, role))
    assert (response.status_code, AnomalyException.query.count()) == (expected, 1 if role == 'lecteur' else 0)


@pytest.mark.parametrize('role', ['lecteur', 'opérateur', 'admin'])
def test_metrics_requires_web_session_even_with_jwt(orm_app, roles, role):
    response = orm_app.test_client().get('/metrics', headers=role_headers(orm_app, role))
    assert (response.status_code, response.headers.get('Location')) == (302, '/login?next=/metrics')


@pytest.mark.parametrize('method', ['put', 'delete'])
def test_read_route_rejects_mutation_without_information_leak(orm_app, roles, method):
    response = getattr(orm_app.test_client(), method)('/api/inventory',
                                                      headers=role_headers(orm_app, 'lecteur'))
    assert response.status_code == 403
    assert b'items' not in response.data


@pytest.mark.parametrize('role,path,method,expected', [
    ('lecteur', '/api/runs', 'get', 200),
    ('opérateur', '/api/runs', 'get', 200),
    ('admin', '/api/runs', 'get', 200),
    ('lecteur', '/api/inventory', 'get', 200),
    ('lecteur', '/api/runs', 'post', 403),
    ('opérateur', '/api/runs', 'post', 202),
    ('admin', '/api/runs', 'post', 202),
    ('lecteur', '/api/runs', 'put', 403),
    ('opérateur', '/api/runs', 'patch', 403),
    ('admin', '/api/runs', 'put', 403),
])
def test_api_role_matrix(orm_app, roles, role, path, method, expected):
    username = {'lecteur': 'reader', 'opérateur': 'operator', 'admin': 'admin'}[role]
    password = os.environ['ADMIN_PASSWORD'] if role == 'admin' else 'test-password-123'
    headers = login_api(orm_app, username, password)
    response = getattr(orm_app.test_client(), method)(path, headers=headers)
    assert response.status_code == expected


def test_disabled_account_revokes_issued_jwt(orm_app, roles):
    headers = login_api(orm_app, 'reader', 'test-password-123')
    assert orm_app.test_client().get('/api/runs', headers=headers).status_code == 200
    admin, csrf = roles['admin']
    account = Account.query.filter_by(username='reader').one()
    assert admin.post(f'/accounts/{account.id}', data={'action': 'disable', 'csrf_token': csrf}).status_code == 302
    assert orm_app.test_client().get('/api/runs', headers=headers).status_code == 401


def test_role_change_revokes_existing_privilege(orm_app, roles):
    operator, _ = roles['opérateur']
    admin, csrf = roles['admin']
    account = Account.query.filter_by(username='operator').one()
    assert admin.post(f'/accounts/{account.id}', data={'action': 'role', 'role': 'lecteur',
                                                       'csrf_token': csrf}).status_code == 302
    assert operator.post('/dashboard/run', data={}).status_code == 403


def test_password_reset_invalidates_old_credentials(orm_app, roles):
    admin, csrf = roles['admin']
    account = Account.query.filter_by(username='reader').one()
    replacement = '-'.join(('reset', 'only', 'credential', '123'))
    response = admin.post(f'/accounts/{account.id}', data={'action': 'password',
                            'password': replacement, 'csrf_token': csrf})
    assert response.status_code == 302
    assert orm_app.test_client().post('/api/login', json={'username': 'reader',
                               'password': 'test-password-123'}).status_code == 401
    assert orm_app.test_client().post('/api/login', json={'username': 'reader',
                                 'password': replacement}).status_code == 200
