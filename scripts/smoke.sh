#!/bin/bash
set -e

# Security hardening: no secrets in argv, restricted permissions, guaranteed cleanup
set +x
umask 077
case "${SMOKE_PROFILE:-}" in
    test|docker) ;;
    *) echo "FAIL: use SMOKE_PROFILE=test make smoke (isolated test data only), or SMOKE_PROFILE=docker make smoke (Docker acceptance, running container)"; exit 1 ;;
esac
SMOKE_TMP=$(mktemp -d)
APP_PID=
cleanup() {
    if [ -n "$APP_PID" ]; then
        kill "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
    fi
    rm -rf -- "$SMOKE_TMP"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

COOKIE_JAR="$SMOKE_TMP/cookies"
if [ "$SMOKE_PROFILE" = "docker" ]; then
    # Docker acceptance: the container started by `docker compose up`, the admin password of its .env (read by the
    # system Python, stdlib only: no venv on the acceptance host), the same HTTP checks, no secret in argv.
    PYTHON="${SMOKE_PYTHON_BIN:-python3}"
    APP_URL="${SMOKE_APP_URL:-127.0.0.1:5000}"
    "$PYTHON" - "${SMOKE_ENV_FILE:-.env}" > "$SMOKE_TMP/password" <<'PY'
import sys
for line in open(sys.argv[1], encoding="utf-8"):
    key, sep, value = line.strip().partition("=")
    if sep and key.strip() == "ADMIN_PASSWORD":
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        sys.stdout.write(value)
        break
PY
    if [ ! -s "$SMOKE_TMP/password" ]; then
        echo "FAIL: ADMIN_PASSWORD missing from ${SMOKE_ENV_FILE:-.env}"
        exit 1
    fi
    ADMIN_PASSWORD=$(< "$SMOKE_TMP/password")
else
PYTHON="$(pwd)/venv/bin/python"

# Test-only launcher: no inherited credentials, dotenv, production DB or network services.
"$PYTHON" - "$SMOKE_TMP" > "$SMOKE_TMP/server.log" 2>&1 <<'PY' &
import os
from pathlib import Path
import secrets
import sys
import dotenv
from werkzeug.serving import make_server

directory = Path(sys.argv[1])
password = secrets.token_urlsafe(32) + '&=+% "\\\né'
os.environ.clear()
os.environ.update(
    APP_ENV='test', SECRET_KEY=secrets.token_urlsafe(32),
    JWT_SECRET_KEY=secrets.token_urlsafe(32), ADMIN_PASSWORD=password,
    DATABASE_URL='sqlite:///' + str(directory / 'smoke.db'),
    RATE_LIMIT_STORE_PATH=str(directory / 'rate-limits.json'),
    USE_MOCK_IPAM='true', USE_MOCK_VIRT='true',
    SMTP_ENABLED='false', WEBHOOK_URL='', EXPORT_ENABLED='false',
)
dotenv.load_dotenv = lambda *args, **kwargs: False
from app import create_app

application = create_app()
server = make_server('127.0.0.1', 0, application)
(directory / 'password').write_text(password)
(directory / 'port').write_text(str(server.server_port))
server.serve_forever()
PY
APP_PID=$!
for i in $(seq 1 30); do
    [ -s "$SMOKE_TMP/port" ] && break
    if ! kill -0 "$APP_PID" 2>/dev/null; then
        echo "FAIL: isolated test application could not start"
        exit 1
    fi
    sleep 1
done
if [ ! -s "$SMOKE_TMP/port" ]; then
    echo "FAIL: isolated test application startup timed out"
    exit 1
fi
APP_URL="127.0.0.1:$(< "$SMOKE_TMP/port")"
ADMIN_PASSWORD=$(< "$SMOKE_TMP/password")
fi

# Helper: write secret to temp file and return path
write_secret() {
    local file="$1"
    local value="$2"
    printf '%s' "$value" > "$file"
    chmod 600 "$file"
    echo "$file"
}

echo "=== Smoke test: waiting for app ==="
for i in $(seq 1 30); do
    if curl -s -f "http://${APP_URL}/healthz" > /dev/null 2>&1; then
        echo "App is ready!"
        break
    fi
    echo "Waiting... ($i/30)"
    sleep 1
done

# Step 1: GET /login → 200
echo "=== Step 1: GET /login ==="
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -c "$COOKIE_JAR" "http://${APP_URL}/login")
if [ "$HTTP_CODE" != "200" ]; then
    echo "FAIL: /login returned $HTTP_CODE, expected 200"
    exit 1
