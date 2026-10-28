from collections import Counter

from flask import jsonify, request
from flask_jwt_extended import create_access_token

from app.models import Anomaly, Asset, ConsolidatedAsset, Run
from collector.inventory_runner import run_inventory
from collector.run_lock import RunAlreadyActive

from . import db, jwt


def _run_json(run):
    return {key: getattr(run, key) for key in (
        "id", "status", "started_at", "finished_at", "matched_name_count",
        "matched_fqdn_count", "matched_ip_count", "no_match_count", "error_message",
    )}


def _snapshot(run_id):
    rows = (db.session.query(Asset, ConsolidatedAsset)
            .join(ConsolidatedAsset, (ConsolidatedAsset.asset_id == Asset.id) &
                  (ConsolidatedAsset.run_id == Asset.run_id))
            .filter(Asset.run_id == run_id).order_by(Asset.id).all())
    return {asset.vm_id: {
        "vm_id": asset.vm_id, "vm_name": asset.vm_name, "ip_final": item.ip_final,
        "dns_final": item.dns_final, "status_final": item.status_final,
        "match_status": item.match_status, "role": item.role,
    } for asset, item in rows}


def _positive_integer(value):
    if value is None or not value.isascii() or not value.isdecimal() or len(value) > 10:
        return None
    number = int(value)
    return number if 0 < number <= 2147483647 else None


