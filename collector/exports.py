"""Exporter — génère les exports consolidés, rapports et bruts après chaque run.

Formats :
  - JSONL.gz : export consolidé (1 ligne = 1 VM), rétention 30 jours
  - report.md : rapport de run lisible (écrasé à chaque run)
  - JSON.gz : exports bruts API (optionnel), rétention 7 jours

Stockage : dossier local ou partage Samba (SMB) configurable via .env.
"""

import gzip
import json
import logging
import os
import ntpath
import re
import shutil
import stat
import tempfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import current_app

from app.extensions import db
from app.models import Run, Asset, IpamRecord, ConsolidatedAsset, Anomaly

logger = logging.getLogger("cloudinventory.exporter")

EXPORT_NAMES = {
    "consolidated": re.compile(r"run_[1-9][0-9]*_[0-9]{8}_[0-9]{6}\.jsonl\.gz"),
    "raw": re.compile(r"(?:vms|ipam)_run_[1-9][0-9]*_[0-9]{8}_[0-9]{6}\.json\.gz"),
}


def _validate_run_id(run_id):
    if type(run_id) is not int or run_id <= 0:
        raise ValueError("Identifiant de run invalide")


def _subdir(export_dir, name):
    root = Path(export_dir).resolve(strict=True)
    path = root / name
    if path.is_symlink():
        raise ValueError("Sous-dossier d'export symbolique interdit")
    path.mkdir(exist_ok=True)
    return path


@contextmanager
def _output(path, compressed=False):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".export-")
    os.close(fd)
    try:
        opener = gzip.open if compressed else open
        with opener(temporary, "wt", encoding="utf-8") as stream:
            yield stream
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _get_export_config():
    """Récupère la config d'export depuis l'app Flask."""
    return {
        "enabled": current_app.config.get("EXPORT_ENABLED", False),
        "local_path": current_app.config.get("EXPORT_LOCAL_PATH", ""),
        "smb_path": current_app.config.get("EXPORT_SMB_PATH", ""),
        "smb_username": current_app.config.get("EXPORT_SMB_USERNAME", ""),
        "smb_password": current_app.config.get("EXPORT_SMB_PASSWORD", ""),
        "retention_consolidated": current_app.config.get(
            "EXPORT_RETENTION_CONSOLIDATED", 30
        ),
        "retention_raw": current_app.config.get("EXPORT_RETENTION_RAW", 7),
        "export_raw": current_app.config.get("EXPORT_RAW_ENABLED", False),
    }


_REMOTE_URI = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)


def _is_remote_uri(value):
    """True si la valeur est une URI (ex. ``smb://serveur/partage``) et non un chemin local."""
    return bool(_REMOTE_URI.match(value.strip()))


def _get_export_dir(config):
    """Détermine et crée le répertoire d'export (Samba ou local).

    Une valeur sous forme d'URI n'est jamais utilisée comme chemin local :
    on replie sur le dossier d'export local prévu (T012).
    """
    smb = config["smb_path"]
    if smb and _is_remote_uri(smb):
        logger.warning(
            "EXPORT_SMB_PATH %s est une URI ; repli sur le dossier d'export local", smb
        )
        smb = ""
    if smb:
        path = Path(smb)
    else:
        local = config["local_path"] or os.path.join(os.getcwd(), "exports")
        path = Path(local)

    path.mkdir(parents=True, exist_ok=True)
    return path


