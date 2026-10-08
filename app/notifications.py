"""Notifications du dernier inventaire, sans nouvelle détection d'anomalies."""
import json
import logging
import os
import smtplib
import ssl
from collections import Counter
from email.mime.text import MIMEText
from html import escape
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from flask import current_app
from flask_login import current_user

from app.extensions import db
from app.models import Anomaly, Asset, IpamRecord, Run
from app.queries import get_stats_data

logger = logging.getLogger("cloudinventory.notifications")


def _setting(name, default=""):
    return current_app.config.get(name, os.getenv(name, default))


def _enabled(name, default="false"):
    return str(_setting(name, default)).lower() == "true"


def notification_context() -> dict:
    """Contexte commun au badge et au bandeau, réservé aux utilisateurs connectés."""
    context = {
        "navbar_anomaly_count": 0,
        "navbar_last_run_id": None,
        "navbar_anomaly_details": {},
    }
    if not current_user.is_authenticated:
        return context
    stats = get_stats_data()
    last_run = db.session.query(Run).order_by(Run.id.desc()).first()
    details = stats.get("anomalies", {})
    context.update(
        navbar_anomaly_count=sum(details.values()),
        navbar_last_run_id=last_run.id if last_run else None,
        navbar_anomaly_details=details,
    )
    return context


def register_notifications() -> None:
    """Enregistre une seule fois le contexte global avant les blueprints."""
    from app.routes import main_bp

    if not getattr(main_bp, "_notifications_registered", False):
        main_bp.app_context_processor(notification_context)
        main_bp._notifications_registered = True


def _html(value):
    return escape(str(value if value is not None else ""), quote=True)


def render_email(run: Run, rows: list) -> str:
    """Échappe chaque valeur issue de la collecte, y compris les anomalies IPAM seules."""
    details = Counter(anomaly.code for anomaly, asset, ipam in rows)
    counts = [run.matched_name_count or 0, run.matched_fqdn_count or 0,
              run.matched_ip_count or 0, run.no_match_count or 0]
    total = sum(counts)
    rate = round(sum(counts[:3]) / total * 100, 1) if total else 0
    summary = "".join(f"<li>{_html(code)} : {_html(count)}</li>"
                      for code, count in sorted(details.items()))
    table = "".join(
        "<tr>" + "".join(f"<td>{_html(value)}</td>" for value in (
            anomaly.code, asset.vm_name if asset else "",
            ipam.dns_name if ipam else "", anomaly.description,
        )) + "</tr>"
        for anomaly, asset, ipam in rows
    )
    metrics = "".join(f"<li>{label} : {_html(value)}</li>" for label, value in zip(
        ("Match nom", "Match FQDN", "Match IP", "Sans correspondance"), counts
    ))
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
        '<h1>CloudInventory — Rapport d’inventaire</h1>'
        f'<p>Run #{_html(run.id)} — {_html(run.status)} — {_html(run.end_date)}</p>'
        f'<p>{_html(len(rows))} anomalies détectées ; {_html(total)} VMs collectées.</p>'
        f'<ul>{metrics}</ul><p>Taux de correspondance : {_html(rate)} %</p>'
        f'<h2>Anomalies par type</h2><ul>{summary}</ul>'
        '<table><thead><tr><th>Type</th><th>VM</th><th>DNS</th><th>Description</th>'
        f'</tr></thead><tbody>{table}</tbody></table></body></html>'
    )


def notify_email(run: Run, rows: list) -> None:
    """SMTP HTML facultatif ; les erreurs restent confinées à ce canal."""
    try:
        if not rows or not _enabled("SMTP_ENABLED") or not _setting("SMTP_TO"):
            return
        message = MIMEText(render_email(run, rows), "html", "utf-8")
        message["Subject"] = f"[CloudInventory] Run #{run.id} — {len(rows)} anomalies"
        message["From"] = _setting("SMTP_FROM", "cloudinventory@localhost")
        message["To"] = _setting("SMTP_TO")
        with smtplib.SMTP(_setting("SMTP_HOST", "localhost"),
                          int(_setting("SMTP_PORT", "587")), timeout=15) as server:
            if _enabled("SMTP_USE_TLS", "true"):
                server.starttls(context=ssl.create_default_context())
            username = _setting("SMTP_USERNAME")
            password = _setting("SMTP_PASSWORD")
            if username and password:
                server.login(username, password)
            server.send_message(message)
    except Exception:
        logger.warning("Échec de notification SMTP ; inventaire conservé")


def notify_webhook(run: Run, anomaly_count: int) -> None:
    """POST JSON compatible avec la référence, délai borné, aucun secret journalisé."""
    try:
        url = _setting("WEBHOOK_URL")
        if not url or not anomaly_count:
            return
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("URL webhook invalide")
        total = sum((run.matched_name_count or 0, run.matched_fqdn_count or 0,
                     run.matched_ip_count or 0, run.no_match_count or 0))
        payload = {
            "text": (f"CloudInventory Run #{run.id} terminé — "
                     f"{total} VMs, {anomaly_count} anomalies détectées"),
            "run_id": run.id,
            "status": run.status,
            "anomaly_count": anomaly_count,
        }
        request = Request(url, data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise ValueError("Réponse webhook en échec")
    except Exception:
        logger.warning("Échec de notification webhook ; inventaire conservé")


def notify_run(run: Run) -> None:
    """Appelé seulement après commit ; réutilise les anomalies du pipeline."""
    if run.status != "SUCCESS":
        return
    rows = (
        db.session.query(Anomaly, Asset, IpamRecord)
        .outerjoin(Asset, Anomaly.asset_id == Asset.id)
        .outerjoin(IpamRecord, Anomaly.ipam_record_id == IpamRecord.id)
        .filter(Anomaly.run_id == run.id)
        .order_by(Anomaly.code, Anomaly.id)
        .all()
    )
    if rows:
        notify_email(run, rows)
        notify_webhook(run, len(rows))
