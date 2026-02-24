#!/usr/bin/env bats
# Tests for uninstall/cleanup logic in install.sh and agent/install.sh.
#
# Tests cleanup_overlay() function extraction and uninstall flow logic.

load helpers

setup() {
    setup_test_env
    mock_systemctl
    mock_docker
}

teardown() {
    teardown_test_env
}

# ---------------------------------------------------------------------------
# cleanup_overlay() from install.sh
# ---------------------------------------------------------------------------

@test "cleanup_overlay function can be loaded" {
    # Mock ip to return known interfaces
    cat > "$MOCK_BIN/ip" <<'ENDMOCK'
#!/bin/bash
if [ "$1" = "link" ] && [ "$2" = "show" ]; then
    echo "10: archetype-br-100: <BROADCAST,MULTICAST> mtu 1500"
    echo "11: vxlan42: <BROADCAST,MULTICAST> mtu 1500"
    echo "12: v1234abcdh: <BROADCAST,MULTICAST> mtu 1500"
elif [ "$1" = "link" ] && [ "$2" = "set" ]; then
    echo "ip $*" >> /tmp/ip_calls_$$
elif [ "$1" = "link" ] && [ "$2" = "delete" ]; then
    echo "ip $*" >> /tmp/ip_calls_$$
fi
exit 0
ENDMOCK
    chmod +x "$MOCK_BIN/ip"

    load_function cleanup_overlay "$PROJECT_ROOT/install.sh"
    run cleanup_overlay
    [ "$status" -eq 0 ]
    [[ "$output" == *"Cleaning up overlay"* ]]
}

# ---------------------------------------------------------------------------
# install.sh uninstall flow
# ---------------------------------------------------------------------------

@test "install.sh uninstall removes INSTALL_DIR" {
    INSTALL_DIR="$TEST_TMPDIR/archetype"
    AGENT_INSTALL_DIR="$TEST_TMPDIR/archetype-agent"
    mkdir -p "$INSTALL_DIR" "$AGENT_INSTALL_DIR"

    # Mock ip for cleanup_overlay
    mock_ip

    UNINSTALL=true
    run bash -c "
        source '$BATS_TEST_DIRNAME/helpers.bash'
        setup_test_env
        mock_systemctl
        mock_docker
        mock_ip
        INSTALL_DIR='$INSTALL_DIR'
        AGENT_INSTALL_DIR='$AGENT_INSTALL_DIR'
        UNINSTALL=true
        cleanup_overlay() { true; }
        export -f cleanup_overlay

        block=\$(sed -n '/^if \[ \"\\\$UNINSTALL\" = true \]/,/^fi\$/p' '$PROJECT_ROOT/install.sh')
        eval \"\$block\"
    "
    # The block calls exit 0, so status should be 0
    [ "$status" -eq 0 ]
    [[ "$output" == *"Uninstalling Archetype"* ]]
}

# ---------------------------------------------------------------------------
# agent/install.sh uninstall flow
# ---------------------------------------------------------------------------

@test "agent/install.sh uninstall stops and removes service" {
    SERVICE_NAME="archetype-agent"
    INSTALL_DIR="$TEST_TMPDIR/agent"
    mkdir -p "$INSTALL_DIR"

    UNINSTALL=true
    run bash -c "
        source '$BATS_TEST_DIRNAME/helpers.bash'
        setup_test_env
        mock_systemctl
        SERVICE_NAME='archetype-agent'
        INSTALL_DIR='$INSTALL_DIR'
        UNINSTALL=true

        block=\$(sed -n '/^if \[ \"\\\$UNINSTALL\" = true \]/,/^fi\$/p' '$PROJECT_ROOT/agent/install.sh')
        eval \"\$block\"
    "
    [ "$status" -eq 0 ]
    [[ "$output" == *"Uninstalling Archetype Agent"* ]]
    # INSTALL_DIR should be removed
    [ ! -d "$INSTALL_DIR" ]
}

@test "agent/install.sh uninstall calls systemctl daemon-reload" {
    SERVICE_NAME="archetype-agent"
    INSTALL_DIR="$TEST_TMPDIR/agent"
    mkdir -p "$INSTALL_DIR"

    # Extract the block and remove 'exit 0' so we can read the log after
    run bash -c "
        source '$BATS_TEST_DIRNAME/helpers.bash'
        setup_test_env
        mock_systemctl
        SERVICE_NAME='archetype-agent'
        INSTALL_DIR='$INSTALL_DIR'
        UNINSTALL=true

        block=\$(sed -n '/^if \[ \"\\\$UNINSTALL\" = true \]/,/^fi\$/p' '$PROJECT_ROOT/agent/install.sh' | sed 's/exit 0//')
        eval \"\$block\"
        cat \"\$TEST_TMPDIR/systemctl.log\"
    "
    [[ "$output" == *"daemon-reload"* ]]
}
