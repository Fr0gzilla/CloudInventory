"""Optional post-commit anomaly alerts; mock transports are the default."""

import json
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)
_TIMEOUT = 5


def _enabled(name: str, default: str = "false") -> bool:
    value = os.environ.get(name, default).lower()
    if value not in ("true", "false"):
        raise ValueError("invalid notification switch")
    return value == "true"


def _address(name: str) -> str:
    value = os.environ.get(name, "")
    if not value or parseaddr(value) != ("", value) or "\r" in value or "\n" in value:
        raise ValueError("invalid mail address")
    return value


def _smtp(message: EmailMessage) -> None:
    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "465"))
    username = os.environ.get("SMTP_USERNAME", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    if not host or not 1 <= port <= 65535 or bool(username) != bool(password):
        raise ValueError("invalid SMTP configuration")
    with smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT, context=ssl.create_default_context()) as client:
        if username:
            client.login(username, password)
        client.send_message(message)


def _webhook(payload: dict[str, object]) -> None:
    url = os.environ.get("WEBHOOK_URL", "")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid webhook configuration")
    request = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    # HTTPS scheme and host are validated above before the request is made.
    with urlopen(request, timeout=_TIMEOUT) as response:  # nosec B310
        response.read()


def _mock_smtp(message: EmailMessage) -> None:
    """Simulated mail delivery; replace with a stub to inspect alerts."""


def _mock_webhook(payload: dict[str, object]) -> None:
    """Simulated HTTP delivery; replace with a stub to inspect alerts."""


def notify_anomalies(run_id: int, anomaly_count: int, finished_at: str) -> None:
    """Send each enabled channel independently, never raising delivery errors."""
    if anomaly_count <= 0:
        return
    payload: dict[str, object] = {
        "run_id": run_id, "status": "SUCCESS", "anomaly_count": anomaly_count,
        "finished_at": finished_at,
    }
    try:
        if _enabled("SMTP_ENABLED"):
            message = EmailMessage()
            message["Subject"] = f"Inventory run {run_id}: {anomaly_count} anomalies"
            if _enabled("USE_MOCK_SMTP", "true"):
                message.set_content(f"Run {run_id} completed with {anomaly_count} anomalies at {finished_at}.")
                _mock_smtp(message)
            else:
                message["From"] = _address("SMTP_FROM")
                message["To"] = _address("SMTP_TO")
                message.set_content(f"Run {run_id} completed with {anomaly_count} anomalies at {finished_at}.")
                _smtp(message)
    except Exception:  # noqa: BLE001 - delivery must not alter a committed run
        logger.warning("SMTP notification failed")
    try:
        if _enabled("WEBHOOK_ENABLED"):
            if _enabled("USE_MOCK_WEBHOOK", "true"):
                _mock_webhook(payload)
            else:
                _webhook(payload)
    except Exception:  # noqa: BLE001 - delivery must not alter a committed run
        logger.warning("Webhook notification failed")
