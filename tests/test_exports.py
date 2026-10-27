import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import db
from app.models import Asset, ConsolidatedAsset, IpamRecord, Run
from collector.exports import export_run
from collector.inventory_runner import run_inventory


SUMMARY = {"finished_at": "2026-10-27T12:00:00+00:00", "matched_name_count": 1,
           "matched_fqdn_count": 0, "matched_ip_count": 0, "no_match_count": 1}
VM_FIELDS = {"vm_id", "vm_name", "type", "node", "status", "tags", "ip_reported",
             "fqdn", "os", "cpu", "ram", "disk"}
RESULT_FIELDS = {"ip_final", "dns_final", "match_status", "role"}
IPAM_FIELDS = {"ip", "dns_name", "status", "tenant", "site", "tags",
               "duplicate_dns", "duplicate_ip", "meta_zone"}


class MemoryStorage:
    def __init__(self):
        self.data = {}
        self.dates = {}
        self.deleted = []

    def write(self, name, data):
        self.data[name] = data
        self.dates[name] = datetime.now(UTC)

    def files(self):
        return list(self.dates.items())

    def delete(self, name):
        self.deleted.append(name)
        del self.data[name]
        del self.dates[name]


def seeded_run():
    run = Run(status="SUCCESS", started_at=SUMMARY["finished_at"])
    db.session.add(run)
    db.session.flush()
    next_asset_id = (db.session.query(db.func.max(Asset.id)).scalar() or 0) + 1
    next_ipam_id = (db.session.query(db.func.max(IpamRecord.id)).scalar() or 0) + 1
    matched = Asset(id=next_asset_id, run_id=run.id, vm_id="vm-1", vm_name="école", type="qemu",
                    node="node-1", status="running", tags="prod", ip_reported="192.0.2.1",
                    fqdn="vm.example", os="Linux", cpu=2.5, ram=4096, disk=80,
                    annotation="not exported", uptime=42)
    unmatched = Asset(id=next_asset_id + 1, run_id=run.id, vm_id="vm-2", vm_name="orphan", type="lxc")
    ipam = IpamRecord(id=next_ipam_id, run_id=run.id, ip="192.0.2.1", dns_name="vm.example",
                      status="active", tenant="tenant", site="site", tags="network",
                      duplicate_dns=0, duplicate_ip=1, meta_zone="zone")
    db.session.add_all([matched, unmatched, ipam])
    db.session.flush()
    next_consolidated_id = (db.session.query(db.func.max(ConsolidatedAsset.id)).scalar() or 0) + 1
    db.session.add_all([
        ConsolidatedAsset(id=next_consolidated_id, run_id=run.id, asset_id=matched.id, ipam_record_id=ipam.id,
                          ip_final="192.0.2.1", dns_final="vm.example",
                          match_status="MATCHED_NAME", role="server"),
        ConsolidatedAsset(id=next_consolidated_id + 1, run_id=run.id, asset_id=unmatched.id,
                          match_status="NO_MATCH", role="unknown"),
    ])
    db.session.commit()
    return run.id


def lines(storage, run_id):
    return [json.loads(line) for line in gzip.decompress(
        storage.data[f"consolidated-{run_id}.jsonl.gz"]).decode("utf-8").splitlines()]


def test_consolidated_jsonl_has_exact_fields_types_and_associated_ipam(orm_app):
    run_id = seeded_run()
    storage = MemoryStorage()
    export_run(run_id, SUMMARY, storage)

    assert lines(storage, run_id) == [
        {"vm_id": "vm-1", "vm_name": "école", "type": "qemu", "node": "node-1",
         "status": "running", "tags": "prod", "ip_reported": "192.0.2.1",
         "fqdn": "vm.example", "os": "Linux", "cpu": 2.5, "ram": 4096.0,
         "disk": 80.0, "ip_final": "192.0.2.1", "dns_final": "vm.example",
         "match_status": "MATCHED_NAME", "role": "server",
         "ipam": {"ip": "192.0.2.1", "dns_name": "vm.example", "status": "active",
                  "tenant": "tenant", "site": "site", "tags": "network",
                  "duplicate_dns": 0, "duplicate_ip": 1, "meta_zone": "zone"}},
        {**dict.fromkeys(VM_FIELDS | RESULT_FIELDS), "vm_id": "vm-2",
         "vm_name": "orphan", "type": "lxc", "match_status": "NO_MATCH",
         "role": "unknown", "ipam": None},
    ]