def configure_api(app, authenticate):
    @jwt.unauthorized_loader
    def missing_token(reason):
        return jsonify(error="Unauthorized"), 401

    @jwt.invalid_token_loader
    def invalid_token(reason):
        return jsonify(error="Unauthorized"), 401

    @jwt.expired_token_loader
    def expired_token(header, payload):
        return jsonify(error="Unauthorized"), 401

    @jwt.revoked_token_loader
    def revoked_token(header, payload):
        return jsonify(error="Unauthorized"), 401

    def login():
        """Authenticate an administrator.
        ---
        tags:
          - Authentication
        security: []
        consumes:
          - application/json
        parameters:
          - in: body
            name: credentials
            required: true
            schema:
              type: object
              required: [username, password]
              properties:
                username:
                  type: string
                password:
                  type: string
                  format: password
        responses:
          200:
            description: Bearer access token
            schema:
              type: object
              properties:
                access_token:
                  type: string
          401:
            description: Invalid credentials
          429:
            description: Too many attempts
        """
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="Invalid credentials"), 400
        username = data.get("username")
        password = data.get("password")
        if (not isinstance(username, str) or not username or len(username) > 128 or
                not isinstance(password, str) or not password or len(password) > 4096):
            return jsonify(error="Invalid credentials"), 400
        result = authenticate(username, password, channel="api")
        if result == "limited":
            return jsonify(error="Too many attempts"), 429
        if result == "invalid":
            return jsonify(error="Invalid credentials"), 401
        return jsonify(access_token=create_access_token(identity="admin"))

    app.add_url_rule("/api/login", endpoint="api.login", view_func=login, methods=["POST"])

    def stats():
        """Statistics of the latest run, including failed runs.
        ---
        tags: [Runs]
        security:
          - Bearer: []
        responses:
          200:
            description: Latest run and persisted asset, match and anomaly counts; null run if empty
            schema:
              type: object
              properties:
                run: {type: object, description: Run fields or null}
                total_assets: {type: integer}
                matches: {type: object, description: Counts by match status}
                anomalies: {type: object, description: Counts by anomaly code}
          401:
            description: Missing or invalid Bearer token
        """
        run = Run.query.order_by(Run.id.desc()).first()
        matches = {code: 0 for code in ("MATCHED_NAME", "MATCHED_FQDN", "MATCHED_IP", "NO_MATCH")}
        anomalies = {}
        total = 0
        if run is not None:
            total = Asset.query.filter_by(run_id=run.id).count()
            matches.update(Counter(row.match_status for row in
                                   ConsolidatedAsset.query.filter_by(run_id=run.id).all()))
            anomalies = dict(Counter(row.code for row in Anomaly.query.filter_by(run_id=run.id).all()))
        return jsonify(run=_run_json(run) if run else None, total_assets=total,
                       matches=matches, anomalies=anomalies)

    def runs():
        """List runs, newest first.
        ---
        tags: [Runs]
        security:
          - Bearer: []
        parameters:
          - {in: query, name: page, type: integer, minimum: 1, default: 1}
          - {in: query, name: per_page, type: integer, minimum: 1, maximum: 100, default: 20}
        responses:
          200:
            description: Paginated runs
            schema:
              type: object
              properties:
                items: {type: array, items: {type: object, description: Run fields}}
                page: {type: integer}
                per_page: {type: integer}
                total: {type: integer}
          400:
            description: Invalid pagination
          401:
            description: Missing or invalid Bearer token
        """
        page = _positive_integer(request.args.get("page", "1"))
        per_page = _positive_integer(request.args.get("per_page", "20"))
        if page is None or per_page is None or per_page > 100:
            return jsonify(error="Invalid pagination"), 400
        result = Run.query.order_by(Run.id.desc()).paginate(page=page, per_page=per_page, error_out=False)
        return jsonify(items=[_run_json(run) for run in result.items], page=page,
                       per_page=per_page, total=result.total)

    def create_run():
        """Start an inventory run with the configured sources.
        ---
        tags: [Runs]
        security:
          - Bearer: []
        responses:
          201:
            description: Persisted run (SUCCESS or FAIL)
            schema:
              type: object
              properties:
                id: {type: integer}
                status: {type: string, enum: [SUCCESS, FAIL]}
                started_at: {type: string}
                finished_at: {type: string}
                matched_name_count: {type: integer}
                matched_fqdn_count: {type: integer}
                matched_ip_count: {type: integer}
                no_match_count: {type: integer}
                error_message: {type: string}
          401:
            description: Missing or invalid Bearer token
          409:
            description: An inventory run is already in progress
        """
        try:
            return jsonify(_run_json(run_inventory())), 201
        except RunAlreadyActive as error:
            return jsonify(error=str(error)), 409

    def run_detail(id):
        """Get a run with its consolidated assets and anomalies.
        ---
        tags: [Runs]
        security:
          - Bearer: []
        parameters:
          - {in: path, name: id, type: integer, required: true, minimum: 1}
        responses:
          200:
            description: Run fields plus consolidated assets and anomalies
            schema:
              type: object
              properties:
                id: {type: integer}
                status: {type: string}
                started_at: {type: string}
                finished_at: {type: string}
                matched_name_count: {type: integer}
                matched_fqdn_count: {type: integer}
                matched_ip_count: {type: integer}
                no_match_count: {type: integer}
                error_message: {type: string}
                assets: {type: array, items: {type: object, description: vm_id and consolidated fields}}
                anomalies: {type: array, items: {type: object, description: id code description asset_id ipam_record_id created_at}}
          401:
            description: Missing or invalid Bearer token
          404:
            description: Run not found
        """
        run = db.session.get(Run, id)
        if run is None:
            return jsonify(error="Run not found"), 404
        result = _run_json(run)
        result["assets"] = list(_snapshot(id).values())
        result["anomalies"] = [{key: getattr(row, key) for key in (
            "id", "code", "description", "asset_id", "ipam_record_id", "created_at",
        )} for row in Anomaly.query.filter_by(run_id=id).order_by(Anomaly.id).all()]
        return jsonify(result)

    def compare_runs():
        """Compare two persisted run snapshots by vm_id (from to to).
        ---
        tags: [Runs]
        security:
          - Bearer: []
        parameters:
          - {in: query, name: from, type: integer, required: true, minimum: 1, description: Older run ID}
          - {in: query, name: to, type: integer, required: true, minimum: 1, description: Newer run ID}
        responses:
          200:
            description: Appeared and disappeared snapshots; changed entries carry before and after snapshots and changed field names
            schema:
              type: object
              properties:
                from: {type: integer}
                to: {type: integer}
                appeared: {type: array, items: {type: object}}
                disappeared: {type: array, items: {type: object}}
                changed: {type: array, items: {type: object, description: vm_id before after fields}}
          400:
            description: Missing, duplicate or invalid run IDs
          401:
            description: Missing or invalid Bearer token
        """
        old_id = _positive_integer(request.args.get("from"))
        new_id = _positive_integer(request.args.get("to"))
        if (old_id is None or new_id is None or old_id == new_id or
                db.session.get(Run, old_id) is None or db.session.get(Run, new_id) is None):
            return jsonify(error="Invalid comparison"), 400
        old = _snapshot(old_id)
        new = _snapshot(new_id)
        fields = ("ip_final", "dns_final", "status_final", "match_status")
        changed = [{"vm_id": vm_id, "before": old[vm_id], "after": new[vm_id],
                    "fields": [field for field in fields if old[vm_id][field] != new[vm_id][field]]}
                   for vm_id in sorted(old.keys() & new.keys())
                   if any(old[vm_id][field] != new[vm_id][field] for field in fields)]
        return jsonify({"from": old_id, "to": new_id,
                        "appeared": [new[key] for key in sorted(new.keys() - old.keys())],
                        "disappeared": [old[key] for key in sorted(old.keys() - new.keys())],
                        "changed": changed})

    app.add_url_rule("/api/stats", endpoint="api.stats", view_func=stats, methods=["GET"])
    app.add_url_rule("/api/runs", endpoint="api.runs", view_func=runs, methods=["GET"])
    app.add_url_rule("/api/runs", endpoint="api.create_run", view_func=create_run, methods=["POST"])
    app.add_url_rule("/api/runs/compare", endpoint="api.compare_runs", view_func=compare_runs, methods=["GET"])
    app.add_url_rule("/api/runs/<int:id>", endpoint="api.run_detail", view_func=run_detail, methods=["GET"])

    from .inventory_api import configure_inventory_api

    configure_inventory_api(app)
