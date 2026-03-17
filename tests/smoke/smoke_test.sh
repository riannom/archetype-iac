#!/usr/bin/env bash
# Single-host smoke test for archetype-iac.
# Builds the real Docker stack in CI and validates the control plane works.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.smoke.yml"
PROJECT_NAME="archetype-smoke-$$"
API_URL=""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() { printf '\033[1;34m[SMOKE]\033[0m %s\n' "$1"; }
pass() { printf '\033[1;32m[PASS]\033[0m  %s\n' "$1"; }
fail() { printf '\033[1;31m[FAIL]\033[0m  %s\n' "$1"; exit 1; }

cleanup() {
    log "Cleaning up..."
    docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" down -v --remove-orphans 2>/dev/null || true
}
trap cleanup EXIT

get_api_url() {
    local port
    port=$(docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" port api 8000 2>/dev/null | cut -d: -f2)
    if [ -z "$port" ]; then
        fail "Could not determine API port"
    fi
    API_URL="http://localhost:$port"
}

# ---------------------------------------------------------------------------
# Step 1: Build and start
# ---------------------------------------------------------------------------

log "Step 1/6: Building and starting stack..."
docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up -d --build --wait --timeout 180
pass "Stack started"

get_api_url
log "API available at $API_URL"

# ---------------------------------------------------------------------------
# Step 2: Healthz endpoint
# ---------------------------------------------------------------------------

log "Step 2/6: Checking /healthz..."
HTTP_CODE=$(curl -sf -o /dev/null -w '%{http_code}' "$API_URL/healthz")
if [ "$HTTP_CODE" = "200" ]; then
    pass "/healthz returned 200"
else
    fail "/healthz returned $HTTP_CODE (expected 200)"
fi

# ---------------------------------------------------------------------------
# Step 3: Health endpoint
# ---------------------------------------------------------------------------

log "Step 3/6: Checking /health..."
HEALTH_BODY=$(curl -sf "$API_URL/health")
if echo "$HEALTH_BODY" | grep -q '"status"'; then
    pass "/health returned status JSON"
else
    fail "/health response missing status: $HEALTH_BODY"
fi

# ---------------------------------------------------------------------------
# Step 4: Authentication
# ---------------------------------------------------------------------------

log "Step 4/6: Authenticating..."
LOGIN_RESP=$(curl -sf -X POST "$API_URL/auth/login" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=admin@localhost&password=smoketest123")

TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || echo "")
if [ -z "$TOKEN" ]; then
    fail "Authentication failed — no access_token in response: $LOGIN_RESP"
fi
pass "Authenticated (got JWT token)"

AUTH_HEADER="Authorization: Bearer $TOKEN"

# ---------------------------------------------------------------------------
# Step 5: Agent registration
# ---------------------------------------------------------------------------

log "Step 5/6: Waiting for agent registration..."
AGENT_FOUND=false
for i in $(seq 1 30); do
    AGENTS=$(curl -sf -H "$AUTH_HEADER" "$API_URL/agents" 2>/dev/null || echo "[]")
    if echo "$AGENTS" | grep -q "smoke-agent"; then
        AGENT_FOUND=true
        break
    fi
    sleep 3
done

if [ "$AGENT_FOUND" = true ]; then
    pass "Agent 'smoke-agent' registered"
else
    fail "Agent 'smoke-agent' did not register after 90s. Agents: $AGENTS"
fi

# ---------------------------------------------------------------------------
# Step 6: Lab CRUD
# ---------------------------------------------------------------------------

log "Step 6/6: Lab CRUD..."

# Create
CREATE_RESP=$(curl -sf -X POST "$API_URL/labs" \
    -H "$AUTH_HEADER" \
    -H "Content-Type: application/json" \
    -d '{"name":"smoke-test-lab"}')
LAB_ID=$(echo "$CREATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
if [ -z "$LAB_ID" ]; then
    fail "Lab creation failed: $CREATE_RESP"
fi
pass "Lab created (id=$LAB_ID)"

# List
LIST_RESP=$(curl -sf -H "$AUTH_HEADER" "$API_URL/labs")
if echo "$LIST_RESP" | grep -q "$LAB_ID"; then
    pass "Lab appears in list"
else
    fail "Lab $LAB_ID not found in list: $LIST_RESP"
fi

# Delete
DEL_CODE=$(curl -sf -o /dev/null -w '%{http_code}' -X DELETE "$API_URL/labs/$LAB_ID" -H "$AUTH_HEADER")
if [ "$DEL_CODE" = "200" ] || [ "$DEL_CODE" = "204" ]; then
    pass "Lab deleted"
else
    fail "Lab deletion returned $DEL_CODE (expected 200 or 204)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

printf '\n\033[1;32mAll 6 smoke checks passed!\033[0m\n'
