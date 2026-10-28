"""Read-only paginated NetBox IP observations."""

import os
from collections import Counter
from urllib.parse import urljoin

from collector.normalization import normalize
from collector.proxmox import Transport, _base_url, _read_json, _transport
from collector.source import IpamRecord


def _label(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("value") or value.get("name") or value.get("slug") or "")
    return str(value or "")


class NetBoxSource:
    """Use NETBOX_URL and NETBOX_TOKEN; pagination stays within the configured API."""

    def __init__(self, transport: Transport | None = None, timeout: float = 10.0):
        if not 0 < timeout <= 30:
            raise ValueError("Invalid source timeout")
        self._transport = transport or _transport
        self._timeout = timeout

    def collect(self) -> list[IpamRecord]:
        base = _base_url(os.environ.get("NETBOX_URL", ""))
        token = os.environ.get("NETBOX_TOKEN", "")
        if not token or "\n" in token:
            raise ValueError("NetBox token configuration missing or invalid")
        api = urljoin(base, "api/")
        url: str | None = urljoin(api, "ipam/ip-addresses/?limit=100")
        seen: set[str] = set()
        records: list[IpamRecord] = []
        while url is not None:
            if url in seen or len(seen) >= 1000:
                raise RuntimeError("Invalid NetBox pagination")
            seen.add(url)
            page = _read_json(url, api, f"Token {token}", self._transport, self._timeout)
            if not isinstance(page, dict) or not isinstance(page.get("results"), list):
                raise TypeError("Invalid NetBox API response")
            for row in page["results"]:
                if not isinstance(row, dict):
                    raise TypeError("Invalid NetBox IP record")
                tags = row.get("tags") or []
                if not isinstance(tags, list):
                    raise TypeError("Invalid NetBox tags")
                custom = row.get("custom_fields")
                if custom is not None and not isinstance(custom, dict):
                    raise TypeError("Invalid NetBox custom fields")
                records.append({
                    "ip": str(row.get("address") or "").split("/", 1)[0],
                    "dns_name": str(row.get("dns_name") or ""),
                    "status": _label(row.get("status")),
                    "tenant": _label(row.get("tenant")),
                    "site": _label(row.get("site") or (row.get("assigned_object") or {}).get("site")),
                    "tags": ",".join(_label(tag) for tag in tags),
                    "duplicate_dns": 0, "duplicate_ip": 0,
                    "meta_zone": _label((custom or {}).get("meta_zone")),
                    "custom_fields": custom,
                })
            next_url = page.get("next")
            if next_url is not None and (not isinstance(next_url, str) or not next_url):
                raise RuntimeError("Invalid NetBox pagination")
            url = urljoin(url, next_url) if next_url else None
        dns_counts = Counter(normalize(row["dns_name"]) for row in records if normalize(row["dns_name"]))
        ip_counts = Counter(row["ip"] for row in records if row["ip"])
        for row in records:
            row["duplicate_dns"] = int(bool(row["dns_name"]) and dns_counts[normalize(row["dns_name"])] > 1)
            row["duplicate_ip"] = int(bool(row["ip"]) and ip_counts[row["ip"]] > 1)
        return records
