import csv
import hashlib
import io
import os

from flask import (
    Blueprint,
    Flask,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_jwt_extended import JWTManager
from flask_login import LoginManager, login_required
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

from app.config import Config

db = SQLAlchemy()
login_manager = LoginManager()
jwt = JWTManager()

# En-tête CSP : ressources et scripts uniquement depuis l'origine — les
# templates (T008+) doivent charger leurs scripts via des fichiers, pas inline.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
    "form-action 'self'; object-src 'none'"
)

# Mémoïsation des hashes d'ADMIN_PASSWORD, indexée par empreinte SHA-256 :
# le mot de passe en clair n'est conservé nulle part après l'amorçage.
_PASSWORD_HASHES = {}


def _env_int(name, default):
    """Entier de configuration lu dans l'environnement, défaut de dev non sensible."""
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} invalide : {raw!r} (entier attendu)") from exc


def _admin_password_hash(raw):
    """Hash werkzeug du mot de passe admin (ou hash déjà fourni dans .env)."""
    key = hashlib.sha256(raw.encode("utf-8")).digest()
    if key not in _PASSWORD_HASHES:
        if raw.startswith(("pbkdf2:", "scrypt:")):
            _PASSWORD_HASHES[key] = raw
        else:
            _PASSWORD_HASHES[key] = generate_password_hash(raw)
    return _PASSWORD_HASHES[key]


def _finalize_config(app, config_class):
    """Configuration finale : hash du mot de passe admin, cookies de session, limites."""
    raw = app.config.pop("ADMIN_PASSWORD", None)
    if hasattr(config_class, "ADMIN_PASSWORD"):
        delattr(config_class, "ADMIN_PASSWORD")
    if raw:
        app.config["ADMIN_PASSWORD_HASH"] = _admin_password_hash(raw)

    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = app.config["SESSION_COOKIE_SECURE"] or (
        os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    )
    app.config.setdefault("ADMIN_USERNAME", os.getenv("ADMIN_USERNAME", "admin"))
    app.config.setdefault("LOGIN_MAX_ATTEMPTS", _env_int("LOGIN_MAX_ATTEMPTS", 5))
    app.config.setdefault(
        "LOGIN_LOCKOUT_SECONDS", _env_int("LOGIN_LOCKOUT_SECONDS", 300)
    )


# Pages web (T008/T009) : `main` porte le tableau de bord, l'inventaire, le
# détail d'un asset et le lancement de run, `auth` reste l'autre blueprint.
main_bp = Blueprint("main", __name__)


def route_exists(target):
    """Endpoint ou chemin déjà enregistré : le gabarit n'affiche que les liens
    réellement servis (les pages de T009 apparaîtront avec leurs routes)."""
    return any(
        rule.endpoint == target or rule.rule == target
        for rule in current_app.url_map.iter_rules()
    )


@main_bp.before_request
def _csrf_required():
    """POST des pages web : jeton de session exigé (cahier « CSRF sur les
    formulaires »), comparaison en temps constant réutilisée depuis auth."""
    if request.method != "POST":
        return None
    from app.auth import _csrf_token_ok

    if not _csrf_token_ok():
        abort(400, description="Jeton CSRF invalide ou manquant.")
    return None


@main_bp.route("/")
@login_required
def dashboard():
    """Tableau de bord (cahier §8.1) : dernier run et statistiques partagées."""
    from app.models import Run
    from app.queries import get_stats_data

    last_run = db.session.query(Run).order_by(Run.id.desc()).first()
    stats = get_stats_data()
    anomaly_details = stats.get("anomalies", {})
    return render_template(
        "dashboard.html",
        run=last_run,
        stats=stats,
        anomaly_details=anomaly_details,
        total_anomalies=sum(anomaly_details.values()),
    )


@main_bp.route("/ajax/stats")
@login_required
def ajax_stats():
    """Statistiques du tableau de bord en JSON (cahier §8.1, chargées en AJAX)."""
    from app.queries import get_stats_data

    return jsonify(get_stats_data())


def _run_payload(run):
    """Run sérialisé pour la réponse JSON du lancement (colonnes de schema.sql)."""
    return {
        "id": run.id,
        "status": run.status,
        "start_date": run.start_date.isoformat() if run.start_date else None,
        "end_date": run.end_date.isoformat() if run.end_date else None,
        "matched_name_count": run.matched_name_count or 0,
        "matched_fqdn_count": run.matched_fqdn_count or 0,
        "matched_ip_count": run.matched_ip_count or 0,
        "no_match_count": run.no_match_count or 0,
        "error_message": run.error_message,
    }


@main_bp.route("/ajax/run", methods=["POST"])
@login_required
def ajax_trigger_run():
    """Lancement asynchrone du pipeline (§ 8.2) : réponse JSON."""
    from collector.inventory_runner import run_inventory

    return jsonify(_run_payload(run_inventory()))


