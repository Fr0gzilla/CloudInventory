import hmac
import os
from importlib import import_module
from pathlib import Path

from dotenv import dotenv_values
from flasgger import Swagger
from flask import Blueprint, Flask, abort, current_app, jsonify, render_template, request, session
from flask_jwt_extended import JWTManager, verify_jwt_in_request
from flask_login import LoginManager, current_user, login_required
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event, inspect

db = SQLAlchemy()
login_manager = LoginManager()
jwt = JWTManager()
swagger = Swagger(template={
    "swagger": "2.0",
    "info": {"title": "Cloud Inventory API", "version": "1.0"},
    "securityDefinitions": {
        "Bearer": {"type": "apiKey", "name": "Authorization", "in": "header",
                   "description": "Bearer <access_token> from POST /api/login"}
    },
    "security": [{"Bearer": []}],
})

web = Blueprint("web", __name__)
api_blueprint = Blueprint("api", __name__)
auth_blueprint = Blueprint("auth", __name__)


@web.get("/")
@login_required
def index():
    from .models import Run

    if inspect(db.engine).has_table("run"):
        latest = Run.query.order_by(Run.id.desc()).first()
        stats = current_app.view_functions["api.stats"]().get_json()
    else:
        latest = None
        stats = {"total_assets": 0, "matches": {code: 0 for code in
                 ("MATCHED_NAME", "MATCHED_FQDN", "MATCHED_IP", "NO_MATCH")}, "anomalies": {}}
    return render_template("index.html", latest=latest, stats=stats,
                           anomaly_total=sum(stats["anomalies"].values()))


@web.get("/dashboard/data")
@login_required
def dashboard_data():
    if not inspect(db.engine).has_table("run"):
        return jsonify(stats={"run": None, "total_assets": 0,
                              "matches": {code: 0 for code in
                                          ("MATCHED_NAME", "MATCHED_FQDN", "MATCHED_IP", "NO_MATCH")},
                              "anomalies": {}}, runs=[])
    stats = current_app.view_functions["api.stats"]().get_json()
    runs = current_app.view_functions["api.runs"]().get_json()
    return jsonify(stats=stats, runs=runs["items"])


@web.post("/dashboard/run")
@login_required
def dashboard_run():
    result, status = current_app.view_functions["api.create_run"]()
    return result, status


@web.get("/inventory")
@login_required
def inventory_page():
    return render_template("inventory.html")


@web.get("/inventory/data")
@login_required
def inventory_data():
    from .inventory_api import _integer, _inventory_query, _row
    from .models import Run

    page = _integer(request.args.get("page", "1"))
    per_page = _integer(request.args.get("per_page", "20"))
    run = Run.query.order_by(Run.id.desc()).first()
    query = _inventory_query(run.id if run else -1, request.args)
    if query is None or page is None or per_page is None or per_page > 100:
        return jsonify(error="Invalid inventory query"), 400
    result = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify(run_id=run.id if run else None,
                   items=[dict(_row(*row), asset_id=row[0].id) for row in result.items],
                   page=page, per_page=per_page, total=result.total)


@web.get("/inventory/export")
@login_required
def inventory_export():
    return current_app.view_functions["api.inventory_export"]()


