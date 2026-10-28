import pytest

from app import db, notifications
from app.models import Anomaly, AnomalyException, Run
from collector.inventory_runner import run_inventory


class Source:
    def __init__(self, records):
        self.records = records

    def collect(self):
        return self.records


def collect(identifiers):
    return run_inventory(Source([{"vm_id": name, "vm_name": name, "type": "qemu"}
                                 for name in identifiers]), Source([]))


@pytest.fixture
def deliveries(monkeypatch):
    mail, hooks = [], []
    monkeypatch.setenv("SMTP_ENABLED", "true")
    monkeypatch.setenv("WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("USE_MOCK_SMTP", "true")
    monkeypatch.setenv("USE_MOCK_WEBHOOK", "true")
    monkeypatch.setattr(notifications, "_mock_smtp", mail.append)
    monkeypatch.setattr(notifications, "_mock_webhook", hooks.append)
    monkeypatch.setattr(notifications, "_smtp", lambda _: pytest.fail("real SMTP called"))
    monkeypatch.setattr(notifications, "_webhook", lambda _: pytest.fail("real webhook called"))
    return mail, hooks


def test_first_run_notifies_both_channels(orm_app, deliveries):
    mail, hooks = deliveries
    run = collect(["vm-1"])
    assert len(mail) == 1
    assert hooks == [{"run_id": run.id, "status": "SUCCESS", "anomaly_count": 1,
                      "finished_at": run.finished_at}]


def test_identical_second_run_does_not_repeat_alert(orm_app, deliveries):
    mail, hooks = deliveries
    collect(["vm-1"])
    collect(["vm-1"])
    assert len(mail) == len(hooks) == 1


def test_new_target_notifies_after_identical_run(orm_app, deliveries):
    _, hooks = deliveries
    collect(["vm-1"])
    second = collect(["vm-1", "vm-2"])
    assert hooks[-1]["run_id"] == second.id
    assert len(hooks) == 2


def test_new_code_on_same_target_notifies(orm_app, deliveries):
    _, hooks = deliveries
    collect(["vm-1"])
    second = run_inventory(Source([{"vm_id": "vm-1", "vm_name": "vm-1", "type": "qemu",
                                   "ip_reported": "192.0.2.10"}]),
                           Source([{"ip": "192.0.2.10", "dns_name": "other"}]))
    codes = {row.code for row in Anomaly.query.filter_by(run_id=second.id)}
    assert codes - {"NO_MATCH"}
    assert hooks[-1]["run_id"] == second.id


def test_failed_intermediate_run_is_not_reference(orm_app, deliveries):
    _, hooks = deliveries
    collect(["vm-1"])
    failed = Run(status="FAIL", started_at="2026-01-01T00:00:00+00:00")
    db.session.add(failed)
    db.session.commit()
    collect(["vm-1"])
    assert len(hooks) == 1


def test_reappearing_anomaly_notifies_after_absence(orm_app, deliveries):
    _, hooks = deliveries
    collect(["vm-1"])
    collect([])
    restored = collect(["vm-1"])
    assert [payload["run_id"] for payload in hooks] == [1, restored.id]


@pytest.mark.parametrize("value,previous,current,expected", [
    ("2", 2, 3, True), ("2", 3, 4, False), ("2", 1, 2, False),
    ("0", 0, 1, True), ("10", 10, 11, True),
    ("invalid", 0, 1, False), ("-1", 0, 1, False),
])
def test_threshold_crossing_only_once(monkeypatch, value, previous, current, expected):
    monkeypatch.setenv("ANOMALY_ALERT_THRESHOLD", value)
    assert notifications.should_alert(current, 0, previous) is expected


def test_default_threshold_is_ten(monkeypatch):
    monkeypatch.delenv("ANOMALY_ALERT_THRESHOLD", raising=False)
    assert notifications.should_alert(10, 0, 9) is False
    assert notifications.should_alert(11, 0, 10) is True


def test_acknowledged_previous_anomaly_is_not_notified_again(orm_app, deliveries):
    _, hooks = deliveries
    first = collect(["vm-1"])
    row = Anomaly.query.filter_by(run_id=first.id).one()
    row.state = "acquittée"
    db.session.commit()
    second = collect(["vm-1"])
    assert second.status == "SUCCESS"
    assert len(hooks) == 1


def test_active_exception_never_notifies(orm_app, deliveries):
    _, hooks = deliveries
    db.session.add(AnomalyException(code="NO_MATCH", target="vm-1", justification="maintenance",
                                    expires_at="2999-01-01T00:00:00+00:00", author="admin"))
    db.session.commit()
    collect(["vm-1"])
    collect(["vm-1"])
    assert hooks == []
