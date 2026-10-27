import hmac
import os
from importlib import import_module
from pathlib import Path

from dotenv import dotenv_values
from flasgger import Swagger
from flask import Blueprint, Flask, abort, jsonify, request, session
from flask_jwt_extended import JWTManager, verify_jwt_in_request
from flask_login import LoginManager, current_user, login_required
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event

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

    @app.before_request
    def protect_web():
        if request.path == "/api" or request.path.startswith("/api/"):
            if request.endpoint != "api.login":
                verify_jwt_in_request()
            return None
        if request.endpoint != "login_page.login" and not current_user.is_authenticated:
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
