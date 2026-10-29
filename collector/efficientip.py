"""Read-only SOLIDserver IPAM observations over verified HTTPS."""

import base64
import os
from collections import Counter
from typing import cast
from urllib.parse import urljoin

from collector.normalization import normalize
from collector.proxmox import Transport, _base_url, _read_json, _transport
from collector.source import IpamRecord


def _record(row: dict) -> IpamRecord:
    if not isinstance(row.get("ip_addr"), str) or not row["ip_addr"]:
        raise TypeError("Invalid EfficientIP IP record")
    fields = ("ip_name", "ip_status", "tenant", "site", "tags", "meta_zone")
    if any(row.get(field) is not None and not isinstance(row[field], str) for field in fields):
        raise TypeError("Invalid EfficientIP IP record")
    return cast(IpamRecord, {
        "ip": row["ip_addr"], "dns_name": row.get("ip_name") or "",
        "status": row.get("ip_status") or "", "tenant": row.get("tenant") or "",
        "site": row.get("site") or "", "tags": row.get("tags") or "",
        "meta_zone": row.get("meta_zone") or "", "duplicate_dns": 0, "duplicate_ip": 0,
        "custom_fields": row.copy(), "ipam_source": "efficientip",
    })


class EfficientIpSource:
    ipam_source = "efficientip"

    def __init__(self, transport: Transport | None = None, timeout: float = 10.0):
        if not 0 < timeout <= 30:
            raise ValueError("Invalid source timeout")
        self._transport = transport or _transport
        self._timeout = timeout

    def collect(self) -> list[IpamRecord]:
        base = _base_url(os.environ.get("EFFICIENTIP_URL", ""))
        username = os.environ.get("EFFICIENTIP_USERNAME", "")
        password = os.environ.get("EFFICIENTIP_PASSWORD", "")
        if not username or not password or any(c in username + password for c in "\r\n"):
            raise ValueError("EfficientIP credentials missing or invalid")
        auth = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        api = urljoin(base, "rest/")
        response = _read_json(urljoin(api, "ip_address_list"), api, f"Basic {auth}",
                              self._transport, self._timeout)
        if not isinstance(response, list) or any(not isinstance(row, dict) for row in response):
            raise TypeError("Invalid EfficientIP API response")
        records = [_record(row) for row in response]
        dns_counts = Counter(normalize(row["dns_name"]) for row in records if normalize(row["dns_name"]))
        ip_counts = Counter(row["ip"] for row in records if row["ip"])
        for row in records:
            row["duplicate_dns"] = int(bool(row["dns_name"]) and dns_counts[normalize(row["dns_name"])] > 1)
            row["duplicate_ip"] = int(ip_counts[row["ip"]] > 1)
        return records
