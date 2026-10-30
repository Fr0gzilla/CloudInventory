import json
from datetime import UTC, datetime, timedelta

import pytest

from app import db
from app.models import Anomaly, AnomalyException, Asset, Run
from collector.anomalies import missing_ipam_fields, required_tag_anomalies
from collector.inventory_runner import run_inventory


class Source:
    def __init__(self, records):
        self.records = records

    def collect(self):
        return self.records


def inventory(*, tags="env:prod owner:ops backup:yes", tenant="team", site="paris",
              status="running", stopped_since=None, vm=True):
    assets = [{"vm_id": "vm-ev18", "vm_name": "vm-ev18", "type": "qemu",
               "status": status, "tags": tags, "vm_stopped_since": stopped_since}] if vm else []
    records = [{"ip": "192.0.2.42", "dns_name": "vm-ev18", "tenant": tenant,
                "site": site}] if vm else []
    return run_inventory(Source(assets), Source(records))


def anomalies(run, code):
    return Anomaly.query.filter_by(run_id=run.id, code=code).order_by(Anomaly.id).all()


@pytest.mark.parametrize("tag", ["env", "owner", "backup"])
def test_each_missing_required_tag_is_reported_independently(tag):
    asset = {"vm_id": "1", "tags": " ".join(f"{name}:valid" for name in ("env", "owner", "backup") if name != tag)}
    assert [row["description"] for row in required_tag_anomalies(asset)] == [f"Missing required tag: {tag}"]


def test_all_required_tags_present_produce_no_compliance_anomaly():
    assert required_tag_anomalies({"vm_id": "1", "tags": "env:prod owner:ops backup:yes"}) == []


@pytest.mark.parametrize("field,code", [("tenant", "MISSING_TENANT"), ("site", "MISSING_SITE")])
def test_blank_ipam_field_is_reported_on_its_observation(field, code):
    record = {"ip": "192.0.2.42", "tenant": "team", "site": "paris"}
    record[field] = "  "
    assert [(row["code"], row["ipam_record"]["ip"]) for row in missing_ipam_fields([record])] == [(code, "192.0.2.42")]


def test_filled_ipam_fields_produce_no_compliance_anomaly():
    assert missing_ipam_fields([{"tenant": "team", "site": "paris"}]) == []


@pytest.mark.parametrize("code,kwargs", [
    ("MISSING_REQUIRED_TAG", {"tags": "env:prod owner:ops"}),
    ("MISSING_TENANT", {"tenant": ""}),
    ("MISSING_SITE", {"site": ""}),
    ("VM_ZOMBIE", {"status": "stopped", "stopped_since": (datetime.now(UTC) - timedelta(days=32)).isoformat()}),
])
def test_new_anomaly_persists_on_own_run_without_invented_severity(orm_app, code, kwargs):
    first = inventory(**kwargs)
    second = inventory(**kwargs)
    selected = anomalies(second, code)
    assert selected and all(row.run_id == second.id and row.severity is None for row in selected)
    assert anomalies(first, code) and {row.id for row in selected}.isdisjoint(row.id for row in anomalies(first, code))


def test_missing_tags_have_separate_exception_targets(orm_app):
    run = inventory(tags="")
    targets = {json.loads(row.tracking_key)[1] for row in anomalies(run, "MISSING_REQUIRED_TAG")}
    assert targets == {"vm-ev18:env", "vm-ev18:owner", "vm-ev18:backup"}


def test_exception_covers_only_its_tag_and_preserves_other_missing_tags(orm_app):
    from app.anomaly_exceptions import anomaly_coverage

    run = inventory(tags="")
    db.session.add(AnomalyException(code="MISSING_REQUIRED_TAG", target="vm-ev18:env",
                                    justification="maintenance", author="test",
                                    expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat()))
    db.session.commit()
    rows = anomalies(run, "MISSING_REQUIRED_TAG")
    covered = anomaly_coverage(rows)
    coverage = {json.loads(row.tracking_key)[1]: covered[row.id] is not None for row in rows}
    assert coverage == {"vm-ev18:env": True, "vm-ev18:owner": False, "vm-ev18:backup": False}


@pytest.mark.parametrize("days,expected", [(29, False), (30, True), (31, True)])
def test_zombie_source_date_requires_strictly_more_than_default_30_days(orm_app, monkeypatch, days, expected):
    monkeypatch.delenv("VM_ZOMBIE_DAYS", raising=False)
    run = inventory(status="stopped", stopped_since=(datetime.now(UTC) - timedelta(days=days, minutes=1)).isoformat())
    assert bool(anomalies(run, "VM_ZOMBIE")) is expected


def test_zombie_threshold_is_configurable(orm_app, monkeypatch):
    monkeypatch.setenv("VM_ZOMBIE_DAYS", "5")
    run = inventory(status="stopped", stopped_since=(datetime.now(UTC) - timedelta(days=6)).isoformat())
    assert len(anomalies(run, "VM_ZOMBIE")) == 1


def test_first_stopped_observation_without_source_date_has_no_invented_age(orm_app):
    assert anomalies(inventory(status="stopped"), "VM_ZOMBIE") == []


@pytest.mark.parametrize("interruption", ["running", "absent"])
def test_resumption_or_absence_breaks_stopped_history(orm_app, monkeypatch, interruption):
    monkeypatch.setenv("VM_ZOMBIE_DAYS", "1")
    previous = inventory(status="stopped")
    db.session.get(Run, previous.id).started_at = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    db.session.commit()
    inventory(status=interruption, vm=interruption != "absent")
    assert anomalies(inventory(status="stopped"), "VM_ZOMBIE") == []


def test_consecutive_dated_stopped_runs_use_existing_history(orm_app, monkeypatch):
    monkeypatch.setenv("VM_ZOMBIE_DAYS", "1")
    previous = inventory(status="stopped")
    db.session.get(Run, previous.id).started_at = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    db.session.commit()
    assert len(anomalies(inventory(status="stopped"), "VM_ZOMBIE")) == 1
