"""Authentification web — compte admin unique, CSRF, limitation des essais (RG25, RG26).

Le mot de passe admin n'est jamais conservé en clair : la configuration finale
ne porte que ``ADMIN_PASSWORD_HASH`` (haché à l'amorçage dans app/__init__.py).
Le jeton CSRF est un secret aléatoire lié à la session, comparé en temps constant.
"""
import hmac
import secrets
import time
from urllib.parse import urlparse

from flask import (
    Blueprint,
    current_app,
    flash,
    get_flashed_messages,
    redirect,
    render_template,
    render_template_string,
    request,
    session,
    url_for,
)
from flask_login import UserMixin, current_user, login_required, login_user, logout_user
from jinja2 import TemplateNotFound
from werkzeug.routing import BuildError
from werkzeug.security import check_password_hash

from app import login_manager

auth_bp = Blueprint("auth", __name__)

# Formulaire de connexion de repli, utilisé tant que templates/login.html
# n'existe pas (T008 livrera le template et le gabarit de base).
_LOGIN_FORM = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connexion — CloudInventory</title>
<style>
body { font-family: system-ui, sans-serif; margin: 3rem auto; max-width: 24rem; }
label { display: block; margin-bottom: .75rem; }
input { display: block; width: 100%; padding: .4rem; }
button { padding: .5rem 1rem; }
.error { color: #b00020; }
</style>
</head>
<body>
<h1>Connexion</h1>
{% for category, message in messages %}<p class="error">{{ message }}</p>{% endfor %}
<form method="post" action="{{ action }}">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <input type="hidden" name="next" value="{{ next }}">
  <label>Utilisateur <input type="text" name="username" autocomplete="username" required></label>
  <label>Mot de passe <input type="password" name="password" autocomplete="current-password" required></label>
  <button type="submit">Se connecter</button>
</form>
</body>
</html>"""

_PROFILE_FORM = """<!doctype html>
<html lang="fr">
<head><meta charset="utf-8"><title>Profil — CloudInventory</title></head>
<body>
<h1>Profil</h1>
<p>Connecté : {{ username }}</p>
<p><a href="{{ logout_url }}">Se déconnecter</a></p>
</body>
</html>"""


class User(UserMixin):
    """Utilisateur unique administrateur — aucun compte en base."""

    def __init__(self, username):
        self.id = username
        self.username = username


@login_manager.user_loader
def load_user(user_id):
    """Reconstruit l'utilisateur de session (identité portée par ADMIN_USERNAME)."""
    admin_username = current_app.config.get("ADMIN_USERNAME", "admin")
    if hmac.compare_digest(user_id.encode("utf-8"), admin_username.encode("utf-8")):
        return User(admin_username)
    return None


def generate_csrf():
    """Jeton CSRF de session : créé à la première demande, stable pour la session."""
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def _csrf_token_ok():
    """Vérifie le jeton CSRF du formulaire, comparaison en temps constant."""
    expected = session.get("csrf_token", "")
    provided = request.form.get("csrf_token", "")
    return bool(expected) and bool(provided) and hmac.compare_digest(
        expected.encode("utf-8"), provided.encode("utf-8")
    )


def _is_safe_redirect(target):
    """Interdit la redirection ouverte : URL relative, sans hôte ni antislash."""
    if not target:
        return False
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return False
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return False
    return not any(char in target for char in "\r\n")


def _default_target():
    """Cible après connexion, quand aucune demande sûre n'est fournie."""
    try:
        return url_for("main.dashboard")
    except BuildError:
        return "/"


def _attempts():
    """Compteur d'essais par application (recréé à chaque app, donc par test)."""
    return current_app.extensions.setdefault("login_attempts", {})


def _render_login():
    """Rendu du formulaire : template T008 s'il existe, sinon gabarit intégré."""
    next_target = request.args.get("next", "")
    context = {
        "csrf_token": generate_csrf(),
        "next": next_target,
        "action": url_for("auth.login", next=next_target),
    }
    try:
        return render_template("login.html", **context)
    except TemplateNotFound:
        return render_template_string(
            _LOGIN_FORM,
            messages=get_flashed_messages(with_categories=True),
            **context,
        )


@auth_bp.app_template_global()
def csrf_token():
    """Jeton CSRF exposé aux templates (champ de formulaire `csrf_token`)."""
    return generate_csrf()


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Connexion : CSRF d'abord (400), puis limitation des essais (429)."""
    if request.method == "POST":
        if not _csrf_token_ok():
            return "Jeton CSRF invalide ou manquant.", 400

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        next_page = request.args.get("next") or request.form.get("next") or ""
        if not _is_safe_redirect(next_page):
            next_page = ""

        store = _attempts()
        key = f"{request.remote_addr or '?'}|{username.lower()}"
        now = time.monotonic()
        entry = store.setdefault(key, {"count": 0, "locked_until": 0.0})
        if entry["locked_until"] > now:
            remaining = int(entry["locked_until"] - now) + 1
            response = current_app.response_class(
                "Trop de tentatives échouées, réessayez plus tard.", status=429
            )
            response.headers["Retry-After"] = str(remaining)
            return response
        if entry["locked_until"]:
            # Verrou expiré : nouvelle fenêtre d'essais.
            entry["count"] = 0
            entry["locked_until"] = 0.0

        admin_username = current_app.config.get("ADMIN_USERNAME", "admin")
        password_hash = current_app.config["ADMIN_PASSWORD_HASH"]
        # Les deux vérifications sont toujours exécutées : coût constant.
        username_ok = hmac.compare_digest(
            username.encode("utf-8"), admin_username.encode("utf-8")
        )
        password_ok = check_password_hash(password_hash, password)

        if username_ok and password_ok:
            store.pop(key, None)
            session.clear()
            login_user(User(admin_username))
            return redirect(next_page or _default_target())

        entry["count"] += 1
        if entry["count"] >= current_app.config["LOGIN_MAX_ATTEMPTS"]:
            entry["locked_until"] = now + current_app.config["LOGIN_LOCKOUT_SECONDS"]
        flash("Identifiants incorrects.", "danger")

    return _render_login()


@auth_bp.route("/logout")
def logout():
    """Déconnexion : efface la session puis renvoie au formulaire."""
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/profile")
@login_required
def profile():
    """Route protégée de référence (RG26) : sert de cible aux tests de redirection."""
    return render_template_string(
        _PROFILE_FORM,
        username=current_user.username,
        logout_url=url_for("auth.logout"),
    )
