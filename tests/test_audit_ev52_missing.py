import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from app import db
from app.models import Account, Anomaly, AnomalyException, Audit, Run, RunSchedule
from app.scheduling import fire_schedule


def login(client, username, password):
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf_token']
    assert client.post('/login', data={'username': username, 'password': password,
                                       'csrf_token': csrf}).status_code == 302
    with client.session_transaction() as session:
        return session['csrf_token']


def bearer(client):
    token = client.post('/api/login', json={'username': 'admin',
                                             'password': os.environ['ADMIN_PASSWORD']}).get_json()['access_token']
    return {'Authorization': f'Bearer {token}'}


def new_anomaly():
    run = Run(status='SUCCESS', started_at='2026-01-01T00:00:00+00:00')
    db.session.add(run)
    db.session.flush()
    row = Anomaly(run_id=run.id, code='NO_MATCH', severity='info', state='ouverte',
                  tracking_key=json.dumps(['NO_MATCH', 'vm-1']),
                  description='observation', created_at='2026-01-01T00:00:00+00:00')
    db.session.add(row)
    db.session.commit()
    return row


def test_jwt_run_records_one_jwt_actor_without_credentials(orm_app):
    client = orm_app.test_client()
    headers = bearer(client)
    before = Audit.query.count()
    response = client.post('/api/runs', headers=headers)
    assert response.status_code in (200, 201, 202)
    traces = Audit.query.order_by(Audit.id).all()[before:]
    assert len(traces) == 1
    assert traces[0].action == 'run manuel'
    assert traces[0].actor_identity == 'local:admin'
    assert traces[0].target.startswith('run:')
    assert datetime.now(UTC) - datetime.fromisoformat(traces[0].created_at) < timedelta(seconds=30)
    assert os.environ['ADMIN_PASSWORD'] not in str((traces[0].actor_identity, traces[0].action, traces[0].target))


def test_scheduled_run_records_system_actor_once(orm_app, monkeypatch):
    import collector.async_runner as runner
    schedule = RunSchedule(frequency='horaire', enabled=True)
    db.session.add(schedule)
    db.session.commit()
    before = Audit.query.count()

    def start():
        run = Run(status='RUNNING', started_at=datetime.now(UTC).isoformat())
        db.session.add(run)
        db.session.commit()
        return run.id

    monkeypatch.setattr(runner, 'start_inventory', start)
    fire_schedule(orm_app, schedule.id)
    traces = Audit.query.order_by(Audit.id).all()[before:]
    assert [(trace.action, trace.actor_identity) for trace in traces] == [('run planifié', 'système')]
    assert traces[0].target == f'run:{Run.query.order_by(Run.id.desc()).first().id}'


def test_jwt_acknowledgement_creates_one_trace_and_repeated_action_does_not(orm_app):
    row = new_anomaly()
    client = orm_app.test_client()
    headers = bearer(client)
    before = Audit.query.count()
    assert client.post(f'/api/anomalies/{row.id}/acknowledge', headers=headers).status_code == 200
    assert client.post(f'/api/anomalies/{row.id}/acknowledge', headers=headers).status_code == 409
    traces = Audit.query.order_by(Audit.id).all()[before:]
    assert [(trace.action, trace.target, trace.actor_identity) for trace in traces] == [
        ('anomalie acquittée', f'anomalie:{row.id}', 'local:admin')]


def test_jwt_exception_creation_and_removal_preserve_prior_trace(orm_app):
    row = new_anomaly()
    client = orm_app.test_client()
    headers = bearer(client)
    before = Audit.query.count()
    payload = {'code': 'NO_MATCH', 'target': 'vm-1', 'justification': 'maintenance',
               'expires_at': (datetime.now(UTC) + timedelta(days=2)).isoformat()}
    created = client.post('/api/anomaly-exceptions', json=payload, headers=headers)
    assert created.status_code == 201
    identifier = created.get_json()['id']
    assert client.delete(f'/api/anomaly-exceptions/{identifier}', headers=headers).status_code in (200, 204)
    assert [(trace.action, trace.target, trace.actor_identity) for trace in Audit.query.order_by(Audit.id).all()[before:]] == [
        ('exception créée', f'exception:{identifier}', 'local:admin'),
        ('exception supprimée', f'exception:{identifier}', 'local:admin')]
    assert Anomaly.query.get(row.id) is not None


@pytest.mark.parametrize('role', ['lecteur', 'opérateur'])
def test_non_admin_roles_cannot_read_audit(orm_app, role):
    admin = orm_app.test_client()
    csrf = login(admin, 'admin', os.environ['ADMIN_PASSWORD'])
    credential = 'test-' + 'pass' + 'word-123'
    assert admin.post('/accounts', data={'csrf_token': csrf, 'username': 'restricted',
                                         'password': credential, 'role': role}).status_code == 302
    client = orm_app.test_client()
    login(client, 'restricted', credential)
    assert client.get('/audit').status_code == 403


def test_disable_and_password_reset_append_distinct_traces_without_hash_or_password(orm_app):
    client = orm_app.test_client()
    csrf = login(client, 'admin', os.environ['ADMIN_PASSWORD'])
    credential = 'test-' + 'pass' + 'word-123'
    assert client.post('/accounts', data={'csrf_token': csrf, 'username': 'audited',
                                         'password': credential, 'role': 'lecteur'}).status_code == 302
    account = Account.query.filter_by(username='audited').one()
    before = Audit.query.count()
    replacement = 'new-' + 'password-123'
    assert client.post(f'/accounts/{account.id}', data={'csrf_token': csrf, 'action': 'password',
                                                        'password': replacement}).status_code == 302
    assert client.post(f'/accounts/{account.id}', data={'csrf_token': csrf, 'action': 'disable'}).status_code == 302
    traces = Audit.query.order_by(Audit.id).all()[before:]
    assert [(trace.action, trace.target) for trace in traces] == [
        ('compte réinitialisé', f'compte:{account.id}'), ('compte désactivé', f'compte:{account.id}')]
    for trace in Audit.query.all():
        fields = str((trace.actor_identity, trace.action, trace.target))
        assert all(secret not in fields for secret in (credential, replacement, account.local_password_hash))
