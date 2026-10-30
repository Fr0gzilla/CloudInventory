"""Persisted node capacity observations and their public forecasts."""

import os
from datetime import UTC, datetime

import pytest

from app import db
from app.capacity import node_capacity_trend
from app.models import NodeCapacityObservation, Run
from collector.inventory_runner import run_inventory


def observation(run_id, node, date, value, limit):
    db.session.add(NodeCapacityObservation(
        run_id=run_id, node_capacity_name=node, node_capacity_measured_at=date,
        node_capacity_value=value, node_capacity_limit=limit,
    ))


def history(node, measurements):
    for index, (date, value, limit) in enumerate(measurements):
        run = Run(status="SUCCESS", started_at=f"2026-01-{index + 1:02d}T00:00:00Z")
        db.session.add(run)
        db.session.flush()
        observation(run.id, node, date, value, limit)
    db.session.commit()


def test_simulated_runs_keep_one_capacity_measurement_per_node_and_run(orm_app):
    first = run_inventory()
    second = run_inventory()
    rows = NodeCapacityObservation.query.order_by(NodeCapacityObservation.run_id).all()
    grouped = {run.id: {row.node_capacity_name for row in rows if row.run_id == run.id}
               for run in (first, second)}
    assert grouped[first.id] == grouped[second.id] and len(grouped[first.id]) > 0
    assert len(rows) == 2 * len(grouped[first.id])


def test_increasing_history_predicts_exact_saturation_date_using_numpy(orm_app, monkeypatch):
    import app.capacity as capacity

    calls = []
    original = capacity.np.polyfit

    def record_polyfit(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(capacity.np, "polyfit", record_polyfit)
    history("alpha", [("2026-01-01T00:00:00Z", 20, 100),
                      ("2026-01-11T00:00:00Z", 40, 100)])
    result = node_capacity_trend("alpha")
    assert result["trend"] == pytest.approx(2)
    assert datetime.fromisoformat(result["saturation_at"]) == datetime(2026, 2, 10, tzinfo=UTC)
    assert calls == [True]


def test_nodes_have_independent_forecasts(orm_app):
    history("alpha", [("2026-01-01T00:00:00Z", 10, 100),
                      ("2026-01-11T00:00:00Z", 20, 100)])
    history("beta", [("2026-01-01T00:00:00Z", 80, 100),
                     ("2026-01-11T00:00:00Z", 70, 100)])
    assert node_capacity_trend("alpha")["saturation_at"] is not None
    assert node_capacity_trend("beta")["reason"] == "non_increasing_trend"


def test_insufficient_history_has_no_forecast(orm_app):
    history("alpha", [("2026-01-01T00:00:00Z", 20, 100)])
    result = node_capacity_trend("alpha")
    assert (result["reason"], result["saturation_at"]) == ("insufficient_history", None)


@pytest.mark.parametrize("values", [(60, 40), (40, 40)], ids=["decreasing", "flat"])
def test_non_increasing_history_has_no_forecast(orm_app, values):
    history("alpha", [("2026-01-01T00:00:00Z", values[0], 100),
                      ("2026-01-11T00:00:00Z", values[1], 100)])
    result = node_capacity_trend("alpha")
    assert (result["reason"], result["saturation_at"]) == ("non_increasing_trend", None)


def test_missing_values_are_retained_but_not_used(orm_app):
    history("alpha", [("2026-01-01T00:00:00Z", 20, 100),
                      ("2026-01-06T00:00:00Z", None, 100),
                      ("2026-01-11T00:00:00Z", 40, 100)])
    result = node_capacity_trend("alpha")
    assert (result["observations"], result["usable_observations"]) == (3, 2)
    assert result["saturation_at"] == "2026-02-10T00:00:00+00:00"


def test_missing_limits_are_retained_but_excluded_from_regression(orm_app):
    history("alpha", [("2026-01-01T00:00:00Z", 20, 100),
                      ("2026-01-06T00:00:00Z", 95, None),
                      ("2026-01-11T00:00:00Z", 40, 100)])
    result = node_capacity_trend("alpha")
    assert (result["observations"], result["usable_observations"]) == (3, 2)
    assert result["saturation_at"] == "2026-02-10T00:00:00+00:00"


def test_missing_latest_limit_prevents_forecast(orm_app):
    history("alpha", [("2026-01-01T00:00:00Z", 20, 100),
                      ("2026-01-11T00:00:00Z", 40, None)])
    result = node_capacity_trend("alpha")
    assert result["saturation_at"] is None


def test_identical_dates_cannot_produce_forecast(orm_app):
    history("alpha", [("2026-01-01T00:00:00Z", 20, 100),
                      ("2026-01-01T00:00:00Z", 40, 100)])
    result = node_capacity_trend("alpha")
    assert (result["reason"], result["saturation_at"]) == ("insufficient_history", None)


@pytest.mark.parametrize("date,value,limit", [
    ("not-a-date", 40, 100), ("2026-01-11T00:00:00Z", float("inf"), 100),
    ("2026-01-11T00:00:00Z", 40, float("inf")),
], ids=["invalid-date", "nonfinite-value", "nonfinite-limit"])
def test_invalid_measurement_cannot_invent_saturation(orm_app, date, value, limit):
    history("alpha", [("2026-01-01T00:00:00Z", 20, 100), (date, value, limit)])
    assert node_capacity_trend("alpha")["saturation_at"] is None


def test_capacity_api_requires_bearer_and_returns_node_json(orm_app):
    history("alpha", [("2026-01-01T00:00:00Z", 20, 100),
                      ("2026-01-11T00:00:00Z", 40, 100)])
    client = orm_app.test_client()
    assert client.get("/api/capacity/nodes").status_code == 401
    token = client.post("/api/login", json={"username": "admin", "password": os.environ["ADMIN_PASSWORD"]}).get_json()["access_token"]
    response = client.get("/api/capacity/nodes", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.get_json()["items"] == [node_capacity_trend("alpha")]
