#!/usr/bin/env bats
# Tests for docker compose command generation patterns across install scripts.
#
# Validates that the correct compose file, flags, and operations are used.

load helpers

setup() {
    setup_test_env
    mock_docker
}

teardown() {
    teardown_test_env
}

# ---------------------------------------------------------------------------
# Compose file reference
# ---------------------------------------------------------------------------

@test "install.sh uses docker-compose.gui.yml" {
    run grep -c "docker-compose.gui.yml" "$PROJECT_ROOT/install.sh"
    [ "$output" -gt 0 ]
}

@test "install-controller.sh uses docker-compose.gui.yml" {
    run grep -c "docker-compose.gui.yml" "$PROJECT_ROOT/install-controller.sh"
    [ "$output" -gt 0 ]
}

# ---------------------------------------------------------------------------
# Fresh install uses down -v for volume removal
# ---------------------------------------------------------------------------

@test "install.sh uses 'down -v' for cleanup" {
    run grep -c "down -v" "$PROJECT_ROOT/install.sh"
    [ "$output" -gt 0 ]
}

@test "install-controller.sh uninstall uses 'down -v'" {
    # The uninstall block calls docker compose down -v
    run grep "down -v" "$PROJECT_ROOT/install-controller.sh"
    [ "$status" -eq 0 ]
}

# ---------------------------------------------------------------------------
# Normal install uses up -d --build
# ---------------------------------------------------------------------------

@test "install.sh uses 'up -d --build' for startup" {
    run grep "up -d --build" "$PROJECT_ROOT/install.sh"
    [ "$status" -eq 0 ]
}

@test "install-controller.sh uses 'up -d' for startup" {
    run grep "up -d" "$PROJECT_ROOT/install-controller.sh"
    [ "$status" -eq 0 ]
}
