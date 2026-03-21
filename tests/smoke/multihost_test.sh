#!/usr/bin/env bash
# Multi-host smoke test for archetype-iac.
# Validates that multiple agents can register with the controller.
#
# Limitation: No VXLAN/OVS testing — requires real hosts with kernel support.
# This only validates control plane multi-agent registration.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.multihost.yml"
PROJECT_NAME="archetype-multihost-$$"
API_URL=""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() { printf '\033[1;34m[MULTI]\033[0m %s\n' "$1"; }
pass() { printf '\033[1;32m[PASS]\033[0m  %s\n' "$1"; }
fail() { printf '\033[1;31m[FAIL]\033[0m  %s\n' "$1"; exit 1; }

dump_logs() {
    log "Dumping container logs for debugging..."
    docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" logs --no-color 2>/dev/null \
        > /tmp/multihost-compose-logs.txt || true
}

cleanup() {
    dump_logs
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

log "Step 1/4: Building and starting multi-agent stack..."
docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up -d --build --wait --timeout 180
pass "Stack started"

get_api_url
log "API available at $API_URL"

# ---------------------------------------------------------------------------
# Step 2: Authenticate
# ---------------------------------------------------------------------------

log "Step 2/4: Authenticating..."
LOGIN_RESP=$(curl -sf -X POST "$API_URL/auth/login" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=admin@localhost&password=smoketest123")

TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || echo "")
if [ -z "$TOKEN" ]; then
    fail "Authentication failed"
fi
pass "Authenticated"

AUTH_HEADER="Authorization: Bearer $TOKEN"

# ---------------------------------------------------------------------------
# Step 3: Wait for both agents
# ---------------------------------------------------------------------------

log "Step 3/4: Waiting for both agents to register..."
BOTH_FOUND=false
for i in $(seq 1 20); do
    AGENTS=$(curl -sf -H "$AUTH_HEADER" "$API_URL/agents" 2>/dev/null || echo "[]")
    HAS_SMOKE=$(echo "$AGENTS" | grep -c "smoke-agent" || true)
    HAS_REMOTE=$(echo "$AGENTS" | grep -c "remote-agent" || true)
    if [ "$HAS_SMOKE" -ge 1 ] && [ "$HAS_REMOTE" -ge 1 ]; then
        BOTH_FOUND=true
        break
    fi
    sleep 3
done

if [ "$BOTH_FOUND" = true ]; then
    pass "Both agents registered (smoke-agent + remote-agent)"
else
    fail "Not all agents registered after 60s. Agents: $AGENTS"
fi

# ---------------------------------------------------------------------------
# Step 4: Verify agent count
# ---------------------------------------------------------------------------

log "Step 4/4: Verifying agent count..."
AGENT_COUNT=$(echo "$AGENTS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
if [ "$AGENT_COUNT" -ge 2 ]; then
    pass "Agent count = $AGENT_COUNT (>= 2)"
else
    fail "Expected >= 2 agents, got $AGENT_COUNT"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

printf '\n\033[1;32mAll 4 multi-host checks passed!\033[0m\n'
