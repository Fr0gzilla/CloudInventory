"""Pure matching anomalies and a separate, run-wide IPAM duplicate pass."""

from collections import Counter
from collections.abc import Iterable
from typing import TypedDict, cast

from collector.matching import ConsolidatedResult
from collector.normalization import normalize
from collector.source import AssetRecord, IpamRecord


class AnomalyResult(TypedDict):
    code: str
    description: str
    asset: AssetRecord | None
    ipam_record: IpamRecord | None


def match_anomalies(result: ConsolidatedResult) -> list[AnomalyResult]:
    """Produce one entry per applicable matching code for one consolidated VM."""
    asset = result["asset"]
    ipam = result["ipam_record"]
    status = result["match_status"]
    entries: list[AnomalyResult] = []

    def add(code: str, description: str) -> None:
        entries.append({"code": code, "description": description,
                        "asset": asset.copy(), "ipam_record": ipam.copy() if ipam else None})

    if status == "NO_MATCH":
        add("NO_MATCH", "No IPAM match for this asset")
        return entries
    if status == "MATCHED_IP":
        add("MATCHED_IP", "Matched by IP only")
        name = normalize(asset.get("vm_name"))
        dns = normalize(ipam.get("dns_name")) if ipam else ""
        if name and dns and name != dns:
            add("HOSTNAME_MISMATCH", "VM hostname differs from IPAM DNS")
    if (ipam and (asset.get("status") or "").strip().lower() == "stopped"
            and (ipam.get("status") or "").strip().lower() == "active"):
        add("STATUS_MISMATCH", "Stopped VM has an active IPAM address")
    return entries


def duplicate_anomalies(ipam_records: Iterable[IpamRecord]) -> list[AnomalyResult]:
    """Count all coexisting IPAM observations, including unmatched ones, once per group."""
    records = tuple(ipam_records)
    entries: list[AnomalyResult] = []
    for code, field, key_of in (
        ("DUPLICATE_DNS", "dns_name", normalize),
        ("DUPLICATE_IP", "ip", lambda value: value),
    ):
        counts = Counter(key_of(cast(str | None, record.get(field))) for record in records if record.get(field))
        seen: set[str] = set()
        for record in records:
            key = key_of(cast(str | None, record.get(field))) if record.get(field) else ""
            if key and counts[key] > 1 and key not in seen:
                seen.add(key)
                entries.append({"code": code, "description": f"{counts[key]} IPAM records share {field}: {key}",
                                "asset": None, "ipam_record": record.copy()})
    return entries


def collect_anomalies(consolidated: Iterable[ConsolidatedResult],
                      ipam_records: Iterable[IpamRecord]) -> list[AnomalyResult]:
    """Emit matching anomalies first, then scan the complete IPAM run for duplicates."""
    entries = [entry for result in consolidated for entry in match_anomalies(result)]
    return entries + duplicate_anomalies(ipam_records)
