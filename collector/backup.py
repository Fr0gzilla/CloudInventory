"""Read-only Proxmox Backup Server observations, with injectable HTTPS transport."""

import os
from datetime import UTC, datetime
from urllib.parse import quote, urljoin

from collector.proxmox import Transport, _base_url, _read_json, _transport
from collector.source import BackupRecord


class PbsBackupSource:
    def __init__(self, transport: Transport | None = None, timeout: float = 10.0):
        if not 0 < timeout <= 30:
            raise ValueError("Invalid source timeout")
        self._transport = transport or _transport
        self._timeout = timeout

    def collect(self) -> list[BackupRecord]:
        base = _base_url(os.environ.get("PBS_URL", ""))
        datastore = os.environ.get("PBS_DATASTORE", "")
        token_id = os.environ.get("PBS_TOKEN_ID", "")
        secret = os.environ.get("PBS_TOKEN_SECRET", "")
        if not datastore or datastore in (".", "..") or "/" in datastore or not token_id or not secret or ("\n" in datastore + token_id + secret):
            raise ValueError("PBS configuration missing or invalid")
        api = urljoin(base, "api2/json/")
        path = f"admin/datastore/{quote(datastore, safe='')}/snapshots"
        result = _read_json(urljoin(api, path), api, f"PBSAPIToken={token_id}:{secret}",
                            self._transport, self._timeout)
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise TypeError("Invalid PBS snapshot response")
        backups: list[BackupRecord] = []
        for item in result["data"]:
            if not isinstance(item, dict):
                raise TypeError("Invalid PBS snapshot entry")
            if item.get("backup-type") != "vm":
                continue
            vmid, timestamp = item.get("backup-id"), item.get("backup-time")
            if not isinstance(vmid, str) or not vmid or type(timestamp) is not int or timestamp < 0:
                raise ValueError("Invalid PBS snapshot entry")
            backups.append({"vm_id": vmid,
                            "backup_id": f"vm/{vmid}/{timestamp}",
                            "backup_created_at": datetime.fromtimestamp(timestamp, UTC).isoformat()})
        return backups
