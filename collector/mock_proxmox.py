"""Deterministic virtualization observations; no hypervisor connection."""

from collector.source import AssetRecord


class MockProxmoxSource:
    def collect(self) -> list[AssetRecord]:
        assets: list[AssetRecord] = []
        themes = (
            ("web", "a", "production front"),
            ("db", "b", "production data"),
            ("dns", "d", "network infrastructure"),
            ("api", "a", "staging applications"),
            ("ops", "s", "supervision"),
        )
        for index, (prefix, letter, theme) in enumerate(themes, start=1):
            for offset in range(9):
                exceptional = offset == 8
                vm_id = 995 + index - 1 if exceptional else 100 + (index - 1) * 8 + offset
                vm_name = (
                    "ghost-vm" if vm_id == 999 else
                    f"unmatched-{vm_id}" if exceptional else
                    "monitoring" if index == 5 and offset == 0 else
                    f"{prefix}-{letter}{500 + offset}"
                )
                ip = (
                    "10.0.1.10" if vm_id == 999 else
                    f"10.0.5.{100 + index}" if exceptional else
                    f"10.0.{index}.{10 + offset}"
                )
                stopped = vm_id in (101, 118)
                assets.append({
                    "vm_id": str(vm_id),
                    "vm_name": vm_name,
                    "fqdn": f"{vm_name}.prod.local",
                    "ip_reported": ip,
                    "node": f"pve{index}",
                    "type": "qemu" if offset % 2 == 0 else "lxc",
                    "status": "stopped" if stopped else "running",
                    "tags": "role:monitoring" if vm_name == "monitoring" else f"zone:{theme.replace(' ', '-')}",
                    "role": None,
                    "os": "debian",
                    "annotation": theme,
                    "cpu": 0.0 if stopped else 0.25,
                    "ram": 0.0 if stopped else 2048.0,
                    "disk": 32.0,
                    "uptime": 0.0 if stopped else 86400.0,
                })
        return assets
