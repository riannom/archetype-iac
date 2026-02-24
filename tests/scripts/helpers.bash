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

# ---------------------------------------------------------------------------
# sleep / polling mocks
# ---------------------------------------------------------------------------

# No-op sleep for testing polling loops without delays.
mock_sleep() {
    cat > "$MOCK_BIN/sleep" <<'ENDMOCK'
#!/bin/bash
exit 0
ENDMOCK
    chmod +x "$MOCK_BIN/sleep"
}

# Mock curl that returns different responses on successive calls.
# Usage: mock_curl_sequence '200:{"status":"ok"}' '500:' '200:{"status":"ok"}'
# Each arg is "exit_code:body". Counter persists across calls.
mock_curl_sequence() {
    local i=0
    for entry in "$@"; do
        local code="${entry%%:*}"
        local body="${entry#*:}"
        printf '%s\n' "$body" > "$TEST_TMPDIR/curl_seq_body_$i"
        printf '%s\n' "$code" > "$TEST_TMPDIR/curl_seq_code_$i"
        i=$((i + 1))
    done
    printf '%s\n' "$i" > "$TEST_TMPDIR/curl_seq_total"
    printf '0\n' > "$TEST_TMPDIR/curl_seq_counter"

    cat > "$MOCK_BIN/curl" <<ENDMOCK
#!/bin/bash
COUNTER_FILE="$TEST_TMPDIR/curl_seq_counter"
TOTAL=\$(cat "$TEST_TMPDIR/curl_seq_total")
IDX=\$(cat "\$COUNTER_FILE")
if [ "\$IDX" -ge "\$TOTAL" ]; then
    IDX=\$((\$TOTAL - 1))
fi
cat "$TEST_TMPDIR/curl_seq_body_\$IDX"
CODE=\$(cat "$TEST_TMPDIR/curl_seq_code_\$IDX")
echo \$((\$IDX + 1)) > "\$COUNTER_FILE"
exit \$CODE
ENDMOCK
    chmod +x "$MOCK_BIN/curl"
}

# ---------------------------------------------------------------------------
# OS release mocking
# ---------------------------------------------------------------------------

# Write a fake /etc/os-release to $TEST_TMPDIR/etc/os-release and set up
# the test to source it instead of the real one.
# Usage: mock_os_release ubuntu 22.04
mock_os_release() {
    local id="$1" version="${2:-}"
    mkdir -p "$TEST_TMPDIR/etc"
    cat > "$TEST_TMPDIR/etc/os-release" <<EOF
ID=$id
VERSION_ID=$version
NAME="Mock OS"
EOF
}

# ---------------------------------------------------------------------------
# systemctl / docker / git recorders
# ---------------------------------------------------------------------------

# Mock systemctl — logs all invocations to systemctl.log, returns success.
mock_systemctl() {
    cat > "$MOCK_BIN/systemctl" <<ENDMOCK
#!/bin/bash
echo "systemctl \$*" >> "$TEST_TMPDIR/systemctl.log"
# is-active checks: return success by default
if [ "\$1" = "is-active" ]; then
    exit 0
fi
exit 0
ENDMOCK
    chmod +x "$MOCK_BIN/systemctl"
}

# Mock docker — logs all invocations to docker.log, returns success.
mock_docker() {
    cat > "$MOCK_BIN/docker" <<ENDMOCK
#!/bin/bash
echo "docker \$*" >> "$TEST_TMPDIR/docker.log"
if [ "\$1" = "--version" ]; then
    echo "Docker version 24.0.0, build abc123"
fi
if [ "\$1" = "compose" ]; then
    echo "docker compose executed"
fi
exit 0
ENDMOCK
    chmod +x "$MOCK_BIN/docker"
}

# Mock git — logs all invocations to git.log, returns success.
mock_git() {
    cat > "$MOCK_BIN/git" <<ENDMOCK
#!/bin/bash
echo "git \$*" >> "$TEST_TMPDIR/git.log"
if [ "\$1" = "rev-parse" ] && [ "\$2" = "--short" ]; then
    echo "abc1234"
fi
exit 0
ENDMOCK
    chmod +x "$MOCK_BIN/git"
}

# ---------------------------------------------------------------------------
# Generic command mock
# ---------------------------------------------------------------------------

# Create a simple pass/fail mock for any command.
# Usage: mock_command <name> [exit_code] [stdout]
mock_command() {
    local name="$1" exit_code="${2:-0}" stdout="${3:-}"
    cat > "$MOCK_BIN/$name" <<ENDMOCK
#!/bin/bash
echo "$stdout"
exit $exit_code
ENDMOCK
    chmod +x "$MOCK_BIN/$name"
}
