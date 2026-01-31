#!/bin/bash
#
# Test script for Docker OVS Plugin enhancements
#
# Prerequisites:
#   - OVS installed and running
#   - Docker installed and running
#   - Agent running on localhost:8001
#
# Usage:
#   ./scripts/test_ovs_plugin.sh [--skip-cleanup] [--agent-url URL]
#

set -e

# Configuration
AGENT_URL="${AGENT_URL:-http://localhost:8001}"
TEST_LAB_ID="test-lab-$(date +%s)"
SKIP_CLEANUP=false
VERBOSE=false

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-cleanup)
            SKIP_CLEANUP=true
            shift
            ;;
        --agent-url)
            AGENT_URL="$2"
            shift 2
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [--skip-cleanup] [--agent-url URL] [--verbose]"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Test counters
TESTS_PASSED=0
TESTS_FAILED=0
TESTS_SKIPPED=0

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[PASS]${NC} $1"
    ((TESTS_PASSED++))
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    ((TESTS_FAILED++))
}

log_skip() {
    echo -e "${YELLOW}[SKIP]${NC} $1"
    ((TESTS_SKIPPED++))
}

log_section() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE} $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
}

# HTTP request helper
api_call() {
    local method="$1"
    local endpoint="$2"
    local data="$3"

    local url="${AGENT_URL}${endpoint}"
    local response

    if [[ "$VERBOSE" == "true" ]]; then
        echo -e "${YELLOW}  -> $method $url${NC}"
        [[ -n "$data" ]] && echo -e "${YELLOW}  -> Data: $data${NC}"
    fi

    if [[ "$method" == "GET" ]]; then
        response=$(curl -s -w "\n%{http_code}" "$url" 2>/dev/null)
    elif [[ "$method" == "POST" ]]; then
        if [[ -n "$data" ]]; then
            response=$(curl -s -w "\n%{http_code}" -X POST -H "Content-Type: application/json" -d "$data" "$url" 2>/dev/null)
        else
            response=$(curl -s -w "\n%{http_code}" -X POST "$url" 2>/dev/null)
        fi
    elif [[ "$method" == "DELETE" ]]; then
        response=$(curl -s -w "\n%{http_code}" -X DELETE "$url" 2>/dev/null)
    fi

    # Split response body and status code
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | sed '$d')

    if [[ "$VERBOSE" == "true" ]]; then
        echo -e "${YELLOW}  <- HTTP $http_code${NC}"
        echo -e "${YELLOW}  <- $body${NC}"
    fi

    # Return body and set HTTP_CODE global
    HTTP_CODE="$http_code"
    echo "$body"
}

# Check if a JSON response has a field with expected value
check_json_field() {
    local json="$1"
    local field="$2"
    local expected="$3"

    local actual=$(echo "$json" | jq -r "$field" 2>/dev/null)

    if [[ "$actual" == "$expected" ]]; then
        return 0
    else
        return 1
    fi
}

# Cleanup function
cleanup() {
    if [[ "$SKIP_CLEANUP" == "true" ]]; then
        log_info "Skipping cleanup (--skip-cleanup specified)"
        log_info "Test lab ID: $TEST_LAB_ID"
        return
    fi

    log_section "Cleanup"

    # Delete management network
    log_info "Deleting management network..."
    api_call DELETE "/ovs-plugin/labs/${TEST_LAB_ID}/mgmt" > /dev/null 2>&1 || true

    # Delete Docker networks
    log_info "Deleting Docker networks..."
    docker network rm "${TEST_LAB_ID}-eth1" 2>/dev/null || true
    docker network rm "${TEST_LAB_ID}-eth2" 2>/dev/null || true
    docker network rm "archetype-mgmt-${TEST_LAB_ID:0:20}" 2>/dev/null || true

    # Remove test container
    log_info "Removing test containers..."
    docker rm -f "test-node-${TEST_LAB_ID}" 2>/dev/null || true

    # Clean up OVS bridge if it exists
    local bridge_name="ovs-${TEST_LAB_ID:0:12}"
    log_info "Cleaning up OVS bridge ${bridge_name}..."
    ovs-vsctl --if-exists del-br "$bridge_name" 2>/dev/null || true

    log_info "Cleanup complete"
}

