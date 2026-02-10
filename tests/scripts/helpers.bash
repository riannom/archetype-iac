#!/usr/bin/env bash
# Shared test helpers for BATS install/upgrade script tests.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Disable color codes for clean test output
GREEN='' YELLOW='' RED='' BLUE='' CYAN='' NC=''
export GREEN YELLOW RED BLUE CYAN NC

# ---------------------------------------------------------------------------
# Test environment setup / teardown
# ---------------------------------------------------------------------------

setup_test_env() {
    TEST_TMPDIR="$(mktemp -d)"
    MOCK_BIN="$TEST_TMPDIR/bin"
    mkdir -p "$MOCK_BIN"
    PATH="$MOCK_BIN:$PATH"
    export TEST_TMPDIR MOCK_BIN PATH
}

teardown_test_env() {
    [[ -n "${TEST_TMPDIR:-}" ]] && rm -rf "$TEST_TMPDIR"
}

# ---------------------------------------------------------------------------
# Stub logging functions used by install scripts
# ---------------------------------------------------------------------------

log_info()    { echo "[INFO] $1"; }
log_warn()    { echo "[WARN] $1"; }
log_error()   { echo "[ERROR] $1"; }
log_section() { echo "=== $1 ==="; }
export -f log_info log_warn log_error log_section

# ---------------------------------------------------------------------------
# Function extraction
# ---------------------------------------------------------------------------

# Extract a top-level shell function from a script and define it in the
# current shell.  The function must start at column 0 ("funcname() {") and
# end with a closing "}" also at column 0.
load_function() {
    local func_name="$1" script_path="$2"
    eval "$(sed -n "/^${func_name}()/,/^}/p" "$script_path")"
}

# ---------------------------------------------------------------------------
# curl mocking
# ---------------------------------------------------------------------------

# Mock curl to return a fixed response body.
# Usage: mock_curl_response '{"tag_name":"v0.4.0"}' [exit_code]
mock_curl_response() {
    local response="$1" exit_code="${2:-0}"
    printf '%s\n' "$response" > "$TEST_TMPDIR/curl_response"
    cat > "$MOCK_BIN/curl" <<ENDMOCK
#!/bin/bash
cat "$TEST_TMPDIR/curl_response"
exit $exit_code
ENDMOCK
    chmod +x "$MOCK_BIN/curl"
}

# Mock curl to fail with an exit code and no output.
# Usage: mock_curl_exit 28   # timeout
mock_curl_exit() {
    local exit_code="$1"
    cat > "$MOCK_BIN/curl" <<ENDMOCK
#!/bin/bash
exit $exit_code
ENDMOCK
    chmod +x "$MOCK_BIN/curl"
}

# ---------------------------------------------------------------------------
# openssl / ip / hostname mocking (for .env generation tests)
# ---------------------------------------------------------------------------

# Mock openssl to return deterministic values.
# Returns different hex values on successive calls so JWT_SECRET != SESSION_SECRET.
mock_openssl() {
    cat > "$MOCK_BIN/openssl" <<ENDMOCK
#!/bin/bash
COUNTER_FILE="$TEST_TMPDIR/openssl_counter"
if [[ "\$1" == "rand" ]]; then
    case "\$2" in
        -hex)
            if [ -f "\$COUNTER_FILE" ]; then
                echo "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
            else
                touch "\$COUNTER_FILE"
                echo "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            fi
            ;;
        -base64)
            echo "dGVzdHBhc3MxMjM="
            ;;
    esac
fi
ENDMOCK
    chmod +x "$MOCK_BIN/openssl"
}

mock_ip() {
    cat > "$MOCK_BIN/ip" <<'ENDMOCK'
#!/bin/bash
echo "1.1.1.1 via 10.0.0.1 dev eth0 src 10.0.0.100 uid 1000"
ENDMOCK
    chmod +x "$MOCK_BIN/ip"
}

mock_hostname() {
    cat > "$MOCK_BIN/hostname" <<'ENDMOCK'
#!/bin/bash
case "$1" in
    -I) echo "10.0.0.100" ;;
    -s) echo "testhost" ;;
    *)  echo "testhost" ;;
esac
ENDMOCK
    chmod +x "$MOCK_BIN/hostname"
}
