from datetime import UTC, datetime, timedelta

import pytest

from app import db
from app.models import Anomaly, Asset
from collector.inventory_runner import run_inventory


class Source:
    def __init__(self, records=None, error=None):
        self.records = records if records is not None else []
        self.error = error

    def collect(self):
        if self.error:
            raise self.error
        return self.records


def collect(snapshot_source=None, backup_source=None):
    return run_inventory(
        virtualization_source=Source([{"vm_id": "101", "vm_name": "one", "type": "qemu"}]),
        ipam_source=Source([]), snapshot_source=snapshot_source, backup_source=backup_source)


def anomalies(run, code):
    return db.session.query(Anomaly).filter_by(run_id=run.id, code=code).all()


def date(days):
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def test_successful_empty_backup_collection_confirms_no_recent_backup(orm_app):
    run = collect(backup_source=Source([]))
    assert db.session.query(Asset).filter_by(run_id=run.id).one().backup_collection_status == "réussie"
    assert len(anomalies(run, "NO_RECENT_BACKUP")) == 1
    assert anomalies(run, "NO_RECENT_BACKUP")[0].asset_id == db.session.query(Asset).filter_by(run_id=run.id).one().id
    assert anomalies(run, "NO_RECENT_BACKUP")[0].severity is None


@pytest.mark.parametrize("source,status", [(None, "inconnue"),
                                            (Source(error=RuntimeError("PBS unavailable")), "échouée")])
def test_unavailable_backup_collection_does_not_claim_absence(orm_app, source, status):
    run = collect(backup_source=source)
    assert db.session.query(Asset).filter_by(run_id=run.id).one().backup_collection_status == status
    assert anomalies(run, "NO_RECENT_BACKUP") == []


@pytest.mark.parametrize("age,expected", [(1, 0), (90, 1)])
def test_recent_backup_threshold_uses_collection_date(orm_app, monkeypatch, age, expected):
    monkeypatch.setenv("RECENT_BACKUP_DAYS", "30")
    run = collect(backup_source=Source([{"vm_id": "101", "backup_id": "b",
                                         "backup_created_at": date(age)}]))
    assert len(anomalies(run, "NO_RECENT_BACKUP")) == expected


def test_unknown_backup_date_does_not_prove_absence(orm_app):
    run = collect(backup_source=Source([{"vm_id": "101", "backup_id": "b", "backup_created_at": None}]))
    assert anomalies(run, "NO_RECENT_BACKUP") == []


@pytest.mark.parametrize("age,expected", [(1, 0), (90, 1)])
def test_snapshot_threshold_detects_only_forgotten_snapshots(orm_app, monkeypatch, age, expected):
    monkeypatch.setenv("FORGOTTEN_SNAPSHOT_DAYS", "30")
    run = collect(snapshot_source=Source([{"vm_id": "101", "snapshot_id": "snap",
                                           "snapshot_created_at": date(age)}]))
    found = anomalies(run, "FORGOTTEN_SNAPSHOT")
    assert len(found) == expected
    if found:
        assert found[0].severity is None


def test_undated_snapshot_cannot_be_declared_forgotten(orm_app):
    run = collect(snapshot_source=Source([{"vm_id": "101", "snapshot_id": "snap",
                                           "snapshot_created_at": None}]))
    assert anomalies(run, "FORGOTTEN_SNAPSHOT") == []


def test_failed_collection_does_not_resolve_previous_backup_anomaly(orm_app):
    first = collect(backup_source=Source([]))
    previous = anomalies(first, "NO_RECENT_BACKUP")[0]
    second = collect(backup_source=Source(error=RuntimeError("PBS unavailable")))
    assert db.session.query(Asset).filter_by(run_id=second.id).one().backup_collection_status == "échouée"
    assert anomalies(second, "NO_RECENT_BACKUP") == []
    assert db.session.get(Anomaly, previous.id).state != "résolue"


def test_existing_no_match_detection_is_preserved_with_new_sources(orm_app):
    run = collect(snapshot_source=Source([]), backup_source=Source([]))
    assert len(anomalies(run, "NO_MATCH")) == 1
