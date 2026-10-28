"""Persist one seven-step inventory run as a single atomic collection."""

import json
import os
from collections import Counter
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import cast

from app import db
from app.anomaly_exceptions import anomaly_coverage
from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run
from app.notifications import notify_anomalies
from collector.anomalies import duplicate_anomalies, match_anomalies, renamed_anomalies
from collector.exports import export_run
from collector.matching import consolidate_asset
from collector.mock_ipam import MockIpamSource
from collector.mock_proxmox import MockProxmoxSource
from collector.normalization import normalize
from collector.purge import purge_old_runs
from collector.run_lock import run_lock
from collector.source import AssetRecord, Source
from collector.source import IpamRecord as IpamObservation


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
        target = asset.vm_id
    elif code == "DUPLICATE_DNS" and ipam is not None:
        target = normalize(ipam.dns_name)
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
                   description=description, created_at=_now(), severity=_severity_for(code),
                   tracking_key=key, state=state,
                   state_changed_at=previous.state_changed_at if state == "acquittée" else None)


def run_inventory(
    virtualization_source: Source[AssetRecord] | None = None,
    ipam_source: Source[IpamObservation] | None = None,
    after_step: Callable[[int], None] | None = None,
) -> Run:
    with run_lock():
        return _run_inventory(virtualization_source, ipam_source, after_step)


