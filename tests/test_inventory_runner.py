from collections import Counter
import json

import pytest

from app import db
from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run
from collector.inventory_runner import _severity_for, run_inventory
from collector.mock_ipam import MockIpamSource
from collector.mock_proxmox import MockProxmoxSource


class Source:
    def __init__(self, records):
        self.records = records

    def collect(self):
        return self.records


def rows(model, run_id):
    return db.session.query(model).filter_by(run_id=run_id).all()


@pytest.mark.parametrize("scenario,expected", [
    ("unchanged", 0),
    ("renamed", 1),
    ("renamed_with_creation", 1),
])
def test_renamed_anomaly_records_names_and_current_asset_only(orm_app, scenario, expected):
    first = run_inventory()
    previous = {item.vm_id: item.vm_name for item in rows(Asset, first.id)}
    db.session.rollback()
    second = run_inventory(virtualization_source=MockProxmoxSource(scenario), ipam_source=MockIpamSource())
    current = {item.id: item for item in rows(Asset, second.id)}
    renamed = [item for item in rows(Anomaly, second.id) if item.code == "RENAMED"]
    assert len(renamed) == expected
    for item in renamed:
        asset = current[item.asset_id]
        assert asset.vm_id in previous
        assert previous[asset.vm_id] != asset.vm_name
        assert previous[asset.vm_id] in item.description
        assert asset.vm_name in item.description
    assert all(current[item.asset_id].vm_id != "new-100" for item in renamed)


def test_anomalies_default_to_info_without_severity_configuration(orm_app, monkeypatch):
    monkeypatch.delenv("ANOMALY_SEVERITIES", raising=False)
    run = run_inventory()
    assert {item.severity for item in rows(Anomaly, run.id)} == {"info"}


def test_explicit_anomaly_severity_is_applied(orm_app, monkeypatch):
    monkeypatch.setenv("ANOMALY_SEVERITIES", '{"NO_MATCH": "majeure"}')
    run = run_inventory()
    assert {item.severity for item in rows(Anomaly, run.id) if item.code == "NO_MATCH"} == {"majeure"}


def test_invalid_explicit_anomaly_severity_is_rejected(orm_app, monkeypatch):
    monkeypatch.setenv("ANOMALY_SEVERITIES", '{"NO_MATCH": "invalid"}')
    run = run_inventory(virtualization_source=Source([{"vm_id": "unmatched", "vm_name": "unmatched", "type": "qemu"}]), ipam_source=Source([]))
    assert run.status == "FAIL" and "No valid anomaly severity configured for NO_MATCH" in run.error_message


@pytest.mark.parametrize("code", [
    "NO_MATCH", "MATCHED_IP", "HOSTNAME_MISMATCH", "STATUS_MISMATCH",
    "DUPLICATE_DNS", "DUPLICATE_IP", "RENAMED",
])
@pytest.mark.parametrize("severity", ["info", "mineure", "majeure", "critique"])
def test_each_known_code_accepts_every_configurable_severity(orm_app, monkeypatch, code, severity):
    monkeypatch.setenv("ANOMALY_SEVERITIES", json.dumps({code: severity}))
    assert _severity_for(code) == severity


@pytest.mark.parametrize("configuration", ['[]', '{"OTHER": "info"}', '{'])
def test_invalid_severity_configuration_is_rejected(orm_app, monkeypatch, configuration):
    monkeypatch.setenv("ANOMALY_SEVERITIES", configuration)
    with pytest.raises((TypeError, ValueError, json.JSONDecodeError)):
        _severity_for("NO_MATCH")


def test_unknown_anomaly_code_is_not_classified(orm_app, monkeypatch):
    monkeypatch.delenv("ANOMALY_SEVERITIES", raising=False)
    with pytest.raises(ValueError, match="Unclassified anomaly code: OTHER"):
        _severity_for("OTHER")


def test_open_anomaly_disappearing_in_next_run_stays_open(orm_app):
    asset = {"vm_id": "missing-42", "vm_name": "missing-42", "type": "qemu"}
    first = run_inventory(Source([asset]), Source([]))
    original = next(row for row in rows(Anomaly, first.id) if row.code == "NO_MATCH")
    original_id = original.id
    db.session.rollback()
    second = run_inventory(Source([]), Source([]))
    assert second.status == "SUCCESS"
    db.session.expire_all()
    assert db.session.get(Anomaly, original_id).state == "ouverte"


