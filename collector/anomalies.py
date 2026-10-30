"""Pure matching anomalies and a separate, run-wide IPAM duplicate pass."""

import re
from collections import Counter
from collections.abc import Iterable
from ipaddress import ip_address, ip_network
from typing import TypedDict, cast

import dns.exception
import dns.name

from collector.matching import ConsolidatedResult
from collector.normalization import normalize
from collector.source import AssetRecord, IpamRecord, NetworkIpamRecord


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
        if result["match_doubt"]:
            add("MATCH_CONFIDENCE_DOUBTFUL", "Ambiguous or uncertain IPAM match")
        return entries
    if result["match_doubt"]:
        add("MATCH_CONFIDENCE_DOUBTFUL", "Ambiguous or uncertain IPAM match")
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


def network_match_anomalies(result: ConsolidatedResult) -> list[AnomalyResult]:
    """Check the reported VM IP against the matched IPAM observation's prefix."""
    asset, ipam = result["asset"], result["ipam_record"]
    prefix = cast(NetworkIpamRecord, ipam).get("network_prefix") if ipam else None
    address = asset.get("ip_reported")
    if not isinstance(prefix, str) or not prefix or not isinstance(address, str) or not address:
        return []
    try:
        outside = ip_address(address) not in ip_network(prefix, strict=False)
    except ValueError:
        return []
    return [{"code": "IP_OUT_OF_PREFIX", "description": f"VM IP {address} outside prefix {prefix}",
             "asset": asset.copy(), "ipam_record": cast(IpamRecord, ipam).copy()}] if outside else []


def vm_ip_conflicts(assets: Iterable[AssetRecord]) -> list[AnomalyResult]:
    """Report each VM sharing an IP with another distinct VM, never an IPAM duplicate."""
    records = tuple(assets)
    identities: dict[str, set[tuple[str, str]]] = {}
    for asset in records:
        address = asset.get("ip_reported")
        if isinstance(address, str) and address:
            try:
                normalized = str(ip_address(address))
            except ValueError:
                continue
            identities.setdefault(normalized, set()).add((cast(str, asset.get("virtual_source", "proxmox")),
                                                          asset["vm_id"]))
    return [{"code": "VM_IP_CONFLICT", "description": f"VM IP {asset['ip_reported']} shared by distinct VMs",
             "asset": asset.copy(), "ipam_record": None}
            for asset in records if isinstance(asset.get("ip_reported"), str)
            and _conflicted_ip(asset["ip_reported"], identities)]


def _conflicted_ip(address: str, identities: dict[str, set[tuple[str, str]]]) -> bool:
    try:
        return len(identities.get(str(ip_address(address)), ())) > 1
    except ValueError:
        return False


def ptr_anomalies(ipam_records: Iterable[IpamRecord]) -> list[AnomalyResult]:
    """Compare only explicitly observed PTR values, without resolving DNS."""
    entries: list[AnomalyResult] = []
    for record in ipam_records:
        name, ptr = record.get("dns_name"), cast(NetworkIpamRecord, record).get("ptr_name")
        if not isinstance(name, str) or not name or not isinstance(ptr, str):
            continue
        if not ptr:
            code, description = "DNS_MISSING_PTR", f"DNS {name} has no observed PTR"
        else:
            try:
                mismatch = dns.name.from_text(name).canonicalize() != dns.name.from_text(ptr).canonicalize()
            except (dns.exception.DNSException, ValueError):
                continue
            if not mismatch:
                continue
            code, description = "PTR_MISMATCH", f"PTR {ptr} differs from DNS {name}"
        entries.append({"code": code, "description": description, "asset": None,
                        "ipam_record": record.copy()})
    return entries


def required_tag_anomalies(asset: AssetRecord) -> list[AnomalyResult]:
    """Report each missing nonempty required VM tag independently."""
    tags = asset.get("tags") or ""
    present = {match.group(1).lower() for match in re.finditer(
        r"(?:^|[\s,;])(env|owner|backup)\s*[:=]\s*([^\s,;]+)", tags, re.IGNORECASE)}
    return [{"code": "MISSING_REQUIRED_TAG", "description": f"Missing required tag: {tag}",
             "asset": asset.copy(), "ipam_record": None}
            for tag in ("env", "owner", "backup") if tag not in present]


def missing_ipam_fields(records: Iterable[IpamRecord]) -> list[AnomalyResult]:
    """Check tenant and site on each IPAM observation, including unmatched ones."""
    return [{"code": code, "description": f"Missing IPAM {field}",
             "asset": None, "ipam_record": record.copy()}
            for record in records for field, code in (
                ("tenant", "MISSING_TENANT"), ("site", "MISSING_SITE"))
            if not isinstance(record.get(field), str) or not cast(str, record.get(field)).strip()]


def renamed_anomalies(current: Iterable[AssetRecord], previous: Iterable[AssetRecord]) -> list[AnomalyResult]:
    """Compare stable VM identifiers across successive successful inventories."""
    old_names = {(asset.get("virtual_source", "proxmox"), asset["vm_id"]): asset.get("vm_name")
                 for asset in previous}
    return [
        {"code": "RENAMED", "description": f"VM renamed from {old_names[(asset.get('virtual_source', 'proxmox'), asset['vm_id'])]} to {asset['vm_name']}",
         "asset": asset.copy(), "ipam_record": None}
        for asset in current
        if (asset.get("virtual_source", "proxmox"), asset["vm_id"]) in old_names
        and old_names[(asset.get("virtual_source", "proxmox"), asset["vm_id"])] != asset.get("vm_name")
        and old_names[(asset.get("virtual_source", "proxmox"), asset["vm_id"])] is not None
        and asset.get("vm_name") is not None
    ]


def collect_anomalies(consolidated: Iterable[ConsolidatedResult],
                      ipam_records: Iterable[IpamRecord]) -> list[AnomalyResult]:
    """Emit matching anomalies first, then scan the complete IPAM run for duplicates."""
    entries = [entry for result in consolidated for entry in match_anomalies(result)]
    return entries + duplicate_anomalies(ipam_records)
