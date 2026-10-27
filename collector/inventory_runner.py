"""Persist one seven-step inventory run as a single atomic collection."""

from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime

from app import db
from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run
from collector.anomalies import duplicate_anomalies, match_anomalies
from collector.exports import export_run
from collector.matching import consolidate_asset
from collector.mock_ipam import MockIpamSource
from collector.mock_proxmox import MockProxmoxSource
from collector.normalization import normalize
from collector.purge import purge_old_runs
from collector.source import AssetRecord, Source
from collector.source import IpamRecord as IpamObservation


def _now() -> str:
    return datetime.now(UTC).isoformat()


def run_inventory(
    virtualization_source: Source[AssetRecord] | None = None,
    ipam_source: Source[IpamObservation] | None = None,
    after_step: Callable[[int], None] | None = None,
) -> Run:
    """Run steps 1–7; after_step receives each completed step number (1 to 7).

    Requires an application context and an idle session. A failure returns a
    separately persisted FAIL run with no observations; an error persisting FAIL
    propagates to the caller.
    """
    virtualization_source = virtualization_source if virtualization_source is not None else MockProxmoxSource()
    ipam_source = ipam_source if ipam_source is not None else MockIpamSource()
    started_at = _now()

    def completed(step: int) -> None:
        if after_step is not None:
            after_step(step)

    try:
        with db.session.begin():
            run = Run(status="RUNNING", started_at=started_at)
            db.session.add(run)
            db.session.flush()
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
                    db.session.add(stored_assets[key])
                row = stored_assets[key]
                for field in ("vm_name", "fqdn", "ip_reported", "node", "type", "status", "tags",
                              "os", "annotation", "cpu", "ram", "disk", "uptime"):
                    setattr(row, field, observation.get(field))
                if row.id is None:
                    db.session.flush()

            stored_ipam: dict[tuple[str | None, str | None], IpamRecord] = {}
            ipam_snapshots: dict[tuple[str | None, str | None], IpamObservation] = {}
            for observation in ipam_records:
                key = (observation.get("ip"), observation.get("dns_name"))
                ipam_snapshots[key] = observation.copy()
                if key not in stored_ipam:
                    stored_ipam[key] = IpamRecord(run_id=run.id)
                    db.session.add(stored_ipam[key])
                row = stored_ipam[key]
                for field in ("ip", "dns_name", "status", "tenant", "site", "tags", "meta_zone"):
                    setattr(row, field, observation.get(field))
                if row.id is None:
                    db.session.flush()
            db.session.flush()
            completed(4)

            unique_ipam = list(ipam_snapshots.values())
            counts = Counter()
            for observation in asset_snapshots.values():
                asset = stored_assets[observation["vm_id"]]
                result = consolidate_asset(observation, unique_ipam)
                matched = result["ipam_record"]
                ipam = stored_ipam[(matched.get("ip"), matched.get("dns_name"))] if matched else None
                asset.role = result["role"]
                db.session.add(ConsolidatedAsset(
                    run_id=run.id, asset_id=asset.id,
                    ipam_record_id=ipam.id if ipam else None,
                    ip_final=result["ip_final"], dns_final=result["dns_final"],
                    status_final=result["status_final"], match_status=result["match_status"],
                    role=result["role"],
                ))
                db.session.flush()
                counts[result["match_status"]] += 1
                for anomaly in match_anomalies(result):
                    db.session.add(Anomaly(
                        run_id=run.id, asset_id=asset.id,
                        ipam_record_id=ipam.id if ipam else None,
                        code=anomaly["code"], description=anomaly["description"],
                        created_at=_now(),
                    ))
                    db.session.flush()
            completed(5)

            dns_counts = Counter(normalize(record.get("dns_name")) for record in unique_ipam
                                 if record.get("dns_name"))
            ip_counts = Counter(record.get("ip") for record in unique_ipam if record.get("ip"))
            for key, row in stored_ipam.items():
                row.duplicate_dns = int(bool(key[1]) and dns_counts[normalize(key[1])] > 1)
                row.duplicate_ip = int(bool(key[0]) and ip_counts[key[0]] > 1)
            for anomaly in duplicate_anomalies(unique_ipam):
                observation = anomaly["ipam_record"]
                ipam = stored_ipam[(observation.get("ip"), observation.get("dns_name"))]
                db.session.add(Anomaly(
                    run_id=run.id, ipam_record_id=ipam.id,
                    code=anomaly["code"], description=anomaly["description"],
                    created_at=_now(),
                ))
                db.session.flush()
            completed(6)

            run.status = "SUCCESS"
            run.finished_at = _now()
            run.matched_name_count = counts["MATCHED_NAME"]
            run.matched_fqdn_count = counts["MATCHED_FQDN"]
            run.matched_ip_count = counts["MATCHED_IP"]
            run.no_match_count = counts["NO_MATCH"]
            completed(7)
            run_id = run.id
            finished_at = run.finished_at
        result = run
        succeeded = True
    except Exception as error:  # noqa: BLE001 - all pipeline failures must leave a FAIL run
        db.session.rollback()
        with db.session.begin():
            failed = Run(status="FAIL", started_at=started_at, finished_at=_now(),
                         error_message=str(error))
            db.session.add(failed)
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
    purge_old_runs()
    return result
