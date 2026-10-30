"""Pure, globally prioritized matching of virtualization and IPAM records."""

from collections.abc import Iterable
from typing import TypedDict, cast

from rapidfuzz.fuzz import ratio

from collector.normalization import normalize
from collector.roles import infer_role
from collector.source import AssetRecord, IpamRecord


class ConsolidatedResult(TypedDict):
    asset: AssetRecord
    ipam_record: IpamRecord | None
    match_status: str
    match_confidence: int | None
    match_doubt: bool
    ip_final: str
    dns_final: str
    status_final: str
    role: str


def match_asset(asset: AssetRecord, ipam_records: Iterable[IpamRecord]) -> tuple[str, IpamRecord | None]:
    """Return the strongest exact match, otherwise one unambiguous fuzzy match."""
    status, record, _, _ = _match_details(asset, ipam_records)
    return status, record


def _match_details(asset: AssetRecord, ipam_records: Iterable[IpamRecord]
                   ) -> tuple[str, IpamRecord | None, int | None, bool]:
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
                    return status, record, 100, False
    names = {value for value in (name, fqdn) if value}
    best = -1.0
    winner: IpamRecord | None = None
    tied = False
    for record in records:
        candidate = normalize(record.get("dns_name"))
        if not names or not candidate:
            continue
        score = max(ratio(value, candidate) for value in names)
        if score > best:
            best, winner, tied = score, record, False
        elif score == best:
            tied = True
    if winner is None:
        return "NO_MATCH", None, None, False
    confidence = min(100, int(best + 0.5))
    doubt = tied or 70 <= confidence < 90
    if confidence >= 70 and not tied:
        return "MATCHED_FUZZY", winner, confidence, doubt
    return "NO_MATCH", None, confidence, doubt


def consolidate_asset(asset: AssetRecord, ipam_records: Iterable[IpamRecord]) -> ConsolidatedResult:
    """Return independent source snapshots and final fields, without changing inputs."""
    status, matched, confidence, doubt = _match_details(asset, ipam_records)
    ipam = matched.copy() if matched is not None else None
    return {
        "asset": asset.copy(),
        "ipam_record": ipam,
        "match_status": status,
        "match_confidence": confidence,
        "match_doubt": doubt,
        "ip_final": (ipam.get("ip") if ipam else None) or asset.get("ip_reported") or "",
        "dns_final": (ipam.get("dns_name") if ipam else None) or asset.get("fqdn") or asset.get("vm_name") or "",
        "status_final": (ipam.get("status") if ipam else None) or asset.get("status") or "",
        "role": infer_role(asset),
    }