# Set up trap for cleanup on exit
trap cleanup EXIT

# ═══════════════════════════════════════════════════════════════
# PREREQUISITES CHECK
# ═══════════════════════════════════════════════════════════════

log_section "Prerequisites Check"

# Check OVS
log_info "Checking OVS installation..."
if command -v ovs-vsctl &> /dev/null; then
    OVS_VERSION=$(ovs-vsctl --version | head -1)
    log_success "OVS installed: $OVS_VERSION"
else
    log_fail "OVS not installed"
    exit 1
fi

# Check OVS daemon
log_info "Checking OVS daemon..."
if ovs-vsctl show &> /dev/null; then
    log_success "OVS daemon is running"
else
    log_fail "OVS daemon not running"
    exit 1
fi

# Check Docker
log_info "Checking Docker..."
if command -v docker &> /dev/null && docker info &> /dev/null; then
    log_success "Docker is running"
else
    log_fail "Docker not running"
    exit 1
fi

# Check agent connectivity
log_info "Checking agent at ${AGENT_URL}..."
HEALTH_RESPONSE=$(api_call GET "/health")
if [[ "$HTTP_CODE" == "200" ]]; then
    AGENT_ID=$(echo "$HEALTH_RESPONSE" | jq -r '.agent_id' 2>/dev/null)
    log_success "Agent is running (ID: $AGENT_ID)"
else
    log_fail "Agent not reachable at ${AGENT_URL}"
    echo "  Make sure the agent is running: uvicorn agent.main:app --port 8001"
    exit 1
fi

# Check if OVS plugin is enabled
log_info "Checking OVS plugin status..."
PLUGIN_HEALTH=$(api_call GET "/ovs-plugin/health")
if check_json_field "$PLUGIN_HEALTH" ".healthy" "true"; then
    log_success "OVS plugin is healthy"
elif check_json_field "$PLUGIN_HEALTH" ".healthy" "false"; then
    # Plugin responded but not healthy - check specific issues
    SOCKET_OK=$(echo "$PLUGIN_HEALTH" | jq -r '.checks.socket_exists' 2>/dev/null)
    OVS_OK=$(echo "$PLUGIN_HEALTH" | jq -r '.checks.ovs_available' 2>/dev/null)
    if [[ "$SOCKET_OK" != "true" ]]; then
        log_fail "Plugin socket not found"
    elif [[ "$OVS_OK" != "true" ]]; then
        log_fail "OVS not available to plugin"
    else
        log_success "OVS plugin responded (checking details...)"
    fi
else
    log_fail "OVS plugin not responding properly"
    echo "  Response: $PLUGIN_HEALTH"
fi

log_info "Test lab ID: $TEST_LAB_ID"

# ═══════════════════════════════════════════════════════════════
# TEST 1: Health Check Endpoint
# ═══════════════════════════════════════════════════════════════

log_section "Test 1: Health Check Endpoint"

log_info "Testing /ovs-plugin/health..."
RESPONSE=$(api_call GET "/ovs-plugin/health")

if [[ "$HTTP_CODE" == "200" ]]; then
    log_success "Health endpoint returned 200"
else
    log_fail "Health endpoint returned $HTTP_CODE"
fi

# Check response structure
if echo "$RESPONSE" | jq -e '.healthy' > /dev/null 2>&1; then
    log_success "Response has 'healthy' field"
else
    log_fail "Response missing 'healthy' field"
fi

if echo "$RESPONSE" | jq -e '.checks' > /dev/null 2>&1; then
    log_success "Response has 'checks' field"
else
    log_fail "Response missing 'checks' field"
fi