def export_consolidated_jsonl(run_id, export_dir):
    """Génère l'export consolidé JSONL.gz pour un run.

    Fichier : consolidated/run_{id}_{date}.jsonl.gz
    """
    _validate_run_id(run_id)
    subdir = _subdir(export_dir, "consolidated")

    run = Run.query.get(run_id)
    if not run:
        return None

    rows = (
        db.session.query(ConsolidatedAsset, Asset, IpamRecord)
        .join(Asset, ConsolidatedAsset.asset_id == Asset.id)
        .outerjoin(IpamRecord, ConsolidatedAsset.ipam_record_id == IpamRecord.id)
        .filter(ConsolidatedAsset.run_id == run_id)
        .all()
    )

    ts = run.end_date or run.start_date or datetime.now(timezone.utc)
    filename = f"run_{run_id}_{ts.strftime('%Y%m%d_%H%M%S')}.jsonl.gz"
    filepath = subdir / filename

    with _output(filepath, compressed=True) as f:
        for ca, asset, ipam in rows:
            record = {
                "run_id": run_id,
                "vm_id": asset.vm_id,
                "vm_name": asset.vm_name,
                "type": asset.type,
                "node": asset.node,
                "status": asset.status,
                "tags": asset.tags,
                "ip_reported": asset.ip_reported,
                "fqdn": asset.fqdn,
                "os": asset.os,
                "cpu_count": asset.cpu_count,
                "cpu_usage": asset.cpu_usage,
                "ram_max": asset.ram_max,
                "ram_used": asset.ram_used,
                "disk_max": asset.disk_max,
                "disk_used": asset.disk_used,
                "uptime": asset.uptime,
                "ip_final": ca.ip_final,
                "dns_final": ca.dns_final,
                "match_status": ca.match_status,
                "role": ca.role,
                "ipam_ip": ipam.ip if ipam else None,
                "ipam_dns": ipam.dns_name if ipam else None,
                "ipam_tenant": ipam.tenant if ipam else None,
                "ipam_site": ipam.site if ipam else None,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    logger.info("Export consolidé : %s (%d lignes)", filepath.name, len(rows))
    return filepath


def export_report_md(run_id, anomaly_details, export_dir, ipam_count=None):
    """Génère le rapport de run en Markdown (écrase le précédent).

    Fichier : report.md
    """
    _validate_run_id(run_id)
    run = Run.query.get(run_id)
    if not run:
        return None

    counts = Counter(
        status for (status,) in db.session.query(ConsolidatedAsset.match_status)
        .filter(ConsolidatedAsset.run_id == run_id)
    )
    total_matched = sum(counts[status] for status in ("MATCHED_NAME", "MATCHED_FQDN", "MATCHED_IP"))
    total_vms = sum(counts.values())
    match_rate = round(total_matched / total_vms * 100, 1) if total_vms > 0 else 0
    anomaly_count = sum(anomaly_details.values())

    started = (
        run.start_date.strftime("%d/%m/%Y %H:%M:%S") if run.start_date else "—"
    )
    ended = run.end_date.strftime("%d/%m/%Y %H:%M:%S") if run.end_date else "—"

    lines = [
        f"# CloudInventory — Rapport Run #{run.id}",
        "",
        f"**Statut** : {run.status}",
        f"**Début** : {started}",
        f"**Fin** : {ended}",
        "",
        "---",
        "",
        "## Résultats",
        "",
        "| Métrique | Valeur |",
        "|---|---|",
        f"| VMs collectées | {total_vms} |",
        f"| IPs IPAM | {ipam_count if ipam_count is not None else 'non disponible'} |",
        f"| Match par nom | {counts['MATCHED_NAME']} |",
        f"| Match par FQDN | {counts['MATCHED_FQDN']} |",
        f"| Match par IP | {counts['MATCHED_IP']} |",
        f"| No match | {counts['NO_MATCH']} |",
        f"| **Taux de correspondance** | **{match_rate}%** ({total_matched}/{total_vms}) |",
        "",
    ]

    if anomaly_count > 0:
        lines.extend([
            "---",
            "",
            f"## Anomalies ({anomaly_count})",
            "",
            "| Type | Nombre |",
            "|---|---|",
        ])
        for atype, acount in anomaly_details.items():
            lines.append(f"| {atype} | {acount} |")
        lines.append("")

        # Détail des anomalies
        anomaly_rows = (
            db.session.query(Anomaly, Asset)
            .outerjoin(Asset, Anomaly.asset_id == Asset.id)
            .filter(Anomaly.run_id == run_id)
            .order_by(Anomaly.code)
            .all()
        )
        if anomaly_rows:
            lines.extend([
                "### Détail",
                "",
                "| Type | VM | Détails |",
                "|---|---|---|",
            ])
            for anomaly, asset in anomaly_rows:
                details = anomaly.description or ""
                lines.append(f"| `{anomaly.code}` | **{asset.vm_name if asset else '—'}** | {details} |")
            lines.append("")
    else:
        lines.extend([
            "---",
            "",
            "## Anomalies",
            "",
            "Aucune anomalie détectée.",
            "",
        ])

    lines.extend([
        "---",
        "",
        f"*Généré automatiquement par CloudInventory le {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M:%S')} UTC*",
    ])

    filepath = Path(export_dir) / "report.md"
    with _output(filepath) as stream:
        stream.write("\n".join(lines))
    logger.info("Rapport Markdown : %s", filepath)
    return filepath


def export_raw_json(run_id, vm_list, ipam_list, export_dir):
    """Génère les exports bruts JSON.gz (optionnel, pour debug/rejeu).

    Fichiers : raw/vms_run_{id}_{date}.json.gz, raw/ipam_run_{id}_{date}.json.gz
    """
    _validate_run_id(run_id)
    subdir = _subdir(export_dir, "raw")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    paths = []

    for name, data in [("vms", vm_list), ("ipam", ipam_list)]:
        filename = f"{name}_run_{run_id}_{ts}.json.gz"
        filepath = subdir / filename
        with _output(filepath, compressed=True) as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        paths.append(filepath)
        logger.info("Export brut : %s (%d entrées)", filepath.name, len(data))

    return paths


def cleanup_old_exports(export_dir, retention_consolidated=30, retention_raw=7):
    """Supprime les exports au-delà de la durée de rétention."""
    retention = _retention(retention_consolidated, retention_raw)
    now = datetime.now(timezone.utc).timestamp()
    deleted = 0
    root_fd = os.open(export_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name, days in retention.items():
            try:
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            except FileNotFoundError:
                continue
            try:
                for filename in os.listdir(fd):
                    if not EXPORT_NAMES[name].fullmatch(filename):
                        continue
                    info = os.stat(filename, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode) and info.st_mtime < now - days * 86400:
                        os.unlink(filename, dir_fd=fd)
                        deleted += 1
            finally:
                os.close(fd)
    finally:
        os.close(root_fd)
    return deleted


def _retention(consolidated, raw):
    values = {"consolidated": int(consolidated), "raw": int(raw)}
    if any(days < 1 for days in values.values()):
        raise ValueError("La rétention doit être un nombre positif de jours")
    return values


def _publish_smb(export_dir, remote, retention, config):
    import smbclient

    remote = remote.replace("/", "\\").rstrip("\\")
    parts = remote[2:].split("\\")
    if not remote.startswith("\\\\") or len(parts) < 2 or any(p in ("", ".", "..") for p in parts):
        raise ValueError("Chemin SMB UNC invalide")
    smbclient.register_session(
        parts[0], username=config.get("smb_username") or None,
        password=config.get("smb_password") or None,
        connection_timeout=10,
        encrypt=True,
    )
    try:
        smbclient.makedirs(remote, exist_ok=True)
        if smbclient.path.islink(remote):
            raise ValueError("Dossier SMB symbolique interdit")
        for name in EXPORT_NAMES:
            target_dir = ntpath.join(remote, name)
            if smbclient.path.islink(target_dir):
                raise ValueError("Sous-dossier SMB symbolique interdit")
            smbclient.makedirs(target_dir, exist_ok=True)
        for source in export_dir.rglob("*"):
            if not source.is_file():
                continue
            target = ntpath.join(remote, *source.relative_to(export_dir).parts)
            if smbclient.path.islink(target):
                raise ValueError("Fichier SMB symbolique interdit")
            with source.open("rb") as src, smbclient.open_file(target, mode="wb") as dst:
                shutil.copyfileobj(src, dst)
        now = datetime.now(timezone.utc).timestamp()
        for name, days in retention.items():
            folder = ntpath.join(remote, name)
            for entry in smbclient.scandir(folder):
                if not EXPORT_NAMES[name].fullmatch(entry.name):
                    continue
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and info.st_mtime < now - days * 86400:
                    smbclient.remove(ntpath.join(folder, entry.name))
    finally:
        smbclient.delete_session(parts[0])


def run_exports(run_id, anomaly_details=None, vm_list=None, ipam_list=None):
    """Point d'entrée principal — génère tous les exports après un run."""
    config = _get_export_config()
    if not config["enabled"]:
        logger.debug("Exports désactivés (EXPORT_ENABLED=false)")
        return

    try:
        _validate_run_id(run_id)
        run = db.session.get(Run, run_id)
        if run is None or run.status != "SUCCESS":
            return
        retention = _retention(config["retention_consolidated"], config["retention_raw"])
        if anomaly_details is None:
            anomaly_details = Counter(row.code for row in Anomaly.query.filter_by(run_id=run_id))
        smb = config["smb_path"]
        if smb.startswith(("\\\\", "//")):
            with tempfile.TemporaryDirectory() as temporary:
                export_dir = Path(temporary)
                _generate(run_id, anomaly_details, vm_list, ipam_list, export_dir, config)
                _publish_smb(export_dir, smb, retention, config)
        else:
            export_dir = _get_export_dir(config)
            _generate(run_id, anomaly_details, vm_list, ipam_list, export_dir, config)
            cleanup_old_exports(export_dir, retention["consolidated"], retention["raw"])
    except Exception as exc:
        db.session.rollback()
        logger.warning("Échec des exports du run #%s (%s) ; SUCCESS conservé", run_id, type(exc).__name__)


def _generate(run_id, anomaly_details, vm_list, ipam_list, export_dir, config):
    export_consolidated_jsonl(run_id, export_dir)
    export_report_md(run_id, anomaly_details, export_dir,
                     ipam_count=len(ipam_list) if ipam_list is not None else None)
    if config["export_raw"] and vm_list is not None and ipam_list is not None:
        export_raw_json(run_id, vm_list, ipam_list, export_dir)
