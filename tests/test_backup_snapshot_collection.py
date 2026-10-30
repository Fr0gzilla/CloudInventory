import json

import pytest

from app import db
from app.models import Asset, BackupObservation, SnapshotObservation
from collector.backup import PbsBackupSource
from collector.inventory_runner import run_inventory
from collector.proxmox import ProxmoxSource


class Source:
    def __init__(self, records):
        self.records = records

    def collect(self):
        return self.records


def test_proxmox_snapshot_transport_keeps_vm_identity_and_skips_current(monkeypatch):
    monkeypatch.setenv("PROXMOX_URL", "https://pve.example")
    monkeypatch.setenv("PROXMOX_TOKEN_ID", "test")
    monkeypatch.setenv("PROXMOX_TOKEN_SECRET", "test")
    paths = []

    def transport(request, timeout):
        paths.append(request.full_url)
        return json.dumps({"data": [{"name": "current"}, {"name": "old", "snaptime": 0},
                                    {"name": "undated"}]}).encode()

    records = ProxmoxSource(transport=transport).collect_snapshots([
        {"vm_id": "101", "node": "node-a", "type": "qemu"},
        {"vm_id": "202", "node": "node-b", "type": "qemu", "virtual_source": "vsphere"},
    ])
    assert records == [
        {"vm_id": "101", "snapshot_id": "old", "snapshot_created_at": "1970-01-01T00:00:00+00:00"},
        {"vm_id": "101", "snapshot_id": "undated", "snapshot_created_at": None},
    ]
    assert len(paths) == 1 and paths[0].endswith("/nodes/node-a/qemu/101/snapshot")


def test_proxmox_snapshot_invalid_response_fails_instead_of_claiming_empty(monkeypatch):
    monkeypatch.setenv("PROXMOX_URL", "https://pve.example")
    monkeypatch.setenv("PROXMOX_TOKEN_ID", "test")
    monkeypatch.setenv("PROXMOX_TOKEN_SECRET", "test")
    with pytest.raises(TypeError, match="Invalid Proxmox snapshot response"):
        ProxmoxSource(transport=lambda request, timeout: b'{"data":null}').collect_snapshots(
            [{"vm_id": "101", "node": "node-a", "type": "qemu"}])


def test_pbs_transport_maps_only_vm_backups_to_utc(monkeypatch):
    for key, value in {"PBS_URL": "https://pbs.example", "PBS_DATASTORE": "store",
                       "PBS_TOKEN_ID": "test", "PBS_TOKEN_SECRET": "test"}.items():
        monkeypatch.setenv(key, value)
    paths = []

    def transport(request, timeout):
        paths.append(request.full_url)
        return json.dumps({"data": [{"backup-type": "vm", "backup-id": "101", "backup-time": 0},
                                    {"backup-type": "ct", "backup-id": "202", "backup-time": 0}]}).encode()

    assert PbsBackupSource(transport=transport).collect() == [
        {"vm_id": "101", "backup_id": "vm/101/0", "backup_created_at": "1970-01-01T00:00:00+00:00"}]
    assert len(paths) == 1 and paths[0].endswith("/admin/datastore/store/snapshots")


@pytest.mark.parametrize("payload,error", [(b'{"data":null}', TypeError),
                                          (b'{"data":[{"backup-type":"vm","backup-id":"101"}]}', ValueError)])
def test_pbs_incomplete_response_is_not_success(monkeypatch, payload, error):
    for key, value in {"PBS_URL": "https://pbs.example", "PBS_DATASTORE": "store",
                       "PBS_TOKEN_ID": "test", "PBS_TOKEN_SECRET": "test"}.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(error):
        PbsBackupSource(transport=lambda request, timeout: payload).collect()


def test_observations_preserve_vm_and_run_across_two_collections(orm_app):
    vm = Source([{"vm_id": "101", "vm_name": "one", "type": "qemu"},
                 {"vm_id": "202", "vm_name": "two", "type": "qemu"}])
    first = run_inventory(virtualization_source=vm, ipam_source=Source([]),
                          snapshot_source=Source([{"vm_id": "101", "snapshot_id": "s1",
                                                   "snapshot_created_at": "2025-01-01T00:00:00+00:00"}]),
                          backup_source=Source([{"vm_id": "202", "backup_id": "b1",
                                                 "backup_created_at": "2025-01-01T00:00:00+00:00"}]))
    second = run_inventory(virtualization_source=vm, ipam_source=Source([]),
                           snapshot_source=Source([{"vm_id": "202", "snapshot_id": "s2",
                                                    "snapshot_created_at": "2025-01-02T00:00:00+00:00"}]),
                           backup_source=Source([{"vm_id": "101", "backup_id": "b2",
                                                  "backup_created_at": "2025-01-02T00:00:00+00:00"}]))
    assets = {asset.id: (asset.run_id, asset.vm_id) for asset in db.session.query(Asset).all()}
    snapshots = {(row.run_id, assets[row.asset_id][1], row.snapshot_id)
                 for row in db.session.query(SnapshotObservation).all()}
    backups = {(row.run_id, assets[row.asset_id][1], row.backup_id)
               for row in db.session.query(BackupObservation).all()}
    assert snapshots == {(first.id, "101", "s1"), (second.id, "202", "s2")}
    assert backups == {(first.id, "202", "b1"), (second.id, "101", "b2")}
    assert all(assets[row.asset_id][0] == row.run_id for row in db.session.query(SnapshotObservation).all())
    assert all(assets[row.asset_id][0] == row.run_id for row in db.session.query(BackupObservation).all())
