import hmac
import secrets
import time
from urllib.parse import urlsplit

from flask import Blueprint, abort, redirect, render_template, request, session, url_for
from flask_login import UserMixin, login_user
from werkzeug.security import check_password_hash, generate_password_hash

from . import login_manager


class Admin(UserMixin):
    def get_id(self):
        return "admin"


def internal_next(value):
    if not isinstance(value, str) or len(value) > 2048 or not value.startswith("/"):
        return None
    if value.startswith("//") or "\\" in value or "%" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return None
    return value


def configure_auth(app, username, password):
    if not isinstance(username, str) or not username or len(username) > 128:
        raise ValueError("ADMIN_USERNAME")
    login_page = Blueprint("login_page", __name__)
    password_hash = generate_password_hash(password)
    attempts = {}
    limit = 5
    window = 900
    login_manager.login_view = "login_page.login"

    @login_manager.unauthorized_handler
    def unauthorized():
        destination = internal_next(request.full_path.rstrip("?"))
        return redirect(url_for("login_page.login", next=destination))

    @login_manager.user_loader
    def load_user(user_id):
        return Admin() if user_id == "admin" else None

    @login_page.route("/login", methods=["GET", "POST"])
    def login():
        destination = internal_next(request.values.get("next")) or url_for("web.health")
        if request.method == "GET":
            session["csrf_token"] = secrets.token_urlsafe(32)
            return render_template("login.html", next=destination, csrf_token=session["csrf_token"])

        token = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not isinstance(token, str) or not expected or not hmac.compare_digest(token, expected):
            abort(400)
        supplied_user = request.form.get("username", "")
        supplied_password = request.form.get("password", "")
        if not isinstance(supplied_user, str) or not isinstance(supplied_password, str):
            abort(400)
        if len(supplied_user) > 128 or len(supplied_password) > 4096:
            abort(400)
        key = request.remote_addr or "unknown"
        now = time.monotonic()
        for ip, state in list(attempts.items()):
            if now - state[0] >= window:
                del attempts[ip]
        started, count = attempts.get(key, (now, 0))
        if count >= limit:
            abort(429)
        if not (supplied_user == username and check_password_hash(password_hash, supplied_password)):
            attempts[key] = (started, count + 1)
            return render_template("login.html", next=destination, csrf_token=expected, error=True), 401
        attempts.pop(key, None)
        session.clear()
        login_user(Admin())
        return redirect(destination)

    app.register_blueprint(login_page)
