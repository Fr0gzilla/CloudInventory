import os
import subprocess
import sys
from pathlib import Path

import pytest


START = Path(__file__).resolve().parents[1] / "scripts" / "start.sh"
KEYS = ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD")
VALID = {key: f"test-only-{key.lower()}-value" for key in KEYS}
FACTORY = "from app import create_app; create_app(database_uri='sqlite:///:memory:')"


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("value", [None, "", "change-me"], ids=["missing", "empty", "reserved"])
def test_entrypoint_does_not_invent_invalid_secrets(key, value):
    environment = {name: item for name, item in os.environ.items() if name not in KEYS}
    environment.update(VALID)
    environment["DATABASE_URL"] = "sqlite:///:memory:"
    if value is None:
        environment.pop(key)
    else:
        environment[key] = value
    result = subprocess.run(["sh", str(START), sys.executable, "-c", FACTORY], env=environment,
                            capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode != 0 and f"ValueError: {key}" in result.stderr and all(
        secret not in result.stdout + result.stderr for secret in VALID.values()
    )


def test_entrypoint_preserves_valid_secrets():
    environment = {name: item for name, item in os.environ.items() if name not in KEYS}
    environment.update(VALID)
    environment["DATABASE_URL"] = "sqlite:///:memory:"
    result = subprocess.run(["sh", str(START), sys.executable, "-c",
                             "import os; from app import create_app; app = create_app(database_uri='sqlite:///:memory:'); "
                             "assert app.config['SECRET_KEY'] == os.environ['SECRET_KEY']; "
                             "assert app.config['JWT_SECRET_KEY'] == os.environ['JWT_SECRET_KEY']; "
                             "assert app.test_client().post('/api/login', json={'username': 'admin', "
                             "'password': os.environ['ADMIN_PASSWORD']}).status_code == 200"],
                            env=environment, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