def test_report_is_replaced_without_retaining_previous_run(orm_app):
    storage = MemoryStorage()
    first = seeded_run()
    export_run(first, SUMMARY, storage)
    second = seeded_run()
    export_run(second, {**SUMMARY, "matched_name_count": 7}, storage)

    assert storage.data["report.md"].decode() == (
        f"# Run {second}\n\nFinished: {SUMMARY['finished_at']}\n\n"
        "MATCHED_NAME: 7\nMATCHED_FQDN: 0\nMATCHED_IP: 0\nNO_MATCH: 1\n")


def test_raw_exports_are_disabled_by_default(orm_app):
    storage = MemoryStorage()
    export_run(seeded_run(), SUMMARY, storage)
    assert set(storage.data) == {"consolidated-1.jsonl.gz", "report.md"}


def test_raw_exports_include_only_whitelisted_fields_when_enabled(orm_app):
    orm_app.config["EXPORT_RAW_ENABLED"] = True
    storage = MemoryStorage()
    run_id = seeded_run()
    export_run(run_id, SUMMARY, storage)

    vm = json.loads(gzip.decompress(storage.data[f"raw-vm-{run_id}.json.gz"]))
    ipam = json.loads(gzip.decompress(storage.data[f"raw-ipam-{run_id}.json.gz"]))
    assert [set(row) for row in vm] == [VM_FIELDS | {"uptime"}] * 2
    assert [set(row) for row in ipam] == [IPAM_FIELDS]
    assert vm[0]["uptime"] == 42.0


@pytest.mark.parametrize("days,config,expected", [
    (31, {}, {"consolidated-98.jsonl.gz", "raw-vm-98.json.gz", "raw-ipam-98.json.gz"}),
    (8, {}, {"raw-vm-98.json.gz", "raw-ipam-98.json.gz"}),
    (5, {"EXPORT_RETENTION_CONSOLIDATED": 4, "EXPORT_RETENTION_RAW": 6},
     {"consolidated-98.jsonl.gz"}),
])
def test_retention_deletes_only_expired_managed_exports(orm_app, days, config, expected):
    orm_app.config.update(config)
    storage = MemoryStorage()
    for name in ("consolidated-98.jsonl.gz", "raw-vm-98.json.gz",
                 "raw-ipam-98.json.gz", "report.md", "other.json.gz"):
        storage.data[name] = b"untouched"
        storage.dates[name] = datetime.now(UTC) - timedelta(days=days)
    export_run(seeded_run(), SUMMARY, storage)
    assert set(storage.deleted) == expected
    assert storage.data["other.json.gz"] == b"untouched"
    assert storage.data["report.md"].startswith(b"# Run")


def test_local_destination_writes_to_configured_directory(orm_app, tmp_path):
    directory = tmp_path / "local"
    orm_app.config["EXPORT_DIRECTORY"] = str(directory)
    run_id = seeded_run()
    export_run(run_id, SUMMARY)
    assert json.loads(gzip.decompress(directory.joinpath(
        f"consolidated-{run_id}.jsonl.gz").read_bytes()).splitlines()[0])["vm_id"] == "vm-1"


def test_samba_destination_uses_injected_adapter_without_network(orm_app):
    storage = MemoryStorage()
    orm_app.config.update(EXPORT_DESTINATION="samba", EXPORT_STORAGE=storage)
    run_id = seeded_run()
    export_run(run_id, SUMMARY)
    assert lines(storage, run_id)[0]["vm_id"] == "vm-1"


