import os
import sqlite3
from threading import Event, Thread

import pytest

from app import create_app, db
from app.models import Asset, Run


@pytest.fixture
def progress_app(monkeypatch, tmp_path):
    for key in ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD"):
        monkeypatch.setenv(key, f"test-{key.lower()}")
    monkeypatch.setenv("EXPORT_DIRECTORY", str(tmp_path / "exports"))
    return create_app(database_uri=f"sqlite:///{tmp_path / 'progress.sqlite3'}")


def admin_client(app):
    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    assert client.post("/login", data={"username": "admin", "password": os.environ["ADMIN_PASSWORD"],
                                       "csrf_token": csrf}).status_code == 302
    with client.session_transaction() as session:
        csrf = session["csrf_token"]
    return client, csrf


@pytest.mark.parametrize("step", range(1, 8))
def test_each_completed_step_is_visible_from_independent_session(progress_app, monkeypatch, step):
    import collector.async_runner as async_runner

    reached, release = Event(), Event()
    threads = []
    start = async_runner.threading.Thread.start
    original = async_runner._run_inventory

    def capture(thread):
        threads.append(thread)
        start(thread)

    def paused(*args, **kwargs):
        def checkpoint(number):
            if number == step:
                reached.set()
                assert release.wait(10)
        return original(*args, after_step=checkpoint, **kwargs)

    monkeypatch.setattr(async_runner.threading.Thread, "start", capture)
    monkeypatch.setattr(async_runner, "_run_inventory", paused)
    client, csrf = admin_client(progress_app)
    try:
        response = client.post("/dashboard/run", data={"csrf_token": csrf})
        data = response.get_json()
        assert response.status_code == 202
        assert reached.wait(10)
        with progress_app.app_context():
            db.session.remove()
            row = db.session.get(Run, data["id"])
            assert (row.current_step, row.completed_steps) == (step, step)
        if step < 7:
            status = client.get(data["status_url"]).get_json()
            assert (status["status"], status["completed_steps"], status["total_steps"],
                    status["redirect_url"]) == ("RUNNING", step, 7, None)
            assert client.post("/dashboard/run", data={"csrf_token": csrf}).status_code == 409
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
    with progress_app.app_context():
        db.session.remove()
        assert db.session.get(Run, data["id"]).status == "SUCCESS"


@pytest.mark.parametrize("step", range(1, 8))
def test_failure_preserves_last_step_and_removes_observations(progress_app, monkeypatch, step):
    import collector.async_runner as async_runner

    threads = []
    start = async_runner.threading.Thread.start
    original = async_runner._run_inventory

    def capture(thread):
        threads.append(thread)
        start(thread)

    def fails(*args, **kwargs):
        def checkpoint(number):
            if number == step:
                raise RuntimeError("step failed")
        return original(*args, after_step=checkpoint, **kwargs)

    monkeypatch.setattr(async_runner.threading.Thread, "start", capture)
    monkeypatch.setattr(async_runner, "_run_inventory", fails)
    client, csrf = admin_client(progress_app)
    try:
        response = client.post("/dashboard/run", data={"csrf_token": csrf})
        assert response.status_code == 202
    finally:
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
    data = client.get(response.get_json()["status_url"]).get_json()
    with progress_app.app_context():
        db.session.remove()
        count = Asset.query.filter_by(run_id=data["id"]).count()
    assert (data["status"], data["completed_steps"], count) == ("FAIL", step, 0)


def test_status_requires_admin_session_and_unknown_run_is_404(progress_app):
    client, _ = admin_client(progress_app)
    assert progress_app.test_client().get("/runs/999/status").status_code == 302
    response = client.get("/runs/999/status")
    assert (response.status_code, response.get_json()) == (404, {"error": "Run not found"})


def observation_counts(path, identifier):
    with sqlite3.connect(path) as connection:
        return tuple(connection.execute(
            f"SELECT count(*) FROM {table} WHERE run_id = ?", (identifier,)
        ).fetchone()[0] for table in
            ("asset", "ipam_record", "consolidated_asset", "anomaly"))


def test_observations_are_atomic_across_independent_connections(progress_app, monkeypatch, tmp_path):
    import collector.async_runner as async_runner

    reached, release = Event(), Event()
    threads = []
    start = async_runner.threading.Thread.start
    original = async_runner._run_inventory

    def capture(thread):
        threads.append(thread)
        start(thread)

    def paused(*args, **kwargs):
        def checkpoint(number):
            if number == 4:
                reached.set()
                assert release.wait(10)
        return original(*args, after_step=checkpoint, **kwargs)

    monkeypatch.setattr(async_runner.threading.Thread, "start", capture)
    monkeypatch.setattr(async_runner, "_run_inventory", paused)
    client, csrf = admin_client(progress_app)
    path = tmp_path / "progress.sqlite3"
    try:
        response = client.post("/dashboard/run", data={"csrf_token": csrf})
        assert response.status_code == 202
        identifier = response.get_json()["id"]
        assert reached.wait(10)
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT completed_steps FROM run WHERE id = ?", (identifier,)).fetchone()[0] == 4
        assert observation_counts(path, identifier) == (0, 0, 0, 0)
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT status FROM run WHERE id = ?", (identifier,)).fetchone()[0] == "SUCCESS"
    assert observation_counts(path, identifier)[0] > 0


