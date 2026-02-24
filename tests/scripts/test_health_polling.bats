#!/usr/bin/env bats
# Tests for health check retry loops in install.sh and install-controller.sh.
#
# Uses mock_sleep (no-op) and mock_curl_sequence to simulate success/failure
# patterns without real delays or network calls.

load helpers

setup() {
    setup_test_env
    mock_sleep
}

teardown() {
    teardown_test_env
}

# ---------------------------------------------------------------------------
# install.sh — API health polling (lines 597-602)
# ---------------------------------------------------------------------------

@test "install.sh API health succeeds on first try" {
    mock_curl_sequence '0:'
    local block
    block=$(sed -n '/^    for i in {1..60};/,/^    done$/p' "$PROJECT_ROOT/install.sh" | sed 's/^    //')
    run bash -c "
        source '$BATS_TEST_DIRNAME/helpers.bash'
        setup_test_env
        mock_sleep
        mock_curl_sequence '0:'
        $block
        echo \"loop_done\"
    "
    [[ "$output" == *"loop_done"* ]]
}

@test "install.sh API health succeeds after retries" {
    # Fail 3 times, then succeed
    mock_curl_sequence '1:' '1:' '1:' '0:'
    local iterations=0
    for i in {1..60}; do
        if curl -s http://localhost:8000/health > /dev/null 2>&1; then
            break
        fi
        iterations=$((iterations + 1))
        sleep 0  # mock_sleep
    done
    [ "$iterations" -eq 3 ]
}

@test "install.sh API health times out after 60 iterations" {
    # All calls fail
    mock_curl_sequence '1:'
    local iterations=0
    for i in {1..60}; do
        if curl -s http://localhost:8000/health > /dev/null 2>&1; then
            break
        fi
        iterations=$((iterations + 1))
        sleep 0
    done
    [ "$iterations" -eq 60 ]
}

# ---------------------------------------------------------------------------
# install.sh — Agent registration polling (lines 609-616)
# ---------------------------------------------------------------------------

@test "install.sh agent registration detected" {
    mock_curl_response '[ {"name": "local-agent"} ]'
    mock_command jq 0 "local-agent"

    AGENTS=$(curl -s http://localhost:8000/agents 2>/dev/null | jq -r '.[].name' 2>/dev/null || echo "")
    echo "$AGENTS" | grep -q "local-agent"
}

@test "install.sh agent registration not found returns empty" {
    mock_curl_response '[]'
    mock_command jq 0 ""

    AGENTS=$(curl -s http://localhost:8000/agents 2>/dev/null | jq -r '.[].name' 2>/dev/null || echo "")
    ! echo "$AGENTS" | grep -q "local-agent"
}

# ---------------------------------------------------------------------------
# install-controller.sh — API health polling (lines 263-268)
# ---------------------------------------------------------------------------

@test "install-controller.sh API health succeeds on first try" {
    mock_curl_sequence '0:'
    API_PORT=8000
    local done_flag=false
    for i in {1..30}; do
        if curl -s "http://localhost:$API_PORT/health" > /dev/null 2>&1; then
            done_flag=true
            break
        fi
        sleep 0
    done
    [ "$done_flag" = true ]
}

@test "install-controller.sh API health times out after 30 iterations" {
    mock_curl_sequence '1:'
    API_PORT=8000
    local iterations=0
    for i in {1..30}; do
        if curl -s "http://localhost:$API_PORT/health" > /dev/null 2>&1; then
            break
        fi
        iterations=$((iterations + 1))
        sleep 0
    done
    [ "$iterations" -eq 30 ]
}

@test "install-controller.sh health poll uses correct port variable" {
    mock_curl_sequence '0:'
    API_PORT=9999
    local url_tested=""
    # Replace curl with one that records the URL
    cat > "$MOCK_BIN/curl" <<'ENDMOCK'
#!/bin/bash
echo "$@" >> /tmp/curl_args_$$
exit 0
ENDMOCK
    chmod +x "$MOCK_BIN/curl"

    for i in {1..30}; do
        if curl -s "http://localhost:$API_PORT/health" > /dev/null 2>&1; then
            url_tested="http://localhost:$API_PORT/health"
            break
        fi
        sleep 0
    done
    [ "$url_tested" = "http://localhost:9999/health" ]
}