fi
echo "/login returned 200"

# Step 2: Extract CSRF token from the form
echo "=== Step 2: Extract CSRF token ==="
RAW_HTML=$(curl -s -b "$COOKIE_JAR" "http://${APP_URL}/login")
CSRF_TOKEN=$(echo "$RAW_HTML" | grep -o 'value="[^"]*"' | head -1 | cut -d'"' -f2)
if [ -z "$CSRF_TOKEN" ]; then
    echo "FAIL: Could not extract CSRF token from /login"
    exit 1
fi

# Step 3: POST /login with CSRF → login (302)
echo "=== Step 3: POST /login with CSRF ==="
LOGIN_DATA_FILE="$SMOKE_TMP/login_data"
printf '%s\0%s' "$ADMIN_PASSWORD" "$CSRF_TOKEN" | "$PYTHON" -c 'import sys, urllib.parse; password, csrf = sys.stdin.read().split("\0"); sys.stdout.write(urllib.parse.urlencode(dict(username="admin", **dict(zip(("password", "csrf_token"), (password, csrf))))))' > "$LOGIN_DATA_FILE"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -b "$COOKIE_JAR" -c "$COOKIE_JAR" -X POST --data-binary @"$LOGIN_DATA_FILE" "http://${APP_URL}/login")
if [ "$HTTP_CODE" != "302" ]; then
    echo "FAIL: /login POST returned $HTTP_CODE, expected 302"
    exit 1
fi
echo "Login successful (302)"

# Step 3.5: Refresh CSRF token after login (session.clear regenerates it)
echo "=== Step 3.5: Refresh CSRF token after login ==="
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -b "$COOKIE_JAR" -c "$COOKIE_JAR" "http://${APP_URL}/login")
RAW_HTML=$(curl -s -b "$COOKIE_JAR" "http://${APP_URL}/login")
CSRF_TOKEN=$(echo "$RAW_HTML" | grep -o 'value="[^"]*"' | head -1 | cut -d'"' -f2)
if [ -z "$CSRF_TOKEN" ]; then
    echo "FAIL: Could not extract refreshed CSRF token from /login"
    exit 1
fi

# Step 4: POST /run with CSRF → trigger run (302)
echo "=== Step 4: POST /run with CSRF ==="
RUN_DATA_FILE="$SMOKE_TMP/run_data"
printf '%s' "$CSRF_TOKEN" | "$PYTHON" -c 'import sys, urllib.parse; sys.stdout.write(urllib.parse.urlencode(dict(csrf_token=sys.stdin.read())))' > "$RUN_DATA_FILE"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -b "$COOKIE_JAR" -c "$COOKIE_JAR" -X POST --data-binary @"$RUN_DATA_FILE" "http://${APP_URL}/run")
if [ "$HTTP_CODE" != "302" ]; then
    echo "FAIL: /run POST returned $HTTP_CODE, expected 302"
    exit 1
fi
echo "Run triggered (302)"

# Step 5: POST /api/login → get JWT token
echo "=== Step 5: POST /api/login ==="
API_LOGIN_FILE="$SMOKE_TMP/api_login"
printf '%s' "$ADMIN_PASSWORD" | "$PYTHON" -c 'import json, sys; json.dump(dict(username="admin", password=sys.stdin.read()), sys.stdout)' > "$API_LOGIN_FILE"
API_RESPONSE=$(curl -s -X POST -H "Content-Type: application/json" --data-binary @"$API_LOGIN_FILE" "http://${APP_URL}/api/login")
ACCESS_TOKEN=$(echo "$API_RESPONSE" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
if [ -z "$ACCESS_TOKEN" ]; then
    echo "FAIL: Could not get access token from /api/login"
    exit 1
fi
echo "Got access token"

# Step 6: GET /api/stats with JWT → protected API
echo "=== Step 6: GET /api/stats with JWT ==="
AUTH_HEADER_FILE=$(write_secret "$SMOKE_TMP/auth_header" "Authorization: Bearer ${ACCESS_TOKEN}")
STATS_RESPONSE=$(curl -s -w "\nHTTP %{http_code}" -H @"$AUTH_HEADER_FILE" "http://${APP_URL}/api/stats")
if ! echo "$STATS_RESPONSE" | grep -q "has_data"; then
    echo "FAIL: Protected API call /api/stats failed"
    exit 1
fi
echo "Protected API call successful"

echo "SMOKE TEST PASSED"
exit 0
