import pytest
from sqlalchemy import select

from app import db
from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run
from collector.inventory_runner import run_inventory
from collector.purge import purge_old_runs


def add_runs(*dates):
    ids = []
    for date in dates:
        run = Run(status="SUCCESS", started_at=date)
        db.session.add(run)
        db.session.flush()
        ids.append(run.id)
    db.session.commit()
    return ids


def remaining(model):
    return db.session.scalars(select(model).order_by(model.id)).all()


def test_keeps_newest_runs_despite_out_of_order_insertion(orm_app):
    old, newest, middle = add_runs("2024-01-01", "2024-03-01", "2024-02-01")
    assert purge_old_runs(2) == 1
    assert [run.id for run in remaining(Run)] == [newest, middle]
    assert db.session.get(Run, old) is None


def test_equal_timestamps_keep_highest_ids(orm_app):
    first, second, third = add_runs(*(["2024-01-01"] * 3))
    assert purge_old_runs(2) == 1
    assert [run.id for run in remaining(Run)] == [second, third]


def test_default_retention_is_thirty(orm_app, monkeypatch):
    monkeypatch.delenv("RUN_RETENTION", raising=False)
    ids = add_runs(*(f"2024-01-{day:02d}" for day in range(1, 32)))
    assert purge_old_runs() == 1
    assert [run.id for run in remaining(Run)] == ids[1:]


def test_configured_retention_is_used(orm_app, monkeypatch):
    monkeypatch.setenv("RUN_RETENTION", "2")
    ids = add_runs("2024-01-01", "2024-01-02", "2024-01-03")
    assert purge_old_runs() == 1
    assert [run.id for run in remaining(Run)] == ids[1:]


def test_explicit_retention_overrides_configuration(orm_app, monkeypatch):
    monkeypatch.setenv("RUN_RETENTION", "1")
    ids = add_runs("2024-01-01", "2024-01-02", "2024-01-03")
    assert purge_old_runs(2) == 1
    assert [run.id for run in remaining(Run)] == ids[1:]


@pytest.mark.parametrize("count", [1, 2], ids=["below", "equal"])
def test_does_not_delete_at_or_below_threshold(orm_app, count):
    ids = add_runs(*(f"2024-01-0{day}" for day in range(1, count + 1)))
    assert purge_old_runs(2) == 0
    assert [run.id for run in remaining(Run)] == ids


def test_removes_all_children_with_restrict_references_and_preserves_survivors(orm_app):
    old, current = add_runs("2024-01-01", "2024-02-01")
    for run_id in (old, current):
        asset = Asset(run_id=run_id, vm_id=f"vm-{run_id}")
        ipam = IpamRecord(run_id=run_id, ip=f"192.0.2.{run_id}")
        db.session.add_all([asset, ipam])
        db.session.flush()
        db.session.add_all([
            ConsolidatedAsset(run_id=run_id, asset_id=asset.id,
                              ipam_record_id=ipam.id, match_status="MATCHED_IP"),
            Anomaly(run_id=run_id, asset_id=asset.id, ipam_record_id=ipam.id,
                    code="MATCHED_IP", description="test", created_at="2024-01-01"),
        ])
    db.session.commit()
    assert purge_old_runs(1) == 1
    for model in (Run, Asset, IpamRecord, ConsolidatedAsset, Anomaly):
        assert [row.run_id if model is not Run else row.id for row in remaining(model)] == [current]
    survivor = remaining(ConsolidatedAsset)[0]
    assert survivor.asset_id == remaining(Asset)[0].id
    assert survivor.ipam_record_id == remaining(IpamRecord)[0].id


@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "2"])
def test_rejects_invalid_explicit_retention_without_deletion(orm_app, value):
    ids = add_runs("2024-01-01", "2024-01-02")
    with pytest.raises(ValueError, match="positive integer"):
        purge_old_runs(value)
    assert [run.id for run in remaining(Run)] == ids


@pytest.mark.parametrize("value", ["invalid", "0", "-2"])
def test_rejects_invalid_configured_retention_without_deletion(orm_app, monkeypatch, value):
    monkeypatch.setenv("RUN_RETENTION", value)
    ids = add_runs("2024-01-01", "2024-01-02")
    with pytest.raises(ValueError, match="positive integer"):
        purge_old_runs()
    assert [run.id for run in remaining(Run)] == ids


def test_success_run_purges_after_all_seven_steps(orm_app, monkeypatch):
    monkeypatch.setenv("RUN_RETENTION", "1")
    old = add_runs("2000-01-01")[0]
    steps = []
    result = run_inventory(after_step=steps.append)
    assert steps == list(range(1, 8))
    assert result.status == "SUCCESS"
    assert [row.id for row in remaining(Run)] == [result.id]
    assert result.id != old


def test_failed_run_purges_after_pipeline_failure(orm_app, monkeypatch):
    monkeypatch.setenv("RUN_RETENTION", "1")
    old = add_runs("2000-01-01")[0]
    steps = []

    def fail_at_four(step):
        steps.append(step)
        if step == 4:
            raise RuntimeError("collection stopped")

    result = run_inventory(after_step=fail_at_four)
    assert steps == [1, 2, 3, 4]
    assert result.status == "FAIL"
    assert [row.id for row in remaining(Run)] == [result.id]
    assert result.id != old


@pytest.mark.parametrize("failed", [False, True])
def test_purge_failure_does_not_change_persisted_run_status(orm_app, monkeypatch, failed):
    import collector.inventory_runner as runner

    def unavailable():
        raise RuntimeError("purge unavailable")

    monkeypatch.setattr(runner, "purge_old_runs", unavailable)

    def callback(step):
        if failed and step == 4:
            raise RuntimeError("collection stopped")

    with pytest.raises(RuntimeError, match="purge unavailable"):
        run_inventory(after_step=callback)
    assert [row.status for row in remaining(Run)] == ["FAIL" if failed else "SUCCESS"]
