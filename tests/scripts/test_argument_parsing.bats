#!/usr/bin/env bats
# Tests for argument parsing across install.sh, agent/install.sh, and upgrade.sh.
#
# Strategy:
#   - Flags that cause early exit (--help, unknown) are tested by running
#     the actual script.
#   - Flags that set variables are tested by extracting the while-loop
#     argument parser with sed and eval-ing it with controlled arguments.

load helpers

setup() {
    setup_test_env
    # Mock curl in case any code path triggers it (e.g. resolve_install_target)
    mock_curl_response '{"tag_name":"v0.4.0"}'
}

teardown() {
    teardown_test_env
}

# ---------------------------------------------------------------------------
# install.sh
# ---------------------------------------------------------------------------

@test "install.sh --help exits 0" {
    run bash "$PROJECT_ROOT/install.sh" --help
    [ "$status" -eq 0 ]
}

@test "install.sh --help shows Usage" {
    run bash "$PROJECT_ROOT/install.sh" --help
    [[ "$output" == *"Usage"* ]]
}

@test "install.sh --help mentions --branch flag" {
    run bash "$PROJECT_ROOT/install.sh" --help
    [[ "$output" == *"--branch"* ]]
}

@test "install.sh rejects unknown flag" {
    run bash "$PROJECT_ROOT/install.sh" --nonexistent-flag
    [ "$status" -ne 0 ]
    [[ "$output" == *"Unknown"* ]]
}

@test "install.sh --controller sets INSTALL_CONTROLLER=true" {
    # Set the same defaults the script uses
    INSTALL_CONTROLLER=false
    INSTALL_AGENT=false
    AGENT_NAME=""
    CONTROLLER_URL=""
    LOCAL_IP=""
    AGENT_PORT="8001"
    UNINSTALL=false
    FRESH_INSTALL=false
    BRANCH_OVERRIDE=""
    BRANCH=""
    INSTALL_DIR="$TEST_TMPDIR"

    set -- --controller
    eval "$(sed -n '/^while \[\[/,/^done$/p' "$PROJECT_ROOT/install.sh")"

    [ "$INSTALL_CONTROLLER" = true ]
}

@test "install.sh --agent sets INSTALL_AGENT=true" {
    INSTALL_CONTROLLER=false
    INSTALL_AGENT=false
    AGENT_NAME=""
    CONTROLLER_URL=""
    LOCAL_IP=""
    AGENT_PORT="8001"
    UNINSTALL=false
    FRESH_INSTALL=false
    BRANCH_OVERRIDE=""
    BRANCH=""
    INSTALL_DIR="$TEST_TMPDIR"

    set -- --agent
    eval "$(sed -n '/^while \[\[/,/^done$/p' "$PROJECT_ROOT/install.sh")"

    [ "$INSTALL_AGENT" = true ]
}

@test "install.sh --branch without value exits non-zero" {
    run bash "$PROJECT_ROOT/install.sh" --branch
    [ "$status" -ne 0 ]
}

# ---------------------------------------------------------------------------
# agent/install.sh
# ---------------------------------------------------------------------------

@test "agent/install.sh --update sets UPDATE_ONLY=true" {
    AGENT_NAME=""
    CONTROLLER_URL=""
    REDIS_URL=""
    LOCAL_IP=""
    AGENT_PORT="8001"
    INSTALL_DOCKER=true
    INSTALL_LIBVIRT=false
    INSTALL_LIBVIRT_PYTHON=false
    UNINSTALL=false
    UPDATE_ONLY=false
    TARGET_MTU="0"
    BRANCH_OVERRIDE=""
    BRANCH=""
    INSTALL_DIR="$TEST_TMPDIR"

    set -- --update
    eval "$(sed -n '/^while \[\[/,/^done$/p' "$PROJECT_ROOT/agent/install.sh")"

    [ "$UPDATE_ONLY" = true ]
}

# ---------------------------------------------------------------------------
# scripts/upgrade.sh
# ---------------------------------------------------------------------------

@test "upgrade.sh --tag sets BRANCH" {
    BRANCH=""
    SKIP_BACKUP=false
    NO_PULL=false
    ROOT_DIR="$TEST_TMPDIR"

    set -- --tag v0.5.0
    eval "$(sed -n '/^while \[\[/,/^done$/p' "$PROJECT_ROOT/scripts/upgrade.sh")"

    [ "$BRANCH" = "v0.5.0" ]
}

@test "upgrade.sh --help exits 0" {
    run bash "$PROJECT_ROOT/scripts/upgrade.sh" --help
    [ "$status" -eq 0 ]
}

@test "upgrade.sh --help mentions --tag flag" {
    run bash "$PROJECT_ROOT/scripts/upgrade.sh" --help
    [[ "$output" == *"--tag"* ]]
}