def test_samba_without_adapter_rejects_export(orm_app):
    orm_app.config.update(EXPORT_DESTINATION="samba", EXPORT_STORAGE=None)
    with pytest.raises(ValueError, match="EXPORT_STORAGE adapter required for Samba"):
        export_run(seeded_run(), SUMMARY)


def test_secrets_are_redacted_in_exported_values_and_not_logged(orm_app, caplog):
    run_id = seeded_run()
    secret = orm_app.config["SECRET_KEY"]
    db.session.query(Asset).filter_by(run_id=run_id, vm_id="vm-1").update(
        {"vm_name": secret, "tags": "password: exposed"})
    db.session.commit()
    orm_app.config["EXPORT_RAW_ENABLED"] = True
    storage = MemoryStorage()
    export_run(run_id, SUMMARY, storage)
    all_content = b"".join(gzip.decompress(value) if name.endswith(".gz") else value
                           for name, value in storage.data.items()).decode()
    assert secret not in all_content
    assert "password: exposed" not in all_content
    assert secret not in caplog.text


def test_admin_password_is_redacted_from_every_export_and_logs(orm_app, monkeypatch, caplog):
    admin_password = "synthetic-admin-value-unique-48271"
    monkeypatch.setenv("ADMIN_PASSWORD", admin_password)
    run_id = seeded_run()
    db.session.query(Asset).filter_by(run_id=run_id, vm_id="vm-1").update(
        {"vm_name": admin_password})
    db.session.query(IpamRecord).filter_by(run_id=run_id).update(
        {"tenant": admin_password})
    db.session.commit()
    assert db.session.query(Asset).filter_by(run_id=run_id, vm_id="vm-1").one().vm_name == admin_password
    orm_app.config["EXPORT_RAW_ENABLED"] = True
    storage = MemoryStorage()

    export_run(run_id, SUMMARY, storage)

    assert set(storage.data) == {
        f"consolidated-{run_id}.jsonl.gz", f"raw-vm-{run_id}.json.gz",
        f"raw-ipam-{run_id}.json.gz", "report.md",
    }
    for name, value in storage.data.items():
        content = gzip.decompress(value).decode("utf-8") if name.endswith(".gz") else value.decode("utf-8")
        assert admin_password not in content, name
    assert admin_password not in caplog.text


def test_successful_runner_exports_only_after_commit(orm_app, monkeypatch):
    import collector.inventory_runner as runner

    observed = []
    original = runner.export_run

    def capture(run_id, summary):
        observed.append(db.session.get(Run, run_id).status)
        return original(run_id, summary)

    monkeypatch.setattr(runner, "export_run", capture)
    run = run_inventory()
    assert observed == ["SUCCESS"]
    assert (Path(orm_app.config["EXPORT_DIRECTORY"])
            / f"consolidated-{run.id}.jsonl.gz").exists()


def test_failed_runner_does_not_export_and_rolls_back_observations(orm_app, monkeypatch):
    import collector.inventory_runner as runner

    called = []
    monkeypatch.setattr(runner, "export_run", lambda *args: called.append(args))

    def fail(step):
        if step == 3:
            raise RuntimeError("collection failed")

    run = run_inventory(after_step=fail)
    assert run.status == "FAIL"
    assert db.session.query(Asset).filter_by(run_id=run.id).count() == 0
    assert called == []


def test_export_failure_propagates_without_changing_committed_success(orm_app, monkeypatch):
    import collector.inventory_runner as runner

    def fail(*args):
        raise OSError("storage unavailable")

    monkeypatch.setattr(runner, "export_run", fail)
    with pytest.raises(OSError, match="storage unavailable"):
        run_inventory()
    assert [row.status for row in db.session.query(Run).all()] == ["SUCCESS"]
