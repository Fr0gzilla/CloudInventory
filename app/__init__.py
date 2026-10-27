import os
from importlib import import_module
from pathlib import Path

from dotenv import dotenv_values
from flasgger import Swagger
from flask import Blueprint, Flask, jsonify
from flask_jwt_extended import JWTManager
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event

db = SQLAlchemy()
login_manager = LoginManager()
jwt = JWTManager()
swagger = Swagger()

web = Blueprint("web", __name__)
api = Blueprint("api", __name__)
auth = Blueprint("auth", __name__)


@web.get("/health")
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
    app.config.update(
        secrets,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        USE_MOCK_VIRT=str(configured("USE_MOCK_VIRT", "true")).lower() != "false",
        USE_MOCK_IPAM=str(configured("USE_MOCK_IPAM", "true")).lower() != "false",
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
    jwt.init_app(app)
    swagger.init_app(app)

    app.register_blueprint(web, url_prefix="/")
    app.register_blueprint(api, url_prefix="/api")
    app.register_blueprint(auth, url_prefix="/auth")
    return app