def _run_inventory(
    virtualization_source: Source[AssetRecord] | None = None,
    ipam_source: Source[IpamObservation] | None = None,
    after_step: Callable[[int], None] | None = None,
    run_id: int | None = None,
) -> Run:
    """Run steps 1–7; after_step receives each completed step number (1 to 7).

    Requires an application context and an idle session. A failure returns a
    separately persisted FAIL run with no observations; an error persisting FAIL
    propagates to the caller.
    """
    virtualization_source = virtualization_source if virtualization_source is not None else MockProxmoxSource()
    ipam_source = ipam_source if ipam_source is not None else MockIpamSource()
    started_at = _now()
    completed_step = 0
    asynchronous = run_id is not None
    pending_consolidated = []
    pending_anomalies = []

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

    try:
        with db.session.begin() if not asynchronous else nullcontext():
            previous = db.session.query(Run.id).filter(Run.status == "SUCCESS").order_by(Run.id.desc()).first()
            previous_assets = (
                db.session.query(Asset).filter(Asset.run_id == previous.id).all() if previous else []
            )
            if run_id is None:
                run = Run(status="RUNNING", started_at=started_at, run_trigger="manuel")
                db.session.add(run)
                db.session.flush()
            else:
                run = db.session.get(Run, run_id)
                started_at = run.started_at
            completed(1)

            assets = virtualization_source.collect()
            completed(2)

            ipam_records = ipam_source.collect()
            completed(3)

            stored_assets: dict[str, Asset] = {}
            asset_snapshots: dict[str, AssetRecord] = {}
            for observation in assets:
                key = observation["vm_id"]
                asset_snapshots[key] = observation.copy()
                if key not in stored_assets:
                    stored_assets[key] = Asset(run_id=run.id, vm_id=key)
                    if not asynchronous:
                        db.session.add(stored_assets[key])
                row = stored_assets[key]
                for field in ("vm_name", "fqdn", "ip_reported", "node", "type", "status", "tags",
                              "os", "annotation", "cpu", "ram", "disk", "uptime"):
                    setattr(row, field, observation.get(field))
                if row.id is None and not asynchronous:
                    db.session.flush()

            stored_ipam: dict[tuple[str | None, str | None], IpamRecord] = {}
            ipam_snapshots: dict[tuple[str | None, str | None], IpamObservation] = {}
            for ipam_observation in ipam_records:
                ipam_key: tuple[str | None, str | None] = (ipam_observation.get("ip"), ipam_observation.get("dns_name"))
                ipam_snapshots[ipam_key] = ipam_observation.copy()
                if ipam_key not in stored_ipam:
                    stored_ipam[ipam_key] = IpamRecord(run_id=run.id)
                    if not asynchronous:
                        db.session.add(stored_ipam[ipam_key])
                ipam_row = stored_ipam[ipam_key]
                for field in ("ip", "dns_name", "status", "tenant", "site", "tags", "meta_zone"):
                    setattr(ipam_row, field, ipam_observation.get(field))
                custom = ipam_observation.get("custom_fields")
                if custom is not None and not isinstance(custom, dict):
                    raise TypeError("Invalid IPAM custom fields")
                ipam_row.custom_fields = json.dumps(custom) if custom is not None else None
                if ipam_row.id is None and not asynchronous:
                    db.session.flush()
            if not asynchronous:
                db.session.flush()
            completed(4)

            unique_ipam = list(ipam_snapshots.values())
            counts: Counter[str] = Counter()
            anomaly_count = 0
            renamed = renamed_anomalies(
                asset_snapshots.values(),
                [{"vm_id": row.vm_id, "vm_name": row.vm_name} for row in previous_assets],
            )
            renamed_by_vm = {entry["asset"]["vm_id"]: entry for entry in renamed if entry["asset"]}
            for observation in asset_snapshots.values():
                asset = stored_assets[observation["vm_id"]]
                result = consolidate_asset(observation, unique_ipam)
                matched = result["ipam_record"]
                ipam = stored_ipam[(matched.get("ip"), matched.get("dns_name"))] if matched else None
                asset.role = result["role"]
                consolidated = {
                    "run_id": run.id, "ip_final": result["ip_final"], "dns_final": result["dns_final"],
                    "status_final": result["status_final"], "match_status": result["match_status"],
                    "role": result["role"],
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
                for anomaly in match_anomalies(result) + (
                    [renamed_by_vm[observation["vm_id"]]] if observation["vm_id"] in renamed_by_vm else []
                ):
                    anomaly_count += 1
                    if asynchronous:
                        pending_anomalies.append((asset, ipam, anomaly["code"], anomaly["description"]))
                    else:
                        db.session.add(_tracked_anomaly(run.id, anomaly["code"], anomaly["description"], asset, ipam))
                        db.session.flush()
            completed(5)

            dns_counts = Counter(normalize(record.get("dns_name")) for record in unique_ipam
                                 if record.get("dns_name"))
            ip_counts = Counter(record.get("ip") for record in unique_ipam if record.get("ip"))
            for ipam_key, ipam_row in stored_ipam.items():
                ipam_row.duplicate_dns = int(bool(ipam_key[1]) and dns_counts[normalize(ipam_key[1])] > 1)
                ipam_row.duplicate_ip = int(bool(ipam_key[0]) and ip_counts[cast(str, ipam_key[0])] > 1)
            for anomaly in duplicate_anomalies(unique_ipam):
                anomaly_count += 1
                ipam_observation = cast(IpamObservation, anomaly["ipam_record"])
                ipam = stored_ipam[(ipam_observation.get("ip"), ipam_observation.get("dns_name"))]
                if asynchronous:
                    pending_anomalies.append((None, ipam, anomaly["code"], anomaly["description"]))
                else:
                    db.session.add(_tracked_anomaly(run.id, anomaly["code"], anomaly["description"], ipam=ipam))
                    db.session.flush()
            completed(6)

            if asynchronous:
                completed(7)
                for row in (*stored_assets.values(), *stored_ipam.values()):
                    db.session.add(row)
                    db.session.flush()
                for asset, ipam, details in pending_consolidated:
                    db.session.add(ConsolidatedAsset(
                        asset_id=asset.id, ipam_record_id=ipam.id if ipam else None, **details,
                    ))
                    db.session.flush()
                for asset, ipam, code, description in pending_anomalies:
                    db.session.add(_tracked_anomaly(run.id, code, description, asset, ipam))
                    db.session.flush()

            if previous:
                current_keys = {key for (key,) in db.session.query(Anomaly.tracking_key)
                                .filter(Anomaly.run_id == run.id, Anomaly.tracking_key.is_not(None))}
                for old in Anomaly.query.filter_by(run_id=previous.id, state="acquittée").all():
                    if old.tracking_key and old.tracking_key not in current_keys:
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
            run_id = run.id
            finished_at = run.finished_at
        if asynchronous:
            db.session.commit()
        result = run
        succeeded = True
    except Exception as error:  # noqa: BLE001 - all pipeline failures must leave a FAIL run
        db.session.rollback()
        with db.session.begin():
            if run_id is None:
                failed = Run(status="FAIL", started_at=started_at, finished_at=_now(),
                             error_message=str(error))
                db.session.add(failed)
            else:
                failed = db.session.get(Run, run_id)
                failed.status = "FAIL"
                failed.finished_at = _now()
                failed.error_message = str(error)
                failed.current_step = completed_step or 1
                failed.completed_steps = completed_step
        result = failed
        succeeded = False
    if succeeded:
        export_run(run_id, {
            "finished_at": finished_at,
            "matched_name_count": counts["MATCHED_NAME"],
            "matched_fqdn_count": counts["MATCHED_FQDN"],
            "matched_ip_count": counts["MATCHED_IP"],
            "no_match_count": counts["NO_MATCH"],
        })
        db.session.rollback()
        rows = Anomaly.query.filter_by(run_id=run_id).all()
        coverage = anomaly_coverage(rows)
        notify_anomalies(run_id, sum(coverage[row.id] is None for row in rows), finished_at)
        db.session.rollback()
    purge_old_runs()
    return result
