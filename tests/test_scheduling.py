import logging
import os
import socket
from threading import Event

import pytest

from app import create_app, db
from app.models import Run, RunSchedule
from app.scheduling import fire_schedule


@pytest.fixture
def schedule_app(monkeypatch, tmp_path):
    for key in ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD"):
        monkeypatch.setenv(key, f"test-schedule-{key}")
    monkeypatch.setenv("EXPORT_DIRECTORY", str(tmp_path / "exports"))
    uri = f"sqlite:///{tmp_path / 'schedules.sqlite3'}"
    def no_network(*args, **kwargs):
        raise AssertionError("network access forbidden")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    app = create_app(database_uri=uri)
    yield app, uri
    scheduler = app.extensions.get("inventory_scheduler")
    if scheduler:
        scheduler.shutdown(wait=False)


def admin(app):
    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        token = session["csrf_token"]
    assert client.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"],
                                       "csrf_token": token}).status_code == 302
    with client.session_transaction() as session:
        return client, session["csrf_token"]


def save(client, token, frequency="horaire", enabled="1", **extra):
    return client.post("/scheduling", data={"csrf_token": token, "frequency": frequency,
                                           "enabled": enabled, **extra})


@pytest.mark.parametrize("frequency", ["horaire", "quotidien"])
def test_creates_schedules_at_requested_frequency(schedule_app, frequency):
    app, _ = schedule_app
    client, token = admin(app)
    assert save(client, token, frequency).status_code == 302
    with app.app_context():
        row = db.session.query(RunSchedule).one()
        assert (row.frequency, row.enabled) == (frequency, 1)


def test_edit_and_disable_schedule_synchronizes_jobs(schedule_app, monkeypatch):
    app, _ = schedule_app
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    app = create_app(database_uri=schedule_app[1])
    try:
        client, token = admin(app)
        assert save(client, token).status_code == 302
        with app.app_context():
            identifier = db.session.query(RunSchedule).one().id
        scheduler = app.extensions["inventory_scheduler"]
        assert scheduler.get_job(f"schedule-{identifier}") is not None
        assert save(client, token, "quotidien", "1", schedule_id=str(identifier)).status_code == 302
        job = scheduler.get_job(f"schedule-{identifier}")
        assert job.trigger.fields[5].__str__() == "0"
        assert save(client, token, "quotidien", "0", schedule_id=str(identifier)).status_code == 302
        with app.app_context():
            row = db.session.get(RunSchedule, identifier)
            assert (row.frequency, row.enabled, row.next_at, scheduler.get_job(f"schedule-{identifier}")) == (
                "quotidien", 0, None, None)
    finally:
        app.extensions["inventory_scheduler"].shutdown(wait=False)


def test_anonymous_and_missing_csrf_cannot_create_schedules(schedule_app):
    app, _ = schedule_app
    client = app.test_client()
    assert client.get("/scheduling").status_code == 302
    assert save(client, "invalid").status_code == 302
    client, token = admin(app)
    assert save(client, "invalid").status_code == 400
    with app.app_context():
        assert db.session.query(RunSchedule).count() == 0


@pytest.mark.parametrize("fields,status", [({"frequency": "weekly"}, 400),
                                            ({"enabled": "maybe"}, 400),
                                            ({"schedule_id": "bad"}, 400),
                                            ({"schedule_id": "999"}, 404)])
def test_invalid_schedule_input_does_not_mutate_existing_row(schedule_app, fields, status):
    app, _ = schedule_app
    client, token = admin(app)
    save(client, token)
    assert save(client, token, **fields).status_code == status
    with app.app_context():
        assert [(row.frequency, row.enabled) for row in db.session.query(RunSchedule).all()] == [("horaire", 1)]


def test_disabled_scheduler_has_no_background_jobs(schedule_app):
    app, _ = schedule_app
    assert "inventory_scheduler" not in app.extensions


def test_restart_restores_persisted_enabled_job(schedule_app, monkeypatch):
    app, uri = schedule_app
    client, token = admin(app)
    save(client, token, "horaire", "1")
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    restarted = create_app(database_uri=uri)
    try:
        with restarted.app_context():
            row = db.session.query(RunSchedule).one()
            assert restarted.extensions["inventory_scheduler"].get_job(f"schedule-{row.id}") is not None
            assert row.next_at is not None
    finally:
        restarted.extensions["inventory_scheduler"].shutdown(wait=False)


def test_due_schedule_uses_real_async_pipeline_and_records_provenance(schedule_app, monkeypatch):
    import collector.async_runner as runner
    app, _ = schedule_app
    client, token = admin(app)
    save(client, token)
    reached, release = Event(), Event()
    original = runner._run_inventory
    original_start = runner.threading.Thread.start
    threads = []

    def pause(*args, **kwargs):
        reached.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    def capture(thread):
        threads.append(thread)
        original_start(thread)

    monkeypatch.setattr(runner, "_run_inventory", pause)
    monkeypatch.setattr(runner.threading.Thread, "start", capture)
    try:
        with app.app_context():
            identifier = db.session.query(RunSchedule).one().id
        fire_schedule(app, identifier)
        assert reached.wait(10)
        with app.app_context():
            db.session.remove()
            run = db.session.query(Run).one()
            assert (run.run_trigger, run.schedule_id) == ("planifié", identifier)
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()


def test_due_schedule_under_active_lock_is_skipped_and_logged(schedule_app, caplog):
    from collector.run_lock import run_lock
    app, _ = schedule_app
    client, token = admin(app)
    save(client, token)
    with app.app_context():
        identifier = db.session.query(RunSchedule).one().id
    with app.app_context():
        with run_lock(), caplog.at_level(logging.INFO):
            fire_schedule(app, identifier)
    with app.app_context():
        assert db.session.query(Run).count() == 0
    assert any("skipped" in record.message and str(identifier) in record.message for record in caplog.records)