def test_acknowledged_anomaly_disappearing_is_resolved_without_erasing_history(orm_app):
    asset = {"vm_id": "missing-43", "vm_name": "missing-43", "type": "qemu"}
    first = run_inventory(Source([asset]), Source([]))
    original = next(row for row in rows(Anomaly, first.id) if row.code == "NO_MATCH")
    original.state = "acquittée"
    original.state_changed_at = original.created_at
    db.session.commit()
    second = run_inventory(Source([]), Source([]))
    assert second.status == "SUCCESS"
    db.session.expire_all()
    assert (db.session.get(Anomaly, original.id).state,
            len(rows(Anomaly, first.id))) == ("résolue", 1)


def test_acknowledgement_is_inherited_without_replacing_previous_observation(orm_app):
    asset = {"vm_id": "missing-44", "vm_name": "missing-44", "type": "qemu"}
    first = run_inventory(Source([asset]), Source([]))
    original = next(row for row in rows(Anomaly, first.id) if row.code == "NO_MATCH")
    original.state = "acquittée"
    original.state_changed_at = original.created_at
    db.session.commit()
    second = run_inventory(Source([asset]), Source([]))
    successor = next(row for row in rows(Anomaly, second.id) if row.code == "NO_MATCH")
    assert (successor.state, successor.state_changed_at, successor.tracking_key,
            original.state, successor.id != original.id) == (
                "acquittée", original.state_changed_at, original.tracking_key, "acquittée", True)


def test_resolved_anomaly_reappearing_starts_open(orm_app):
    asset = {"vm_id": "missing-45", "vm_name": "missing-45", "type": "qemu"}
    first = run_inventory(Source([asset]), Source([]))
    original = next(row for row in rows(Anomaly, first.id) if row.code == "NO_MATCH")
    original.state = "acquittée"
    db.session.commit()
    run_inventory(Source([]), Source([]))
    third = run_inventory(Source([asset]), Source([]))
    successor = next(row for row in rows(Anomaly, third.id) if row.code == "NO_MATCH")
    assert (original.state, successor.state, successor.id != original.id) == ("résolue", "ouverte", True)


def test_ambiguous_target_without_stable_vm_id_is_not_merged(orm_app):
    asset = {"vm_id": "", "vm_name": "unnamed", "type": "qemu"}
    first = run_inventory(Source([asset]), Source([]))
    original = next(row for row in rows(Anomaly, first.id) if row.code == "NO_MATCH")
    original.state = "acquittée"
    db.session.commit()
    second = run_inventory(Source([asset]), Source([]))
    successor = next(row for row in rows(Anomaly, second.id) if row.code == "NO_MATCH")
    assert (original.tracking_key, successor.tracking_key, successor.state) == (None, None, "ouverte")


def test_default_run_persists_seven_ordered_steps_and_four_counters(orm_app):
    steps = []
    run = run_inventory(after_step=steps.append)
    persisted = db.session.get(Run, run.id)
    consolidated = rows(ConsolidatedAsset, run.id)

    assert steps == list(range(1, 8))
    assert persisted.status == "SUCCESS"
    assert persisted.started_at and persisted.finished_at and persisted.error_message is None
    assert len(rows(Asset, run.id)) == len(MockProxmoxSource().collect())
    assert len(rows(IpamRecord, run.id)) == len(MockIpamSource().collect())
    assert len(consolidated) == len(rows(Asset, run.id))
    counts = Counter(row.match_status for row in consolidated)
    assert (persisted.matched_name_count, persisted.matched_fqdn_count,
            persisted.matched_ip_count, persisted.no_match_count) == (
        counts["MATCHED_NAME"], counts["MATCHED_FQDN"],
        counts["MATCHED_IP"], counts["NO_MATCH"])
    assert counts["NO_MATCH"] > 0


def test_consolidations_roles_and_anomalies_reference_the_same_run(orm_app):
    run = run_inventory()
    assets = {row.id: row for row in rows(Asset, run.id)}
    ipam = {row.id: row for row in rows(IpamRecord, run.id)}
    consolidated = rows(ConsolidatedAsset, run.id)
    anomalies = rows(Anomaly, run.id)

    assert consolidated and anomalies
    assert all(row.asset_id in assets and row.role == assets[row.asset_id].role
               and row.role for row in consolidated)
    assert all(row.ipam_record_id is None or row.ipam_record_id in ipam
               for row in consolidated)
    assert all((row.asset_id is None or row.asset_id in assets)
               and (row.ipam_record_id is None or row.ipam_record_id in ipam)
               for row in anomalies)
    assert any(row.match_status == "NO_MATCH" and row.ipam_record_id is None
               for row in consolidated)
    assert any(row.id not in {item.ipam_record_id for item in consolidated}
               for row in ipam.values())


