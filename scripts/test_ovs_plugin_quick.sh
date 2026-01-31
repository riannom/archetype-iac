#!/bin/bash
#
# Quick test for Docker OVS Plugin API endpoints
# Does NOT create real resources - just validates API responses
#
# Usage:
#   ./scripts/test_ovs_plugin_quick.sh [agent-url]
#

AGENT_URL="${1:-http://localhost:8001}"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

echo "Testing OVS Plugin endpoints at $AGENT_URL"
echo "============================================"
echo ""

PASSED=0
FAILED=0

test_endpoint() {
    local method="$1"
    local endpoint="$2"
    local expected_field="$3"
    local data="$4"

    local url="${AGENT_URL}${endpoint}"

    if [[ "$method" == "GET" ]]; then
        response=$(curl -s -w "\n%{http_code}" "$url" 2>/dev/null)
    else
        if [[ -n "$data" ]]; then
            response=$(curl -s -w "\n%{http_code}" -X "$method" -H "Content-Type: application/json" -d "$data" "$url" 2>/dev/null)
        else
            response=$(curl -s -w "\n%{http_code}" -X "$method" "$url" 2>/dev/null)
        fi
    fi

    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | sed '$d')

    # Check HTTP code
    if [[ "$http_code" != "200" && "$http_code" != "404" ]]; then
        echo -e "${RED}FAIL${NC} $method $endpoint - HTTP $http_code"
        ((FAILED++))
        return
    fi

    # Check expected field if provided
    if [[ -n "$expected_field" ]]; then
        if echo "$body" | jq -e "$expected_field" > /dev/null 2>&1; then
            echo -e "${GREEN}PASS${NC} $method $endpoint"
            ((PASSED++))
        else
            echo -e "${RED}FAIL${NC} $method $endpoint - missing field $expected_field"
            echo "  Response: $body"
            ((FAILED++))
        fi
    else
        echo -e "${GREEN}PASS${NC} $method $endpoint - HTTP $http_code"
        ((PASSED++))
    fi
}

# Test agent health first
echo "Checking agent connectivity..."
if ! curl -s "${AGENT_URL}/health" > /dev/null 2>&1; then
    echo -e "${RED}ERROR: Agent not reachable at $AGENT_URL${NC}"
    echo "Start the agent first: uvicorn agent.main:app --port 8001"
    exit 1
fi
echo ""

# Test endpoints
echo "Testing Plugin Health & Status..."
test_endpoint GET "/ovs-plugin/health" ".healthy"
test_endpoint GET "/ovs-plugin/status" ".labs_count"

echo ""
echo "Testing Lab-specific endpoints (with fake lab ID)..."
test_endpoint GET "/ovs-plugin/labs/fake-lab-id" ""  # May return error, that's OK
test_endpoint GET "/ovs-plugin/labs/fake-lab-id/ports" ".ports"
test_endpoint GET "/ovs-plugin/labs/fake-lab-id/flows" ""  # May return error

echo ""
echo "Testing Management Network endpoints..."
test_endpoint POST "/ovs-plugin/labs/fake-lab-id/mgmt" ""  # Will fail gracefully

echo ""
echo "Testing VXLAN endpoints..."
test_endpoint POST "/ovs-plugin/labs/fake-lab-id/vxlan" "" '{"link_id":"test","local_ip":"127.0.0.1","remote_ip":"127.0.0.2","vni":200001,"vlan_tag":100}'

echo ""
echo "Testing External Interface endpoints..."
test_endpoint GET "/ovs-plugin/labs/fake-lab-id/external" ".interfaces"
test_endpoint POST "/ovs-plugin/labs/fake-lab-id/external" "" '{"external_interface":"eth99"}'

echo ""
echo "============================================"
echo "Results: $PASSED passed, $FAILED failed"

if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}All endpoint tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed${NC}"
    exit 1
fi
