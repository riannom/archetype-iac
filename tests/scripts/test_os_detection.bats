#!/usr/bin/env bats
# Tests for OS detection from /etc/os-release across all 3 installers.
#
# Strategy: mock /etc/os-release via TEST_TMPDIR, rewrite the detection block
# to source from the mock path, then verify $OS and $VERSION variables.

load helpers

setup() {
    setup_test_env
}

teardown() {
    teardown_test_env
}

# Helper: extract and run OS detection with a mocked /etc/os-release.
# The install scripts check /etc/os-release directly; we rewrite the path
# to point at our mock before eval-ing.
run_os_detection() {
    local script="$1"
    local os_id="$2"
    local version="${3:-}"

    mock_os_release "$os_id" "$version"

    # Extract the OS detection block and rewrite /etc/os-release path
    local block
    block=$(sed -n '/^# Detect OS$/,/^fi$/p' "$script" \
        | sed "s|/etc/os-release|$TEST_TMPDIR/etc/os-release|g")
    eval "$block"
}

# ---------------------------------------------------------------------------
# install.sh
# ---------------------------------------------------------------------------

@test "install.sh detects ubuntu" {
    run_os_detection "$PROJECT_ROOT/install.sh" "ubuntu" "22.04"
    [ "$OS" = "ubuntu" ]
}

@test "install.sh detects rocky" {
    run_os_detection "$PROJECT_ROOT/install.sh" "rocky" "9.3"
    [ "$OS" = "rocky" ]
}

@test "install.sh exits on missing /etc/os-release" {
    # Don't create the mock file
    local block
    block=$(sed -n '/^# Detect OS$/,/^fi$/p' "$PROJECT_ROOT/install.sh" \
        | sed "s|/etc/os-release|$TEST_TMPDIR/etc/os-release|g")
    run bash -c "$block"
    [ "$status" -ne 0 ]
}

# ---------------------------------------------------------------------------
# install-controller.sh
# ---------------------------------------------------------------------------

@test "install-controller.sh detects debian" {
    run_os_detection "$PROJECT_ROOT/install-controller.sh" "debian" "12"
    [ "$OS" = "debian" ]
}

@test "install-controller.sh detects rhel" {
    run_os_detection "$PROJECT_ROOT/install-controller.sh" "rhel" "9.2"
    [ "$OS" = "rhel" ]
}

# ---------------------------------------------------------------------------
# agent/install.sh
# ---------------------------------------------------------------------------

@test "agent/install.sh detects ubuntu and sets VERSION" {
    run_os_detection "$PROJECT_ROOT/agent/install.sh" "ubuntu" "22.04"
    [ "$OS" = "ubuntu" ]
    [ "$VERSION" = "22.04" ]
}

@test "agent/install.sh detects rocky and sets VERSION" {
    run_os_detection "$PROJECT_ROOT/agent/install.sh" "rocky" "9.3"
    [ "$OS" = "rocky" ]
    [ "$VERSION" = "9.3" ]
}

@test "agent/install.sh exits on missing /etc/os-release" {
    local block
    block=$(sed -n '/^# Detect OS$/,/^fi$/p' "$PROJECT_ROOT/agent/install.sh" \
        | sed "s|/etc/os-release|$TEST_TMPDIR/etc/os-release|g")
    run bash -c "$block"
    [ "$status" -ne 0 ]
}
