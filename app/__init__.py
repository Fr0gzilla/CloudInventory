import hashlib
import os

from flask import Flask, current_app, request
from flask_jwt_extended import JWTManager
from flask_login import LoginManager
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


def route_exists(target):
    """Endpoint ou chemin déjà enregistré : le gabarit n'affiche que les liens
    réellement servis (les pages de T009 apparaîtront avec leurs routes)."""
    return any(
        rule.endpoint == target or rule.rule == target
        for rule in current_app.url_map.iter_rules()
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
    from app.routes import main_bp

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
