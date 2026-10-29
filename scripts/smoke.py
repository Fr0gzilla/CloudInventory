"""Exercise the production WSGI entry point over loopback HTTP."""

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="cloudinventory-smoke-") as directory:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        address = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        for key in ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD"):
            env[key] = secrets.token_urlsafe(48)
        env.update(
            DATABASE_URL=f"sqlite:///{Path(directory) / 'smoke.sqlite3'}",
            USE_MOCK_VIRT="true",
            USE_MOCK_IPAM="true",
            USE_MOCK_SMTP="true",
            USE_MOCK_WEBHOOK="true",
            SMTP_ENABLED="false",
            WEBHOOK_ENABLED="false",
            SESSION_COOKIE_SECURE="false",
        )
        process = subprocess.Popen(
            [sys.argv[1], "--bind", f"127.0.0.1:{port}", "--workers", "1", "app:create_app()"],
            env=env,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"server exited with code {process.returncode}")
                try:
                    with urllib.request.urlopen(address + "/health", timeout=1) as response:
                        if response.status != 200:
                            raise RuntimeError(f"health: HTTP {response.status}")
                    break
                except urllib.error.URLError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("server did not become healthy") from None
                    time.sleep(0.1)

            def request(path: str, *, payload: dict | None = None, token: str | None = None,
                        expected_status: int = 200) -> dict:
                headers = {"Content-Type": "application/json"}
                if token:
                    headers["Authorization"] = "Bearer " + token
                data = json.dumps(payload).encode() if payload is not None else None
                req = urllib.request.Request(address + path, data=data, headers=headers)
                try:
                    with urllib.request.urlopen(req, timeout=10) as response:
                        body = json.load(response)
                        if response.status != expected_status:
                            raise RuntimeError(f"{path}: HTTP {response.status}")
                        return body
                except urllib.error.HTTPError as error:
                    raise RuntimeError(f"{path}: HTTP {error.code}") from error

            login = request("/api/login", payload={"username": "admin", "password": env["ADMIN_PASSWORD"]})
            token = login["access_token"]
            run = request("/api/runs", payload={}, token=token, expected_status=202)
            run_id = run["id"]
            deadline = time.monotonic() + 15
            while True:
                result = request(f"/api/runs/{run_id}", token=token)
                status = result.get("status")
                if status == "SUCCESS":
                    break
                if status != "RUNNING":
                    raise RuntimeError(f"simulated run {run_id} failed: {status}")
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"simulated run {run_id} timed out waiting for SUCCESS")
                time.sleep(0.1)
            request("/api/runs", token=token)
            print("smoke: health, authenticated simulated run SUCCESS over HTTP")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
