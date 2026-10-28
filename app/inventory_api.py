import csv
import io
import json
from datetime import UTC, datetime

from flask import Response, jsonify, request
from sqlalchemy import or_

from app.anomaly_exceptions import (
    anomaly_coverage,
    create_exception,
    delete_exception,
    list_exceptions,
)
from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run

from . import db

FIELDS = (
    "vm_id", "vm_name", "type", "node", "status", "tags", "ip_reported", "fqdn",
    "os", "cpu", "ram", "disk", "uptime", "ip_final", "dns_final", "status_final",
    "match_status", "role", "ipam_ip", "ipam_dns_name", "ipam_status", "ipam_tenant",
    "ipam_site", "ipam_tags", "ipam_meta_zone",
)
SORT_COLUMNS = {
    "vm_id": Asset.vm_id, "vm_name": Asset.vm_name, "type": Asset.type,
    "node": Asset.node, "status": ConsolidatedAsset.status_final,
    "ip_final": ConsolidatedAsset.ip_final, "dns_final": ConsolidatedAsset.dns_final,
    "match_status": ConsolidatedAsset.match_status, "role": ConsolidatedAsset.role,
    "cpu": Asset.cpu, "ram": Asset.ram, "disk": Asset.disk, "uptime": Asset.uptime,
}
MATCH_CODES = {"MATCHED_NAME", "MATCHED_FQDN", "MATCHED_IP", "NO_MATCH"}
ANOMALY_CODES = {
    "NO_MATCH", "MATCHED_IP", "HOSTNAME_MISMATCH", "STATUS_MISMATCH",
    "DUPLICATE_DNS", "DUPLICATE_IP", "RENAMED",
}


def _integer(value):
    if value is None or not value.isascii() or not value.isdecimal() or len(value) > 10:
        return None
    number = int(value)
    return number if 0 < number <= 2147483647 else None


def _pattern(value):
    return "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _row(asset, consolidated, ipam):
    return {field: (getattr(ipam, field[5:]) if ipam else None)
            if field.startswith("ipam_") else
            getattr(consolidated, field) if field in
            ("ip_final", "dns_final", "status_final", "match_status", "role") else
            getattr(asset, field) for field in FIELDS}


def _inventory_query(run_id, args):
    if (any(len(args.getlist(key)) != 1 for key in args) or
            any(key not in ("q", "status", "node", "type", "match", "tag", "meta_zone",
                            "sort", "order", "page", "per_page") for key in args)):
        return None
    sort = args.get("sort", "vm_id")
    order = args.get("order", "asc")
    if sort not in SORT_COLUMNS or order not in ("asc", "desc"):
        return None
    for key in ("q", "status", "node", "type", "match", "tag", "meta_zone"):
        value = args.get(key, "")
        if len(value) > 255 or "\x00" in value:
            return None
    if args.get("type") and args["type"] not in ("qemu", "lxc"):
        return None
    if args.get("match") and args["match"] not in MATCH_CODES:
        return None
    query = (db.session.query(Asset, ConsolidatedAsset, IpamRecord)
             .join(ConsolidatedAsset, (ConsolidatedAsset.asset_id == Asset.id) &
                   (ConsolidatedAsset.run_id == Asset.run_id))
             .outerjoin(IpamRecord, (IpamRecord.id == ConsolidatedAsset.ipam_record_id) &
                        (IpamRecord.run_id == Asset.run_id))
             .filter(Asset.run_id == run_id))
    if args.get("q"):
        pattern = _pattern(args["q"])
        query = query.filter(or_(*(column.ilike(pattern, escape="\\") for column in (
            Asset.vm_id, Asset.vm_name, Asset.fqdn, Asset.ip_reported,
            ConsolidatedAsset.ip_final, ConsolidatedAsset.dns_final))))
    for key, column in (("status", ConsolidatedAsset.status_final), ("node", Asset.node),
                        ("type", Asset.type), ("match", ConsolidatedAsset.match_status)):
        if args.get(key):
            query = query.filter(column == args[key])
    if args.get("tag"):
        pattern = _pattern(args["tag"])
        query = query.filter(Asset.tags.ilike(pattern, escape="\\"))
    if args.get("meta_zone"):
        query = query.filter(IpamRecord.meta_zone == args["meta_zone"])
    column = SORT_COLUMNS[sort]
    return query.order_by(column.desc() if order == "desc" else column.asc(), Asset.id.asc())


