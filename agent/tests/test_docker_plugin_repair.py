"""Tests for stale OVS port cleanup in repair_endpoints.

Verifies that _cleanup_stale_ovs_ports() correctly identifies and removes
OVS ports from previous container incarnations while preserving tracked ports.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestCleanupStaleOvsPorts:
    """Tests for _cleanup_stale_ovs_ports logic."""

    @pytest.mark.asyncio
    async def test_no_stale_ports_noop(self):
        """No stale ports → nothing deleted."""
        plugin = MagicMock()
        plugin.endpoints = {}

        # list-ports returns empty
        plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
        plugin._run_cmd = AsyncMock(return_value=(0, "", ""))

        from agent.network.docker_plugin import DockerOVSPlugin
        result = await DockerOVSPlugin._cleanup_stale_ovs_ports(plugin, "archetype-abc-ceos1")
        assert result == 0

    @pytest.mark.asyncio
    async def test_stale_port_removed(self):
        """Port with external_ids referencing container is removed."""
        plugin = MagicMock()
        plugin.endpoints = {}

        call_count = 0
        async def fake_ovs_vsctl(*args):
            nonlocal call_count
            call_count += 1
            if args[0] == "list-ports":
                return (0, "stale_veth123\nother_port", "")
            if args[0] == "get" and args[1] == "interface":
                port = args[2]
                if port == "stale_veth123":
                    return (0, '{container_name="archetype-abc-ceos1"}', "")
                return (0, '{container_name="archetype-abc-ceos2"}', "")
            if args[0] == "--if-exists":
                return (0, "", "")
            return (0, "", "")

        from types import SimpleNamespace
        plugin._ovs_vsctl = fake_ovs_vsctl
        plugin._run_cmd = AsyncMock(return_value=(0, "", ""))

        # Mock settings
        with pytest.MonkeyPatch.context() as m:
            m.setattr("agent.network.docker_plugin.settings", SimpleNamespace(ovs_bridge_name="arch-ovs"))
            from agent.network.docker_plugin import DockerOVSPlugin
            result = await DockerOVSPlugin._cleanup_stale_ovs_ports(plugin, "archetype-abc-ceos1")

        assert result == 1

    @pytest.mark.asyncio
    async def test_tracked_port_not_deleted(self):
        """Port that matches a tracked endpoint veth is not deleted."""
        plugin = MagicMock()

        # Simulate a tracked endpoint
        ep = MagicMock()
        ep.container_name = "archetype-abc-ceos1"
        ep.host_veth = "tracked_veth"
        plugin.endpoints = {"ep1": ep}

        async def fake_ovs_vsctl(*args):
            if args[0] == "list-ports":
                return (0, "tracked_veth", "")
            return (0, "", "")

        plugin._ovs_vsctl = fake_ovs_vsctl
        plugin._run_cmd = AsyncMock(return_value=(0, "", ""))

        from types import SimpleNamespace
        with pytest.MonkeyPatch.context() as m:
            m.setattr("agent.network.docker_plugin.settings", SimpleNamespace(ovs_bridge_name="arch-ovs"))
            from agent.network.docker_plugin import DockerOVSPlugin
            result = await DockerOVSPlugin._cleanup_stale_ovs_ports(plugin, "archetype-abc-ceos1")

        assert result == 0
