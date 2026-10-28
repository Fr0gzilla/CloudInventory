import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from app import create_app, db
from app.models import Run
from collector.inventory_runner import run_inventory
from collector.run_lock import RunAlreadyActive, run_lock


@pytest.fixture
def file_app(monkeypatch, tmp_path):
    for name in ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD"):
        monkeypatch.setenv(name, f"test-{name.lower()}")
    monkeypatch.setenv("EXPORT_DIRECTORY", str(tmp_path / "exports"))
    path = tmp_path / "inventory.sqlite3"
    app = create_app(database_uri=f"sqlite:///{path}")
    return app, path


@pytest.fixture
def lock_holder():
    processes = []

    def start(path):
        script = (
            "import fcntl, sys; "
            "f = open(sys.argv[1], 'a+b'); "
            "fcntl.flock(f, fcntl.LOCK_EX); "
            "sys.stdout.write('READY\\n'); sys.stdout.flush(); "
            "sys.stdin.buffer.read(1)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        processes.append(process)
        assert process.stdout.readline() == b"READY\n"
        return process

    yield start
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


def test_single_run_succeeds_with_explicit_lock_path(file_app, monkeypatch, tmp_path):
    lock_path = tmp_path / "custom.run.lock"
    monkeypatch.setenv("RUN_LOCK_PATH", str(lock_path))
    explicit = create_app(database_uri=f"sqlite:///{tmp_path / 'explicit.sqlite3'}")
    with explicit.app_context():
        result = run_inventory()
        assert (result.status, explicit.config["RUN_LOCK_PATH"], lock_path.is_file()) == (
            "SUCCESS", str(lock_path), True,
        )


def test_concurrent_api_run_returns_conflict_without_persisting_run(file_app, lock_holder):
    app, _ = file_app
    with app.app_context():
        client = app.test_client()
        token = client.post("/api/login", json={
            "username": "admin", "password": os.environ["ADMIN_PASSWORD"],
        }).get_json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        holder = lock_holder(app.config["RUN_LOCK_PATH"])
        response = client.post("/api/runs", headers=headers)
        assert (response.status_code, response.get_json(), db.session.query(Run).count()) == (
            409, {"error": "An inventory run is already in progress"}, 0,
        )
        holder.stdin.write(b"x")
        holder.stdin.flush()
        assert holder.wait(timeout=5) == 0
        next_response = client.post("/api/runs", headers=headers)
        assert (next_response.status_code, db.session.query(Run).count()) == (201, 1)


def test_lock_survives_competing_process_and_releases_after_abrupt_death(file_app, lock_holder):
    app, _ = file_app
    with app.app_context():
        path = Path(app.config["RUN_LOCK_PATH"])
        holder = lock_holder(path)
        with pytest.raises(RunAlreadyActive, match="^An inventory run is already in progress$"):
            with run_lock():
                pytest.fail("second process entered the protected section")
        holder.send_signal(signal.SIGKILL)
        assert holder.wait(timeout=5) == -signal.SIGKILL
        with run_lock():
            assert path.is_file()


def test_failed_pipeline_releases_lock_for_next_run(file_app):
    app, _ = file_app

    class BrokenSource:
        def collect(self):
            raise RuntimeError("collection unavailable")

    with app.app_context():
        failed = run_inventory(virtualization_source=BrokenSource())
        succeeded = run_inventory()
        assert (failed.status, succeeded.status, db.session.query(Run).count()) == (
            "FAIL", "SUCCESS", 2,
        )


def test_default_lock_path_shares_docker_sqlite_volume(file_app):
    app, path = file_app
    assert Path(app.config["RUN_LOCK_PATH"]) == path.with_suffix(".run.lock")
