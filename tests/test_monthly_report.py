import os
import sys
from types import SimpleNamespace

import pytest

from app import db
from app.models import Anomaly, Asset, ConsolidatedAsset, NodeCapacityObservation, Run
from test_accounts_ev48 import create as create_account


@pytest.fixture
def access(orm_app):
    client = orm_app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf_token']
    assert client.post('/login', data={'username': 'admin', 'password': os.environ['ADMIN_PASSWORD'],
                                       'csrf_token': csrf}).status_code == 302
    with client.session_transaction() as session:
        csrf = session['csrf_token']
    for username, role in [('operator', 'opérateur'), ('reader', 'lecteur')]:
        assert create_account(client, csrf, username=username, role=role).status_code == 302
        with client.session_transaction() as session:
            csrf = session['csrf_token']

    def headers(username='admin'):
        password = os.environ['ADMIN_PASSWORD'] if username == 'admin' else 'test-password-123'
        response = orm_app.test_client().post('/api/login', json={'username': username, 'password': password})
        assert response.status_code == 200
        return {'Authorization': 'Bearer ' + response.json['access_token']}

    return client, headers


def run(started, status='SUCCESS'):
    item = Run(status=status, started_at=started)
    db.session.add(item)
    db.session.commit()
    return item


def vm(item, vm_id, name):
    asset = Asset(run_id=item.id, vm_id=vm_id, vm_name=name)
    db.session.add(asset)
    db.session.flush()
    db.session.add(ConsolidatedAsset(run_id=item.id, asset_id=asset.id, match_status='MATCHED_NAME'))
    db.session.commit()


def report(client, headers, month='2026-02'):
    return client.get('/api/reports/monthly', query_string={'month': month}, headers=headers)


def test_real_monthly_pdf_has_pdf_signature_and_four_sections(access):
    try:
        import weasyprint  # noqa: F401
    except OSError as error:
        pytest.skip(f'WeasyPrint system library unavailable: {error}')
    client, headers = access
    run('2026-02-12T12:00:00Z')
    response = report(client, headers())
    assert response.status_code == 200
    assert response.mimetype == 'application/pdf'
    assert response.data.startswith(b'%PDF')


def test_monthly_html_contains_four_sections(access, monkeypatch):
    import app.monthly_report as module
    client, headers = access
    run('2026-02-12T12:00:00Z')
    captured = {}

    class Renderer:
        def __init__(self, *, string, url_fetcher):
            captured['html'] = string

        def write_pdf(self):
            return b'%PDF'

    monkeypatch.setitem(sys.modules, 'weasyprint', SimpleNamespace(HTML=Renderer))
    assert report(client, headers()).status_code == 200
    assert all(section in captured['html'] for section in (
        'Synthèse du dernier run du mois', 'Anomalies par code',
        'VM apparues et disparues', 'Prévisions de capacité par nœud'))


def test_month_without_runs_returns_clear_json_not_pdf(access):
    client, headers = access
    response = report(client, headers())
    assert (response.status_code, response.get_json(), response.mimetype) == (
        404, {'error': 'No runs for this month'}, 'application/json')


def test_report_requires_authentication(access):
    client, _ = access
    assert report(client, {}).status_code == 401


@pytest.mark.parametrize('username,expected', [('admin', 404), ('operator', 404), ('reader', 403)])
def test_monthly_report_enforces_admin_operator_and_reader_roles(access, username, expected):
    client, headers = access
    assert report(client, headers(username)).status_code == expected


@pytest.mark.parametrize('query', [{}, {'month': '2026-00'}, {'month': '2026-13'},
                                        {'month': '2026-2'}, {'month': '2026-02&x=1'},
                                        [('month', '2026-02'), ('month', '2026-03')]])
def test_invalid_or_missing_month_is_rejected(access, query):
    client, headers = access
    response = client.get('/api/reports/monthly', query_string=query, headers=headers())
    assert (response.status_code, response.get_json()) == (400, {'error': 'Invalid month'})


@pytest.mark.parametrize('month,inside,outside', [
    ('2026-02', '2026-02-28T23:59:59Z', '2026-03-01T00:00:00Z'),
    ('2026-12', '2026-12-31T23:59:59Z', '2027-01-01T00:00:00Z'),
])
def test_calendar_end_is_exclusive(access, monkeypatch, month, inside, outside):
    import app.monthly_report as module
    client, headers = access
    selected = run(inside)
    run(outside)
    captured = {}
    monkeypatch.setattr(module, 'render_template', lambda template, **data: captured.update(data) or '<p>ok</p>')
    monkeypatch.setitem(sys.modules, 'weasyprint', SimpleNamespace(
        HTML=lambda **kwargs: type('Renderer', (), {'write_pdf': lambda self: b'%PDF'})()))
    assert report(client, headers(), month).status_code == 200
    assert captured['run'].id == selected.id


def test_report_reuses_snapshot_diff_and_capacity_forecast(access, monkeypatch):
    import app.monthly_report as module
    client, headers = access
    before = run('2026-01-31T23:59:59Z')
    current = run('2026-02-28T23:59:59Z')
    vm(before, 'gone', 'old')
    vm(current, 'new', 'new')
    db.session.add(NodeCapacityObservation(run_id=current.id, node_capacity_name='node-a',
                   node_capacity_measured_at='2026-02-28T00:00:00Z', node_capacity_value=30,
                   node_capacity_limit=100))
    db.session.commit()
    captured = {}
    monkeypatch.setattr(module, 'render_template', lambda template, **data: captured.update(data) or '<p>ok</p>')
    monkeypatch.setitem(sys.modules, 'weasyprint', SimpleNamespace(
        HTML=lambda **kwargs: type('Renderer', (), {'write_pdf': lambda self: b'%PDF'})()))
    assert report(client, headers()).status_code == 200
    assert ([item['vm_id'] for item in captured['appeared']],
            [item['vm_id'] for item in captured['disappeared']], captured['capacities'][0]['node']) == (
                ['new'], ['gone'], 'node-a')


def test_pdf_render_escapes_user_content_and_disallows_resource_fetch(access, monkeypatch):
    import app.monthly_report as module
    client, headers = access
    run('2026-01-31T00:00:00Z')
    current = run('2026-02-01T00:00:00Z')
    db.session.add(Anomaly(run_id=current.id, code='NO_MATCH',
                           description='<img src="https://example.invalid/secret">',
                           created_at='2026-02-01T00:00:00Z'))
    db.session.commit()
    captured = {}

    class Renderer:
        def __init__(self, *, string, url_fetcher):
            captured.update(html=string, fetcher=url_fetcher)

        def write_pdf(self):
            return b'%PDF'

    monkeypatch.setitem(sys.modules, 'weasyprint', SimpleNamespace(HTML=Renderer))
    vm(current, 'unsafe', '<img src="https://example.invalid/secret">')
    assert report(client, headers()).status_code == 200
    assert '&lt;img src=&#34;https://example.invalid/secret&#34;&gt;' in captured['html']
    assert '<img src="https://example.invalid/secret">' not in captured['html']
    with pytest.raises(ValueError):
        captured['fetcher']('https://example.invalid/secret')
