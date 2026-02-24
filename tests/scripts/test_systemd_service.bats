#!/usr/bin/env bats
# Tests for systemd unit file generation from install.sh and agent/install.sh.
#
# Extracts the heredoc that writes the .service file, evaluates it with
# controlled variables, then inspects the generated file content.

load helpers

setup() {
    setup_test_env
    mkdir -p "$TEST_TMPDIR/etc/systemd/system"
}

teardown() {
    teardown_test_env
}

# ---------------------------------------------------------------------------
# install.sh  (agent service at /etc/systemd/system/archetype-agent.service)
# ---------------------------------------------------------------------------

generate_install_sh_service() {
    AGENT_INSTALL_DIR="$TEST_TMPDIR/opt/archetype-agent"
    mkdir -p "$AGENT_INSTALL_DIR"

    # Rewrite the path so we write to TEST_TMPDIR instead of real /etc
    local block
    block=$(sed -n '/^    cat > \/etc\/systemd\/system\/archetype-agent.service/,/^EOF$/p' \
        "$PROJECT_ROOT/install.sh" \
        | sed "s|/etc/systemd/system|$TEST_TMPDIR/etc/systemd/system|g")
    eval "$block"
}

@test "install.sh service has correct ExecStart path" {
    generate_install_sh_service
    grep -q "ExecStart=$TEST_TMPDIR/opt/archetype-agent/venv/bin/python -m agent.main" \
        "$TEST_TMPDIR/etc/systemd/system/archetype-agent.service"
}

@test "install.sh service has correct EnvironmentFile" {
    generate_install_sh_service
    grep -q "EnvironmentFile=$TEST_TMPDIR/opt/archetype-agent/agent.env" \
        "$TEST_TMPDIR/etc/systemd/system/archetype-agent.service"
}

@test "install.sh service has correct WorkingDirectory" {
    generate_install_sh_service
    grep -q "WorkingDirectory=$TEST_TMPDIR/opt/archetype-agent/repo" \
        "$TEST_TMPDIR/etc/systemd/system/archetype-agent.service"
}

@test "install.sh service requires docker" {
    generate_install_sh_service
    grep -q "Requires=docker.service" \
        "$TEST_TMPDIR/etc/systemd/system/archetype-agent.service"
}

@test "install.sh service has WantedBy=multi-user.target" {
    generate_install_sh_service
    grep -q "WantedBy=multi-user.target" \
        "$TEST_TMPDIR/etc/systemd/system/archetype-agent.service"
}

# ---------------------------------------------------------------------------
# agent/install.sh  (service name is variable: $SERVICE_NAME)
# ---------------------------------------------------------------------------

generate_agent_install_service() {
    INSTALL_DIR="$TEST_TMPDIR/opt/archetype-agent"
    SERVICE_NAME="archetype-agent"
    mkdir -p "$INSTALL_DIR"

    local block
    block=$(sed -n '/^cat > \/etc\/systemd\/system\/\$SERVICE_NAME.service/,/^EOF$/p' \
        "$PROJECT_ROOT/agent/install.sh" \
        | sed "s|/etc/systemd/system|$TEST_TMPDIR/etc/systemd/system|g")
    eval "$block"
}

@test "agent/install.sh service has StandardOutput=journal" {
    generate_agent_install_service
    grep -q "StandardOutput=journal" \
        "$TEST_TMPDIR/etc/systemd/system/archetype-agent.service"
}

@test "agent/install.sh service has SyslogIdentifier" {
    generate_agent_install_service
    grep -q "SyslogIdentifier=archetype-agent" \
        "$TEST_TMPDIR/etc/systemd/system/archetype-agent.service"
}