def test_failed_run_rolls_back_all_observations_from_independent_connection(progress_app, monkeypatch, tmp_path):
    import collector.async_runner as async_runner

    threads = []
    start = async_runner.threading.Thread.start
    original = async_runner._run_inventory

    def capture(thread):
        threads.append(thread)
        start(thread)

    def failing(*args, **kwargs):
        def checkpoint(number):
            if number == 7:
                raise RuntimeError("forced rollback")
        return original(*args, after_step=checkpoint, **kwargs)

    monkeypatch.setattr(async_runner.threading.Thread, "start", capture)
    monkeypatch.setattr(async_runner, "_run_inventory", failing)
    client, csrf = admin_client(progress_app)
    try:
        response = client.post("/dashboard/run", data={"csrf_token": csrf})
        assert response.status_code == 202
    finally:
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
    identifier = response.get_json()["id"]
    with sqlite3.connect(tmp_path / "progress.sqlite3") as connection:
        assert connection.execute("SELECT status FROM run WHERE id = ?", (identifier,)).fetchone()[0] == "FAIL"
    assert observation_counts(tmp_path / "progress.sqlite3", identifier) == (0, 0, 0, 0)


def test_restart_recovers_interrupted_run(progress_app, tmp_path):
    with progress_app.app_context():
        row = Run(status="RUNNING", started_at="2026-01-01", current_step=4, completed_steps=3)
        db.session.add(row)
        db.session.commit()
        identifier = row.id
    restarted = create_app(database_uri=f"sqlite:///{tmp_path / 'progress.sqlite3'}")
    with restarted.app_context():
        row = db.session.get(Run, identifier)
        assert (row.status, row.completed_steps, row.error_message) == (
            "FAIL", 3, "Inventory run interrupted")


def test_restart_does_not_recover_active_worker(progress_app, monkeypatch, tmp_path):
    import collector.async_runner as async_runner

    reached, release = Event(), Event()
    threads = []
    start = async_runner.threading.Thread.start
    original = async_runner._run_inventory

    def capture(thread):
        threads.append(thread)
        start(thread)

    def paused(*args, **kwargs):
        def checkpoint(number):
            if number == 4:
                reached.set()
                assert release.wait(10)
        return original(*args, after_step=checkpoint, **kwargs)

    monkeypatch.setattr(async_runner.threading.Thread, "start", capture)
    monkeypatch.setattr(async_runner, "_run_inventory", paused)
    client, csrf = admin_client(progress_app)
    try:
        response = client.post("/dashboard/run", data={"csrf_token": csrf})
        assert response.status_code == 202
        assert reached.wait(10)
        restarted = create_app(database_uri=f"sqlite:///{tmp_path / 'progress.sqlite3'}")
        with restarted.app_context():
            assert db.session.get(Run, response.get_json()["id"]).status == "RUNNING"
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()


def test_old_run_without_progress_exposes_null_steps(progress_app):
    with progress_app.app_context():
        row = Run(status="SUCCESS", started_at="2026-01-01")
        db.session.add(row)
        db.session.commit()
        identifier = row.id
    client, _ = admin_client(progress_app)
    payload = client.get(f"/runs/{identifier}/status").get_json()
    assert (payload["current_step"], payload["completed_steps"]) == (None, None)


def test_simultaneous_admissions_return_exactly_one_accepted_and_one_conflict(progress_app, monkeypatch):
    import collector.async_runner as async_runner

    reached, release, start_requests = Event(), Event(), Event()
    workers = []
    original_start = async_runner.threading.Thread.start
    original_run = async_runner._run_inventory

    def capture(thread):
        workers.append(thread)
        original_start(thread)

    def paused(*args, **kwargs):
        reached.set()
        assert release.wait(10)
        return original_run(*args, **kwargs)

    monkeypatch.setattr(async_runner.threading.Thread, "start", capture)
    monkeypatch.setattr(async_runner, "_run_inventory", paused)
    clients = [admin_client(progress_app) for _ in range(2)]
    responses = [None, None]

    def submit(index):
        assert start_requests.wait(10)
        client, csrf = clients[index]
        responses[index] = client.post("/dashboard/run", data={"csrf_token": csrf})

    requests = [Thread(target=submit, args=(index,)) for index in range(2)]
    try:
        for request in requests:
            request.start()
        start_requests.set()
        assert reached.wait(10)
        for request in requests:
            request.join(timeout=10)
            assert not request.is_alive()
        assert sorted(response.status_code for response in responses) == [202, 409]
    finally:
        start_requests.set()
        release.set()
        for request in requests:
            request.join(timeout=10)
        for worker in workers:
            worker.join(timeout=10)
            assert not worker.is_alive()
