import hmac
import logging
import math
import secrets
import time
import unicodedata
from threading import Lock
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import (
    Blueprint,
    abort,
    g,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import UserMixin, login_required, login_user, logout_user
from werkzeug.security import check_password_hash

from . import login_manager

security_log = logging.getLogger("security.auth")
security_log.setLevel(logging.INFO)
if not any(getattr(handler, "_cloudinventory_auth", False) for handler in security_log.handlers):
    handler = logging.StreamHandler()
    handler._cloudinventory_auth = True
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%SZ")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    security_log.addHandler(handler)


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
    attempts_lock = Lock()
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
        ip = request.remote_addr or "unknown"
        valid_user = hmac.compare_digest(supplied_user.encode("utf-8", errors="surrogatepass"),
                                         username.encode("utf-8", errors="surrogatepass"))
        normalized_user = unicodedata.normalize("NFKC", supplied_user).strip().casefold()[:128]
        bucket = (ip, "admin") if valid_user else (ip, "other", normalized_user)
        others = (ip, "others")
        with attempts_lock:
            now = time.monotonic()
            for entry_key, entry in list(attempts.items()):
                if entry_key[1] != "admin" and now - entry[0] >= window:
                    del attempts[entry_key]
            if valid_user:
                started, count, next_at = attempts.get(bucket, (now, 0, now))
                remaining = next_at - now if count >= limit else 0
            else:
                started, count = attempts.get(bucket, (now, 0))
                global_started, global_count = attempts.get(others, (now, 0))
                remaining = max((started + window - now if count >= limit else 0),
                                (global_started + window - now if global_count >= 20 else 0))
            if remaining > 0:
                g.auth_retry_after = max(1, math.ceil(remaining))
                security_log.warning("auth result=limited channel=%s ip=%s", channel, ip)
                return "limited"
            if password_hash.startswith("$argon2"):
                try:
                    valid_password = hasher.verify(password_hash, supplied_password)
                except (InvalidHashError, VerificationError):
                    valid_password = False
            else:
                valid_password = check_password_hash(password_hash, supplied_password)
            if not (valid_user and valid_password):
                if valid_user:
                    failures = count + 1
                    delay = min(900, 30 * 2 ** min(max(0, failures - limit), 5)) if failures >= limit else 0
                    attempts[bucket] = (started, failures, now + delay)
                else:
                    attempts[bucket] = (started, count + 1)
                    attempts[others] = (global_started, global_count + 1)
                security_log.warning("auth result=invalid channel=%s ip=%s", channel, ip)
                return "invalid"
            if not password_hash.startswith("$argon2") or hasher.check_needs_rehash(password_hash):
                password_hash = hasher.hash(supplied_password)
            attempts.pop(bucket, None)
            security_log.info("auth result=success channel=%s ip=%s", channel, ip)
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
            response = make_response("Too Many Requests", 429)
            response.headers["Retry-After"] = str(g.auth_retry_after)
            return response
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
