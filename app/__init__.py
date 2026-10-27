import hmac
import os
from importlib import import_module
from pathlib import Path

from dotenv import dotenv_values
from flasgger import Swagger
from flask import Blueprint, Flask, abort, jsonify, request, session
from flask_jwt_extended import JWTManager
from flask_login import LoginManager, current_user, login_required
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event

db = SQLAlchemy()
login_manager = LoginManager()
jwt = JWTManager()
swagger = Swagger()

web = Blueprint("web", __name__)
api = Blueprint("api", __name__)
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
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        USE_MOCK_VIRT=str(configured("USE_MOCK_VIRT", "true")).lower() != "false",
        USE_MOCK_IPAM=str(configured("USE_MOCK_IPAM", "true")).lower() != "false",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=str(configured("SESSION_COOKIE_SECURE", "true")).lower() != "false",
    )

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

    configure_auth(app, configured("ADMIN_USERNAME", "admin"), password)
    del password

    @app.before_request
    def protect_web():
        if not request.path.startswith("/api/") and request.path != "/api":
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
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    jwt.init_app(app)
    swagger.init_app(app)

    app.register_blueprint(web, url_prefix="/")
    app.register_blueprint(api, url_prefix="/api")
    app.register_blueprint(auth_blueprint, url_prefix="/auth")
    return app
