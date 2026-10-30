"""Persist one seven-step inventory run as a single atomic collection."""

import json
import logging
import math
import os
from collections import Counter
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import cast

from app import db
from app.anomaly_exceptions import anomaly_coverage
from app.models import (
    Anomaly,
    Asset,
    BackupObservation,
    ConsolidatedAsset,
    IpamRecord,
    NodeCapacityObservation,
    Run,
    SnapshotObservation,
)
from app.notifications import notify_anomalies, should_alert
from app.observability import collection_errors, log_run, run_anomalies, run_duration
from collector.anomalies import (
    capacity_anomalies,
    duplicate_anomalies,
    match_anomalies,
    missing_ipam_fields,
    network_match_anomalies,
    ptr_anomalies,
    renamed_anomalies,
    required_tag_anomalies,
    snapshot_backup_anomalies,
    vm_ip_conflicts,
)
from collector.backup import PbsBackupSource
from collector.efficientip import EfficientIpSource
from collector.exports import export_run
from collector.matching import consolidate_asset
from collector.mock_efficientip import MockEfficientIpSource
from collector.mock_ipam import MockIpamSource
from collector.mock_proxmox import MockProxmoxSource
from collector.mock_vsphere import MockVsphereSource
from collector.netbox import NetBoxSource
from collector.normalization import normalize
from collector.proxmox import ProxmoxSource
from collector.purge import purge_old_runs
from collector.run_lock import run_lock
from collector.source import (
    AssetRecord,
    BackupRecord,
    CapacityAssetRecord,
    NodeCapacityRecord,
    SnapshotRecord,
    Source,
    ZombieAssetRecord,
)
from collector.source import IpamRecord as IpamObservation
from collector.vsphere import VsphereSource


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _zombie_days() -> int:
    value = os.environ.get("VM_ZOMBIE_DAYS", "30")
    if not value.isascii() or not value.isdecimal() or int(value) < 1:
        raise ValueError("Invalid VM zombie threshold")
    return int(value)


def _observation_days(name: str, default: str) -> int:
    value = os.environ.get(name, default)
    if not value.isascii() or not value.isdecimal() or int(value) < 1:
        raise ValueError(f"Invalid {name} threshold")
    return int(value)


def _capacity_threshold(name: str, default: str) -> float:
    value = os.environ.get(name, default)
    try:
        threshold = float(value)
    except ValueError as error:
        raise ValueError(f"Invalid {name} threshold") from error
    if not math.isfinite(threshold) or not 0 <= threshold <= 100:
        raise ValueError(f"Invalid {name} threshold")
    return threshold


