"""API REST (cahier §9) — endpoints JSON protégés par JWT, documentés sur /apidocs.

Réutilisation intégrale des lectures de ``app/queries`` (inventaire, stats,
comparaison, export CSV), du sérialiseur de run et des pourcentages de
``app/routes`` et du pipeline ``collector.inventory_runner.run_inventory`` :
aucune logique n'est réécrite ici. La comparaison de runs lit l'instantané
``consolidated_asset`` (ip_final, dns_final, match_status, vm_status), jamais
la ligne ``asset`` ; le mot de passe admin n'est lu que sous forme de hash.
"""
import hmac
import logging

from flask import Blueprint, Response, current_app, jsonify, request
from flask_jwt_extended import create_access_token, jwt_required
from werkzeug.security import check_password_hash

from app.extensions import db
from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run
from app.queries import (
    _inventory_rows,
    build_inventory_query,
    export_inventory_csv,
    get_run_comparison_data,
    get_stats_data,
    serialize_inventory_item,
)
from app.routes import _percent, _run_payload

logger = logging.getLogger("cloudinventory.api")

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _anomaly_item(anomaly, asset=None, run=None):
    """Anomalie sérialisée : clés de la référence (type, details, created_at),
    lues sur les colonnes du schéma (code, description, detected_at)."""
    item = {
        "id": anomaly.id,
        "type": anomaly.code,
        "details": anomaly.description,
        "created_at": (
            anomaly.detected_at.isoformat() if anomaly.detected_at else None
        ),
        "asset": {"id": asset.id, "vm_name": asset.vm_name} if asset else None,
    }
    if run is not None:
        item["run_id"] = run.id
        item["run_date"] = run.start_date.isoformat() if run.start_date else None
    return item


