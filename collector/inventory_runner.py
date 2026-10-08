"""Orchestrateur du run d'inventaire — le pipeline en sept étapes du cahier § 8.2.

Le run reste dans l'état RUNNING pendant tout le pipeline (docs/modele/etats.md) :

1. initialisation : un ``Run`` RUNNING, validé seul (RG17) ;
2. collecte virtualisation et 3. collecte IPAM : sources interchangeables mock/réel
   (``collector.collect_vms`` / ``collector.collect_ipam``, selon USE_MOCK_VIRT / USE_MOCK_IPAM) ;
4. upsert des ``Asset`` par vm_id et des ``IpamRecord`` par ip + dns_name (RG18) ;
5. consolidation : matching 4 niveaux (``collector.matching``, RG01 → RG05), rôle
   (``collector.role``, RG15, RG16) et anomalies de correspondance (``collector.anomalies`` :
   NO_MATCH, HOSTNAME_MISMATCH, STATUS_MISMATCH — RG06 → RG11) ; une ligne consolidée par asset ;
6. anomalies IPAM : doublons DNS et IP (RG12, RG13), une anomalie par groupe ;
7. finalisation : SUCCESS, compteurs et end_date (RG19).

Une exception à n'importe quelle étape : rollback de la transaction, puis le même run passe FAIL
avec son message (RG20) ; rien d'autre n'est écrit.
"""
import json
import logging
from collections import Counter
from datetime import datetime, timezone

import collector
from app.extensions import db
from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run
from collector.anomalies import (
    detect_hostname_mismatch,
    detect_status_mismatch,
    find_duplicate_dns,
    find_duplicate_ip,
)
from collector.matching import build_dns_index, build_ip_index, resolve_match
from collector.role import deduce_role

logger = logging.getLogger("cloudinventory.runner")

# Champs de la VM recopiés sur l'asset à chaque run (RG18 ; colonnes § 8.3, fiche § 8.7, export RG24).
ASSET_FIELDS = (
    "vm_name", "fqdn", "ip_reported", "node", "type", "status", "tags", "os", "annotation",
    "cpu_count", "cpu_usage", "ram_max", "ram_used", "disk_max", "disk_used", "uptime",
)

# Longueur maximale du message d'échec conservé sur le run (RG20).
ERROR_MESSAGE_MAX = 1000


