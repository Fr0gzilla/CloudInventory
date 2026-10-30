import pytest

from app import db
from app.models import Anomaly, Asset, Run
from collector.inventory_runner import run_inventory


class Source:
    def __init__(self, records):
        self.records = records

    def collect(self):
        return self.records


def asset(**changes):
    return {"vm_id": "capacity-1", "vm_name": "capacity-1", "type": "qemu",
            "status": "running", **changes}


def execute(records):
    run = run_inventory(Source(records), Source([]))
    assert run.status == "SUCCESS", run.error_message
    return run


def codes(run):
    return {row.code for row in db.session.query(Anomaly).filter_by(run_id=run.id)
            if row.code in {"DISK_OVER_90", "RAM_SATURATED", "VM_UNDERUSED"}}


@pytest.mark.parametrize("measurement,code,value", [
    ("disk_usage_percent", "DISK_OVER_90", 90.1),
    ("ram_usage_percent", "RAM_SATURATED", 95),
])
def test_capacity_anomaly_persists_on_observed_asset_and_run(orm_app, measurement, code, value):
    run = execute([asset(**{measurement: value})])
    stored = db.session.query(Asset).filter_by(run_id=run.id).one()
    finding = db.session.query(Anomaly).filter_by(run_id=run.id, code=code).one()
    assert (getattr(stored, measurement), finding.asset_id, finding.run_id,
            finding.severity) == (value, stored.id, run.id, None)


@pytest.mark.parametrize("measurement,value,excluded", [
    ("disk_usage_percent", 90, "DISK_OVER_90"),
    ("ram_usage_percent", 94.9, "RAM_SATURATED"),
    ("ram_usage_percent", 94.999, "RAM_SATURATED"),
])
def test_capacity_measurement_below_boundary_does_not_trigger(orm_app, measurement, value, excluded):
    assert excluded not in codes(execute([asset(**{measurement: value})]))


@pytest.mark.parametrize("missing", ["disk_usage_percent", "ram_usage_percent"])
def test_missing_measurement_does_not_invent_capacity_anomaly(orm_app, missing):
    measurements = {"disk_usage_percent": 91, "ram_usage_percent": 96}
    measurements.pop(missing)
    run = execute([asset(**measurements)])
    assert ("DISK_OVER_90" if missing == "disk_usage_percent" else "RAM_SATURATED") not in codes(run)
    assert getattr(db.session.query(Asset).filter_by(run_id=run.id).one(), missing) is None


def test_allocated_capacity_alone_does_not_imply_usage(orm_app):
    assert codes(execute([asset(disk=500, ram=128)])) == set()


@pytest.mark.parametrize("name,value,measurement,expected", [
    ("DISK_USAGE_THRESHOLD", "80", "disk_usage_percent", "DISK_OVER_90"),
    ("RAM_SATURATION_THRESHOLD", "80", "ram_usage_percent", "RAM_SATURATED"),
])
def test_configurable_capacity_thresholds(orm_app, monkeypatch, name, value, measurement, expected):
    monkeypatch.setenv(name, value)
    assert expected in codes(execute([asset(**{measurement: 85})]))


@pytest.mark.parametrize("name,value", [
    ("DISK_USAGE_THRESHOLD", "nan"), ("RAM_SATURATION_THRESHOLD", "101"),
    ("VM_UNDERUSED_THRESHOLD", "-1"), ("VM_UNDERUSED_RUNS", "0"),
    ("VM_UNDERUSED_RUNS", "abc"),
])
def test_invalid_capacity_configuration_fails_run(orm_app, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=f"Invalid {name} threshold"):
        run_inventory(Source([asset()]), Source([]))


@pytest.mark.parametrize("measurement,value", [
    ("disk_usage_percent", -1), ("ram_usage_percent", 101),
    ("disk_usage_percent", "invalid"), ("ram_usage_percent", float("nan")),
])
def test_invalid_observed_usage_fails_run(orm_app, measurement, value):
    run = run_inventory(Source([asset(**{measurement: value})]), Source([]))
    assert db.session.get(Run, run.id).status == "FAIL"


def test_thirty_complete_runs_detect_underuse_with_asset_identity(orm_app):
    for _ in range(29):
        execute([asset(disk_usage_percent=20, ram_usage_percent=20)])
    run = execute([asset(disk_usage_percent=20, ram_usage_percent=20)])
    stored = db.session.query(Asset).filter_by(run_id=run.id).one()
    finding = db.session.query(Anomaly).filter_by(run_id=run.id, code="VM_UNDERUSED").one()
    assert (finding.asset_id, finding.run_id, finding.severity) == (stored.id, run.id, None)


def test_twenty_nine_runs_do_not_establish_underuse(orm_app):
    for _ in range(28):
        execute([asset(disk_usage_percent=10, ram_usage_percent=10)])
    assert "VM_UNDERUSED" not in codes(execute([asset(disk_usage_percent=10, ram_usage_percent=10)]))


@pytest.mark.parametrize("interruption", ["missing_usage", "missing_vm", "stopped", "failed"])
def test_incomplete_history_blocks_underuse(orm_app, interruption):
    for index in range(30):
        record = asset(disk_usage_percent=10, ram_usage_percent=10)
        if index == 14:
            if interruption == "missing_usage":
                record.pop("ram_usage_percent")
            elif interruption == "stopped":
                record["status"] = "stopped"
            elif interruption == "missing_vm":
                record = asset(vm_id="another", disk_usage_percent=10, ram_usage_percent=10)
            else:
                run_inventory(Source([record]), Source([]), after_step=lambda step: (_ for _ in ()).throw(RuntimeError("interrupted")) if step == 7 else None)
                continue
        run = execute([record])
    assert "VM_UNDERUSED" not in codes(run)


def test_configurable_underuse_window_and_threshold(orm_app, monkeypatch):
    monkeypatch.setenv("VM_UNDERUSED_RUNS", "2")
    monkeypatch.setenv("VM_UNDERUSED_THRESHOLD", "25")
    execute([asset(disk_usage_percent=25, ram_usage_percent=25)])
    assert "VM_UNDERUSED" in codes(execute([asset(disk_usage_percent=25, ram_usage_percent=25)]))