if echo "$RESPONSE" | jq -e '.uptime_seconds' > /dev/null 2>&1; then
    UPTIME=$(echo "$RESPONSE" | jq -r '.uptime_seconds')
    log_success "Response has 'uptime_seconds' field ($UPTIME s)"
else
    log_fail "Response missing 'uptime_seconds' field"
fi

# ═══════════════════════════════════════════════════════════════
# TEST 2: Status Endpoint (Empty State)
# ═══════════════════════════════════════════════════════════════

log_section "Test 2: Status Endpoint (Empty State)"

log_info "Testing /ovs-plugin/status..."
RESPONSE=$(api_call GET "/ovs-plugin/status")

if [[ "$HTTP_CODE" == "200" ]]; then
    log_success "Status endpoint returned 200"
else
    log_fail "Status endpoint returned $HTTP_CODE"
fi

# Check response structure
for field in healthy labs_count endpoints_count networks_count bridges; do
    if echo "$RESPONSE" | jq -e ".$field" > /dev/null 2>&1; then
        VALUE=$(echo "$RESPONSE" | jq -r ".$field")
        log_success "Response has '$field' field (value: $VALUE)"
    else
        log_fail "Response missing '$field' field"
    fi
done

# ═══════════════════════════════════════════════════════════════
# TEST 3: Create Docker Network (Triggers Bridge Creation)
# ═══════════════════════════════════════════════════════════════

log_section "Test 3: Create Docker Network"

log_info "Creating Docker network with OVS plugin..."
NETWORK_NAME="${TEST_LAB_ID}-eth1"

# Create the network
if docker network create -d archetype-ovs \
    -o lab_id="$TEST_LAB_ID" \
    -o interface_name=eth1 \
    "$NETWORK_NAME" > /dev/null 2>&1; then
    log_success "Docker network created: $NETWORK_NAME"
else
    log_fail "Failed to create Docker network"
    echo "  Note: Make sure the archetype-ovs plugin is registered with Docker"
fi

# Verify network exists
if docker network inspect "$NETWORK_NAME" > /dev/null 2>&1; then
    log_success "Docker network is inspectable"
else
    log_fail "Docker network not found after creation"
fi

# Check OVS bridge was created
BRIDGE_NAME="ovs-${TEST_LAB_ID:0:12}"
log_info "Checking OVS bridge: $BRIDGE_NAME"
if ovs-vsctl br-exists "$BRIDGE_NAME" 2>/dev/null; then
    log_success "OVS bridge created: $BRIDGE_NAME"
else
    log_fail "OVS bridge not found: $BRIDGE_NAME"
fi

# ═══════════════════════════════════════════════════════════════
# TEST 4: Status Endpoint (With Lab)
# ═══════════════════════════════════════════════════════════════

log_section "Test 4: Status Endpoint (With Lab)"

log_info "Testing /ovs-plugin/status after network creation..."
RESPONSE=$(api_call GET "/ovs-plugin/status")

LABS_COUNT=$(echo "$RESPONSE" | jq -r '.labs_count' 2>/dev/null)
if [[ "$LABS_COUNT" -ge 1 ]]; then
    log_success "Status shows $LABS_COUNT lab(s)"
else
    log_fail "Status shows no labs after network creation"
fi

# Check bridges array
BRIDGES_COUNT=$(echo "$RESPONSE" | jq -r '.bridges | length' 2>/dev/null)
if [[ "$BRIDGES_COUNT" -ge 1 ]]; then
    log_success "Status shows $BRIDGES_COUNT bridge(s)"
else
    log_fail "Status shows no bridges"
fi

# ═══════════════════════════════════════════════════════════════
# TEST 5: Lab-Specific Status
# ═══════════════════════════════════════════════════════════════

log_section "Test 5: Lab-Specific Status"

log_info "Testing /ovs-plugin/labs/${TEST_LAB_ID}..."
RESPONSE=$(api_call GET "/ovs-plugin/labs/${TEST_LAB_ID}")

