"""On-demand monthly PDF from persisted runs, snapshots and capacity trends."""

import re
from collections import Counter
from datetime import date

from flask import Response, jsonify, render_template, request

from app.capacity import node_capacity_trend
from app.models import Anomaly, NodeCapacityObservation, Run

from . import db


def _deny_resource(url, timeout=10, ssl_context=None):
    raise ValueError("External and local resources are disabled for PDF rendering")


def monthly_report(snapshot):
    values = request.args.getlist("month")
    if len(values) != 1 or not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", values[0]):
        return jsonify(error="Invalid month"), 400
    month = values[0]
    try:
        start = date.fromisoformat(month + "-01")
    except ValueError:
        return jsonify(error="Invalid month"), 400
    end = (date(start.year + (start.month == 12), start.month % 12 + 1, 1)
           if (start.year, start.month) != (9999, 12) else None)
    query = Run.query.filter(Run.started_at >= start.isoformat())
    if end is not None:
        query = query.filter(Run.started_at < end.isoformat())
    runs = (query
            .order_by(Run.started_at, Run.id).all())
    if not runs:
        return jsonify(error="No runs for this month"), 404

    latest = runs[-1]
    anomaly_counts = Counter(row.code for row in Anomaly.query.filter_by(run_id=latest.id).all())
    successful = [run for run in runs if run.status == "SUCCESS"]
    current = successful[-1] if successful else None
    previous = (Run.query.filter(Run.status == "SUCCESS", Run.started_at < start.isoformat())
                .order_by(Run.started_at.desc(), Run.id.desc()).first())
    if previous is None and len(successful) > 1:
        previous = successful[0]
    appeared = disappeared = None
    if previous is not None and current is not None and previous.id != current.id:
        old, new = snapshot(previous.id), snapshot(current.id)
        appeared = [new[key] for key in sorted(new.keys() - old.keys())]
        disappeared = [old[key] for key in sorted(old.keys() - new.keys())]

    names = [name for (name,) in db.session.query(NodeCapacityObservation.node_capacity_name)
             .distinct().order_by(NodeCapacityObservation.node_capacity_name).all()]
    capacities = [node_capacity_trend(name) for name in names]

    from weasyprint import HTML

    html = render_template("monthly_report.html", month=month, run=latest,
                           anomalies=sorted(anomaly_counts.items()), previous=previous,
                           current=current, appeared=appeared, disappeared=disappeared,
                           capacities=capacities)
    pdf = HTML(string=html, url_fetcher=_deny_resource).write_pdf()
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="monthly-report-{month}.pdf"'
    })
