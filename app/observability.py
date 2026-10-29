"""Run-scoped JSON events and process-wide inventory metrics."""

import json
import logging
from datetime import UTC, datetime

from flask import Blueprint, Response, abort
from flask_login import current_user, login_required
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

metrics = Blueprint("metrics", __name__)

run_duration = Histogram(
    "cloudinventory_run_duration_seconds", "Duration of completed inventory runs in seconds"
)
run_anomalies = Counter(
    "cloudinventory_run_anomalies_total", "Anomalies detected in successful inventory runs"
)
collection_errors = Counter(
    "cloudinventory_collection_errors_total", "Source collection failures"
)


class RunJSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "event": record.msg,
            "run_id": record.run_id,
        })


class RunLogHandler(logging.StreamHandler):
    _cloudinventory_run = True


run_log = logging.getLogger("cloudinventory.run")
run_log.setLevel(logging.INFO)
run_log.propagate = False
if not any(getattr(handler, "_cloudinventory_run", False) for handler in run_log.handlers):
    handler = RunLogHandler()
    handler.setFormatter(RunJSONFormatter())
    run_log.addHandler(handler)


def log_run(event: str, run_id: int) -> None:
    run_log.info(event, extra={"run_id": run_id})


@metrics.get("/metrics")
@login_required
def metrics_page():
    if current_user.get_id() != "admin":
        abort(403)
    return Response(generate_latest(), content_type=CONTENT_TYPE_LATEST)
