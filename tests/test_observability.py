import json
import logging
from datetime import datetime

from prometheus_client.parser import text_string_to_metric_families

from app import create_app, db
from app.models import Anomaly
from app.observability import run_log
from collector.inventory_runner import run_inventory


class Source:
    def __init__(self, records=(), error=None):
        self.records = records
        self.error = error

    def collect(self):
        if self.error:
            raise self.error
        return self.records


def value(body, name):
    samples = [sample for family in text_string_to_metric_families(body)
               for sample in family.samples if sample.name == name]
    assert len(samples) == 1
    assert samples[0].labels == {}
    return samples[0].value


def admin_client(app, password):
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf_token']
    assert client.post('/login', data={'username': 'admin', 'password': password,
                                        'csrf_token': csrf}).status_code == 302
    return client


def test_run_emits_json_utc_with_run_id_without_sensitive_fields(orm_app):
    class Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []

        def emit(self, record):
            self.records.append(record)

    capture = Capture()
    run_log.addHandler(capture)
    try:
        run = run_inventory(virtualization_source=Source(), ipam_source=Source())
    finally:
        run_log.removeHandler(capture)
    events = [json.loads(handler.format(record)) for record in capture.records
              for handler in run_log.handlers if getattr(handler, '_cloudinventory_run', False)]
    assert {event['event'] for event in events} == {'inventory_started', 'inventory_succeeded'}
    assert all(event['run_id'] == run.id and datetime.fromisoformat(event['timestamp']).utcoffset().total_seconds() == 0
               and set(event) == {'timestamp', 'level', 'event', 'run_id'} for event in events)


def test_metrics_reject_anonymous_even_on_loopback_or_proxy_headers(orm_app):
    client = orm_app.test_client()
    for headers in ({}, {'X-Forwarded-For': '127.0.0.1'}):
        response = client.get('/metrics', headers=headers, environ_overrides={'REMOTE_ADDR': '127.0.0.1'})
        assert response.status_code != 200


def test_admin_metrics_expose_three_unlabelled_families_and_run_changes(orm_app):
    client = admin_client(orm_app, __import__('os').environ['ADMIN_PASSWORD'])
    before = client.get('/metrics')
    assert before.status_code == 200
    assert 'text/plain' in before.content_type
    initial = before.get_data(as_text=True)
    names = ('cloudinventory_run_duration_seconds_count', 'cloudinventory_run_anomalies_total',
             'cloudinventory_collection_errors_total')
    baseline = {name: value(initial, name) for name in names}
    run = run_inventory(virtualization_source=Source([{'vm_id': 'sensitive-vm', 'vm_name': 'sensitive-vm', 'type': 'qemu'}]),
                        ipam_source=Source())
    count = Anomaly.query.filter_by(run_id=run.id).count()
    assert run.status == 'SUCCESS' and count > 0
    db.session.rollback()
    after_success = client.get('/metrics').get_data(as_text=True)
    assert value(after_success, names[0]) == baseline[names[0]] + 1
    assert value(after_success, names[1]) == baseline[names[1]] + count
    assert 'sensitive-vm' not in after_success
    failed = run_inventory(virtualization_source=Source(error=RuntimeError('source unavailable')),
                           ipam_source=Source())
    after_failure = client.get('/metrics').get_data(as_text=True)
    assert failed.status == 'FAIL'
    assert value(after_failure, names[0]) == baseline[names[0]] + 2
    assert value(after_failure, names[2]) == baseline[names[2]] + 1
    assert value(after_failure, names[1]) == baseline[names[1]] + count


def test_factories_do_not_duplicate_run_handlers_or_metrics(orm_app):
    before = len([handler for handler in run_log.handlers if getattr(handler, '_cloudinventory_run', False)])
    create_app(database_uri='sqlite:///:memory:')
    create_app(database_uri='sqlite:///:memory:')
    assert len([handler for handler in run_log.handlers if getattr(handler, '_cloudinventory_run', False)]) == before == 1
