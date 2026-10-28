import hmac
import logging
import secrets
import time
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import Blueprint, abort, redirect, render_template, request, session, url_for
from flask_login import UserMixin, login_required, login_user, logout_user
from werkzeug.security import check_password_hash

from . import login_manager

security_log = logging.getLogger("security.auth")
security_log.setLevel(logging.INFO)
security_log.addHandler(logging.StreamHandler())


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
    if not isinstance(password, str) or len(password) < 12:
        raise ValueError("ADMIN_PASSWORD must be at least 12 characters")
    login_page = Blueprint("login_page", __name__)
    hasher = PasswordHasher()
    password_hash = hasher.hash(password)
    attempts = {}
    state = {"epoch": secrets.randbits(128)}
    limit = 5
    window = 900
    absolute_lifetime = 8 * 60 * 60
    idle_lifetime = 30 * 60
    login_manager.login_view = "login_page.login"

    @app.context_processor
    def csrf_context():
        return {"csrf_token": session.get("csrf_token", "")}

    @login_manager.unauthorized_handler
    def unauthorized():
        destination = internal_next(request.full_path.rstrip("?"))
        return redirect(url_for("login_page.login", next=destination))

    @login_manager.user_loader
    def load_user(user_id):
        now = time.time()
        started = session.get("login_at")
        last_seen = session.get("last_seen")
        if (user_id != "admin" or session.get("epoch") != state["epoch"] or
                type(started) not in (int, float) or type(last_seen) not in (int, float) or
                not 0 <= now - started < absolute_lifetime or
                not 0 <= now - last_seen < idle_lifetime):
            session.clear()
            return None
        session["last_seen"] = now
        return Admin()

    def authenticate(supplied_user, supplied_password, channel="web"):
        nonlocal password_hash
        key = request.remote_addr or "unknown"
        now = time.monotonic()
        for ip, state in list(attempts.items()):
            if now - state[0] >= window:
                del attempts[ip]
        started, count = attempts.get(key, (now, 0))
        if count >= limit:
            security_log.warning("auth result=limited channel=%s ip=%s", channel, key)
            return "limited"
        valid_user = hmac.compare_digest(supplied_user.encode("utf-8", errors="surrogatepass"),
                                         username.encode("utf-8", errors="surrogatepass"))
        if password_hash.startswith("$argon2"):
            try:
                valid_password = hasher.verify(password_hash, supplied_password)
            except (InvalidHashError, VerificationError):
                valid_password = False
        else:
            valid_password = check_password_hash(password_hash, supplied_password)
        if not (valid_user and valid_password):
            attempts[key] = (started, count + 1)
            security_log.warning("auth result=invalid channel=%s ip=%s", channel, key)
            return "invalid"
        if not password_hash.startswith("$argon2") or hasher.check_needs_rehash(password_hash):
            password_hash = hasher.hash(supplied_password)
        attempts.pop(key, None)
        security_log.info("auth result=success channel=%s ip=%s", channel, key)
        return None

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
        result = authenticate(supplied_user, supplied_password)
        if result == "limited":
            abort(429)
        if result == "invalid":
            return render_template("login.html", next=destination, csrf_token=expected, error=True), 401
        session.clear()
        login_user(Admin())
        session.permanent = True
        session["epoch"] = state["epoch"]
        session["login_at"] = session["last_seen"] = time.time()
        session["csrf_token"] = secrets.token_urlsafe(32)
        return redirect(destination)

    @login_page.post("/logout")
    @login_required
    def logout():
        state["epoch"] += 1
        security_log.info("auth result=logout channel=web ip=%s", request.remote_addr or "unknown")
        logout_user()
        session.clear()
        return redirect(url_for("login_page.login"))

    app.register_blueprint(login_page)
    return authenticate
