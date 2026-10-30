import os
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app import db
from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


@pytest.fixture
def api(orm_app):
    client = orm_app.test_client()
    token = client.post('/api/login', json={
        'username': 'admin', 'password': os.environ['ADMIN_PASSWORD'],
    }).get_json()['access_token']
    return client, {'Authorization': f'Bearer {token}'}


def run(status='SUCCESS'):
    item = Run(status=status, started_at='2026-01-01T00:00:00Z')
    db.session.add(item)
    db.session.flush()
    return item


def asset(item, name, node='host', ip=None, ipam=False):
    vm = Asset(run_id=item.id, vm_id=name, vm_name=name, node=node, type='qemu', ip_reported=ip)
    db.session.add(vm)
    db.session.flush()
    record = None
    if ipam:
        record = IpamRecord(run_id=item.id, ip=ip)
        db.session.add(record)
        db.session.flush()
    db.session.add(ConsolidatedAsset(run_id=item.id, asset_id=vm.id,
                                     ipam_record_id=record.id if record else None,
                                     ip_final=ip, match_status='MATCHED_NAME'))
    db.session.flush()
    return vm, record


def anomaly(item, *, vm=None, record=None, severity='majeure', state='ouverte'):
    db.session.add(Anomaly(run_id=item.id, asset_id=vm.id if vm else None,
                           ipam_record_id=record.id if record else None,
                           code='NO_MATCH', description='test', created_at='2026-01-01',
                           severity=severity, state=state))
    db.session.flush()


def graph(api):
    client, headers = api
    response = client.get('/api/topology', headers=headers)
    assert response.status_code == 200
    return response.get_json()


def by_id(data):
    return {node['id']: node for node in data['nodes']}


def test_empty_graph_has_no_run_or_elements(api):
    assert graph(api) == {'run_id': None, 'nodes': [], 'edges': []}


def test_latest_failed_or_running_run_replaces_previous_success(api):
    previous = run()
    asset(previous, 'previous', ip='10.0.0.1')
    current = run('RUNNING')
    vm, _ = asset(current, 'current', ip='10.0.0.2')
    data = graph(api)
    assert data['run_id'] == current.id and {node['id'] for node in data['nodes']} == {
        'node:known:host', f'vm:{vm.id}', 'ip:10.0.0.2',
    }


def test_graph_links_host_to_vm_and_vm_to_ip(api):
    vm, _ = asset(run(), 'machine', ip='192.0.2.1')
    data = graph(api)
    assert {tuple(edge[key] for key in ('source', 'target')) for edge in data['edges']} == {
        ('node:known:host', f'vm:{vm.id}'), (f'vm:{vm.id}', 'ip:192.0.2.1'),
    }


def test_missing_host_uses_unknown_node_without_inventing_ip(api):
    vm, _ = asset(run(), 'orphan', node=None)
    data = graph(api)
    assert (by_id(data)['node:unknown']['label'], data['edges']) == (
        'inconnu', [{'source': 'node:unknown', 'target': f'vm:{vm.id}'}],
    )


@pytest.mark.parametrize('severity,color', [
    ('info', '#2563eb'), ('mineure', '#eab308'),
    ('majeure', '#f97316'), ('critique', '#dc2626'),
])
def test_open_anomaly_colors_linked_host_vm_and_ip(api, severity, color):
    item = run()
    vm, record = asset(item, 'affected', ip='192.0.2.3', ipam=True)
    anomaly(item, record=record, severity=severity)
    nodes = by_id(graph(api))
    assert {(nodes[key]['anomaly_severity'], nodes[key]['color']) for key in (
        'node:known:host', f'vm:{vm.id}', 'ip:192.0.2.3',
    )} == {(severity, color)}


def test_maximum_open_severity_propagates_without_contaminating_other_vm(api):
    item = run()
    first, _ = asset(item, 'first', ip='192.0.2.4')
    second, _ = asset(item, 'second', ip='192.0.2.5')
    anomaly(item, vm=first, severity='mineure')
    anomaly(item, vm=first, severity='critique')
    nodes = by_id(graph(api))
    assert (nodes[f'vm:{first.id}']['anomaly_severity'],
            nodes['node:known:host']['anomaly_severity'],
            nodes[f'vm:{second.id}']['anomaly_severity']) == ('critique', 'critique', None)


def test_closed_and_old_anomalies_do_not_color_current_graph(api):
    old = run()
    old_vm, _ = asset(old, 'old')
    anomaly(old, vm=old_vm, severity='critique')
    current = run('FAIL')
    vm, _ = asset(current, 'new', ip='192.0.2.6')
    anomaly(current, vm=vm, severity='majeure', state='résolue')
    assert {node['color'] for node in graph(api)['nodes']} == {'#64748b'}


def test_hostile_labels_remain_json_text_and_ids_do_not_collide(api):
    item = run()
    hostile = '<img src=x onerror=alert(1)>'
    first, _ = asset(item, hostile, node='1', ip='1')
    second, _ = asset(item, 'second', node='node:known:1', ip='2')
    data = graph(api)
    nodes = by_id(data)
    assert nodes[f'vm:{first.id}']['label'] == hostile
    assert {f'vm:{first.id}', f'vm:{second.id}', 'node:known:1',
            'node:known:node:known:1', 'ip:1', 'ip:2'} <= set(nodes)


def test_anonymous_api_is_denied_and_invalid_query_is_rejected(api):
    client, headers = api
    assert (client.get('/api/topology').status_code,
            client.get('/api/topology?run_id=1', headers=headers).status_code) == (401, 400)


def test_topology_page_requires_login_and_has_local_scripts_and_navigation(orm_app):
    client = orm_app.test_client()
    assert client.get('/topology').status_code == 302
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf_token']
    assert client.post('/login', data={'username': 'admin',
                                      'password': os.environ['ADMIN_PASSWORD'],
                                      'csrf_token': csrf}).status_code == 302
    response = client.get('/topology')
    tags = Tags()
    tags.feed(response.get_data(as_text=True))
    scripts = [attrs for tag, attrs in tags.tags if tag == 'script']
    assert response.status_code == 200
    assert {'/static/topology.js', '/static/vendor/cytoscape/cytoscape.min.js'} <= {
        attrs.get('src') for attrs in scripts}
    assert any(tag == 'a' and attrs.get('href') == '/topology' for tag, attrs in tags.tags)
    assert all(attrs.get('src', '').startswith('/') and 'nonce' not in attrs
               for attrs in scripts)
    assert not any(tag in ('script', 'link') and attrs.get('src', attrs.get('href', '')).startswith(('http:', 'https:', '//'))
                   for tag, attrs in tags.tags)


def test_cytoscape_vendor_is_served_with_version_and_license(api):
    client, _ = api
    vendor = client.get('/static/vendor/cytoscape/cytoscape.min.js')
    license_response = client.get('/static/vendor/cytoscape/LICENSE.md')
    assert vendor.status_code == license_response.status_code == 200
    assert '3.34.4' in vendor.get_data(as_text=True)
    assert 'MIT' in license_response.get_data(as_text=True)


def test_javascript_uses_json_contract_and_inserts_labels_as_text():
    source = (Path(__file__).resolve().parents[1] / 'app/static/topology.js').read_text()
    assert all(f'data.{field}' in source for field in ('nodes', 'edges', 'run_id'))
    assert all(f'node.{field}' in source for field in ('id', 'label', 'type', 'color', 'anomaly_severity'))
    assert all(f'edge.{field}' in source for field in ('source', 'target'))
    assert 'textContent' in source and 'innerHTML' not in source