if [[ "$HTTP_CODE" == "200" ]]; then
    log_success "Lab status endpoint returned 200"
else
    log_fail "Lab status endpoint returned $HTTP_CODE"
fi

if echo "$RESPONSE" | jq -e '.bridge_name' > /dev/null 2>&1; then
    BRIDGE=$(echo "$RESPONSE" | jq -r '.bridge_name')
    log_success "Lab has bridge: $BRIDGE"
else
    # Might be an error response
    if echo "$RESPONSE" | jq -e '.error' > /dev/null 2>&1; then
        ERROR=$(echo "$RESPONSE" | jq -r '.error')
        log_fail "Lab status error: $ERROR"
    else
        log_fail "Lab status missing bridge_name"
    fi
fi

# ═══════════════════════════════════════════════════════════════
# TEST 6: Lab Ports Endpoint
# ═══════════════════════════════════════════════════════════════

log_section "Test 6: Lab Ports Endpoint"

log_info "Testing /ovs-plugin/labs/${TEST_LAB_ID}/ports..."
RESPONSE=$(api_call GET "/ovs-plugin/labs/${TEST_LAB_ID}/ports")

if [[ "$HTTP_CODE" == "200" ]]; then
    log_success "Lab ports endpoint returned 200"
else
    log_fail "Lab ports endpoint returned $HTTP_CODE"
fi

if echo "$RESPONSE" | jq -e '.ports' > /dev/null 2>&1; then
    PORT_COUNT=$(echo "$RESPONSE" | jq -r '.ports | length')
    log_success "Lab ports response has 'ports' array ($PORT_COUNT ports)"
else
    log_fail "Lab ports response missing 'ports' field"
fi

# ═══════════════════════════════════════════════════════════════
# TEST 7: Lab Flows Endpoint
# ═══════════════════════════════════════════════════════════════

log_section "Test 7: Lab Flows Endpoint"

log_info "Testing /ovs-plugin/labs/${TEST_LAB_ID}/flows..."
RESPONSE=$(api_call GET "/ovs-plugin/labs/${TEST_LAB_ID}/flows")

if [[ "$HTTP_CODE" == "200" ]]; then
    log_success "Lab flows endpoint returned 200"
else
    log_fail "Lab flows endpoint returned $HTTP_CODE"
fi

if echo "$RESPONSE" | jq -e '.flows' > /dev/null 2>&1; then
    FLOW_COUNT=$(echo "$RESPONSE" | jq -r '.flow_count')
    log_success "Lab has $FLOW_COUNT flow(s)"
else
    if echo "$RESPONSE" | jq -e '.error' > /dev/null 2>&1; then
        ERROR=$(echo "$RESPONSE" | jq -r '.error')
        log_fail "Lab flows error: $ERROR"
    else
        log_fail "Lab flows response missing 'flows' field"
    fi
fi

# ═══════════════════════════════════════════════════════════════
# TEST 8: Management Network
# ═══════════════════════════════════════════════════════════════

log_section "Test 8: Management Network"

log_info "Creating management network..."
RESPONSE=$(api_call POST "/ovs-plugin/labs/${TEST_LAB_ID}/mgmt")

if check_json_field "$RESPONSE" ".success" "true"; then
    log_success "Management network created"

    # Check network details
    SUBNET=$(echo "$RESPONSE" | jq -r '.network.subnet' 2>/dev/null)
    GATEWAY=$(echo "$RESPONSE" | jq -r '.network.gateway' 2>/dev/null)
    NETWORK_NAME=$(echo "$RESPONSE" | jq -r '.network.network_name' 2>/dev/null)
    log_info "  Subnet: $SUBNET, Gateway: $GATEWAY"
    log_info "  Network name: $NETWORK_NAME"

    # Verify Docker network exists
    if docker network inspect "$NETWORK_NAME" > /dev/null 2>&1; then
        log_success "Docker management network exists"
    else
        log_fail "Docker management network not found"
    fi
