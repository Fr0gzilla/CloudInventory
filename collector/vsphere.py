"""Read-only vSphere REST source, using the verified Proxmox HTTPS transport."""

import base64
import os
from typing import cast
from urllib.parse import urljoin

from collector.proxmox import Transport, _base_url, _read_json, _transport
from collector.source import AssetRecord


def _record(item: dict) -> AssetRecord:
    vm_id = item.get("vm")
    if not isinstance(vm_id, str) or not vm_id:
        raise RuntimeError("Invalid vSphere VM identifier")
    name = item.get("name")
    if name is not None and not isinstance(name, str):
        raise RuntimeError("Invalid vSphere VM response")
    state = item.get("power_state")
    if state is not None and (not isinstance(state, str) or
                              state not in ("POWERED_ON", "POWERED_OFF", "SUSPENDED")):
        raise RuntimeError("Invalid vSphere VM response")
    for field in ("cpu_count", "memory_size_MiB"):
        if item.get(field) is not None and (isinstance(item[field], bool) or
                                            not isinstance(item[field], (int, float))):
            raise RuntimeError("Invalid vSphere VM response")
    return cast(AssetRecord, {
        "vm_id": vm_id, "vm_name": name,
        "fqdn": name if name and "." in name else None,
        "ip_reported": None, "node": None, "type": "qemu",
        "status": {"POWERED_ON": "running", "POWERED_OFF": "stopped",
                   "SUSPENDED": "suspended"}.get(state) if state else None,
        "tags": None, "role": None, "os": None, "annotation": None,
        "cpu": float(item["cpu_count"]) if item.get("cpu_count") is not None else None,
        "ram": float(item["memory_size_MiB"]) if item.get("memory_size_MiB") is not None else None,
        "disk": None, "uptime": None,
    })


class VsphereSource:
    virtual_source = "vsphere"

    def __init__(self, transport: Transport | None = None, timeout: float = 10.0):
        if not 0 < timeout <= 30:
            raise ValueError("Invalid source timeout")
        self._transport = transport or _transport
        self._timeout = timeout

    def collect(self) -> list[AssetRecord]:
        base = _base_url(os.environ.get("VSPHERE_URL", ""))
        username = os.environ.get("VSPHERE_USERNAME", "")
        password = os.environ.get("VSPHERE_PASSWORD", "")
        if not username or not password or any(char in username + password for char in "\r\n"):
            raise ValueError("vSphere credentials missing or invalid")
        api = urljoin(base, "api/")
        basic = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        session = _read_json(urljoin(api, "session"), api, f"Basic {basic}",
                             self._transport, self._timeout, method="POST")
        if not isinstance(session, str) or not session or any(char in session for char in "\r\n"):
            raise RuntimeError("Invalid vSphere session response")
        inventory = _read_json(urljoin(api, "vcenter/vm"), api, session,
                               self._transport, self._timeout,
                               auth_header="vmware-api-session-id")
        if not isinstance(inventory, list) or any(not isinstance(item, dict) for item in inventory):
            raise RuntimeError("Invalid vSphere VM list")
        return [_record(item) for item in inventory]
