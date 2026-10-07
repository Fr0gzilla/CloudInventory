from flask import Flask
from flask_jwt_extended import JWTManager
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

from app.config import Config

db = SQLAlchemy()
login_manager = LoginManager()
jwt = JWTManager()


def create_app(config_class=Config):
    """Factory : charge .env, valide les secrets, initialise les extensions."""
    load_dotenv()
    config_class.validate()

    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)
    jwt.init_app(app)

    with app.app_context():
        db.create_all()

    return app