@main_bp.route("/run", methods=["POST"])
@login_required
def trigger_run():
    """Lancement classique : mêmes effets que l'AJAX, puis retour au tableau
    de bord (le détail de run — `/runs/<id>` — sera livré par T009)."""
    from collector.inventory_runner import run_inventory

    run = run_inventory()
    if run.status == "SUCCESS":
        flash(
            f"Run #{run.id} terminé : {run.matched_name_count or 0} VMs appariées "
            f"par nom, {run.no_match_count or 0} sans correspondance.",
            "success",
        )
    else:
        flash(
            f"Run #{run.id} en échec : {run.error_message or 'erreur inconnue'}",
            "danger",
        )
    return redirect(url_for("main.dashboard"))


# ---------- Inventaire (T009) : filtres, tri, pagination, CSV, détail asset ----

# Tri autorisé (cahier §8.3) : la colonne est lue dans la liste blanche de queries.
_INVENTORY_SORTS = ("vm_name", "status", "ip", "cpu", "ram", "match")

# Pagination : 25 éléments par page (cahier §15.1), surchargeable par PER_PAGE.
_PER_PAGE = 25

# Caractères amorçant une formule dans un tableur (OWASP « CSV injection »).
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    """Cellule CSV neutralisée : « ' » devant toute valeur commençant comme une formule."""
    text = "" if value is None else str(value)
    if text[:1] in _FORMULA_START or text.lstrip()[:1] in ("=", "+", "-", "@"):
        return "'" + text
    return text


def _percent(used, total):
    """Pourcentage d'usage d'une métrique, ou None si la mesure manque."""
    if total and total > 0 and used is not None:
        return round(used / total * 100, 1)
    return None


def _uptime_label(seconds):
    """Durée d'activité lisible (« 3j 4h ») depuis des secondes, ou None."""
    if seconds and seconds > 0:
        return f"{seconds // 86400}j {(seconds % 86400) // 3600}h"
    return None


def _inventory_args():
    """Filtres et tri lus dans la query string (noms alignés sur queries.build_inventory_query)."""
    sort = request.args.get("sort", "vm_name")
    return {
        "q": request.args.get("q", "").strip(),
        "status": request.args.get("status", ""),
        "node": request.args.get("node", ""),
        "vm_type": request.args.get("type", ""),
        "match": request.args.get("match", ""),
        "tag": request.args.get("tag", ""),
        "role": request.args.get("role", ""),
        "sort": sort if sort in _INVENTORY_SORTS else "vm_name",
        "order": "desc" if request.args.get("order") == "desc" else "asc",
    }


@main_bp.route("/inventory")
@login_required
def inventory():
    """Inventaire consolidé du dernier run (cahier §8.3) : filtres, tri, pagination."""
    from app.models import Asset, ConsolidatedAsset, Run
    from app.queries import build_inventory_query

    args = _inventory_args()

    def _url(page=1, sort=None, order=None, **overrides):
        """Lien de la page inventaire conservant les filtres en cours (url_for, pas de concaténation)."""
        params = {
            ("type" if key == "vm_type" else key): value
            for key, value in args.items()
            if value and key not in ("sort", "order")
        }
        for key, value in overrides.items():
            if value:
                params[key] = value
            else:
                params.pop(key, None)
        params["sort"] = sort or args["sort"]
        params["order"] = order or args["order"]
        params["page"] = page
        return url_for("main.inventory", **params)

    common = {
        "search_url": url_for("main.ajax_inventory_search"),
        "clear_url": url_for("main.inventory"),
        "reset_url": _url(page=1, q=""),
        **args,
    }

    last_run = db.session.query(Run).order_by(Run.id.desc()).first()
    if not last_run:
        return render_template(
            "inventory.html",
            run=None,
            items=[],
            pagination=None,
            filters={},
            tag_filters={},
            sort_links={},
            page_links={},
            **common,
        )

    query = build_inventory_query(last_run.id, **args)
    pagination = query.paginate(
        page=request.args.get("page", 1, type=int) or 1,
        per_page=current_app.config.get("PER_PAGE", _PER_PAGE),
        error_out=False,
    )

    filters = {
        "statuses": _distinct_values(Asset.status),
        "nodes": _distinct_values(Asset.node),
        "types": _distinct_values(Asset.type),
        "matches": _distinct_values(ConsolidatedAsset.match_status),
        "roles": sorted(_distinct_values(ConsolidatedAsset.role)),
    }

    tag_filters = {}
    for (tags,) in (
        db.session.query(Asset.tags).filter(Asset.tags.isnot(None)).distinct().all()
    ):
        for chunk in tags.split(","):
            chunk = chunk.strip()
            if ":" not in chunk:
                continue
            category, value = chunk.split(":", 1)
            tag_filters.setdefault(category.strip(), set()).add(value.strip())
    tag_filters = {k: sorted(v) for k, v in sorted(tag_filters.items())}

    sort_links = {
        col: _url(
            page=1,
            sort=col,
            order="desc" if args["sort"] == col and args["order"] == "asc" else "asc",
        )
        for col in _INVENTORY_SORTS
    }

    wanted_pages = {
        p
        for p in pagination.iter_pages(
            left_edge=1, right_edge=1, left_current=2, right_current=2
        )
        if p
    }
    if pagination.has_prev:
        wanted_pages.add(pagination.prev_num)
    if pagination.has_next:
        wanted_pages.add(pagination.next_num)
    page_links = {p: _url(page=p) for p in wanted_pages}

    return render_template(
        "inventory.html",
        run=last_run,
        items=pagination.items,
        pagination=pagination,
        filters=filters,
        tag_filters=tag_filters,
        sort_links=sort_links,
        page_links=page_links,
        **common,
    )