def test_duplicate_dns_and_ip_are_derived_from_observations(orm_app):
    run = run_inventory()
    ipam = rows(IpamRecord, run.id)
    anomalies = rows(Anomaly, run.id)

    assert any(row.duplicate_dns == 1 for row in ipam)
    assert any(row.duplicate_ip == 1 for row in ipam)
    assert {"DUPLICATE_DNS", "DUPLICATE_IP"} <= {row.code for row in anomalies}
    assert all(row.ipam_record_id in {item.id for item in ipam}
               for row in anomalies if row.code in {"DUPLICATE_DNS", "DUPLICATE_IP"})


def test_two_successful_runs_retain_independent_snapshots(orm_app):
    first = run_inventory()
    first_ids = {model: {row.id for row in rows(model, first.id)}
                 for model in (Asset, IpamRecord, ConsolidatedAsset, Anomaly)}
    db.session.rollback()
    second = run_inventory()

    assert first.id != second.id
    assert [row.status for row in db.session.query(Run).order_by(Run.id)] == ["SUCCESS", "SUCCESS"]
    for model, ids in first_ids.items():
        assert ids
        assert {row.id for row in rows(model, first.id)} == ids
        assert not ids.intersection(row.id for row in rows(model, second.id))
    assert {row.vm_id for row in rows(Asset, first.id)} == {
        row.vm_id for row in rows(Asset, second.id)}


@pytest.mark.parametrize("step", range(1, 8))
def test_exception_after_each_step_rolls_back_all_observations(orm_app, step):
    def fail(completed):
        if completed == step:
            raise RuntimeError(f"failure at step {step}")

    run = run_inventory(after_step=fail)

    assert db.session.get(Run, run.id).status == "FAIL"
    assert db.session.get(Run, run.id).error_message == f"failure at step {step}"
    assert db.session.get(Run, run.id).finished_at is not None
    assert [row.id for row in db.session.query(Run).all()] == [run.id]
    for model in (Asset, IpamRecord, ConsolidatedAsset, Anomaly):
        assert rows(model, run.id) == []


def test_failed_second_run_does_not_change_first_success(orm_app):
    first = run_inventory()
    before = {model: {row.id for row in rows(model, first.id)}
               for model in (Asset, IpamRecord, ConsolidatedAsset, Anomaly)}
    db.session.rollback()

    def fail(step):
        if step == 7:
            raise RuntimeError("second run failed")

    second = run_inventory(after_step=fail)
    assert db.session.get(Run, first.id).status == "SUCCESS"
    assert db.session.get(Run, second.id).status == "FAIL"
    assert db.session.get(Run, second.id).error_message == "second run failed"
    for model, ids in before.items():
        assert {row.id for row in rows(model, first.id)} == ids
        assert rows(model, second.id) == []


@pytest.mark.parametrize("source_name,expected_steps", [
    ("virtualization_source", [1]), ("ipam_source", [1, 2]),
])
def test_failing_source_persists_only_fail_run(orm_app, source_name, expected_steps):
    class BrokenSource:
        def collect(self):
            raise RuntimeError("collection unavailable")

    steps = []
    run = run_inventory(**{source_name: BrokenSource()}, after_step=steps.append)
    assert steps == expected_steps
    assert db.session.get(Run, run.id).status == "FAIL"
    assert db.session.get(Run, run.id).error_message == "collection unavailable"
    for model in (Asset, IpamRecord, ConsolidatedAsset, Anomaly):
        assert rows(model, run.id) == []


def test_repeated_source_keys_upsert_within_one_run(orm_app):
    asset = MockProxmoxSource().collect()[0]
    updated = {**asset, "vm_name": "replacement", "fqdn": "replacement.prod.local"}
    ipam = MockIpamSource().collect()[0]
    updated_ipam = {**ipam, "tenant": "Updated"}

    run = run_inventory(Source([asset, updated]), Source([ipam, updated_ipam]))

    assert db.session.get(Run, run.id).status == "SUCCESS"
    assert [(row.vm_id, row.vm_name) for row in rows(Asset, run.id)] == [
        (asset["vm_id"], "replacement")]
    assert [(row.ip, row.dns_name, row.tenant) for row in rows(IpamRecord, run.id)] == [
        (ipam["ip"], ipam["dns_name"], "Updated")]
    assert len(rows(ConsolidatedAsset, run.id)) == 1
