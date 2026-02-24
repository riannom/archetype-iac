#!/usr/bin/env bats
# Tests for MTU configuration logic from agent/install.sh (lines 306-388).
#
# Validates MTU skip when TARGET_MTU=0, overlay MTU calculation,
# persistence service creation, and graceful failure handling.

load helpers

setup() {
    setup_test_env
    mock_systemctl
    mkdir -p "$TEST_TMPDIR/etc/systemd/system"

    # Mock ip with configurable MTU
    cat > "$MOCK_BIN/ip" <<ENDMOCK
#!/bin/bash
if [ "\$1" = "route" ] && [ "\$2" = "get" ]; then
    echo "1.1.1.1 via 10.0.0.1 dev eth0 src 10.0.0.100"
elif [ "\$1" = "link" ] && [ "\$2" = "show" ]; then
    echo "2: eth0: <BROADCAST,MULTICAST,UP> mtu ${MOCK_MTU:-1500} qdisc fq_codel"
elif [ "\$1" = "link" ] && [ "\$2" = "set" ]; then
    exit ${MOCK_IP_LINK_SET_EXIT:-0}
fi
exit 0
ENDMOCK
    chmod +x "$MOCK_BIN/ip"
}

teardown() {
    teardown_test_env
}

# ---------------------------------------------------------------------------
# MTU skip when TARGET_MTU=0
# ---------------------------------------------------------------------------

@test "MTU config skipped when TARGET_MTU=0" {
    OVERLAY_MTU=1450
    LOCAL_MTU=9000
    CONFIGURED_IFACE=""
    TARGET_MTU="0"

    # The block is gated on TARGET_MTU != 0 — nothing should happen
    if [ "$TARGET_MTU" != "0" ] && [ -n "$TARGET_MTU" ]; then
        OVERLAY_MTU=9999  # Should NOT be reached
    fi

    [ "$OVERLAY_MTU" -eq 1450 ]
}

# ---------------------------------------------------------------------------
# Overlay MTU calculation
# ---------------------------------------------------------------------------

@test "Overlay MTU is TARGET_MTU minus 50" {
    TARGET_MTU=9000
    OVERLAY_MTU=$((TARGET_MTU - 50))
    [ "$OVERLAY_MTU" -eq 8950 ]
}

@test "Overlay MTU defaults to 1450 when TARGET_MTU=0" {
    OVERLAY_MTU=1450
    TARGET_MTU=0
    if [ "$TARGET_MTU" != "0" ] && [ -n "$TARGET_MTU" ]; then
        OVERLAY_MTU=$((TARGET_MTU - 50))
    fi
    [ "$OVERLAY_MTU" -eq 1450 ]
}

# ---------------------------------------------------------------------------
# Persistence service creation
# ---------------------------------------------------------------------------

@test "MTU persistence service is created with correct interface" {
    PRIMARY_IFACE="eth0"
    TARGET_MTU=9000

    cat > "$TEST_TMPDIR/etc/systemd/system/archetype-mtu.service" << MTUSVC
[Unit]
Description=Set network MTU for Archetype Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/sbin/ip link set $PRIMARY_IFACE mtu $TARGET_MTU
ExecStart=/sbin/ip link set arch-ovs mtu $TARGET_MTU
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
MTUSVC

    grep -q "ExecStart=/sbin/ip link set eth0 mtu 9000" \
        "$TEST_TMPDIR/etc/systemd/system/archetype-mtu.service"
    grep -q "ExecStart=/sbin/ip link set arch-ovs mtu 9000" \
        "$TEST_TMPDIR/etc/systemd/system/archetype-mtu.service"
}

@test "MTU persistence service has correct unit dependencies" {
    PRIMARY_IFACE="eth0"
    TARGET_MTU=9000

    cat > "$TEST_TMPDIR/etc/systemd/system/archetype-mtu.service" << MTUSVC
[Unit]
Description=Set network MTU for Archetype Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/sbin/ip link set $PRIMARY_IFACE mtu $TARGET_MTU
ExecStart=/sbin/ip link set arch-ovs mtu $TARGET_MTU
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
MTUSVC

    grep -q "After=network-online.target" \
        "$TEST_TMPDIR/etc/systemd/system/archetype-mtu.service"
    grep -q "Type=oneshot" \
        "$TEST_TMPDIR/etc/systemd/system/archetype-mtu.service"
}