def _distinct_values(column):
    """Valeurs distinctes non nules d'une colonne, pour les listes de filtres."""
    return [
        row[0]
        for row in db.session.query(column)
        .filter(column.isnot(None))
        .distinct()
        .all()
    ]


@main_bp.route("/ajax/inventory/search")
@login_required
def ajax_inventory_search():
    """Recherche live de l'inventaire (cahier §8.3) : 100 lignes max en JSON."""
    from app.models import Run
    from app.queries import build_inventory_query, serialize_inventory_item

    last_run = db.session.query(Run).order_by(Run.id.desc()).first()
    if not last_run:
        return jsonify({"items": [], "total": 0})

    args = _inventory_args()
    query = build_inventory_query(last_run.id, **args)
    total = query.count()

    items = []
    for ca, asset, ipam in query.limit(100).all():
        item = serialize_inventory_item(ca, asset, ipam)
        item.update(
            {
                "detail_url": url_for("main.asset_detail", asset_id=asset.id),
                "ip": ca.ip_final or asset.ip_reported or "",
                "dns": ca.dns_final or item["dns"],
                "cpu": asset.cpu_usage,
                "ram_pct": _percent(asset.ram_used, asset.ram_max),
                "disk_pct": _percent(asset.disk_used, asset.disk_max),
                "uptime": _uptime_label(asset.uptime),
            }
        )
        items.append(item)

    return jsonify({"items": items, "total": total})


@main_bp.route("/inventory/export")
@login_required
def inventory_export():
    """Export CSV du dernier run (cahier §8.3) : cellules neutralisées contre les formules."""
    from app.models import Run
    from app.queries import export_inventory_csv

    last_run = db.session.query(Run).order_by(Run.id.desc()).first()
    if not last_run:
        abort(404, description="Aucun run d'inventaire à exporter.")

    reader = csv.reader(io.StringIO(export_inventory_csv(last_run.id)), delimiter=";")
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    for row in reader:
        writer.writerow([_csv_safe(cell) for cell in row])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": (
                f"attachment; filename=inventaire_run{last_run.id}.csv"
            )
        },
    )


@main_bp.route("/assets/<int:asset_id>")
@login_required
def asset_detail(asset_id):
    """Fiche d'un asset (cahier §8.7) : infos, métriques, 30 derniers runs, anomalies."""
    from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run

    asset = db.get_or_404(Asset, asset_id)

    history = (
        db.session.query(ConsolidatedAsset, Run, IpamRecord)
        .join(Run, ConsolidatedAsset.run_id == Run.id)
        .outerjoin(IpamRecord, ConsolidatedAsset.ipam_record_id == IpamRecord.id)
        .filter(ConsolidatedAsset.asset_id == asset_id)
        .order_by(Run.id.desc())
        .limit(30)
        .all()
    )
    anomalies = (
        db.session.query(Anomaly, Run)
        .join(Run, Anomaly.run_id == Run.id)
        .filter(Anomaly.asset_id == asset_id)
        .order_by(Anomaly.id.desc())
        .all()
    )

    gb = 1073741824
    metrics = {
        "cpu_count": asset.cpu_count,
        "cpu": asset.cpu_usage,
        "ram_pct": _percent(asset.ram_used, asset.ram_max),
        "ram_used_gb": round(asset.ram_used / gb, 1) if asset.ram_used else None,
        "ram_max_gb": round(asset.ram_max / gb, 1) if asset.ram_max else None,
        "disk_pct": _percent(asset.disk_used, asset.disk_max),
        "disk_used_gb": round(asset.disk_used / gb, 1) if asset.disk_used else None,
        "disk_max_gb": round(asset.disk_max / gb, 1) if asset.disk_max else None,
        "uptime": _uptime_label(asset.uptime),
    }

    return render_template(
        "asset_detail.html",
        asset=asset,
        metrics=metrics,
        history=history,
        anomalies=anomalies,
    )


def create_app(config_class=Config):
    """Factory : charge .env, valide les secrets, initialise les extensions."""
    load_dotenv()
    config_class.validate()

    app = Flask(__name__)
    app.config.from_object(config_class)
    _finalize_config(app, config_class)

    db.init_app(app)
    login_manager.init_app(app)
    jwt.init_app(app)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Veuillez vous connecter."
    login_manager.login_message_category = "warning"

    from app.auth import auth_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.add_template_global(route_exists)

    @app.after_request
    def _security_headers(response):
        """En-têtes de sécurité sur toutes les réponses (CAHIER « Sécurité »)."""
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

    with app.app_context():
        from app import models  # noqa: F401  — enregistre les tables avant create_all
        db.create_all()

    return app