# ──────────────────────────────────────────────
# AUTH — obtenir un token JWT (seule route sans @jwt_required, cahier §10.2)
# ──────────────────────────────────────────────
@api_bp.route("/login", methods=["POST"])
def api_login():
    """Obtenir un token JWT
    ---
    tags:
      - Authentification
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [username, password]
          properties:
            username:
              type: string
              example: admin
            password:
              type: string
              example: admin
    responses:
      200:
        description: Token JWT
        schema:
          type: object
          properties:
            access_token:
              type: string
      400:
        description: Body JSON requis
      401:
        description: Identifiants incorrects
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Body JSON requis"}), 400

    username = data.get("username", "")
    password = data.get("password", "")
    if not isinstance(username, str):
        username = ""
    if not isinstance(password, str):
        password = ""

    admin_username = current_app.config.get("ADMIN_USERNAME", "admin")
    password_hash = current_app.config["ADMIN_PASSWORD_HASH"]
    # Les deux vérifications sont toujours exécutées : coût constant.
    username_ok = hmac.compare_digest(
        username.encode("utf-8"), admin_username.encode("utf-8")
    )
    password_ok = check_password_hash(password_hash, password)

    if not (username_ok and password_ok):
        return jsonify({"error": "Identifiants incorrects"}), 401

    token = create_access_token(identity=admin_username)
    logger.info("Connexion API réussie pour '%s'", admin_username)
    return jsonify({"access_token": token}), 200


# ──────────────────────────────────────────────
# STATS — tableau de bord
# ──────────────────────────────────────────────
@api_bp.route("/stats", methods=["GET"])
@jwt_required()
def api_stats():
    """Statistiques du dashboard
    ---
    tags:
      - Dashboard
    security:
      - Bearer: []
    responses:
      200:
        description: Statistiques globales (match, anomalies, evolution)
    """
    return jsonify(get_stats_data())


# ──────────────────────────────────────────────
# RUNS — lister / créer / détail / comparer / purger
# ──────────────────────────────────────────────
@api_bp.route("/runs", methods=["GET"])
@jwt_required()
def api_runs_list():
    """Liste paginee des runs
    ---
    tags:
      - Runs
    security:
      - Bearer: []
    parameters:
      - name: page
        in: query
        type: integer
        default: 1
      - name: per_page
        in: query
        type: integer
        default: 25
    responses:
      200:
        description: Liste paginee des runs
    """
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    pagination = (
        db.session.query(Run)
        .order_by(Run.id.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return jsonify({
        "runs": [_run_payload(run) for run in pagination.items],
        "page": pagination.page,
        "pages": pagination.pages,
        "total": pagination.total,
    })


@api_bp.route("/runs", methods=["POST"])
@jwt_required()
def api_trigger_run():
    """Lancer un nouvel inventaire
    ---
    tags:
      - Runs
    security:
      - Bearer: []
    responses:
      201:
        description: Run cree et execute
    """
    from collector.inventory_runner import run_inventory

    return jsonify(_run_payload(run_inventory())), 201


@api_bp.route("/runs/<int:run_id>", methods=["GET"])
@jwt_required()
def api_run_detail(run_id):
    """Detail d'un run avec inventaire et anomalies
    ---
    tags:
      - Runs
    security:
      - Bearer: []
    parameters:
      - name: run_id
        in: path
        type: integer
        required: true
    responses:
      200:
        description: Detail du run
      404:
        description: Run introuvable
    """
    run = db.get_or_404(Run, run_id)

    inventory = [
        serialize_inventory_item(ca, asset, ipam)
        for ca, asset, ipam in _inventory_rows(run_id).all()
    ]

    anomaly_rows = (
        db.session.query(Anomaly, Asset)
        .outerjoin(Anomaly.asset)
        .filter(Anomaly.run_id == run_id)
        .all()
    )

    result = _run_payload(run)
    result["inventory"] = inventory
    result["anomalies"] = [_anomaly_item(anomaly, asset) for anomaly, asset in anomaly_rows]
    return jsonify(result)


@api_bp.route("/runs/compare", methods=["GET"])
@jwt_required()
def api_run_compare():
    """Comparer deux runs
    ---
    tags:
      - Runs
    security:
      - Bearer: []
    parameters:
      - name: run1
        in: query
        type: integer
        required: true
      - name: run2
        in: query
        type: integer
        required: true
    responses:
      200:
        description: Differences entre les deux runs (added, removed, changed)
      400:
        description: Parametres run1 et run2 requis
      404:
        description: Run introuvable
    """
    run1_id = request.args.get("run1", type=int)
    run2_id = request.args.get("run2", type=int)

    if not run1_id or not run2_id:
        return jsonify({"error": "Les paramètres run1 et run2 sont requis"}), 400

    db.get_or_404(Run, run1_id)
    db.get_or_404(Run, run2_id)

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

    return jsonify({
        "run1": run1_id,
        "run2": run2_id,
        "added": added,
        "removed": removed,
        "changed": changed,
    })


@api_bp.route("/runs/purge", methods=["POST"])
@jwt_required()
def api_purge_runs():
    """Purger les anciens runs (garder les N derniers)
    ---
    tags:
      - Runs
    security:
      - Bearer: []
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            keep:
              type: integer
              default: 30
              description: Nombre de runs a conserver
    responses:
      200:
        description: Nombre de runs supprimes
      400:
        description: Body JSON objet requis et parametre keep invalide
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Body JSON requis"}), 400

    keep = data.get("keep", 30)
    # bool est un sous-ensemble de int en Python : {"keep": true} doit être
    # rejeté, sinon il vaudrait keep=1 et supprimerait tous les autres runs.
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        return jsonify({"error": "Le paramètre 'keep' doit être un entier >= 1"}), 400

    all_runs = db.session.query(Run).order_by(Run.id.desc()).all()
    to_delete = all_runs[keep:]
    if not to_delete:
        return jsonify({"deleted": 0, "kept": len(all_runs)})

    run_ids = [run.id for run in to_delete]
    newest_kept_run_id = all_runs[0].id

    # PRAGMA foreign_keys=ON : asset.consolidated_run_id est RESTRICT, les
    # assets sont conservés (comme la référence) et rattachés au run conservé
    # le plus récent avant la suppression des runs.
    reattached = db.session.query(Asset).filter(
        Asset.consolidated_run_id.in_(run_ids)
    ).update({Asset.consolidated_run_id: newest_kept_run_id},
             synchronize_session=False)
    deleted_anomalies = db.session.query(Anomaly).filter(
        Anomaly.run_id.in_(run_ids)
    ).delete(synchronize_session=False)
    deleted_consolidated = db.session.query(ConsolidatedAsset).filter(
        ConsolidatedAsset.run_id.in_(run_ids)
    ).delete(synchronize_session=False)
    deleted = db.session.query(Run).filter(Run.id.in_(run_ids)).delete(
        synchronize_session=False
    )
    db.session.commit()

    logger.info(
        "Purge API : %d runs supprimés (anomalies %d, consolidés %d, assets "
        "réattachés %d, conservé les %d derniers)",
        deleted,
        deleted_anomalies,
        deleted_consolidated,
        reattached,
        keep,
    )
    return jsonify({"deleted": deleted, "kept": keep})