else
    ERROR=$(echo "$RESPONSE" | jq -r '.error' 2>/dev/null)
    log_fail "Management network creation failed: $ERROR"
fi

# Test idempotency - creating again should return existing
log_info "Testing management network idempotency..."
RESPONSE2=$(api_call POST "/ovs-plugin/labs/${TEST_LAB_ID}/mgmt")
if check_json_field "$RESPONSE2" ".success" "true"; then
    log_success "Management network creation is idempotent"
else
    log_fail "Management network creation not idempotent"
fi

# ═══════════════════════════════════════════════════════════════
# TEST 9: External Interface List (Empty)
# ═══════════════════════════════════════════════════════════════

log_section "Test 9: External Interface Listing"

log_info "Testing /ovs-plugin/labs/${TEST_LAB_ID}/external..."
RESPONSE=$(api_call GET "/ovs-plugin/labs/${TEST_LAB_ID}/external")

if [[ "$HTTP_CODE" == "200" ]]; then
    log_success "External list endpoint returned 200"
else
    log_fail "External list endpoint returned $HTTP_CODE"
fi

if echo "$RESPONSE" | jq -e '.interfaces' > /dev/null 2>&1; then
    IFACE_COUNT=$(echo "$RESPONSE" | jq -r '.interfaces | length')
    log_success "External list has 'interfaces' array ($IFACE_COUNT interfaces)"
else
    log_fail "External list missing 'interfaces' field"
fi

# ═══════════════════════════════════════════════════════════════
# TEST 10: External Interface Attachment (if dummy interface available)
# ═══════════════════════════════════════════════════════════════

log_section "Test 10: External Interface Attachment"

# Create a dummy interface for testing
DUMMY_IFACE="test-ext-$$"
log_info "Creating dummy interface for testing: $DUMMY_IFACE"

if ip link add "$DUMMY_IFACE" type dummy 2>/dev/null; then
    ip link set "$DUMMY_IFACE" up
    log_success "Created dummy interface: $DUMMY_IFACE"

    # Try to attach it
    log_info "Attaching external interface..."
    RESPONSE=$(api_call POST "/ovs-plugin/labs/${TEST_LAB_ID}/external" \
        "{\"external_interface\": \"$DUMMY_IFACE\", \"vlan_tag\": 100}")

    if check_json_field "$RESPONSE" ".success" "true"; then
        VLAN=$(echo "$RESPONSE" | jq -r '.vlan_tag')
        log_success "External interface attached (VLAN: $VLAN)"

        # Verify it's listed
        LIST_RESPONSE=$(api_call GET "/ovs-plugin/labs/${TEST_LAB_ID}/external")
        IFACE_COUNT=$(echo "$LIST_RESPONSE" | jq -r '.interfaces | length')
        if [[ "$IFACE_COUNT" -ge 1 ]]; then
            log_success "External interface appears in list"
        else
            log_fail "External interface not in list after attachment"
        fi

        # Test detachment
        log_info "Detaching external interface..."
        DETACH_RESPONSE=$(api_call DELETE "/ovs-plugin/labs/${TEST_LAB_ID}/external/${DUMMY_IFACE}")
        if check_json_field "$DETACH_RESPONSE" ".success" "true"; then
            log_success "External interface detached"
        else
            log_fail "External interface detachment failed"
        fi
    else
        ERROR=$(echo "$RESPONSE" | jq -r '.error' 2>/dev/null)
        log_fail "External interface attachment failed: $ERROR"
    fi

    # Clean up dummy interface
    ip link delete "$DUMMY_IFACE" 2>/dev/null || true
else
    log_skip "Could not create dummy interface (requires root)"
fi

# ═══════════════════════════════════════════════════════════════
# TEST 11: VXLAN Tunnel Creation
# ═══════════════════════════════════════════════════════════════

log_section "Test 11: VXLAN Tunnel"

# Get local IP
LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' || echo "127.0.0.1")
REMOTE_IP="10.255.255.1"  # Fake remote IP for testing
VNI=200001

