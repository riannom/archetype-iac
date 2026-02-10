#!/usr/bin/env bats
# Tests for .env file generation in install-controller.sh.
#
# The .env generation section is extracted from the script via sed and
# executed with mocked openssl / ip / hostname commands so we can verify
# the generated file contents without running a real installation.

load helpers

setup() {
    setup_test_env
    mock_openssl
    mock_ip
    mock_hostname

    # Variables consumed by the heredoc
    INSTALL_DIR="$TEST_TMPDIR"
    WEB_PORT="8080"
    API_PORT="8000"

    # Extract and run the .env generation section from install-controller.sh
    eval "$(sed -n '/^# Generate secrets$/,/^chmod 644/p' "$PROJECT_ROOT/install-controller.sh")"
}

teardown() {
    teardown_test_env
}

@test "generated .env contains DATABASE_URL" {
    grep -q "DATABASE_URL=" "$TEST_TMPDIR/.env"
}

@test "generated .env contains PROVIDER=docker" {
    grep -q "PROVIDER=docker" "$TEST_TMPDIR/.env"
}

@test "generated .env contains INTERNAL_URL" {
    grep -q "INTERNAL_URL=" "$TEST_TMPDIR/.env"
}

@test "generated .env JWT_SECRET is 64 hex characters" {
    local jwt
    jwt=$(grep '^JWT_SECRET=' "$TEST_TMPDIR/.env" | cut -d= -f2)
    [[ "$jwt" =~ ^[a-f0-9]{64}$ ]]
}

@test "generated .env SESSION_SECRET differs from JWT_SECRET" {
    local jwt session
    jwt=$(grep '^JWT_SECRET=' "$TEST_TMPDIR/.env" | cut -d= -f2)
    session=$(grep '^SESSION_SECRET=' "$TEST_TMPDIR/.env" | cut -d= -f2)
    [ "$jwt" != "$session" ]
}
