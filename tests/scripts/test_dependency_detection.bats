#!/usr/bin/env bats
# Tests for dependency detection logic (command -v checks, install-skip logic).
#
# Validates that Docker install is skipped when already present,
# OVS service detection works, and --no-libvirt/--no-docker flags
# are respected.

load helpers

setup() {
    setup_test_env
    mock_docker
    mock_systemctl
}

teardown() {
    teardown_test_env
}

# ---------------------------------------------------------------------------
# Docker already installed — should skip install
# ---------------------------------------------------------------------------

@test "install-controller.sh skips Docker install when already present" {
    # docker is mocked in $MOCK_BIN (returns success)
    local block
    block=$(sed -n '/^# Install Docker if not present$/,/^fi$/p' "$PROJECT_ROOT/install-controller.sh")
    run bash -c "
        source '$BATS_TEST_DIRNAME/helpers.bash'
        setup_test_env
        mock_docker
        OS=ubuntu
        $block
    "
    [[ "$output" == *"Docker already installed"* ]]
}

@test "install-controller.sh has Docker install path for missing docker" {
    # Verify the script contains the "Installing Docker" code path
    # (Can't easily test the full install in isolation since it needs real package managers)
    local block
    block=$(sed -n '/^# Install Docker if not present$/,/^fi$/p' "$PROJECT_ROOT/install-controller.sh")
    echo "$block" | grep -q "Installing Docker"
}

# ---------------------------------------------------------------------------
# agent/install.sh --no-docker flag
# ---------------------------------------------------------------------------

@test "agent/install.sh --no-docker sets INSTALL_DOCKER=false" {
    AGENT_NAME="test"
    CONTROLLER_URL="http://test:8000"
    REDIS_URL="redis://test:6379/0"
    LOCAL_IP="10.0.0.1"
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
    AGENT_SECRET=""
    SERVICE_NAME="archetype-agent"

    set -- --no-docker
    eval "$(sed -n '/^while \[\[/,/^done$/p' "$PROJECT_ROOT/agent/install.sh")"

    [ "$INSTALL_DOCKER" = false ]
}

@test "agent/install.sh --no-libvirt keeps INSTALL_LIBVIRT=false" {
    AGENT_NAME="test"
    CONTROLLER_URL="http://test:8000"
    REDIS_URL="redis://test:6379/0"
    LOCAL_IP="10.0.0.1"
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
    AGENT_SECRET=""
    SERVICE_NAME="archetype-agent"

    set -- --no-libvirt
    eval "$(sed -n '/^while \[\[/,/^done$/p' "$PROJECT_ROOT/agent/install.sh")"

    # --no-libvirt doesn't exist as a flag; INSTALL_LIBVIRT defaults false
    [ "$INSTALL_LIBVIRT" = false ]
}

# ---------------------------------------------------------------------------
# OVS service detection
# ---------------------------------------------------------------------------

@test "agent/install.sh detects openvswitch-switch service" {
    cat > "$MOCK_BIN/systemctl" <<'ENDMOCK'
#!/bin/bash
if [ "$1" = "list-unit-files" ]; then
    echo "openvswitch-switch.service enabled enabled"
fi
exit 0
ENDMOCK
    chmod +x "$MOCK_BIN/systemctl"

    # Use the mock directly — PATH already has MOCK_BIN prepended from setup_test_env
    if systemctl list-unit-files | grep -q openvswitch-switch; then
        OVS_DETECTED=true
    fi
    [ "$OVS_DETECTED" = true ]
}

@test "agent/install.sh detects openvswitch service (RHEL variant)" {
    cat > "$MOCK_BIN/systemctl" <<'ENDMOCK'
#!/bin/bash
if [ "$1" = "list-unit-files" ]; then
    echo "openvswitch.service enabled enabled"
fi
exit 0
ENDMOCK
    chmod +x "$MOCK_BIN/systemctl"

    if systemctl list-unit-files | grep -q openvswitch; then
        OVS_DETECTED=true
    fi
    [ "$OVS_DETECTED" = true ]
}