log_info "Creating VXLAN tunnel (local=$LOCAL_IP, remote=$REMOTE_IP, vni=$VNI)..."
RESPONSE=$(api_call POST "/ovs-plugin/labs/${TEST_LAB_ID}/vxlan" \
    "{\"link_id\": \"test-link\", \"local_ip\": \"$LOCAL_IP\", \"remote_ip\": \"$REMOTE_IP\", \"vni\": $VNI, \"vlan_tag\": 100}")

if check_json_field "$RESPONSE" ".success" "true"; then
    PORT_NAME=$(echo "$RESPONSE" | jq -r '.port_name')
    log_success "VXLAN tunnel created: $PORT_NAME"

    # Verify VXLAN interface exists
    if ip link show "vx$VNI" > /dev/null 2>&1; then
        log_success "VXLAN interface exists: vx$VNI"
    else
        log_fail "VXLAN interface not found"
    fi

    # Test deletion
    log_info "Deleting VXLAN tunnel..."
    DELETE_RESPONSE=$(api_call DELETE "/ovs-plugin/labs/${TEST_LAB_ID}/vxlan/${VNI}")
    if check_json_field "$DELETE_RESPONSE" ".success" "true"; then
        log_success "VXLAN tunnel deleted"
    else
        log_fail "VXLAN tunnel deletion failed"
    fi
else
    ERROR=$(echo "$RESPONSE" | jq -r '.error' 2>/dev/null)
    log_fail "VXLAN tunnel creation failed: $ERROR"
fi

# ═══════════════════════════════════════════════════════════════
# TEST 12: State Recovery Simulation
# ═══════════════════════════════════════════════════════════════

log_section "Test 12: State Recovery Check"

log_info "This test verifies the state recovery logic is present"
log_info "Full state recovery testing requires agent restart"

# Check that bridge still exists after all operations
if ovs-vsctl br-exists "$BRIDGE_NAME" 2>/dev/null; then
    log_success "OVS bridge still exists: $BRIDGE_NAME"

    # List ports
    PORTS=$(ovs-vsctl list-ports "$BRIDGE_NAME" 2>/dev/null | wc -l)
    log_info "Bridge has $PORTS port(s)"
else
    log_fail "OVS bridge disappeared: $BRIDGE_NAME"
fi

# Verify plugin can still see the lab
RESPONSE=$(api_call GET "/ovs-plugin/labs/${TEST_LAB_ID}")
if echo "$RESPONSE" | jq -e '.bridge_name' > /dev/null 2>&1; then
    log_success "Plugin still tracks the lab"
else
    log_fail "Plugin lost track of the lab"
fi

# ═══════════════════════════════════════════════════════════════
# TEST 13: Delete Management Network
# ═══════════════════════════════════════════════════════════════

log_section "Test 13: Delete Management Network"

log_info "Deleting management network..."
RESPONSE=$(api_call DELETE "/ovs-plugin/labs/${TEST_LAB_ID}/mgmt")

if check_json_field "$RESPONSE" ".success" "true"; then
    log_success "Management network deleted"
elif check_json_field "$RESPONSE" ".error" "Network not found"; then
    log_success "Management network already deleted"
else
    ERROR=$(echo "$RESPONSE" | jq -r '.error' 2>/dev/null)
    log_fail "Management network deletion failed: $ERROR"
fi

# ═══════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════

log_section "Test Summary"

TOTAL=$((TESTS_PASSED + TESTS_FAILED + TESTS_SKIPPED))

echo ""
echo -e "  ${GREEN}Passed:${NC}  $TESTS_PASSED"
echo -e "  ${RED}Failed:${NC}  $TESTS_FAILED"
echo -e "  ${YELLOW}Skipped:${NC} $TESTS_SKIPPED"
echo -e "  Total:   $TOTAL"
echo ""

if [[ $TESTS_FAILED -eq 0 ]]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed.${NC}"
    exit 1
fi
