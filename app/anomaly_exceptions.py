"""Evaluate justified exceptions without changing persisted anomaly state."""

import json
from datetime import UTC, datetime

from flask import request
from flask_jwt_extended import get_jwt_identity
from flask_login import current_user

from app import db
from app.models import Account, Anomaly, AnomalyException

CODES = frozenset({
    "NO_MATCH", "MATCHED_IP", "HOSTNAME_MISMATCH", "STATUS_MISMATCH",
    "DUPLICATE_DNS", "DUPLICATE_IP", "RENAMED",
})


def _expiration(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("Invalid exception")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("Invalid exception") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Invalid exception")
    return parsed.astimezone(UTC)


def exception_row(row):
    return {key: getattr(row, key) for key in (
        "id", "code", "target", "justification", "expires_at", "author",
    )}


def list_exceptions():
    return [exception_row(row) for row in AnomalyException.query.order_by(AnomalyException.id).all()]


def create_exception(data):
    if not isinstance(data, dict) or set(data) != {"code", "target", "justification", "expires_at"}:
        raise ValueError("Invalid exception")
    code, target, justification = (data[key] for key in ("code", "target", "justification"))
    if (not isinstance(code, str) or code not in CODES or
            not isinstance(target, str) or not target.strip() or len(target) > 255 or
            target != target.strip() or any(ord(char) < 32 or ord(char) == 127 for char in target) or
            not isinstance(justification, str) or not justification.strip() or
            len(justification) > 2000 or "\x00" in justification):
        raise ValueError("Invalid exception")
    expires = _expiration(data["expires_at"])
    if expires <= datetime.now(UTC):
        raise ValueError("Invalid exception")
    key = json.dumps([code, target])
    if not Anomaly.query.filter_by(tracking_key=key).first():
        raise ValueError("Invalid exception")
    identity = get_jwt_identity() if request.path.startswith("/api/") else (
        current_user.get_id() if current_user.is_authenticated else None
    )
    account = db.session.get(Account, int(identity)) if isinstance(identity, str) and identity.isdecimal() else None
    if (account is None or account.origin_code != "local" or
            account.local_password_hash.startswith("disabled:") or
            account.role_code not in ("admin", "opérateur")):
        raise ValueError("Invalid exception")
    row = AnomalyException(code=code, target=target, justification=justification.strip(),
                           expires_at=expires.isoformat(), author=account.username)
    db.session.add(row)
    db.session.commit()
    return exception_row(row)


def delete_exception(id):
    row = db.session.get(AnomalyException, id)
    if row is None:
        return False
    db.session.delete(row)
    db.session.commit()
    return True


def anomaly_coverage(anomalies, now=None):
    """Return coverage by anomaly ID; a shared group key is never silently covered."""
    now = now or datetime.now(UTC)
    rows = list(anomalies)
    keys = [row.tracking_key for row in rows if row.tracking_key]
    if not keys:
        return {row.id: None for row in rows}
    all_rows = Anomaly.query.filter(Anomaly.run_id.in_({row.run_id for row in rows}),
                                    Anomaly.tracking_key.in_(keys)).all()
    counts = {}
    for row in all_rows:
        identity = (row.run_id, row.tracking_key)
        counts[identity] = counts.get(identity, 0) + 1
    active = {}
    for exception in AnomalyException.query.filter(AnomalyException.code.in_({row.code for row in rows})).all():
        try:
            expiry = _expiration(exception.expires_at)
        except ValueError:
            continue
        if expiry > now:
            key = json.dumps([exception.code, exception.target])
            active.setdefault(key, exception.id)
    return {row.id: active.get(row.tracking_key) if row.tracking_key and
            counts.get((row.run_id, row.tracking_key)) == 1 else None for row in rows}
