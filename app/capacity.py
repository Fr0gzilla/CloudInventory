"""Node capacity trends from persisted observations, in source-defined units."""

import math
from datetime import UTC, datetime, timedelta

import numpy as np

from app.models import NodeCapacityObservation


def node_capacity_trend(name: str) -> dict:
    rows = (NodeCapacityObservation.query.filter_by(node_capacity_name=name)
            .order_by(NodeCapacityObservation.run_id, NodeCapacityObservation.id).all())
    result = {"node": name, "observations": len(rows), "usable_observations": 0,
              "trend": None, "saturation_at": None, "reason": None}
    points = []
    for row in rows:
        try:
            when = datetime.fromisoformat(row.node_capacity_measured_at)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            when = when.astimezone(UTC)
        except (TypeError, ValueError, OverflowError):
            continue
        value = row.node_capacity_value
        limit = row.node_capacity_limit
        if (value is not None and math.isfinite(value) and limit is not None
                and math.isfinite(limit) and limit > 0):
            points.append((when, value, limit))
    points.sort(key=lambda point: point[0])
    distinct = {}
    for point in points:
        distinct[point[0]] = point
    points = list(distinct.values())
    result["usable_observations"] = len(points)
    if len(points) < 2:
        result["reason"] = "insufficient_history"
        return result
    origin = points[0][0]
    days = np.array([(when - origin).total_seconds() / 86400 for when, _, _ in points])
    values = np.array([value for _, value, _ in points], dtype=float)
    if not np.isfinite(days).all() or not np.isfinite(values).all():
        result["reason"] = "invalid_observations"
        return result
    slope = float(np.polyfit(days, values, 1)[0])
    if not math.isfinite(slope):
        result["reason"] = "invalid_observations"
        return result
    result["trend"] = slope
    if slope <= np.finfo(float).eps * max(1.0, float(np.max(np.abs(values)))):
        result["reason"] = "non_increasing_trend"
        return result
    latest_time, latest_value, limit = points[-1]
    if limit is None or not math.isfinite(limit) or limit <= 0:
        result["reason"] = "missing_or_invalid_limit"
        return result
    if latest_value >= limit:
        result["saturation_at"] = latest_time.isoformat()
        return result
    horizon = (limit - latest_value) / slope
    if not math.isfinite(horizon) or horizon < 0:
        result["reason"] = "invalid_forecast"
        return result
    try:
        result["saturation_at"] = (latest_time + timedelta(days=horizon)).isoformat()
    except (OverflowError, ValueError):
        result["reason"] = "invalid_forecast"
    return result