def _usage_percent(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Invalid capacity usage observation")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 100:
        raise ValueError("Invalid capacity usage observation")
    return number


def _observed_at(value: str) -> datetime:
    date = datetime.fromisoformat(value)
    if date.tzinfo is None or date.utcoffset() is None:
        raise ValueError("Invalid VM stop observation date")
    return date.astimezone(UTC)


def _zombie_since(asset: Asset, started_at: str) -> datetime:
    first = _observed_at(started_at)
    source_date = _observed_at(asset.vm_stopped_since) if asset.vm_stopped_since else None
    history = (db.session.query(Run.started_at, Asset.status)
               .outerjoin(Asset, (Asset.run_id == Run.id) & (Asset.vm_id == asset.vm_id) &
                          (Asset.virtual_source == asset.virtual_source))
               .filter(Run.status == "SUCCESS").order_by(Run.id.desc()).all())
    for observed_at, status in history:
        if (status or "").strip().lower() != "stopped":
            if source_date and source_date > _observed_at(observed_at):
                return source_date
            break
        first = _observed_at(observed_at)
    else:
        return source_date or first
    return first


def _severity_for(code: str) -> str:
    from app.inventory_api import ANOMALY_CODES

    if code not in ANOMALY_CODES:
        raise ValueError(f"Unclassified anomaly code: {code}")
    configured = json.loads(os.environ.get("ANOMALY_SEVERITIES", "{}"))
    if not isinstance(configured, dict):
        raise TypeError("Invalid anomaly severities configuration")
    if any(key not in ANOMALY_CODES for key in configured):
        raise ValueError("Invalid anomaly severities configuration")
    severity = configured.get(code, "info")
    if severity not in (
        "info", "mineure", "majeure", "critique"
    ):
        raise ValueError(f"No valid anomaly severity configured for {code}")
    return severity


def _tracked_anomaly(run_id: int, code: str, description: str,
                     asset: Asset | None = None, ipam: IpamRecord | None = None) -> Anomaly:
    if asset is not None:
        target = (json.dumps(["vsphere", asset.vm_id]) if asset.virtual_source == "vsphere"
                  and asset.vm_id else asset.vm_id)
        if code == "MISSING_REQUIRED_TAG" and target:
            target = f"{target}:{description.removeprefix('Missing required tag: ')}"
    elif code in ("DUPLICATE_DNS", "DNS_MISSING_PTR", "PTR_MISMATCH", "MISSING_TENANT", "MISSING_SITE") and ipam is not None:
        target = normalize(ipam.dns_name)
        if not target:
            target = ipam.ip
    elif code == "DUPLICATE_IP" and ipam is not None:
        target = ipam.ip
    else:
        target = None
    key = json.dumps([code, target]) if target else None
    previous = (Anomaly.query.filter_by(tracking_key=key).order_by(Anomaly.id.desc()).first()
                if key else None)
    state = previous.state if previous and previous.state != "résolue" else "ouverte"
    return Anomaly(run_id=run_id, asset_id=asset.id if asset else None,
                   ipam_record_id=ipam.id if ipam else None, code=code,
                    description=description, created_at=_now(),
                      severity=None if code in ("MATCH_CONFIDENCE_DOUBTFUL", "IP_OUT_OF_PREFIX",
                                                "VM_IP_CONFLICT", "DNS_MISSING_PTR", "PTR_MISMATCH",
                                                 "MISSING_REQUIRED_TAG", "MISSING_TENANT", "MISSING_SITE", "VM_ZOMBIE",
                                                 "NO_RECENT_BACKUP", "FORGOTTEN_SNAPSHOT",
                                                 "DISK_OVER_90", "RAM_SATURATED", "VM_UNDERUSED")
                     else _severity_for(code),
                   tracking_key=key, state=state,
                   state_changed_at=previous.state_changed_at if state == "acquittée" and previous else None)


def run_inventory(
    virtualization_source: Source[AssetRecord] | None = None,
    ipam_source: Source[IpamObservation] | None = None,
    after_step: Callable[[int], None] | None = None,
    snapshot_source: Source[SnapshotRecord] | None = None,
    backup_source: Source[BackupRecord] | None = None,
) -> Run:
    with run_lock():
        return _run_inventory(virtualization_source, ipam_source, after_step,
                              snapshot_source=snapshot_source, backup_source=backup_source)


def _run_inventory(
    virtualization_source: Source[AssetRecord] | None = None,
    ipam_source: Source[IpamObservation] | None = None,
    after_step: Callable[[int], None] | None = None,
    run_id: int | None = None,
    snapshot_source: Source[SnapshotRecord] | None = None,
    backup_source: Source[BackupRecord] | None = None,
) -> Run:
    """Run steps 1–7; after_step receives each completed step number (1 to 7).

    Requires an application context and an idle session. A failure returns a
    separately persisted FAIL run with no observations; an error persisting FAIL
    propagates to the caller.
    """
    if virtualization_source is None:
        source_name = os.environ.get("VIRTUALIZATION_SOURCE", "proxmox").lower()
        source_mode = os.environ.get("VIRTUALIZATION_MODE", "mock").lower()
        if source_name not in ("proxmox", "vsphere") or source_mode not in ("mock", "real"):
            raise ValueError("Invalid virtualization source configuration")
        virtualization_source = {
            ("proxmox", "mock"): MockProxmoxSource,
            ("proxmox", "real"): ProxmoxSource,
            ("vsphere", "mock"): MockVsphereSource,
            ("vsphere", "real"): VsphereSource,
        }[(source_name, source_mode)]()
    source_default = getattr(virtualization_source, "virtual_source", "proxmox")
    if ipam_source is None:
        ipam_name = os.environ.get("IPAM_SOURCE", "netbox").lower()
        ipam_mode = os.environ.get("IPAM_MODE", "mock").lower()
        if ipam_name not in ("netbox", "efficientip") or ipam_mode not in ("mock", "real"):
            raise ValueError("Invalid IPAM source configuration")
        ipam_source = {
            ("netbox", "mock"): MockIpamSource,
            ("netbox", "real"): NetBoxSource,
            ("efficientip", "mock"): MockEfficientIpSource,
            ("efficientip", "real"): EfficientIpSource,
        }[(ipam_name, ipam_mode)]()
    ipam_default = getattr(ipam_source, "ipam_source", None)
    started_at = _now()
    zombie_days = _zombie_days()
    snapshot_days = _observation_days("FORGOTTEN_SNAPSHOT_DAYS", "30")
    backup_days = _observation_days("RECENT_BACKUP_DAYS", "7")
    disk_threshold = _capacity_threshold("DISK_USAGE_THRESHOLD", "90")
    ram_threshold = _capacity_threshold("RAM_SATURATION_THRESHOLD", "95")
    underused_threshold = _capacity_threshold("VM_UNDERUSED_THRESHOLD", "20")
    underused_window = _observation_days("VM_UNDERUSED_RUNS", "30")
    completed_step = 0
    asynchronous = run_id is not None
    pending_consolidated = []
    pending_anomalies: list[tuple[Asset | None, IpamRecord | None, str, str]] = []

    def completed(step: int) -> None:
        nonlocal completed_step
        completed_step = step
        if asynchronous:
            db.session.query(Run).filter_by(id=run_id).update({
                "current_step": step, "completed_steps": step,
            })
            db.session.commit()
        if after_step is not None:
            after_step(step)

    if not asynchronous and db.session().in_transaction() and not (
        db.session.new or db.session.dirty or db.session.deleted
    ):
        db.session.rollback()

    try:
        with db.session.begin() if not asynchronous else nullcontext():
            previous = db.session.query(Run).filter(Run.status == "SUCCESS").order_by(Run.id.desc()).first()
            previous_assets = (
                db.session.query(Asset).filter(Asset.run_id == previous.id).all() if previous else []
            )
            recent_ids = [identifier for (identifier,) in db.session.query(Run.id)
                          .filter(Run.status == "SUCCESS").order_by(Run.id.desc())
                          .limit(underused_window - 1).all()]
            capacity_history: dict[tuple[str, str], dict[int, Asset]] = {}
            if len(recent_ids) == underused_window - 1 and recent_ids:
                for past in db.session.query(Asset).filter(Asset.run_id.in_(recent_ids)).all():
                    capacity_history.setdefault((past.virtual_source or "proxmox", past.vm_id), {})[
                        past.run_id] = past
            if run_id is None:
                run = Run(status="RUNNING", started_at=started_at, run_trigger="manuel")
                db.session.add(run)
                db.session.flush()
            else:
                run = cast(Run, db.session.get(Run, run_id))
                started_at = run.started_at
            log_run("inventory_started", run.id)
            completed(1)

            try:
                assets = virtualization_source.collect()
            except Exception:
                collection_errors.inc()
                raise
            capacity_collector = getattr(virtualization_source, "collect_node_capacity", None)
            capacity_records: list[NodeCapacityRecord] = (
                capacity_collector(assets) if capacity_collector is not None else
                [{"node_capacity_name": name, "node_capacity_measured_at": None,
                  "node_capacity_value": None, "node_capacity_limit": None}
                 for name in sorted({asset["node"] for asset in assets if asset.get("node")})]
            )
            if not isinstance(capacity_records, list):
                raise TypeError("Invalid node capacity observations")
            for capacity_entry in capacity_records:
                if not isinstance(capacity_entry, dict):
                    raise TypeError("Invalid node capacity observation")
                name = capacity_entry.get("node_capacity_name")
                measured = capacity_entry.get("node_capacity_measured_at")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("Invalid node capacity name")
                if measured is not None:
                    if not isinstance(measured, str):
                        raise TypeError("Invalid node capacity date")
                    _observed_at(measured)
                values = (capacity_entry.get("node_capacity_value"),
                          capacity_entry.get("node_capacity_limit"))
                if any(value is not None and (type(value) not in (int, float) or not math.isfinite(value))
                       for value in values):
                    raise ValueError("Invalid node capacity value or limit")
                if values[1] is not None and values[1] <= 0:
                    raise ValueError("Invalid node capacity limit")
                db.session.add(NodeCapacityObservation(
                    run_id=run.id, node_capacity_name=name,
                    node_capacity_measured_at=measured or started_at,
                    node_capacity_value=values[0], node_capacity_limit=values[1],
                ))
                db.session.flush()
            completed(2)

            try:
                ipam_records = ipam_source.collect()
            except Exception:
                collection_errors.inc()
                raise
            completed(3)

            snapshots: list[SnapshotRecord] = []
            if snapshot_source is not None or isinstance(virtualization_source, ProxmoxSource):
                try:
                    if snapshot_source is None:
                        snapshots = cast(ProxmoxSource, virtualization_source).collect_snapshots(assets)
                    else:
                        snapshots = snapshot_source.collect()
                except (OSError, RuntimeError, ValueError, TypeError):
                    collection_errors.inc()
                    logging.getLogger(__name__).warning("Snapshot collection unavailable", exc_info=True)
                    snapshots = []

            if backup_source is None and os.environ.get("PBS_URL"):
                backup_source = PbsBackupSource()
            backups: list[BackupRecord] = []
            backup_status = "inconnue"
            if backup_source is not None:
                try:
                    backups = backup_source.collect()
                    if not isinstance(backups, list):
                        raise TypeError("Invalid backup collection")
                    backup_status = "réussie"
                except (OSError, RuntimeError, ValueError, TypeError):
                    collection_errors.inc()
                    logging.getLogger(__name__).warning("Backup collection unavailable", exc_info=True)
                    backup_status = "échouée"

            stored_assets: dict[tuple[str, str], Asset] = {}
            asset_snapshots: dict[tuple[str, str], AssetRecord] = {}
            for observation in assets:
                source = observation.get("virtual_source", source_default)
                if source not in ("proxmox", "vsphere") or not isinstance(observation.get("vm_id"), str):
                    raise ValueError("Invalid virtualization observation")
                key = (source, observation["vm_id"])
                asset_snapshots[key] = cast(AssetRecord, {**observation, "virtual_source": source})
                if key not in stored_assets:
                    stored_assets[key] = Asset(run_id=run.id, vm_id=key[1], virtual_source=source)
                    if not asynchronous:
                        db.session.add(stored_assets[key])
                row = stored_assets[key]
                for field in ("vm_name", "fqdn", "ip_reported", "node", "type", "status", "tags",
                              "os", "annotation", "cpu", "ram", "disk", "uptime"):
                    setattr(row, field, observation.get(field))
                stopped_since = cast(ZombieAssetRecord, observation).get("vm_stopped_since")
                if stopped_since is not None and not isinstance(stopped_since, str):
                    raise TypeError("Invalid VM stop observation date")
                row.vm_stopped_since = stopped_since
                row.disk_usage_percent = _usage_percent(observation.get("disk_usage_percent"))
                row.ram_usage_percent = _usage_percent(observation.get("ram_usage_percent"))
                if row.id is None and not asynchronous:
                    db.session.flush()

            stored_ipam: dict[tuple[str | None, str | None, str | None], IpamRecord] = {}
            ipam_snapshots: dict[tuple[str | None, str | None, str | None], IpamObservation] = {}
            for ipam_observation in ipam_records:
                origin = cast(str | None, ipam_observation.get("ipam_source", ipam_default))
                if origin is not None and origin not in ("netbox", "efficientip"):
                    raise ValueError("Invalid IPAM observation source")
                ipam_key = (origin, ipam_observation.get("ip"), ipam_observation.get("dns_name"))
                ipam_snapshots[ipam_key] = ipam_observation.copy()
                if ipam_key not in stored_ipam:
                    stored_ipam[ipam_key] = IpamRecord(run_id=run.id)
                    if not asynchronous:
                        db.session.add(stored_ipam[ipam_key])
                ipam_row = stored_ipam[ipam_key]
                ipam_row.ipam_source = origin
                for field in ("ip", "dns_name", "status", "tenant", "site", "tags", "meta_zone"):
                    setattr(ipam_row, field, ipam_observation.get(field))
                custom = ipam_observation.get("custom_fields")
                if custom is not None and not isinstance(custom, dict):
                    raise TypeError("Invalid IPAM custom fields")
                ipam_row.custom_fields = json.dumps(custom) if custom is not None else None
                for field in ("network_prefix", "ptr_name"):
                    value = cast(dict[str, object], ipam_observation).get(
                        field, custom.get(field) if custom else None)
                    if value is not None and not isinstance(value, str):
                        raise TypeError("Invalid IPAM network observation")
                    setattr(ipam_row, field, value)
                    if value is not None:
                        cast(dict[str, object], ipam_snapshots[ipam_key])[field] = value
                if ipam_row.id is None and not asynchronous:
                    db.session.flush()
            if not asynchronous:
                db.session.flush()
            snapshots_by_vm: dict[tuple[str, str], list[dict[str, object]]] = {}
            backups_by_vm: dict[tuple[str, str], list[dict[str, object]]] = {}
            pending_observations: list[tuple[Asset, type[SnapshotObservation | BackupObservation], dict]] = []
            for records, grouped, model, identity, created in (
                (snapshots, snapshots_by_vm, SnapshotObservation, "snapshot_id", "snapshot_created_at"),
                (backups, backups_by_vm, BackupObservation, "backup_id", "backup_created_at"),
            ):
                for record in cast(list[dict[str, object]], records):
                    if not isinstance(record, dict) or not isinstance(record.get("vm_id"), str):
                        raise TypeError("Invalid VM observation")
                    source = record.get("virtual_source", "proxmox")
                    key = (source, record["vm_id"])
                    if source not in ("proxmox", "vsphere") or not record["vm_id"] or key not in stored_assets:
                        raise ValueError("Observation does not match an inventoried VM")
                    if record.get(identity) is not None and not isinstance(record[identity], str):
                        raise TypeError("Invalid observation identifier")
                    if record.get(created) is not None:
                        if not isinstance(record[created], str):
                            raise TypeError("Invalid observation date")
                        _observed_at(cast(str, record[created]))
                    grouped.setdefault(key, []).append(record)
                    asset = stored_assets[key]
                    details = {"run_id": run.id, identity: record.get(identity), created: record.get(created)}
                    if asynchronous:
                        pending_observations.append((asset, model, details))
                    else:
                        db.session.add(model(asset_id=asset.id, **details))
            for key, asset in stored_assets.items():
                if key[0] == "proxmox" or backup_source is not None:
                    asset.backup_collection_status = backup_status
            completed(4)

            unique_ipam = list(ipam_snapshots.values())
            counts: Counter[str] = Counter()
            anomaly_count = 0
            conclusive_doubt_keys: set[str] = set()
            renamed = renamed_anomalies(
                asset_snapshots.values(),
                [cast(AssetRecord, {"vm_id": row.vm_id, "vm_name": row.vm_name,
                                    "virtual_source": row.virtual_source or "proxmox"})
                 for row in previous_assets],
            )
            renamed_by_vm = {(entry["asset"].get("virtual_source", "proxmox"), entry["asset"]["vm_id"]): entry
                             for entry in renamed if entry["asset"]}
            for observation in asset_snapshots.values():
                key = (cast(str, observation.get("virtual_source", source_default)), observation["vm_id"])
                asset = stored_assets[key]
                result = consolidate_asset(observation, unique_ipam)
                if result["match_confidence"] is not None and not result["match_doubt"]:
                    target = (json.dumps(["vsphere", asset.vm_id]) if asset.virtual_source == "vsphere"
                              and asset.vm_id else asset.vm_id)
                    conclusive_doubt_keys.add(json.dumps(["MATCH_CONFIDENCE_DOUBTFUL", target]))
                matched = result["ipam_record"]
                ipam = stored_ipam[(cast(str | None, matched.get("ipam_source", ipam_default)),
                                    matched.get("ip"), matched.get("dns_name"))] if matched else None
                asset.role = result["role"]
                consolidated = {
                    "run_id": run.id, "ip_final": result["ip_final"], "dns_final": result["dns_final"],
                    "status_final": result["status_final"], "match_status": result["match_status"],
                    "role": result["role"], "match_confidence": result["match_confidence"],
                    "match_doubt": int(result["match_doubt"]),
                }
                if asynchronous:
                    pending_consolidated.append((asset, ipam, consolidated))
                else:
                    db.session.add(ConsolidatedAsset(
                        asset_id=asset.id, ipam_record_id=ipam.id if ipam else None,
                        **consolidated,
                    ))
                    db.session.flush()
                counts[result["match_status"]] += 1
                past_assets = capacity_history.get(key, {})
                history = ([(past_assets[identifier].status,
                             past_assets[identifier].disk_usage_percent,
                             past_assets[identifier].ram_usage_percent)
                            for identifier in recent_ids]
                           if len(past_assets) == len(recent_ids) and len(recent_ids) == underused_window - 1
                           else [])
                for anomaly in match_anomalies(result) + network_match_anomalies(result) + required_tag_anomalies(observation) + capacity_anomalies(
                    cast(CapacityAssetRecord, observation), history, disk_threshold,
                    ram_threshold, underused_threshold, underused_window) + (
                    [renamed_by_vm[key]] if key in renamed_by_vm else []
                ) + snapshot_backup_anomalies(
                    observation, cast(list[SnapshotRecord], snapshots_by_vm.get(key, [])),
                    cast(list[BackupRecord], backups_by_vm.get(key, [])),
                    asset.backup_collection_status, datetime.fromisoformat(started_at),
                    snapshot_days, backup_days):
                    anomaly_count += 1
                    if asynchronous:
                        pending_anomalies.append((asset, ipam, anomaly["code"], anomaly["description"]))
                    else:
                        db.session.add(_tracked_anomaly(run.id, anomaly["code"], anomaly["description"], asset, ipam))
                        db.session.flush()
                if (asset.status or "").strip().lower() == "stopped":
                    since = _zombie_since(asset, started_at)
                    if _observed_at(started_at) - since > timedelta(days=zombie_days):
                        anomaly_count += 1
                        description = f"VM stopped for more than {zombie_days} days"
                        if asynchronous:
                            pending_anomalies.append((asset, None, "VM_ZOMBIE", description))
                        else:
                            db.session.add(_tracked_anomaly(run.id, "VM_ZOMBIE", description, asset))
                            db.session.flush()
            completed(5)

            for anomaly in vm_ip_conflicts(asset_snapshots.values()):
                snapshot = cast(AssetRecord, anomaly["asset"])
                asset = stored_assets[(cast(str, snapshot.get("virtual_source", "proxmox")), snapshot["vm_id"])]
                anomaly_count += 1
                if asynchronous:
                    pending_anomalies.append((asset, None, anomaly["code"], anomaly["description"]))
                else:
                    db.session.add(_tracked_anomaly(run.id, anomaly["code"], anomaly["description"], asset))
                    db.session.flush()

            dns_counts = Counter(normalize(record.get("dns_name")) for record in unique_ipam
                                 if record.get("dns_name"))
            ip_counts = Counter(record.get("ip") for record in unique_ipam if record.get("ip"))
            for ipam_key, ipam_row in stored_ipam.items():
                ipam_row.duplicate_dns = int(bool(ipam_key[2]) and dns_counts[normalize(ipam_key[2])] > 1)
                ipam_row.duplicate_ip = int(bool(ipam_key[1]) and ip_counts[cast(str, ipam_key[1])] > 1)
            for anomaly in duplicate_anomalies(unique_ipam) + ptr_anomalies(unique_ipam) + missing_ipam_fields(unique_ipam):
                anomaly_count += 1
                ipam_observation = cast(IpamObservation, anomaly["ipam_record"])
                ipam = stored_ipam[(cast(str | None, ipam_observation.get("ipam_source", ipam_default)),
                                    ipam_observation.get("ip"), ipam_observation.get("dns_name"))]
                if asynchronous:
                    pending_anomalies.append((None, ipam, anomaly["code"], anomaly["description"]))
                else:
                    db.session.add(_tracked_anomaly(run.id, anomaly["code"], anomaly["description"], ipam=ipam))
                    db.session.flush()
            completed(6)

            if asynchronous:
                completed(7)
                for stored_row in (*stored_assets.values(), *stored_ipam.values()):
                    db.session.add(stored_row)
                    db.session.flush()
                for asset, model, details in pending_observations:
                    db.session.add(model(asset_id=asset.id, **details))
                    db.session.flush()
                for asset, ipam, details in pending_consolidated:
                    db.session.add(ConsolidatedAsset(
                        asset_id=asset.id, ipam_record_id=ipam.id if ipam else None, **details,
                    ))
                    db.session.flush()
                for pending_asset, ipam, code, description in pending_anomalies:
                    db.session.add(_tracked_anomaly(run.id, code, description, pending_asset, ipam))
                    db.session.flush()

            if previous:
                current_keys = {key for (key,) in db.session.query(Anomaly.tracking_key)
                                .filter(Anomaly.run_id == run.id, Anomaly.tracking_key.is_not(None))}
                for old in Anomaly.query.filter_by(run_id=previous.id, state="acquittée").all():
                    if (old.tracking_key and old.tracking_key not in current_keys
                             and old.code not in ("IP_OUT_OF_PREFIX", "VM_IP_CONFLICT",
                                                  "DNS_MISSING_PTR", "PTR_MISMATCH",
                                                   "FORGOTTEN_SNAPSHOT", "NO_RECENT_BACKUP",
                                                   "DISK_OVER_90", "RAM_SATURATED", "VM_UNDERUSED")
                            and (old.code != "MATCH_CONFIDENCE_DOUBTFUL"
                                 or old.tracking_key in conclusive_doubt_keys)):
                        old.state = "résolue"
                        old.state_changed_at = _now()

            run.status = "SUCCESS"
            run.finished_at = _now()
            run.matched_name_count = counts["MATCHED_NAME"]
            run.matched_fqdn_count = counts["MATCHED_FQDN"]
            run.matched_ip_count = counts["MATCHED_IP"]
            run.no_match_count = counts["NO_MATCH"]
            if not asynchronous:
                completed(7)
            run_id = cast(int, run.id)
            finished_at = run.finished_at
        if asynchronous:
            db.session.commit()
        result = run
        succeeded = True
        metrics_run_id = run_id
        metrics_finished_at = finished_at
    except Exception as error:  # noqa: BLE001 - all pipeline failures must leave a FAIL run
        db.session.rollback()
        with db.session.begin():
            if run_id is None:
                failed = Run(status="FAIL", started_at=started_at, finished_at=_now(),
                             error_message=str(error))
                db.session.add(failed)
            else:
                failed = cast(Run, db.session.get(Run, run_id))
                failed.status = "FAIL"
                failed.finished_at = _now()
                failed.error_message = str(error)
                failed.current_step = completed_step or 1
                failed.completed_steps = completed_step
            db.session.flush()
            metrics_run_id = failed.id
            metrics_finished_at = failed.finished_at
        result = failed
        succeeded = False
    duration = max(0.0, (datetime.fromisoformat(metrics_finished_at) -
                         datetime.fromisoformat(started_at)).total_seconds())
    run_duration.observe(duration)
    log_run("inventory_succeeded" if succeeded else "inventory_failed", metrics_run_id)
    if succeeded:
        run_anomalies.inc(anomaly_count)
        export_run(cast(int, run_id), {
            "finished_at": finished_at,
            "matched_name_count": counts["MATCHED_NAME"],
            "matched_fqdn_count": counts["MATCHED_FQDN"],
            "matched_ip_count": counts["MATCHED_IP"],
            "no_match_count": counts["NO_MATCH"],
        })
        db.session.rollback()
        rows = Anomaly.query.filter_by(run_id=run_id).all()
        coverage = anomaly_coverage(rows, now=datetime.fromisoformat(finished_at))
        eligible = [row for row in rows if row.state == "ouverte" and coverage[row.id] is None]
        previous_rows = Anomaly.query.filter_by(run_id=previous.id).all() if previous else []
        previous_coverage = anomaly_coverage(
            previous_rows,
            now=datetime.fromisoformat(previous.finished_at or previous.started_at),
        ) if previous else {}
        previous_eligible = [row for row in previous_rows
                             if row.state == "ouverte" and previous_coverage[row.id] is None]
        previous_keys = {row.tracking_key for row in previous_eligible if row.tracking_key}
        new_count = sum(row.tracking_key not in previous_keys for row in eligible)
        alert_count = len(eligible) if should_alert(
            len(eligible), new_count, len(previous_eligible)
        ) else 0
        notify_anomalies(cast(int, run_id), alert_count, finished_at)
        db.session.rollback()
    purge_old_runs()
    return result
