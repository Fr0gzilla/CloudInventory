"""Deterministic IPAM observations, including intentional duplicate records."""

from collector.mock_proxmox import MockProxmoxSource
from collector.source import IpamRecord


class MockIpamSource:
    def collect(self) -> list[IpamRecord]:
        records: list[IpamRecord] = []
        tenants = ("Production", "Infra", "Dev", "Staging", "Supervision", "DevOps")
        for asset in MockProxmoxSource().collect():
            if int(asset["vm_id"]) >= 995:
                continue
            node = int(asset["node"][-1])
            records.append({
                "ip": asset["ip_reported"],
                "dns_name": asset["vm_name"],
                "status": "active",
                "tenant": tenants[(node - 1) % len(tenants)],
                "site": "DC1" if node <= 3 else "DC2",
                "tags": "reserved" if asset["vm_name"] == "decom-server" else "",
                "duplicate_dns": 1 if asset["vm_name"] == "monitoring" else 0,
                "duplicate_ip": 1 if asset["ip_reported"] == "10.0.4.16" else 0,
                "meta_zone": "production" if node <= 3 else "staging",
                "custom_fields": {"meta_zone": "production" if node <= 3 else "staging"},
            })
        records.extend((
            {
                "ip": "10.0.5.200", "dns_name": "decom-server", "status": "reserved",
                "tenant": "Infra", "site": "DC2", "tags": "reserved",
                "duplicate_dns": 0, "duplicate_ip": 0, "meta_zone": "decommissioned",
                "custom_fields": {"meta_zone": "decommissioned"},
            },
            {
                "ip": "10.0.5.201", "dns_name": "orphan-lab", "status": "reserved",
                "tenant": "DevOps", "site": "DC2", "tags": "",
                "duplicate_dns": 0, "duplicate_ip": 0, "meta_zone": "lab",
                "custom_fields": {"meta_zone": "lab"},
            },
            {
                "ip": "10.0.5.50", "dns_name": "monitoring", "status": "active",
                "tenant": "Supervision", "site": "DC2", "tags": "",
                "duplicate_dns": 1, "duplicate_ip": 0, "meta_zone": "staging",
                "custom_fields": {"meta_zone": "staging"},
            },
            {
                "ip": "10.0.4.16", "dns_name": "shared-address", "status": "active",
                "tenant": "Staging", "site": "DC2", "tags": "",
                "duplicate_dns": 0, "duplicate_ip": 1, "meta_zone": "staging",
                "custom_fields": {"meta_zone": "staging"},
            },
        ))
        return records