def _utcnow():
    """Horodatage UTC sans fuseau, comme les colonnes DateTime du schéma SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def run_inventory(collect_vms=None, collect_ipam=None, now=None):
    """Exécute un run d'inventaire complet (§ 8.2) et rend le ``Run``, SUCCESS ou FAIL.

    Args:
        collect_vms: source virtualisation, ``() -> list[dict]`` ; défaut ``collector.collect_vms``
            (mock ou Proxmox selon USE_MOCK_VIRT).
        collect_ipam: source IPAM, ``() -> list[dict]`` ; défaut ``collector.collect_ipam``
            (mock ou NetBox selon USE_MOCK_IPAM).
        now: horloge ``() -> datetime`` ; défaut l'heure UTC.

    Returns:
        le ``Run`` validé en base. Un échec ne lève pas : le run est FAIL et porte le message.
    """
    collect_vms = collect_vms or collector.collect_vms
    collect_ipam = collect_ipam or collector.collect_ipam
    now = now or _utcnow

    # 1. Initialisation (RG17) : validée seule, la trace du run survit au rollback d'un échec.
    run = Run(status="RUNNING", start_date=now())
    db.session.add(run)
    db.session.commit()
    run_id = run.id
    logger.info("Run #%d démarré", run_id)

    try:
        vms = collect_vms()     # 2. collecte virtualisation (RG31, RG32)
        ipam = collect_ipam()   # 3. collecte IPAM (RG33, RG34)
        # Un couple (ip, dns_name) répété par la source ne fait qu'un enregistrement (RG18) :
        # matching et doublons portent sur ce qui est enregistré.
        ipam = list({(rec["ip"], rec["dns_name"]): rec for rec in ipam}.values())

        assets = _upsert_assets(vms, run_id)          # 4. upsert (RG18)
        records = _upsert_ipam_records(ipam)
        detected_at = now()
        counts, rows = _consolidate(run_id, assets, ipam, records, detected_at)   # 5.
        _detect_ipam_duplicates(run_id, ipam, records, rows, detected_at)        # 6.
        for row, codes in rows.values():
            row.anomaly_codes = json.dumps(codes)

        # 7. Finalisation (RG19)
        run.matched_name_count = counts["MATCHED_NAME"]
        run.matched_fqdn_count = counts["MATCHED_FQDN"]
        run.matched_ip_count = counts["MATCHED_IP"]
        run.no_match_count = counts["NO_MATCH"]
        run.status = "SUCCESS"
        run.end_date = now()
        db.session.commit()
    except Exception as exc:  # RG20 : toute exception du pipeline fait échouer le run
        db.session.rollback()
        logger.exception("Run #%d en échec", run_id)
        run.status = "FAIL"
        run.end_date = now()
        run.error_message = _error_message(exc)
        db.session.commit()
        return run

    logger.info(
        "Run #%d terminé — %d par nom, %d par FQDN, %d par IP, %d sans correspondance",
        run_id, run.matched_name_count, run.matched_fqdn_count, run.matched_ip_count, run.no_match_count,
    )
    return run


def _upsert_assets(vms, run_id):
    """Étape 4 (RG18) : un asset par vm_id, créé ou mis à jour, rattaché au run courant.

    Returns:
        les assets du run, un par vm_id, dans l'ordre de la collecte.
    """
    known = {asset.vm_id: asset for asset in db.session.scalars(db.select(Asset))}
    assets = {}
    for vm in vms:
        vm_id = str(vm["vm_id"])
        asset = known.get(vm_id)
        if asset is None:
            asset = known[vm_id] = Asset(vm_id=vm_id)
            db.session.add(asset)
        for field in ASSET_FIELDS:
            setattr(asset, field, vm.get(field))
        asset.consolidated_run_id = run_id
        assets[vm_id] = asset
    db.session.flush()
    return list(assets.values())


def _upsert_ipam_records(ipam):
    """Étape 4 (RG18) : un enregistrement par couple (ip, dns_name), créé ou mis à jour.

    Returns:
        dict ``{(ip, dns_name) → IpamRecord}`` des enregistrements de cette collecte.
    """
    known = {(rec.ip, rec.dns_name): rec for rec in db.session.scalars(db.select(IpamRecord))}
    records = {}
    for raw in ipam:
        key = (raw["ip"], raw["dns_name"])
        record = known.get(key)
        if record is None:
            record = known[key] = IpamRecord(ip=raw["ip"], dns_name=raw["dns_name"])
            db.session.add(record)
        record.tenant = raw.get("tenant")
        record.site = raw.get("site")
        records[key] = record
    db.session.flush()
    return records


def _consolidate(run_id, assets, ipam, records, detected_at):
    """Étape 5 : matching 4 niveaux, rôle et anomalies de correspondance ; une ligne consolidée par asset.

    Le statut IPAM (STATUS_MISMATCH) se lit dans la collecte : ``ipam_record`` ne le conserve pas.

    Returns:
        (compteurs par statut de correspondance, ``{asset.id → (ConsolidatedAsset, codes)}``).
    """
    dns_index = build_dns_index(ipam)
    ip_index = build_ip_index(ipam)
    counts = Counter()
    rows = {}
    for asset in assets:
        # RG01 → RG05 : nom, FQDN, IP, puis NO_MATCH
        status, raw = resolve_match(asset.vm_name, asset.fqdn, asset.ip_reported, dns_index, ip_index)
        record = records[(raw["ip"], raw["dns_name"])] if raw else None
        role = deduce_role(asset.vm_name, asset.tags)  # RG15, RG16
        counts[status] += 1

        found = []
        if status == "NO_MATCH":  # RG05, RG08 : le dernier niveau du matching est l'anomalie
            found.append(("NO_MATCH", f"VM '{asset.vm_name}' sans correspondance IPAM (ni hostname, ni FQDN, ni IP)"))
        elif status == "MATCHED_IP" and detect_hostname_mismatch(asset.vm_name, asset.ip_reported, raw):
            # RG06, RG09, RG10 : MATCHED_IP est le statut de correspondance, HOSTNAME_MISMATCH l'anomalie
            found.append((
                "HOSTNAME_MISMATCH",
                f"VM '{asset.vm_name}' rapprochée par IP ({asset.ip_reported}) "
                f"mais hostname différent du DNS NetBox '{raw['dns_name']}'",
            ))
        if raw and detect_status_mismatch(asset.status, raw.get("status")):  # RG07, RG11
            found.append(("STATUS_MISMATCH", f"VM '{asset.vm_name}' arrêtée mais IP {raw['ip']} active dans NetBox"))
        for code, description in found:
            db.session.add(Anomaly(
                run_id=run_id, asset_id=asset.id, ipam_record_id=record.id if record else None,
                code=code, description=description, detected_at=detected_at,
            ))

        # L'asset garde le dernier résultat ; la ligne consolidée, celui de ce run (§ 8.5, 8.6)
        asset.match_status = status
        asset.role = role
        asset.source = "IPAM" if record else "VIRT"
        row = ConsolidatedAsset(
            run_id=run_id,
            asset_id=asset.id,
            ipam_record_id=record.id if record else None,
            match_status=status,
            role=role,
            ip_final=record.ip if record else asset.ip_reported,
            dns_final=record.dns_name if record else asset.vm_name,
            vm_status=asset.status,
            consolidated_at=detected_at,
        )
        db.session.add(row)
        rows[asset.id] = (row, [code for code, _ in found])
    return counts, rows


def _detect_ipam_duplicates(run_id, ipam, records, rows, detected_at):
    """Étape 6 (RG12, RG13) : une anomalie par groupe de doublons ; indicateurs posés sur les enregistrements.

    L'anomalie va au premier asset consolidé sur un enregistrement du groupe, sinon à l'enregistrement seul
    (``anomaly.asset_id`` est facultatif).
    """
    asset_of = {}  # IpamRecord.id → premier asset consolidé dessus
    for asset_id, (row, _codes) in rows.items():
        if row.ipam_record_id is not None:
            asset_of.setdefault(row.ipam_record_id, asset_id)

    groups = [
        ("DUPLICATE_DNS", "is_duplicate_dns", group,
         f"DNS '{name}' présent {len(group)} fois dans NetBox (IP : {', '.join(rec['ip'] for rec in group)})")
        for name, group in find_duplicate_dns(ipam).items()
    ] + [
        ("DUPLICATE_IP", "is_duplicate_ip", group,
         f"IP {ip} présente {len(group)} fois dans NetBox (DNS : {', '.join(rec['dns_name'] for rec in group)})")
        for ip, group in find_duplicate_ip(ipam).items()
    ]

    for record in records.values():
        record.is_duplicate_dns = False
        record.is_duplicate_ip = False
    for code, flag, group, description in groups:
        members = [records[(rec["ip"], rec["dns_name"])] for rec in group]
        for record in members:
            setattr(record, flag, True)
        anchor = next((record for record in members if record.id in asset_of), members[0])
        asset_id = asset_of.get(anchor.id)
        db.session.add(Anomaly(
            run_id=run_id, asset_id=asset_id, ipam_record_id=anchor.id,
            code=code, description=description, detected_at=detected_at,
        ))
        if asset_id is not None:
            rows[asset_id][1].append(code)


def _error_message(exc):
    """Message d'échec conservé sur le run (RG20) : type et texte de l'exception, tronqués."""
    text = str(exc)
    message = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
    return message[:ERROR_MESSAGE_MAX]
