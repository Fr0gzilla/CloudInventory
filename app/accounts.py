"""Local account administration. A disabled account retains its hash prefixed with disabled:.

The prefix is not a valid Argon2 hash; it preserves the password without a schema change
while ensuring neither web sessions nor previously issued JWTs can authenticate.
"""

from argon2 import PasswordHasher
from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required
from sqlalchemy.exc import IntegrityError

from . import db
from .models import Account

accounts = Blueprint("accounts", __name__)
hasher = PasswordHasher()
ROLES = ("admin", "opérateur", "lecteur")


@accounts.get("/accounts")
@login_required
def list_accounts():
    return render_template("accounts.html", accounts=Account.query.filter_by(origin_code="local").order_by(Account.id).all(), roles=ROLES)


@accounts.post("/accounts")
@login_required
def create_account():
    username = request.form.get("username")
    password = request.form.get("password")
    role = request.form.get("role")
    if (not isinstance(username, str) or not username.strip() or username != username.strip()
            or len(username) > 128 or any(ord(c) < 32 for c in username)
            or not isinstance(password, str) or not 12 <= len(password) <= 4096 or role not in ROLES):
        abort(400)
    if Account.query.filter_by(origin_code="local", username=username).first():
        abort(400)
    db.session.add(Account(origin_code="local", username=username, role_code=role,
                           local_password_hash=hasher.hash(password)))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        abort(400)
    return redirect(url_for("accounts.list_accounts"))


@accounts.post("/accounts/<int:id>")
@login_required
def update_account(id):
    action = request.form.get("action")
    role = request.form.get("role")
    password = request.form.get("password")
    if (action not in ("role", "disable", "password") or
            (action == "role" and role not in ROLES) or
            (action == "password" and (not isinstance(password, str) or not 12 <= len(password) <= 4096))):
        abort(400)
    account = db.session.get(Account, id)
    if account is None or account.origin_code != "local":
        abort(404)
    active = not account.local_password_hash.startswith("disabled:")
    if not active:
        abort(400)
    if account.role_code == "admin" and (action == "disable" or
                                          action == "role" and role != "admin"):
        others = Account.query.filter(Account.role_code == "admin", Account.id != id,
                                      Account.origin_code == "local",
                                      ~Account.local_password_hash.startswith("disabled:")).count()
        if not others:
            abort(400)
    if action == "role":
        account.role_code = role
    elif action == "disable":
        account.local_password_hash = "disabled:" + account.local_password_hash
    else:
        account.local_password_hash = hasher.hash(password)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        current_app.logger.exception("Could not update account")
        abort(500)
    return redirect(url_for("accounts.list_accounts"))
