"""Pure, globally prioritized matching of virtualization and IPAM records."""

from collections.abc import Iterable
from typing import TypedDict, cast

from collector.normalization import normalize
from collector.roles import infer_role
from collector.source import AssetRecord, IpamRecord


class ConsolidatedResult(TypedDict):
    asset: AssetRecord
    ipam_record: IpamRecord | None
    match_status: str
    ip_final: str
    dns_final: str
    status_final: str
    role: str


def match_asset(asset: AssetRecord, ipam_records: Iterable[IpamRecord]) -> tuple[str, IpamRecord | None]:
    """Return the first record at the strongest nonempty name, FQDN or IP level."""
    records = tuple(ipam_records)
    name = normalize(asset.get("vm_name"))
    fqdn = normalize(asset.get("fqdn"))
    ip = asset.get("ip_reported")
    for status, wanted, field, normalized in (
        ("MATCHED_NAME", name, "dns_name", True),
        ("MATCHED_FQDN", fqdn, "dns_name", True),
        ("MATCHED_IP", ip, "ip", False),
    ):
        if wanted:
            for record in records:
                candidate = cast(str | None, record.get(field))
                if candidate and (normalize(candidate) if normalized else candidate) == wanted:
                    return status, record
    return "NO_MATCH", None


def consolidate_asset(asset: AssetRecord, ipam_records: Iterable[IpamRecord]) -> ConsolidatedResult:
    """Return independent source snapshots and final fields, without changing inputs."""
    status, matched = match_asset(asset, ipam_records)
    ipam = matched.copy() if matched is not None else None
    return {
        "asset": asset.copy(),
        "ipam_record": ipam,
        "match_status": status,
        "ip_final": (ipam.get("ip") if ipam else None) or asset.get("ip_reported") or "",
        "dns_final": (ipam.get("dns_name") if ipam else None) or asset.get("fqdn") or asset.get("vm_name") or "",
        "status_final": (ipam.get("status") if ipam else None) or asset.get("status") or "",
        "role": infer_role(asset),
    }