# ──────────────────────────────────────────────
# INVENTORY — liste avec filtres et export
# ──────────────────────────────────────────────
@api_bp.route("/inventory", methods=["GET"])
@jwt_required()
def api_inventory():
    """Inventaire pagine avec filtres et tri
    ---
    tags:
      - Inventaire
    security:
      - Bearer: []
    parameters:
      - name: q
        in: query
        type: string
        description: Recherche libre (VM, IP, DNS)
      - name: status
        in: query
        type: string
        enum: [running, stopped]
      - name: node
        in: query
        type: string
      - name: type
        in: query
        type: string
        enum: [qemu, lxc]
      - name: match
        in: query
        type: string
        enum: [MATCHED_NAME, MATCHED_FQDN, MATCHED_IP, NO_MATCH]
      - name: tag
        in: query
        type: string
      - name: role
        in: query
        type: string
        description: Filtrer par role fonctionnel
      - name: sort
        in: query
        type: string
        default: vm_name
        enum: [vm_name, status, ip, cpu, ram, match]
      - name: order
        in: query
        type: string
        default: asc
        enum: [asc, desc]
      - name: page
        in: query
        type: integer
        default: 1
      - name: per_page
        in: query
        type: integer
        default: 25
    responses:
      200:
        description: Liste paginee de l'inventaire
    """
    last_run = db.session.query(Run).order_by(Run.id.desc()).first()
    if not last_run:
        return jsonify({"items": [], "total": 0, "page": 1, "pages": 0})

    sort = request.args.get("sort", "vm_name")
    query = build_inventory_query(
        last_run.id,
        q=request.args.get("q", "").strip(),
        status=request.args.get("status", ""),
        node=request.args.get("node", ""),
        vm_type=request.args.get("type", ""),
        match=request.args.get("match", ""),
        tag=request.args.get("tag", ""),
        role=request.args.get("role", ""),
        sort=sort,
        order=request.args.get("order", "asc"),
    )
    pagination = query.paginate(
        page=request.args.get("page", 1, type=int),
        per_page=request.args.get("per_page", 25, type=int),
        error_out=False,
    )

    items = [
        serialize_inventory_item(ca, asset, ipam)
        for ca, asset, ipam in pagination.items
    ]

    return jsonify({
        "items": items,
        "page": pagination.page,
        "pages": pagination.pages,
        "total": pagination.total,
        "run_id": last_run.id,
    })


@api_bp.route("/inventory/export", methods=["GET"])
@jwt_required()
def api_inventory_export():
    """Export CSV de l'inventaire
    ---
    tags:
      - Inventaire
    security:
      - Bearer: []
    produces:
      - text/csv
    responses:
      200:
        description: Fichier CSV de l'inventaire
      404:
        description: Aucun run disponible
    """
    last_run = db.session.query(Run).order_by(Run.id.desc()).first()
    if not last_run:
        return jsonify({"error": "Aucun run disponible"}), 404

    return Response(
        export_inventory_csv(last_run.id),
        mimetype="text/csv",
        headers={
            "Content-Disposition": (
                f"attachment; filename=inventaire_run{last_run.id}.csv"
            )
        },
    )


