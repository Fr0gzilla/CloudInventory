"""Deterministic offline SOLIDserver IPAM observations."""

from collector.efficientip import _record
from collector.source import IpamRecord


class MockEfficientIpSource:
    ipam_source = "efficientip"

    def collect(self) -> list[IpamRecord]:
        return [_record({"ip_addr": "192.0.2.50", "ip_name": "offline.example.org",
                         "ip_status": "active", "tenant": "Lab", "site": "DC1",
                         "tags": "", "meta_zone": "lab", "site_id": "42"}),
                _record({"ip_addr": "192.0.2.51", "ip_name": "orphan.example.org",
                         "ip_status": "reserved", "tenant": "Lab", "site": "DC1",
                         "tags": "", "meta_zone": "lab", "site_id": "42"})]
