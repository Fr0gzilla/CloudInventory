"""Transactional, append-only audit of effective domain actions."""

from contextvars import ContextVar
from datetime import UTC, datetime

from flask import Blueprint, abort, g, has_request_context, render_template, request
from flask_jwt_extended import get_jwt_identity
from flask_login import current_user, login_required
from sqlalchemy import event, inspect, select

from app import db
from app.models import Account, Anomaly, AnomalyException, Audit, Run, RunSchedule

audit = Blueprint("audit", __name__)
scheduled_run = ContextVar("scheduled_run", default=None)


def record_action(connection, action, target):
    """Insert only allowlisted action metadata in the caller's transaction."""
    account_id = None
    identity = None
    if has_request_context():
        if request.path.startswith("/api/") and getattr(g, "_jwt_extended_jwt", None) is not None:
            identity = get_jwt_identity()
        elif not request.path.startswith("/api/") and current_user.is_authenticated:
            identity = current_user.get_id()
    if isinstance(identity, str) and identity.isdecimal():
        account = connection.execute(
            select(Account.id, Account.origin_code, Account.username).where(Account.id == int(identity))
        ).first()
        if account is not None:
            account_id = account.id
            actor = f"{account.origin_code}:{account.username}"
        else:
            actor = "système"
    else:
        actor = "système"
    connection.execute(Audit.__table__.insert().values(
        account_id=account_id, actor_identity=actor, action=action,
        created_at=datetime.now(UTC).isoformat(), target=target,
    ))


@event.listens_for(Run, "after_insert")
def _run_started(mapper, connection, row):
    schedule_id = scheduled_run.get()
    action = "run planifié" if schedule_id is not None else "run manuel"
    record_action(connection, action, f"run:{row.id}")


@event.listens_for(Anomaly, "after_update")
def _anomaly_changed(mapper, connection, row):
    state = inspect(row).attrs.state
    if state.history.has_changes() and row.state in ("acquittée", "résolue"):
        record_action(connection, "anomalie acquittée" if row.state == "acquittée" else "anomalie résolue",
                      f"anomalie:{row.id}")


@event.listens_for(AnomalyException, "after_insert")
def _exception_created(mapper, connection, row):
    record_action(connection, "exception créée", f"exception:{row.id}")


@event.listens_for(AnomalyException, "after_delete")
def _exception_removed(mapper, connection, row):
    record_action(connection, "exception supprimée", f"exception:{row.id}")


@event.listens_for(RunSchedule, "after_insert")
def _schedule_created(mapper, connection, row):
    record_action(connection, "planification créée", f"planification:{row.id}")


@event.listens_for(RunSchedule, "after_update")
def _schedule_changed(mapper, connection, row):
    if any(inspect(row).attrs[key].history.has_changes() for key in ("frequency", "enabled")):
        record_action(connection, "planification modifiée", f"planification:{row.id}")


@event.listens_for(Account, "after_insert")
def _account_created(mapper, connection, row):
    record_action(connection, "compte créé", f"compte:{row.id}")


@event.listens_for(Account, "after_update")
def _account_changed(mapper, connection, row):
    changes = inspect(row).attrs
    if changes.role_code.history.has_changes():
        action = "compte modifié"
    elif changes.local_password_hash.history.has_changes() and has_request_context() and request.endpoint == "accounts.update_account":
        action = "compte désactivé" if row.local_password_hash.startswith("disabled:") else "compte réinitialisé"
    else:
        return
    record_action(connection, action, f"compte:{row.id}")


@audit.get("/audit")
@login_required
def audit_page():
    from flask_login import current_user

    if current_user.role_code != "admin":
        abort(403)
    actor = request.args.get("actor", "")
    action = request.args.get("action", "")
    target = request.args.get("target", "")
    since = request.args.get("since", "")
    until = request.args.get("until", "")
    if (set(request.args) - {"actor", "action", "target", "since", "until"} or
            any(len(value) > 128 for value in (actor, action, target, since, until)) or
            any(value and not _valid_date(value) for value in (since, until))):
        abort(400)
    query = db.session.query(Audit)
    if actor:
        query = query.filter(Audit.actor_identity == actor)
    if action:
        query = query.filter(Audit.action == action)
    if target:
        query = query.filter(Audit.target == target)
    if since:
        query = query.filter(Audit.created_at >= since)
    if until:
        query = query.filter(Audit.created_at < until + "T~")
    rows = query.order_by(Audit.id.desc()).limit(200).all()
    return render_template("audit.html", rows=rows, actor=actor, action=action,
                           target=target, since=since, until=until)


def _valid_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        return False
