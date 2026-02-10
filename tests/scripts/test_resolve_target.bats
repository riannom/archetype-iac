#!/usr/bin/env bats
# Tests for resolve_install_target() and resolve_upgrade_target().

load helpers

setup() {
    setup_test_env
    BRANCH=""
    BRANCH_OVERRIDE=""
}

teardown() {
    teardown_test_env
}

# ---------------------------------------------------------------------------
# resolve_install_target  (install.sh, install-controller.sh, agent/install.sh)
# ---------------------------------------------------------------------------

@test "resolve_install_target: uses --branch override" {
    load_function resolve_install_target "$PROJECT_ROOT/install.sh"
    BRANCH_OVERRIDE="v1.0.0"
    resolve_install_target
    [ "$BRANCH" = "v1.0.0" ]
}

@test "resolve_install_target: extracts tag from GitHub API response" {
    load_function resolve_install_target "$PROJECT_ROOT/install.sh"
    mock_curl_response '{"tag_name":"v0.4.0","name":"Release v0.4.0"}'
    resolve_install_target
    [ "$BRANCH" = "v0.4.0" ]
}

@test "resolve_install_target: falls back to main on curl timeout (exit 28)" {
    load_function resolve_install_target "$PROJECT_ROOT/install.sh"
    mock_curl_exit 28
    resolve_install_target
    [ "$BRANCH" = "main" ]
}

@test "resolve_install_target: falls back to main on empty JSON response" {
    load_function resolve_install_target "$PROJECT_ROOT/install.sh"
    mock_curl_response '{}'
    resolve_install_target
    [ "$BRANCH" = "main" ]
}

@test "resolve_install_target: falls back to main on network error (exit 6)" {
    load_function resolve_install_target "$PROJECT_ROOT/install.sh"
    mock_curl_exit 6
    resolve_install_target
    [ "$BRANCH" = "main" ]
}

@test "resolve_install_target: handles malformed HTML response gracefully" {
    load_function resolve_install_target "$PROJECT_ROOT/install.sh"
    mock_curl_response '<html>404 Not Found</html>'
    resolve_install_target
    [ "$BRANCH" = "main" ]
}

# Verify the same function works identically in install-controller.sh
@test "resolve_install_target (install-controller.sh): extracts tag from API" {
    load_function resolve_install_target "$PROJECT_ROOT/install-controller.sh"
    mock_curl_response '{"tag_name":"v0.3.8"}'
    resolve_install_target
    [ "$BRANCH" = "v0.3.8" ]
}

# Verify agent/install.sh has the same resolve logic
@test "resolve_install_target (agent/install.sh): extracts tag from API" {
    load_function resolve_install_target "$PROJECT_ROOT/agent/install.sh"
    mock_curl_response '{"tag_name":"v0.4.0"}'
    resolve_install_target
    [ "$BRANCH" = "v0.4.0" ]
}

# ---------------------------------------------------------------------------
# resolve_upgrade_target  (scripts/upgrade.sh)
# ---------------------------------------------------------------------------

@test "resolve_upgrade_target: --tag sets BRANCH directly (skips resolve)" {
    load_function resolve_upgrade_target "$PROJECT_ROOT/scripts/upgrade.sh"
    BRANCH="v0.5.0"   # Already set by --tag in the argument parser
    resolve_upgrade_target
    [ "$BRANCH" = "v0.5.0" ]
}

@test "resolve_upgrade_target: --branch skips resolve" {
    load_function resolve_upgrade_target "$PROJECT_ROOT/scripts/upgrade.sh"
    BRANCH="feature-x"
    resolve_upgrade_target
    [ "$BRANCH" = "feature-x" ]
}

@test "resolve_upgrade_target: resolves latest release when BRANCH is empty" {
    load_function resolve_upgrade_target "$PROJECT_ROOT/scripts/upgrade.sh"
    mock_curl_response '{"tag_name":"v0.4.0"}'
    BRANCH=""
    resolve_upgrade_target
    [ "$BRANCH" = "v0.4.0" ]
}

@test "resolve_upgrade_target: falls back to main on API failure" {
    load_function resolve_upgrade_target "$PROJECT_ROOT/scripts/upgrade.sh"
    mock_curl_exit 28
    BRANCH=""
    resolve_upgrade_target
    [ "$BRANCH" = "main" ]
}
