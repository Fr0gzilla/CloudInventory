"""Deterministic offline vSphere inventory."""

from collector.source import AssetRecord
from collector.vsphere import _record


class MockVsphereSource:
    virtual_source = "vsphere"

    def collect(self) -> list[AssetRecord]:
        return [_record({"vm": "100", "name": "vsphere-web.example.org",
                         "power_state": "POWERED_ON", "cpu_count": 2,
                         "memory_size_MiB": 4096})]