@web.get("/runs")
@login_required
def runs_page():
    from .api import _positive_integer
    from .models import Run

    page = _positive_integer(request.args.get("page", "1"))
    if page is None:
        abort(400)
    runs = Run.query.order_by(Run.id.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template("runs.html", runs=runs)


@web.get("/runs/<int:id>")
@login_required
def run_page(id):
    from .models import Anomaly, Run

    run = db.get_or_404(Run, id)
    anomalies = Anomaly.query.filter_by(run_id=id).order_by(Anomaly.id).all()
    return render_template("run_detail.html", run=run, anomalies=anomalies)


@web.get("/runs/compare")
@login_required
def run_compare_page():
    from .api import _positive_integer
    from .models import Anomaly, Run

    runs = Run.query.order_by(Run.id.desc()).all()
    comparison = None
    error = None
    old_id = new_id = None
    if request.args:
        if (set(request.args) != {"from", "to"} or
                any(len(request.args.getlist(key)) != 1 for key in request.args)):
            error = "Sélection invalide : choisissez deux runs distincts."
        else:
            old_id = _positive_integer(request.args.get("from"))
            new_id = _positive_integer(request.args.get("to"))
            if old_id is None or new_id is None or old_id == new_id:
                error = "Sélection invalide : choisissez deux runs distincts."
            elif db.session.get(Run, old_id) is None or db.session.get(Run, new_id) is None:
                error = "Run inconnu : choisissez un run disponible."
            else:
                comparison = current_app.view_functions["api.compare_runs"]().get_json()
                counts = dict(db.session.query(Anomaly.run_id, db.func.count(Anomaly.id))
                              .filter(Anomaly.run_id.in_((old_id, new_id)))
                              .group_by(Anomaly.run_id).all())
                comparison["anomaly_delta"] = counts.get(new_id, 0) - counts.get(old_id, 0)
                comparison["old_anomalies"] = counts.get(old_id, 0)
                comparison["new_anomalies"] = counts.get(new_id, 0)
    return render_template("run_compare.html", runs=runs, comparison=comparison,
                           error=error, old_id=old_id, new_id=new_id)


@web.get("/anomalies")
@login_required
def anomalies_page():
    from .inventory_api import ANOMALY_CODES, _integer
    from .models import Anomaly, Run

    runs = Run.query.order_by(Run.id.desc()).all()
    selected_id = runs[0].id if runs else None
    selected_code = request.args.get("code", "")
    error = None
    if (any(key not in ("run_id", "code") or len(request.args.getlist(key)) != 1
            for key in request.args) or
            (selected_code and selected_code not in ANOMALY_CODES)):
        error = "Filtre d'anomalies invalide."
    elif "run_id" in request.args:
        selected_id = _integer(request.args["run_id"])
        if selected_id is None:
            error = "Filtre d'anomalies invalide."
        elif db.session.get(Run, selected_id) is None:
            error = "Run inconnu : choisissez un run disponible."
    query = (Anomaly.query.filter_by(run_id=selected_id) if selected_id is not None
             else Anomaly.query.filter(False))
    if selected_code in ANOMALY_CODES:
        query = query.filter_by(code=selected_code)
    anomalies = query.order_by(Anomaly.id).all() if error is None else []
    return render_template("anomalies.html", runs=runs, selected_id=selected_id,
                           selected_code=selected_code if not error else "",
                           codes=sorted(ANOMALY_CODES), anomalies=anomalies, error=error)


@web.get("/assets/<int:id>")
@login_required
def asset_page(id):
    from .inventory_api import FIELDS, _row
    from .models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run

    asset = db.get_or_404(Asset, id)
    latest_runs = db.session.query(Run.id).order_by(Run.id.desc()).limit(30).subquery()
    rows = (db.session.query(Asset, ConsolidatedAsset, IpamRecord)
            .outerjoin(ConsolidatedAsset, (ConsolidatedAsset.asset_id == Asset.id) &
                       (ConsolidatedAsset.run_id == Asset.run_id))
            .outerjoin(IpamRecord, (IpamRecord.id == ConsolidatedAsset.ipam_record_id) &
                       (IpamRecord.run_id == Asset.run_id))
            .filter(Asset.vm_id == asset.vm_id,
                    Asset.run_id.in_(db.session.query(latest_runs.c.id)))
            .order_by(Asset.run_id.desc(), Asset.id.desc()).all())

    def entry(observed, consolidated, ipam):
        values = (_row(observed, consolidated, ipam) if consolidated else
                  {field: getattr(observed, field, None) for field in FIELDS})
        return dict(values, asset_id=observed.id, run_id=observed.run_id)

    history = [entry(*row) for row in rows]
    selected = next((item for item in history if item["asset_id"] == id), None)
    if selected is None:
        current = (db.session.query(Asset, ConsolidatedAsset, IpamRecord)
                   .outerjoin(ConsolidatedAsset, (ConsolidatedAsset.asset_id == Asset.id) &
                              (ConsolidatedAsset.run_id == Asset.run_id))
                   .outerjoin(IpamRecord, (IpamRecord.id == ConsolidatedAsset.ipam_record_id) &
                              (IpamRecord.run_id == Asset.run_id))
                   .filter(Asset.id == id).one())
        selected = entry(*current)
    anomalies = (Anomaly.query.filter(Anomaly.asset_id.in_({id, *(a.id for a, _, _ in rows)}))
                 .order_by(Anomaly.run_id.desc(), Anomaly.id).all())
    return render_template("asset_detail.html", asset=selected, history=history,
                           anomalies=anomalies)


@web.get("/health")
@login_required
def health():
    return jsonify(status="ok")


def create_app() -> Flask:
    dotenv_config = dotenv_values(Path.cwd() / ".env")

    def configured(name, default=None):
        return os.environ.get(name, dotenv_config.get(name, default))

    secrets = {}
    for key in ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD"):
        value = configured(key)
        if not value or value == "change-me":
            raise ValueError(key)
        secrets[key] = value

    app = Flask(__name__)
    password = secrets.pop("ADMIN_PASSWORD")
    app.config.update(
        secrets,
        EXPORT_DIRECTORY=configured("EXPORT_DIRECTORY", "exports"),
        EXPORT_DESTINATION=configured("EXPORT_DESTINATION", "local"),
        EXPORT_RAW_ENABLED=str(configured("EXPORT_RAW_ENABLED", "false")).lower() == "true",
        EXPORT_RETENTION_CONSOLIDATED=int(configured("EXPORT_RETENTION_CONSOLIDATED", "30")),
        EXPORT_RETENTION_RAW=int(configured("EXPORT_RETENTION_RAW", "7")),
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        USE_MOCK_VIRT=str(configured("USE_MOCK_VIRT", "true")).lower() != "false",
        USE_MOCK_IPAM=str(configured("USE_MOCK_IPAM", "true")).lower() != "false",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=str(configured("SESSION_COOKIE_SECURE", "true")).lower() != "false",
    )
    if app.config["EXPORT_DESTINATION"] not in ("local", "samba") or any(
        app.config[key] < 0 for key in ("EXPORT_RETENTION_CONSOLIDATED", "EXPORT_RETENTION_RAW")
    ):
        raise ValueError("invalid export configuration")

    import_module("app.models")
    db.init_app(app)
    with app.app_context():
        engine = db.engine
        if engine.dialect.name == "sqlite":
            def enable_foreign_keys(connection, connection_record):
                cursor = connection.cursor()
                try:
                    cursor.execute("PRAGMA foreign_keys = ON")
                finally:
                    cursor.close()

            event.listen(engine, "connect", enable_foreign_keys)

    login_manager.init_app(app)
    from .auth import configure_auth

    authenticate = configure_auth(app, configured("ADMIN_USERNAME", "admin"), password)
    del password

    from .api import configure_api

    configure_api(app, authenticate)

    @app.context_processor
    def latest_anomaly_context():
        if not current_user.is_authenticated:
            return {}
        from .models import Anomaly, Run

        if not inspect(db.engine).has_table("run"):
            return {"latest_anomaly_run": None, "latest_anomaly_count": 0}
        latest_run = Run.query.filter_by(status="SUCCESS").order_by(Run.id.desc()).first()
        anomaly_count = Anomaly.query.filter_by(run_id=latest_run.id).count() if latest_run else 0
        return {"latest_anomaly_run": latest_run, "latest_anomaly_count": anomaly_count}

    @app.before_request
    def protect_web():
        if request.path == "/api" or request.path.startswith("/api/"):
            if request.endpoint != "api.login":
                verify_jwt_in_request()
            return None
        if request.endpoint not in ("login_page.login", "static") and not current_user.is_authenticated:
            return login_manager.unauthorized()
        if request.method == "POST" and request.endpoint != "login_page.login":
            token = request.form.get("csrf_token", "")
            expected = session.get("csrf_token", "")
            if not isinstance(token, str) or not expected or not hmac.compare_digest(token, expected):
                abort(400)

    @app.after_request
    def security_headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        if request.path == "/apidocs" or request.path.startswith(("/apidocs/", "/flasgger_static/")):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; "
                "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://unpkg.com; "
                "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
                "img-src 'self' data:"
            )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    swagger.init_app(app)

    app.register_blueprint(web, url_prefix="/")
    app.register_blueprint(api_blueprint, url_prefix="/api")
    app.register_blueprint(auth_blueprint, url_prefix="/auth")
    jwt.init_app(app)
    return app