def _csv_safe(value):
    if value is None:
        return ""
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def configure_inventory_api(app):
    def inventory():
        """List the latest run's consolidated inventory.
        ---
        tags: [Inventory]
        security: [{Bearer: []}]
        parameters:
          - {in: query, name: q, type: string, description: Case-insensitive text search}
          - {in: query, name: status, type: string}
          - {in: query, name: node, type: string}
          - {in: query, name: type, type: string, enum: [qemu, lxc]}
          - {in: query, name: match, type: string, enum: [MATCHED_NAME, MATCHED_FQDN, MATCHED_IP, NO_MATCH]}
          - {in: query, name: tag, type: string}
          - {in: query, name: meta_zone, type: string, description: Exact IPAM zone}
          - {in: query, name: sort, type: string, description: One of vm_id vm_name type node status ip_final dns_final match_status role cpu ram disk uptime}
          - {in: query, name: order, type: string, enum: [asc, desc], default: asc}
          - {in: query, name: page, type: integer, minimum: 1, default: 1}
          - {in: query, name: per_page, type: integer, minimum: 1, maximum: 100, default: 20}
        responses:
          200:
            description: Latest run ID (null when empty) and paginated joined asset and IPAM fields
            schema:
              type: object
              properties:
                run_id: {type: integer, description: Latest run ID or null}
                items: {type: array, items: {type: object, description: FIELDS asset consolidated and ipam_* fields}}
                page: {type: integer}
                per_page: {type: integer}
                total: {type: integer}
          400: {description: Invalid filter sort or pagination}
          401: {description: Missing or invalid Bearer token}
        """
        page = _integer(request.args.get("page", "1"))
        per_page = _integer(request.args.get("per_page", "20"))
        run = Run.query.order_by(Run.id.desc()).first()
        query = _inventory_query(run.id if run else -1, request.args)
        if query is None or page is None or per_page is None or per_page > 100:
            return jsonify(error="Invalid inventory query"), 400
        result = query.paginate(page=page, per_page=per_page, error_out=False)
        return jsonify(run_id=run.id if run else None,
                       items=[_row(*row) for row in result.items], page=page,
                       per_page=per_page, total=result.total)

    def export():
        """Download the complete filtered inventory as UTF-8 CSV.
        ---
        tags: [Inventory]
        security: [{Bearer: []}]
        produces: [text/csv]
        parameters:
          - {in: query, name: q, type: string}
          - {in: query, name: status, type: string}
          - {in: query, name: node, type: string}
          - {in: query, name: type, type: string, enum: [qemu, lxc]}
          - {in: query, name: match, type: string, enum: [MATCHED_NAME, MATCHED_FQDN, MATCHED_IP, NO_MATCH]}
          - {in: query, name: tag, type: string}
          - {in: query, name: meta_zone, type: string, description: Exact IPAM zone}
          - {in: query, name: sort, type: string, description: Same whitelist as inventory}
          - {in: query, name: order, type: string, enum: [asc, desc]}
        responses:
          200: {description: CSV header FIELDS and all matching rows without pagination, type: file}
          400: {description: Invalid filter or sort}
          401: {description: Missing or invalid Bearer token}
        """
        if "page" in request.args or "per_page" in request.args:
            return jsonify(error="Invalid inventory query"), 400
        run = Run.query.order_by(Run.id.desc()).first()
        query = _inventory_query(run.id if run else -1, request.args)
        if query is None:
            return jsonify(error="Invalid inventory query"), 400
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(FIELDS)
        for row in query.yield_per(500):
            data = _row(*row)
            writer.writerow([_csv_safe(data[field]) for field in FIELDS])
        return Response(output.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="inventory.csv"'})

    def asset_detail(id):
        """Get an asset and its history by observed asset ID.
        ---
        tags: [Inventory]
        security: [{Bearer: []}]
        parameters:
          - {in: path, name: id, type: integer, required: true, minimum: 1}
        responses:
          200:
            description: Asset fields, history across the latest 30 runs by vm_id and linked anomalies
            schema:
              type: object
              properties:
                asset: {type: object, description: Asset and consolidated and IPAM fields}
                history: {type: array, items: {type: object, description: run_id asset_id and inventory fields}}
                anomalies: {type: array, items: {type: object, description: id run_id asset_id ipam_record_id code description created_at}}
          401: {description: Missing or invalid Bearer token}
          404: {description: Asset not found}
        """
        asset = db.session.get(Asset, id)
        if asset is None:
            return jsonify(error="Asset not found"), 404
        run_ids = db.session.query(Run.id).order_by(Run.id.desc()).limit(30).subquery()
        rows = (db.session.query(Asset, ConsolidatedAsset, IpamRecord)
                .outerjoin(ConsolidatedAsset, (ConsolidatedAsset.asset_id == Asset.id) &
                           (ConsolidatedAsset.run_id == Asset.run_id))
                .outerjoin(IpamRecord, (IpamRecord.id == ConsolidatedAsset.ipam_record_id) &
                           (IpamRecord.run_id == Asset.run_id))
                .filter(Asset.vm_id == asset.vm_id, Asset.run_id.in_(db.session.query(run_ids.c.id)))
                .order_by(Asset.run_id.desc(), Asset.id.desc()).all())
        history = [dict(_row(a, c, i) if c else
                        {field: getattr(a, field, None) for field in FIELDS},
                        asset_id=a.id, run_id=a.run_id) for a, c, i in rows]
        if not any(entry["asset_id"] == id for entry in history):
            current = (db.session.query(Asset, ConsolidatedAsset, IpamRecord)
                       .outerjoin(ConsolidatedAsset, (ConsolidatedAsset.asset_id == Asset.id) &
                                  (ConsolidatedAsset.run_id == Asset.run_id))
                       .outerjoin(IpamRecord, (IpamRecord.id == ConsolidatedAsset.ipam_record_id) &
                                  (IpamRecord.run_id == Asset.run_id))
                       .filter(Asset.id == id).one())
            selected = dict(_row(*current) if current[1] else
                            {field: getattr(asset, field, None) for field in FIELDS},
                            asset_id=id, run_id=asset.run_id)
        else:
            selected = next(entry for entry in history if entry["asset_id"] == id)
        anomalies = Anomaly.query.filter(Anomaly.asset_id.in_({id, *(a.id for a, _, _ in rows)}))
        return jsonify(asset=selected,
                       history=history, anomalies=[{key: getattr(a, key) for key in (
                           "id", "run_id", "asset_id", "ipam_record_id", "code",
                           "description", "created_at")} for a in anomalies.order_by(Anomaly.run_id.desc(), Anomaly.id).all()])

    def anomalies():
        """List persisted anomalies for the latest run or a selected run.
        ---
        tags: [Inventory]
        security: [{Bearer: []}]
        parameters:
          - {in: query, name: run_id, type: integer, minimum: 1, description: Default latest run}
          - {in: query, name: code, type: string, enum: [NO_MATCH, MATCHED_IP, HOSTNAME_MISMATCH, STATUS_MISMATCH, DUPLICATE_DNS, DUPLICATE_IP, RENAMED]}
          - {in: query, name: severity, type: string, enum: [info, mineure, majeure, critique]}
          - {in: query, name: state, type: string, enum: [ouverte, acquittée, résolue]}
        responses:
          200:
            description: Anomalies of the selected run
            schema:
              type: object
              properties:
                run_id: {type: integer, description: Selected run ID or null}
                items: {type: array, items: {type: object, description: id run_id asset_id ipam_record_id code description created_at}}
          400: {description: Invalid filter}
          401: {description: Missing or invalid Bearer token}
        """
        if (any(len(request.args.getlist(key)) != 1 for key in request.args) or
                any(key not in ("run_id", "code", "severity", "state") for key in request.args) or
                ("code" in request.args and request.args["code"] not in ANOMALY_CODES) or
                ("severity" in request.args and request.args["severity"] not in
                 ("info", "mineure", "majeure", "critique")) or
                ("state" in request.args and request.args["state"] not in
                 ("ouverte", "acquittée", "résolue"))):
            return jsonify(error="Invalid anomaly filter"), 400
        run = Run.query.order_by(Run.id.desc()).first()
        run_id = _integer(request.args["run_id"]) if "run_id" in request.args else (run.id if run else None)
        if "run_id" in request.args and run_id is None:
            return jsonify(error="Invalid anomaly filter"), 400
        query = Anomaly.query.filter_by(run_id=run_id) if run_id is not None else Anomaly.query.filter(False)
        if request.args.get("code"):
            query = query.filter_by(code=request.args["code"])
        if "severity" in request.args:
            query = query.filter_by(severity=request.args["severity"])
        if "state" in request.args:
            query = query.filter_by(state=request.args["state"])
        rows = query.order_by(Anomaly.id).all()
        coverage = anomaly_coverage(rows)
        return jsonify(run_id=run_id, items=[{**{key: getattr(a, key) for key in (
            "id", "run_id", "asset_id", "ipam_record_id", "code", "description", "created_at",
            "tracking_key", "state", "state_changed_at", "severity",
        )}, "target": json.loads(a.tracking_key)[1] if a.tracking_key else None,
            "covered": coverage[a.id] is not None, "exception_id": coverage[a.id]}
            for a in rows])

    def exceptions():
        if request.method == "GET":
            if request.args:
                return jsonify(error="Invalid exception"), 400
            return jsonify(items=list_exceptions())
        if (request.content_length is not None and request.content_length > 4096 or
                request.mimetype != "application/json" or request.args):
            return jsonify(error="Invalid exception"), 400
        data = request.get_json(silent=True)
        try:
            row = create_exception(data)
        except ValueError:
            return jsonify(error="Invalid exception"), 400
        return jsonify(row), 201

    def remove_exception(id):
        if request.args or request.data:
            return jsonify(error="Invalid exception"), 400
        if not delete_exception(id):
            return jsonify(error="Exception not found"), 404
        return "", 204

    def change_anomaly(id, action):
        if request.content_length and request.content_length > 1024:
            return jsonify(error="Invalid anomaly transition"), 400
        if request.data or request.args:
            return jsonify(error="Invalid anomaly transition"), 400
        anomaly = db.session.get(Anomaly, id)
        if anomaly is None:
            return jsonify(error="Anomaly not found"), 404
        if anomaly.tracking_key:
            latest = (Anomaly.query.filter_by(tracking_key=anomaly.tracking_key)
                      .order_by(Anomaly.id.desc()).first())
            if latest.id != anomaly.id:
                return jsonify(error="Invalid anomaly transition"), 409
        expected, target = (("ouverte", "acquittée") if action == "acknowledge"
                            else ("acquittée", "résolue"))
        if anomaly.state != expected:
            return jsonify(error="Invalid anomaly transition"), 409
        anomaly.state = target
        anomaly.state_changed_at = datetime.now(UTC).isoformat()
        duration = None
        if target == "résolue":
            first = (Anomaly.query.filter_by(tracking_key=anomaly.tracking_key)
                     .order_by(Anomaly.id).first() if anomaly.tracking_key else anomaly)
            duration = (datetime.fromisoformat(anomaly.state_changed_at) -
                        datetime.fromisoformat(first.created_at)).total_seconds()
        db.session.commit()
        return jsonify(id=anomaly.id, state=anomaly.state, state_changed_at=anomaly.state_changed_at,
                       resolution_seconds=duration)

    app.add_url_rule("/api/inventory", endpoint="api.inventory", view_func=inventory)
    app.add_url_rule("/api/inventory/export", endpoint="api.inventory_export", view_func=export)
    app.add_url_rule("/api/assets/<int:id>", endpoint="api.asset_detail", view_func=asset_detail)
    app.add_url_rule("/api/anomalies", endpoint="api.anomalies", view_func=anomalies)
    app.add_url_rule("/api/anomaly-exceptions", endpoint="api.anomaly_exceptions",
                     view_func=exceptions, methods=["GET", "POST"])
    app.add_url_rule("/api/anomaly-exceptions/<int:id>", endpoint="api.delete_anomaly_exception",
                     view_func=remove_exception, methods=["DELETE"])
    app.add_url_rule("/api/anomalies/<int:id>/acknowledge", endpoint="api.acknowledge_anomaly",
                     view_func=lambda id: change_anomaly(id, "acknowledge"), methods=["POST"])
    app.add_url_rule("/api/anomalies/<int:id>/resolve", endpoint="api.resolve_anomaly",
                     view_func=lambda id: change_anomaly(id, "resolve"), methods=["POST"])
