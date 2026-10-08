"""Lectures de base partagées entre les routes web et l'API REST.

Aligné sur reference/CloudInventory.v2/app/queries.py, adapté au schéma
docs/modele/schema.sql : l'inventaire d'un run se lit par
``consolidated_asset.run_id`` (FK NOT NULL vers run, RG35), exactement comme
la référence ; la colonne ``meta_zone`` du projet d'origine n'existe pas
dans ce schéma.
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
    "cpu": Asset.cpu_usage,
    "ram": Asset.ram_used,
    "match": ConsolidatedAsset.match_status,
    "role": ConsolidatedAsset.role,
}

# Caractères amorçant une formule dans un tableur (OWASP « CSV injection »).
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    """Cellule CSV neutralisée : « ' » devant toute valeur commençant comme une formule."""
    text = "" if value is None else str(value)
    if text[:1] in _FORMULA_START or text.lstrip()[:1] in ("=", "+", "-", "@"):
        return "'" + text
    return text


def _inventory_rows(run_id):
    """Jointure consolidé ↔ asset ↔ IPAM (outer join : IPAM facultatif) filtrée par run."""
    return (
        db.session.query(ConsolidatedAsset, Asset, IpamRecord)
        .join(Asset, ConsolidatedAsset.asset_id == Asset.id)
        .outerjoin(IpamRecord, ConsolidatedAsset.ipam_record_id == IpamRecord.id)
        .filter(ConsolidatedAsset.run_id == run_id)
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
    """Serialise un item d'inventaire en dict JSON-compatible.

    IP, DNS et statut proviennent exclusivement de l'instantané du run
    (`consolidated_asset`, RG « instantané ») : aucun repli sur `asset` ni
    sur l'IPAM, une valeur absente ou vide de l'instantané reste vide ; les
    métriques restent lues sur `asset` par la route.
    """
    return {
        "id": asset.id,
        "vm_id": asset.vm_id,
        "vm_name": asset.vm_name,
        "node": asset.node,
        "status": ca.vm_status or "",
        "type": asset.type,
        "tags": asset.tags or "",
        "ip": ca.ip_final or "",
        "dns": ca.dns_final or "",
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


def compare_runs(run1_id, run2_id):
    """Comparaison de deux runs : ajouts, suppressions, modifications.

    Lit exclusivement l'instantané ``consolidated_asset`` (ip_final, dns_final,
    match_status, vm_status), jamais la ligne ``asset``.

    Returns:
        dict avec clefs ``added``, ``removed``, ``changed`` conformes au contrat
        de l'API T010 §9 (voir ``api_run_compare``).
    """
    data1 = get_run_comparison_data(run1_id)
    data2 = get_run_comparison_data(run2_id)

    names1 = set(data1.keys())
    names2 = set(data2.keys())

    added = [
        {"vm_name": name, "status": data2[name][0].vm_status}
        for name in sorted(names2 - names1)
    ]
    removed = [
        {"vm_name": name, "status": data1[name][0].vm_status}
        for name in sorted(names1 - names2)
    ]

    changed = []
    for name in sorted(names1 & names2):
        ca1, _, _ = data1[name]
        ca2, _, _ = data2[name]
        diffs = []
        if ca1.ip_final != ca2.ip_final:
            diffs.append({"field": "IP", "before": ca1.ip_final, "after": ca2.ip_final})
        if ca1.dns_final != ca2.dns_final:
            diffs.append({"field": "DNS", "before": ca1.dns_final, "after": ca2.dns_final})
        if ca1.match_status != ca2.match_status:
            diffs.append(
                {"field": "Match", "before": ca1.match_status, "after": ca2.match_status}
            )
        if ca1.vm_status != ca2.vm_status:
            diffs.append({"field": "Status", "before": ca1.vm_status, "after": ca2.vm_status})
        if diffs:
            changed.append({"vm_name": name, "changes": diffs})

    return {
        "run1": run1_id,
        "run2": run2_id,
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def get_anomalies_list(anomaly_type="", run_id=None):
    """Liste des anomalies avec filtrage optionnel par type et run.

    Returns:
        list de tuples (anomaly, run) triées par detected_at décroissant.
    """
    query = db.session.query(Anomaly, Run).join(Anomaly.run).order_by(Anomaly.detected_at.desc())

    if anomaly_type:
        query = query.filter(Anomaly.code == anomaly_type)
    if run_id:
        query = query.filter(Anomaly.run_id == run_id)

    return query.all()


def export_inventory_csv(run_id):
    """Contenu CSV (séparateur `;`) de l'inventaire d'un run (web + API).

    IP, DNS et état proviennent exclusivement de l'instantané du run
    (ip_final, dns_final, vm_status), sans repli sur `asset` ni sur l'IPAM ;
    chaque cellule est neutralisée contre l'injection de formules à la
    source (OWASP), sans relecture par la route.
    """
    rows = _inventory_rows(run_id).all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Hostname", "Hote", "Etat", "Type", "IP", "DNS", "FQDN",
        "Role", "Tenant", "Site", "Match", "Source",
    ])
    for ca, asset, ipam in rows:
        writer.writerow([
            _csv_safe(cell)
            for cell in [
                asset.vm_name,
                asset.node,
                ca.vm_status or "",
                asset.type,
                ca.ip_final or "",
                ca.dns_final or "",
                asset.fqdn or "",
                ca.role or "",
                ipam.tenant if ipam else "",
                ipam.site if ipam else "",
                ca.match_status,
                asset.source or "",
            ]
        ])

    return output.getvalue()
