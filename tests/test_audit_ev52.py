import os
from datetime import UTC, datetime
from html.parser import HTMLParser

import pytest

from app import db
from app.models import Account, Audit, Run, RunSchedule
from collector.purge import purge_old_runs


class AuditRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.in_body = False
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == 'tbody':
            self.in_body = True
        if tag == 'tr' and self.in_body:
            self.rows.append([])
        if tag == 'td' and self.in_body:
            self.rows[-1].append('')
            self.in_cell = True

    def handle_endtag(self, tag):
        if tag == 'td':
            self.in_cell = False
        if tag == 'tbody':
            self.in_body = False

    def handle_data(self, data):
        if self.in_cell:
            self.rows[-1][-1] += data


@pytest.fixture
def admin(orm_app):
    client = orm_app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf_token']
    assert client.post('/login', data={'username': 'admin', 'password': os.environ['ADMIN_PASSWORD'],
                                       'csrf_token': csrf}).status_code == 302
    with client.session_transaction() as session:
        return client, session['csrf_token']


def rows(response):
    parser = AuditRows()
    parser.feed(response.get_data(as_text=True))
    return parser.rows


def test_web_run_records_single_admin_action_with_date_and_target(orm_app, admin, monkeypatch):
    from collector import inventory_runner
    monkeypatch.setattr(inventory_runner, 'run_inventory', lambda **kwargs: None)
    client, csrf = admin
    before = Audit.query.count()
    response = client.post('/dashboard/run', data={'csrf_token': csrf})
    assert response.status_code in (200, 202, 302)
    traces = Audit.query.filter(Audit.id > before).all()
    assert len(traces) == 1
    trace = traces[0]
    assert trace.action == 'run manuel' and trace.target.startswith('run:')
    assert trace.actor_identity == 'local:admin'
    assert abs((datetime.now(UTC) - datetime.fromisoformat(trace.created_at)).total_seconds()) < 30


def test_account_creation_and_role_change_record_only_effective_actions(orm_app, admin):
    client, csrf = admin
    initial = Audit.query.count()
    data = {'csrf_token': csrf, 'username': 'audit-reader', 'password': 'test-password-123', 'role': 'lecteur'}
    assert client.post('/accounts', data=data).status_code == 302
    account = Account.query.filter_by(username='audit-reader').one()
    assert client.post(f'/accounts/{account.id}', data={'csrf_token': csrf, 'action': 'role',
                                                       'role': 'opérateur'}).status_code == 302
    assert [(row.action, row.target) for row in Audit.query.filter(Audit.id > initial).order_by(Audit.id)] == [
        ('compte créé', f'compte:{account.id}'), ('compte modifié', f'compte:{account.id}')]
    assert all('test-password-123' not in str((row.actor_identity, row.action, row.target))
               for row in Audit.query.all())


def test_failed_account_creation_does_not_append_audit(orm_app, admin):
    client, csrf = admin
    before = Audit.query.count()
    response = client.post('/accounts', data={'csrf_token': csrf, 'username': '',
                                              'password': 'test-password-123', 'role': 'lecteur'})
    assert response.status_code == 400
    assert Audit.query.count() == before


def test_schedule_creation_and_update_have_distinct_traces(orm_app, admin):
    client, csrf = admin
    before = Audit.query.count()
    assert client.post('/scheduling', data={'csrf_token': csrf, 'frequency': 'horaire', 'enabled': '1'}).status_code == 302
    schedule = RunSchedule.query.one()
    assert client.post('/scheduling', data={'csrf_token': csrf, 'frequency': 'quotidien',
                                            'enabled': '1', 'schedule_id': str(schedule.id)}).status_code == 302
    assert [(row.action, row.target) for row in Audit.query.filter(Audit.id > before).order_by(Audit.id)] == [
        ('planification créée', f'planification:{schedule.id}'),
        ('planification modifiée', f'planification:{schedule.id}')]


def test_purging_run_preserves_its_prior_audit_entries(orm_app):
    for _ in range(2):
        db.session.add(Run(status='SUCCESS', started_at='2026-01-01T00:00:00Z'))
        db.session.commit()
    oldest = Run.query.order_by(Run.id).first()
    target = f'run:{oldest.id}'
    prior = [(row.id, row.target) for row in Audit.query.filter_by(target=target).all()]
    assert prior
    db.session.rollback()
    assert purge_old_runs(1) == 1
    assert [(row.id, row.target) for row in Audit.query.filter_by(target=target).all()] == prior


def test_audit_page_filters_each_column_and_dates(orm_app, admin):
    client, _ = admin
    db.session.add(Audit(actor_identity='local:admin', action='action-a', target='run:10', created_at='2026-01-02T12:00:00Z'))
    db.session.commit()
    db.session.add(Audit(actor_identity='système', action='action-b', target='run:11', created_at='2026-02-02T12:00:00Z'))
    db.session.commit()
    for query in ('actor=local%3Aadmin', 'action=action-a', 'target=run%3A10',
                  'since=2026-01-02&until=2026-01-02'):
        filtered = rows(client.get('/audit?' + query))
        assert ['2026-01-02T12:00:00Z', 'local:admin', 'action-a', 'run:10'] in filtered
        assert ['2026-02-02T12:00:00Z', 'système', 'action-b', 'run:11'] not in filtered


def test_audit_page_escapes_target_and_rejects_invalid_dates(orm_app, admin):
    client, _ = admin
    target = '<script>alert(1)</script>'
    db.session.add(Audit(actor_identity='système', action='test', target=target,
                         created_at='2026-01-02T12:00:00Z'))
    db.session.commit()
    response = client.get('/audit')
    assert target not in response.get_data(as_text=True)
    assert target in [row[3] for row in rows(response)]
    assert client.get('/audit?since=not-a-date').status_code == 400


def test_audit_is_admin_only_and_has_no_write_route(orm_app, admin):
    client, csrf = admin
    assert client.get('/audit').status_code == 200
    assert client.post('/audit', data={'csrf_token': csrf}).status_code in (403, 405)
    assert orm_app.test_client().get('/audit').status_code == 302
    assert not any(rule.rule.startswith('/audit/') for rule in orm_app.url_map.iter_rules())
