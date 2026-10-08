#!/bin/bash
set -e

APP_URL="127.0.0.1:5000"
# Charger les variables d'environnement (ADMIN_PASSWORD, etc.)
source .env
COOKIE_JAR=cookies.txt
CSRF_FIELD='name="csrf_token"[^>]*value="([^"]*)"'

echo "=== Smoke test: waiting for app ==="
for i in $(seq 1 30); do
    if curl -s -f "http://${APP_URL}/login" > /dev/null 2>&1; then
        echo "App is ready!"
        break
    fi
    echo "Waiting... ($i/30)"
    sleep 1
done

# Step 1: GET /login → 200
echo "=== Step 1: GET /login ==="
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -c $COOKIE_JAR "http://${APP_URL}/login")
if [ "$HTTP_CODE" != "200" ]; then
    echo "FAIL: /login returned $HTTP_CODE, expected 200"
    exit 1
fi
echo "/login returned 200"

# Step 2: Extract CSRF token from the form
echo "=== Step 2: Extract CSRF token ==="
RAW_HTML=$(curl -s -b $COOKIE_JAR "http://${APP_URL}/login")
CSRF_TOKEN=$(echo "$RAW_HTML" | grep -o 'value="[^"]*"' | head -1 | cut -d'"' -f2)
if [ -z "$CSRF_TOKEN" ]; then
    echo "FAIL: Could not extract CSRF token from /login"
    exit 1
fi
echo "CSRF token extracted: ${CSRF_TOKEN}"

# Step 3: POST /login with CSRF → login (302)
echo "=== Step 3: POST /login with CSRF ==="
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -b $COOKIE_JAR -c $COOKIE_JAR -X POST -d "username=admin&password=${ADMIN_PASSWORD}&csrf_token=${CSRF_TOKEN}" "http://${APP_URL}/login")
if [ "$HTTP_CODE" != "302" ]; then
    echo "FAIL: /login POST returned $HTTP_CODE, expected 302"
    exit 1
fi
echo "Login successful (302)"

# Step 3.5: Refresh CSRF token after login (session.clear regenerates it)
echo "=== Step 3.5: Refresh CSRF token after login ==="
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -b $COOKIE_JAR -c $COOKIE_JAR "http://${APP_URL}/login")
RAW_HTML=$(curl -s -b $COOKIE_JAR "http://${APP_URL}/login")
CSRF_TOKEN=$(echo "$RAW_HTML" | grep -o 'value="[^"]*"' | head -1 | cut -d'"' -f2)
if [ -z "$CSRF_TOKEN" ]; then
    echo "FAIL: Could not extract refreshed CSRF token from /login"
    exit 1
fi
echo "Refreshed CSRF token extracted: ${CSRF_TOKEN}"

# Step 4: POST /run with CSRF → trigger run (302)
echo "=== Step 4: POST /run with CSRF ==="
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -b $COOKIE_JAR -c $COOKIE_JAR -X POST -d "csrf_token=${CSRF_TOKEN}" "http://${APP_URL}/run")
if [ "$HTTP_CODE" != "302" ]; then
    echo "FAIL: /run POST returned $HTTP_CODE, expected 302"
    exit 1
fi
echo "Run triggered (302)"

# Step 5: POST /api/login → get JWT token
echo "=== Step 5: POST /api/login ==="
API_RESPONSE=$(curl -s -X POST -H "Content-Type: application/json" -d "{\"username\": \"admin\", \"password\": \"${ADMIN_PASSWORD}\"}" "http://${APP_URL}/api/login")
ACCESS_TOKEN=$(echo "$API_RESPONSE" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
if [ -z "$ACCESS_TOKEN" ]; then
    echo "FAIL: Could not get access token from /api/login"
    exit 1
fi
echo "Got access token"

# Step 6: GET /api/stats with JWT → protected API
echo "=== Step 6: GET /api/stats with JWT ==="
STATS_RESPONSE=$(curl -s -w "\nHTTP %{http_code}" -H "Authorization: Bearer ${ACCESS_TOKEN}" "http://${APP_URL}/api/stats")
if ! echo "$STATS_RESPONSE" | grep -q "has_data"; then
    echo "FAIL: Protected API call /api/stats failed"
    echo "$STATS_RESPONSE" | tail -c 400
    exit 1
fi
echo "Protected API call successful"

echo "SMOKE TEST PASSED"
rm -f $COOKIE_JAR
exit 0