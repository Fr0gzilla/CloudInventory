from datetime import UTC, datetime, timedelta

import pytest

from app import db
from app.anomaly_exceptions import anomaly_coverage
from app.models import Anomaly, AnomalyException, Asset, ConsolidatedAsset, Run
from collector.inventory_runner import _run_inventory, run_inventory


class Source:
    def __init__(self, records, virtual_source=None):
        self.records = records
        self.virtual_source = virtual_source

    def collect(self):
        return self.records


def inventory(name="web-a500", candidates=("web-a500-old",), source="proxmox", async_run=False):
    vm = [{"vm_id": "vm-57", "vm_name": name, "type": "qemu", "virtual_source": source}]
    ipam = [{"ip": "192.0.2.57", "dns_name": candidate} for candidate in candidates]
    virtualization = Source(vm, source)
    if async_run:
        pending = Run(status="RUNNING", started_at=datetime.now(UTC).isoformat())
        db.session.add(pending)
        db.session.commit()
        return _run_inventory(virtualization, Source(ipam), run_id=pending.id)
    return run_inventory(virtualization, Source(ipam))


def doubtful(run):
    return db.session.query(Anomaly).filter_by(run_id=run.id, code="MATCH_CONFIDENCE_DOUBTFUL").one()


@pytest.mark.parametrize("source,async_run", [("proxmox", False), ("vsphere", False),
                                                ("proxmox", True), ("vsphere", True)])
def test_doubtful_fuzzy_result_persists_run_vm_score_and_null_severity(orm_app, source, async_run):
    run = inventory(source=source, async_run=async_run)
    db.session.expire_all()
    asset = db.session.query(Asset).filter_by(run_id=run.id, vm_id="vm-57").one()
    result = db.session.query(ConsolidatedAsset).filter_by(run_id=run.id, asset_id=asset.id).one()
    anomaly = doubtful(run)
    assert (db.session.get(Run, run.id).status, asset.virtual_source,
            result.match_status, result.match_confidence, result.match_doubt,
            anomaly.asset_id, anomaly.severity) == (
                "SUCCESS", source, "MATCHED_FUZZY", 80, 1, asset.id, None)


def test_doubtful_exception_requires_matching_code_target_and_unexpired_date(orm_app):
    row = doubtful(inventory())
    now = datetime.now(UTC)
    wrong = AnomalyException(code="MATCH_CONFIDENCE_DOUBTFUL", target="another-vm",
                             justification="maintenance", expires_at=(now + timedelta(days=1)).isoformat(),
                             author="admin")
    expired = AnomalyException(code="MATCH_CONFIDENCE_DOUBTFUL", target="vm-57",
                               justification="maintenance", expires_at=(now - timedelta(days=1)).isoformat(),
                               author="admin")
    active = AnomalyException(code="MATCH_CONFIDENCE_DOUBTFUL", target="vm-57",
                              justification="maintenance", expires_at=(now + timedelta(days=1)).isoformat(),
                              author="admin")
    for exception in (wrong, expired, active):
        db.session.add(exception)
        db.session.flush()
    db.session.commit()
    assert anomaly_coverage([row], now=now) == {row.id: active.id}
    assert anomaly_coverage([row], now=now + timedelta(days=2)) == {row.id: None}


@pytest.mark.parametrize("source", ["proxmox", "vsphere"])
def test_acknowledged_doubt_is_followed_and_resolved_only_after_conclusive_run(orm_app, source):
    first = inventory(source=source)
    original = doubtful(first)
    original.state = "acquittée"
    original.state_changed_at = original.created_at
    db.session.commit()
    second = inventory(source=source)
    successor = doubtful(second)
    assert (successor.state, successor.tracking_key, original.state) == (
        "acquittée", original.tracking_key, "acquittée")
    successor_id = successor.id
    db.session.rollback()
    inventory(name="web-a500-old", source=source)
    db.session.expire_all()
    assert db.session.get(Anomaly, successor_id).state == "résolue"


@pytest.mark.parametrize("name,candidates", [(None, ("web-a500-old",)),
                                                  ("web-a500", ())])
def test_missing_names_or_ipam_do_not_resolve_acknowledged_doubt(orm_app, name, candidates):
    first = inventory()
    row = doubtful(first)
    row.state = "acquittée"
    db.session.commit()
    inventory(name=name, candidates=candidates)
    db.session.expire_all()
    assert db.session.get(Anomaly, row.id).state == "acquittée"


def test_missing_vm_does_not_resolve_acknowledged_doubt(orm_app):
    first = inventory()
    row = doubtful(first)
    row.state = "acquittée"
    db.session.commit()
    run_inventory(Source([]), Source([]))
    db.session.expire_all()
    assert db.session.get(Anomaly, row.id).state == "acquittée"


def test_exact_name_still_wins_over_fuzzy_candidate_on_next_run(orm_app):
    inventory()
    second = inventory(candidates=("web-a500-old", "web-a500"))
    result = db.session.query(ConsolidatedAsset).filter_by(run_id=second.id).one()
    assert (result.match_status, result.match_confidence, result.match_doubt,
            db.session.query(Anomaly).filter_by(run_id=second.id,
                                                 code="MATCH_CONFIDENCE_DOUBTFUL").count()) == (
                "MATCHED_NAME", 100, 0, 0)
