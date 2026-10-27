"""Whitelisted, replaceable exports for a committed inventory run."""

import gzip
import json
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from dotenv import dotenv_values
from flask import current_app
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db
from app.models import Asset, ConsolidatedAsset, IpamRecord


class ExportStorage(Protocol):
    """Storage adapter; a Samba adapter can implement this without network code here."""

    def write(self, name: str, data: bytes) -> None: ...

    def files(self) -> list[tuple[str, datetime]]: ...

    def delete(self, name: str) -> None: ...


class LocalExportStorage:
    def __init__(self, directory: str):
        self.directory = Path(directory)

    def write(self, name: str, data: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as output:
                temporary = Path(output.name)
                output.write(data)
            temporary.replace(self.directory / name)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def files(self) -> list[tuple[str, datetime]]:
        if not self.directory.exists():
            return []
        return [(file.name, datetime.fromtimestamp(file.stat().st_mtime, UTC))
                for file in self.directory.iterdir() if file.is_file()]

    def delete(self, name: str) -> None:
        (self.directory / name).unlink()


_CREDENTIAL = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|bearer)\s*[:= ]")
_CONSOLIDATED = ("vm_id", "vm_name", "type", "node", "status", "tags", "ip_reported",
                 "fqdn", "os", "cpu", "ram", "disk", "ip_final", "dns_final",
                 "match_status", "role")
_VM = ("vm_id", "vm_name", "type", "node", "status", "tags", "ip_reported",
       "fqdn", "os", "cpu", "ram", "disk", "uptime")
_IPAM = ("ip", "dns_name", "status", "tenant", "site", "tags", "duplicate_dns",
         "duplicate_ip", "meta_zone")
_MANAGED = re.compile(r"(?:consolidated|raw-vm|raw-ipam)-[0-9]+\.json(?:l)?\.gz\Z")


def _safe(value: object) -> object:
    if not isinstance(value, str):
        return value
    known = [current_app.config.get(key) for key in ("SECRET_KEY", "JWT_SECRET_KEY")]
    known.append(os.environ.get("ADMIN_PASSWORD") or dotenv_values(Path.cwd() / ".env").get("ADMIN_PASSWORD"))
    if _CREDENTIAL.search(value) or any(isinstance(secret, str) and secret and secret in value
                                        for secret in known):
        return "[REDACTED]"
    return value


def _project(row, fields: tuple[str, ...]) -> dict:
    return {field: _safe(getattr(row, field)) for field in fields}


def _json_gzip(value: object) -> bytes:
    return gzip.compress(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"), mtime=0)


def export_run(run_id: int, summary: dict[str, object], storage: ExportStorage | None = None) -> None:
    """Export one successful committed run; storage may be injected for Samba/tests.

    summary contains only finished_at and the four match counters. Storage files()
    supplies UTC-aware modification times; only managed files older than the
    configured number of days are removed. Errors propagate to the caller.
    """
    config = current_app.config
    if storage is None:
        storage = config.get("EXPORT_STORAGE")
    if storage is None:
        if config["EXPORT_DESTINATION"] != "local":
            raise ValueError("EXPORT_STORAGE adapter required for Samba")
        storage = LocalExportStorage(config["EXPORT_DIRECTORY"])

    with Session(db.engine) as session:
        rows = session.execute(
            select(Asset, ConsolidatedAsset, IpamRecord).join(
                ConsolidatedAsset, (ConsolidatedAsset.asset_id == Asset.id)
                & (ConsolidatedAsset.run_id == Asset.run_id)
            ).outerjoin(IpamRecord, (IpamRecord.id == ConsolidatedAsset.ipam_record_id)
                        & (IpamRecord.run_id == Asset.run_id))
            .where(Asset.run_id == run_id).order_by(Asset.id)
        ).all()
        assets = session.scalars(select(Asset).where(Asset.run_id == run_id).order_by(Asset.id)).all()
        ipam = session.scalars(select(IpamRecord).where(IpamRecord.run_id == run_id).order_by(IpamRecord.id)).all()

    consolidated = []
    for row in rows:
        asset, result, associated = row[0], row[1], row[2]
        entry = _project(asset, _CONSOLIDATED[:12])
        entry.update(_project(result, _CONSOLIDATED[12:]))
        entry["ipam"] = _project(associated, _IPAM) if result.ipam_record_id is not None else None
        consolidated.append(json.dumps(entry, ensure_ascii=False, allow_nan=False))
    storage.write(f"consolidated-{run_id}.jsonl.gz", gzip.compress(
        ("\n".join(consolidated) + ("\n" if consolidated else "")).encode("utf-8"), mtime=0))

    report = (f"# Run {run_id}\n\nFinished: {summary['finished_at']}\n\n"
              f"MATCHED_NAME: {summary['matched_name_count']}\n"
              f"MATCHED_FQDN: {summary['matched_fqdn_count']}\n"
              f"MATCHED_IP: {summary['matched_ip_count']}\n"
              f"NO_MATCH: {summary['no_match_count']}\n")
    storage.write("report.md", report.encode("utf-8"))
    if config["EXPORT_RAW_ENABLED"]:
        storage.write(f"raw-vm-{run_id}.json.gz", _json_gzip([_project(row, _VM) for row in assets]))
        storage.write(f"raw-ipam-{run_id}.json.gz", _json_gzip([_project(row, _IPAM) for row in ipam]))

    now = datetime.now(UTC)
    for name, modified in storage.files():
        if not _MANAGED.fullmatch(name):
            continue
        days = (config["EXPORT_RETENTION_CONSOLIDATED"] if name.startswith("consolidated-")
                else config["EXPORT_RETENTION_RAW"])
        if modified < now - timedelta(days=days):
            storage.delete(name)
