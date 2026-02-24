#!/usr/bin/env bats
# Tests for agent .env generation from install.sh and agent/install.sh.
#
# Extracts the heredoc block, evaluates with controlled variables,
# then inspects the generated file for expected keys/values.

load helpers

setup() {
    setup_test_env
}

teardown() {
    teardown_test_env
}

# ---------------------------------------------------------------------------
# install.sh — agent.env generation (lines 676-694)
# ---------------------------------------------------------------------------

generate_install_sh_agent_env() {
    AGENT_INSTALL_DIR="$TEST_TMPDIR"
    AGENT_NAME="test-agent"
    CONTROLLER_URL="http://10.0.0.1:8000"
    LOCAL_IP="10.0.0.100"
    AGENT_PORT="8001"
    INSTALL_LIBVIRT=false
    AGENT_SECRET_OVERRIDE="secret123"
    AGENT_COMMIT="abc1234"

    # Derive STANDALONE_REDIS_URL as the script does
    CONTROLLER_HOST=$(echo "$CONTROLLER_URL" | sed -E 's|https?://([^:/]+).*|\1|')
    STANDALONE_REDIS_URL="redis://${CONTROLLER_HOST}:16379/0"

    eval "$(sed -n '/^    cat > \$AGENT_INSTALL_DIR\/agent.env/,/^EOF$/p' "$PROJECT_ROOT/install.sh" | sed 's/^    //')"
}

@test "install.sh agent.env contains AGENT_NAME" {
    generate_install_sh_agent_env
    grep -q "ARCHETYPE_AGENT_AGENT_NAME=test-agent" "$TEST_TMPDIR/agent.env"
}

@test "install.sh agent.env contains CONTROLLER_URL" {
    generate_install_sh_agent_env
    grep -q "ARCHETYPE_AGENT_CONTROLLER_URL=http://10.0.0.1:8000" "$TEST_TMPDIR/agent.env"
}

@test "install.sh agent.env derives REDIS_URL from controller host" {
    generate_install_sh_agent_env
    grep -q "ARCHETYPE_AGENT_REDIS_URL=redis://10.0.0.1:16379/0" "$TEST_TMPDIR/agent.env"
}

@test "install.sh agent.env contains CONTROLLER_SECRET" {
    generate_install_sh_agent_env
    grep -q "ARCHETYPE_AGENT_CONTROLLER_SECRET=secret123" "$TEST_TMPDIR/agent.env"
}

@test "install.sh agent.env contains ENABLE_LIBVIRT=false" {
    generate_install_sh_agent_env
    grep -q "ARCHETYPE_AGENT_ENABLE_LIBVIRT=false" "$TEST_TMPDIR/agent.env"
}

# ---------------------------------------------------------------------------
# agent/install.sh — agent.env generation (lines 556-578)
# ---------------------------------------------------------------------------

generate_agent_install_env() {
    INSTALL_DIR="$TEST_TMPDIR"
    AGENT_NAME="remote-agent"
    CONTROLLER_URL="http://10.0.0.1:8000"
    AGENT_SECRET="agentsecret456"
    REDIS_URL="redis://10.0.0.1:16379/0"
    LOCAL_IP="10.0.0.200"
    AGENT_PORT="8001"
    INSTALL_LIBVIRT=true
    OVERLAY_MTU=8950
    LOCAL_MTU=9000

    eval "$(sed -n '/^cat > \$INSTALL_DIR\/agent.env/,/^EOF$/p' "$PROJECT_ROOT/agent/install.sh")"
}

@test "agent/install.sh agent.env contains OVERLAY_MTU" {
    generate_agent_install_env
    grep -q "ARCHETYPE_AGENT_OVERLAY_MTU=8950" "$TEST_TMPDIR/agent.env"
}

@test "agent/install.sh agent.env contains LOCAL_MTU" {
    generate_agent_install_env
    grep -q "ARCHETYPE_AGENT_LOCAL_MTU=9000" "$TEST_TMPDIR/agent.env"
}

@test "agent/install.sh agent.env contains ENABLE_LIBVIRT=true" {
    generate_agent_install_env
    grep -q "ARCHETYPE_AGENT_ENABLE_LIBVIRT=true" "$TEST_TMPDIR/agent.env"
}

@test "agent/install.sh agent.env contains CONTROLLER_SECRET" {
    generate_agent_install_env
    grep -q "ARCHETYPE_AGENT_CONTROLLER_SECRET=agentsecret456" "$TEST_TMPDIR/agent.env"
}

@test "agent/install.sh agent.env contains WORKSPACE_PATH" {
    generate_agent_install_env
    grep -q "ARCHETYPE_AGENT_WORKSPACE_PATH=/var/lib/archetype-agent" "$TEST_TMPDIR/agent.env"
}

@test "agent/install.sh agent.env contains ENABLE_VXLAN=true" {
    generate_agent_install_env
    grep -q "ARCHETYPE_AGENT_ENABLE_VXLAN=true" "$TEST_TMPDIR/agent.env"
}

@test "agent/install.sh agent.env contains LOCAL_IP" {
    generate_agent_install_env
    grep -q "ARCHETYPE_AGENT_LOCAL_IP=10.0.0.200" "$TEST_TMPDIR/agent.env"
}
