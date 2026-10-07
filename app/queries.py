"""Lectures de base partagées entre les routes web et l'API REST.

Aligné sur reference/CloudInventory.v2/app/queries.py, adapté au schéma
docs/modele/schema.sql : ``consolidated_asset`` n'a pas de ``run_id``, le run se
retrouve via ``asset.consolidated_run_id`` ; les colonnes de métriques et
``meta_zone`` du projet d'origine n'existent pas dans ce schéma.
Les colonnes de tri sont lues via une liste blanche (jamais concaténées).
"""
import csv
import io

from app.extensions import db
from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run

# Liste blanche des colonnes de tri (paramètre non SQL-safe sinon).
_SORT_COLUMNS = {
    "vm_name": Asset.vm_name,
    "vm_id": Asset.vm_id,
    "status": Asset.status,
    "node": Asset.node,
    "type": Asset.type,
    "ip": Asset.ip_reported,
    "fqdn": Asset.fqdn,
    "match": ConsolidatedAsset.match_status,
    "role": ConsolidatedAsset.role,
}


def _inventory_rows(run_id):
    """Jointure consolidé ↔ asset ↔ IPAM (outer join : IPAM facultatif) filtrée par run."""
    return (
        db.session.query(ConsolidatedAsset, Asset, IpamRecord)
        .join(Asset, ConsolidatedAsset.asset_id == Asset.id)
        .outerjoin(IpamRecord, ConsolidatedAsset.ipam_record_id == IpamRecord.id)
        .filter(Asset.consolidated_run_id == run_id)
    )


def build_inventory_query(run_id, q="", status="", node="", vm_type="",
                          match="", tag="", role="", sort="vm_name", order="asc"):
    """Construit la requête inventaire d'un run avec filtres et tri (liste blanche)."""
    query = _inventory_rows(run_id)

    if q:
        pattern = f"%{q}%"
        query = query.filter(
            db.or_(
                Asset.vm_name.ilike(pattern),
                Asset.ip_reported.ilike(pattern),
                IpamRecord.dns_name.ilike(pattern),
                IpamRecord.ip.ilike(pattern),
            )
        )
    if status:
        query = query.filter(Asset.status == status)
    if node:
        query = query.filter(Asset.node == node)
    if vm_type:
        query = query.filter(Asset.type == vm_type)
    if match:
        query = query.filter(ConsolidatedAsset.match_status == match)
    if tag:
        query = query.filter(Asset.tags.ilike(f"%{tag}%"))
    if role:
        query = query.filter(ConsolidatedAsset.role == role)

    sort_col = _SORT_COLUMNS.get(sort, Asset.vm_name)
    query = query.order_by(sort_col.desc() if order == "desc" else sort_col.asc())

    return query


def serialize_inventory_item(ca, asset, ipam):
    """Serialise un item d'inventaire en dict JSON-compatible."""
    return {
        "id": asset.id,
        "vm_id": asset.vm_id,
        "vm_name": asset.vm_name,
        "node": asset.node,
        "status": asset.status,
        "type": asset.type,
        "tags": asset.tags or "",
        "ip": asset.ip_reported or "",
        "dns": ipam.dns_name if ipam else "",
        "fqdn": asset.fqdn or "",
        "match_status": ca.match_status,
        "role": ca.role or "Indéterminé",
        "anomaly_codes": ca.anomaly_codes or "",
        "source": asset.source,
        "tenant": ipam.tenant if ipam else None,
        "site": ipam.site if ipam else None,
    }


def get_stats_data():
    """Statistiques du dashboard (partagé web + API) : dernier run, anomalies, évolution."""
    last_run = (
        db.session.query(Run).order_by(Run.id.desc()).limit(1).first()
    )
    if not last_run:
        return {"has_data": False}

    match_data = {
        "matched_name": last_run.matched_name_count or 0,
        "matched_fqdn": last_run.matched_fqdn_count or 0,
        "matched_ip": last_run.matched_ip_count or 0,
        "no_match": last_run.no_match_count or 0,
    }

    anomaly_stats = (
        db.session.query(Anomaly.code, db.func.count(Anomaly.id))
        .filter(Anomaly.run_id == last_run.id)
        .group_by(Anomaly.code)
        .all()
    )
    anomaly_data = {code: count for code, count in anomaly_stats}

    recent_runs = (
        db.session.query(Run)
        .filter(Run.status == "SUCCESS")
        .order_by(Run.id.desc())
        .limit(10)
        .all()
    )
    recent_runs.reverse()
    evolution = {
        "labels": [f"#{r.id}" for r in recent_runs],
        "matched_name": [r.matched_name_count or 0 for r in recent_runs],
        "matched_fqdn": [r.matched_fqdn_count or 0 for r in recent_runs],
        "matched_ip": [r.matched_ip_count or 0 for r in recent_runs],
        "no_match": [r.no_match_count or 0 for r in recent_runs],
    }

    return {
        "has_data": True,
        "match": match_data,
        "anomalies": anomaly_data,
        "evolution": evolution,
    }


def get_run_comparison_data(run_id):
    """Lignes d'un run pour la comparaison, indexées par nom de VM (web + API)."""
    rows = _inventory_rows(run_id).all()
    return {asset.vm_name: (ca, asset, ipam) for ca, asset, ipam in rows}


def export_inventory_csv(run_id):
    """Contenu CSV (séparateur `;`) de l'inventaire d'un run (web + API)."""
    rows = _inventory_rows(run_id).all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Hostname", "Hote", "Etat", "Type", "IP", "DNS", "FQDN",
        "Role", "Tenant", "Site", "Match", "Source",
    ])
    for ca, asset, ipam in rows:
        writer.writerow([
            asset.vm_name, asset.node, asset.status, asset.type,
            asset.ip_reported or "", ipam.dns_name if ipam else "",
            asset.fqdn or "", ca.role or "",
            ipam.tenant if ipam else "", ipam.site if ipam else "",
            ca.match_status, asset.source or "",
        ])

    return output.getvalue()
