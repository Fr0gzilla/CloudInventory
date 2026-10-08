import hashlib
import os

from flask import (
    Blueprint,
    Flask,
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


# Pages web (T008) : `main` porte le tableau de bord et le lancement de run,
# `auth` (connexion, déconnexion) reste le seul autre blueprint.
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