# ──────────────────────────────────────────────
# ASSETS — détail d'une VM
# ──────────────────────────────────────────────
@api_bp.route("/assets/<int:asset_id>", methods=["GET"])
@jwt_required()
def api_asset_detail(asset_id):
    """Detail d'un asset avec historique et anomalies
    ---
    tags:
      - Assets
    security:
      - Bearer: []
    parameters:
      - name: asset_id
        in: path
        type: integer
        required: true
    responses:
      200:
        description: Detail de l'asset (metriques, historique, anomalies)
      404:
        description: Asset introuvable
    """
    asset = db.get_or_404(Asset, asset_id)

    history_rows = (
        db.session.query(ConsolidatedAsset, Run, IpamRecord)
        .join(Run, ConsolidatedAsset.run_id == Run.id)
        .outerjoin(IpamRecord, ConsolidatedAsset.ipam_record_id == IpamRecord.id)
        .filter(ConsolidatedAsset.asset_id == asset_id)
        .order_by(Run.id.desc())
        .limit(30)
        .all()
    )

    history = [
        {
            "run_id": run.id,
            "run_date": run.start_date.isoformat() if run.start_date else None,
            "ip": ca.ip_final,
            "dns": ca.dns_final,
            "match_status": ca.match_status,
            "source": asset.source,
            "tenant": ipam.tenant if ipam else None,
            "site": ipam.site if ipam else None,
        }
        for ca, run, ipam in history_rows
    ]

    anomaly_rows = (
        db.session.query(Anomaly, Run)
        .outerjoin(Anomaly.run)
        .filter(Anomaly.asset_id == asset_id)
        .order_by(Anomaly.id.desc())
        .all()
    )

    return jsonify({
        "id": asset.id,
        "vm_id": asset.vm_id,
        "vm_name": asset.vm_name,
        "type": asset.type,
        "node": asset.node,
        "status": asset.status,
        "tags": asset.tags,
        "ip_reported": asset.ip_reported,
        "fqdn": asset.fqdn,
        "annotation": asset.annotation,
        "cpu_count": asset.cpu_count,
        "cpu_usage": asset.cpu_usage,
        "ram_max": asset.ram_max,
        "ram_used": asset.ram_used,
        "ram_pct": _percent(asset.ram_used, asset.ram_max),
        "disk_max": asset.disk_max,
        "disk_used": asset.disk_used,
        "disk_pct": _percent(asset.disk_used, asset.disk_max),
        "uptime": asset.uptime,
        "history": history,
        "anomalies": [
            _anomaly_item(anomaly, asset, run) for anomaly, run in anomaly_rows
        ],
    })


# ──────────────────────────────────────────────
# ANOMALIES — liste avec filtres
# ──────────────────────────────────────────────
@api_bp.route("/anomalies", methods=["GET"])
@jwt_required()
def api_anomalies():
    """Liste paginee des anomalies
    ---
    tags:
      - Anomalies
    security:
      - Bearer: []
    parameters:
      - name: type
        in: query
        type: string
        description: Filtrer par type d'anomalie
      - name: run
        in: query
        type: integer
        description: Filtrer par ID de run
      - name: page
        in: query
        type: integer
        default: 1
      - name: per_page
        in: query
        type: integer
        default: 25
    responses:
      200:
        description: Liste paginee des anomalies
    """
    query = (
        db.session.query(Anomaly, Asset, Run)
        .outerjoin(Anomaly.asset)
        .outerjoin(Anomaly.run)
        .order_by(Anomaly.id.desc())
    )

    anomaly_type = request.args.get("type", "")
    run_id = request.args.get("run", type=int)

    if anomaly_type:
        query = query.filter(Anomaly.code == anomaly_type)
    if run_id:
        query = query.filter(Anomaly.run_id == run_id)

    pagination = query.paginate(
        page=request.args.get("page", 1, type=int),
        per_page=request.args.get("per_page", 25, type=int),
        error_out=False,
    )

    items = [
        _anomaly_item(anomaly, asset, run)
        for anomaly, asset, run in pagination.items
    ]

    return jsonify({
        "items": items,
        "page": pagination.page,
        "pages": pagination.pages,
        "total": pagination.total,
    })
