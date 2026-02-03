"""Tests for DockerOVSPlugin per-lab bridge migration."""
from __future__ import annotations

import pytest

from agent.network.docker_plugin import DockerOVSPlugin, LabBridge, NetworkState


@pytest.mark.asyncio
async def test_migrate_per_lab_bridges_moves_ports(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.network.docker_plugin.settings.workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    calls: list[tuple] = []

    async def _ovs_vsctl(*args):
        calls.append(args)
        if args == ("list-br",):
            return 0, "arch-ovs\novs-lab123\nbr-int\n", ""
        if args == ("list-ports", "ovs-lab123"):
            return 0, "vh1\nvh2\n", ""
        if args == ("get", "port", "vh1", "tag"):
            return 0, "[123]", ""
        if args == ("get", "port", "vh2", "tag"):
            return 0, "[]", ""
        return 0, "", ""

    monkeypatch.setattr(plugin, "_ovs_vsctl", _ovs_vsctl)

    await plugin._migrate_per_lab_bridges()

    assert ("--if-exists", "del-port", "ovs-lab123", "vh1") in calls
    assert ("--if-exists", "del-port", "ovs-lab123", "vh2") in calls
    assert ("add-port", "arch-ovs", "vh1", "tag=123") in calls
    assert ("add-port", "arch-ovs", "vh2") in calls
    assert ("--if-exists", "del-br", "ovs-lab123") in calls


@pytest.mark.asyncio
async def test_migrate_per_lab_bridges_drops_empty_bridge(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.network.docker_plugin.settings.workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    calls: list[tuple] = []

    async def _ovs_vsctl(*args):
        calls.append(args)
        if args == ("list-br",):
            return 0, "arch-ovs\novs-empty\n", ""
        if args == ("list-ports", "ovs-empty"):
            return 0, "\n", ""
        return 0, "", ""

    monkeypatch.setattr(plugin, "_ovs_vsctl", _ovs_vsctl)

    await plugin._migrate_per_lab_bridges()

    assert ("--if-exists", "del-br", "ovs-empty") in calls


def test_migrate_state_to_shared_bridge(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.network.docker_plugin.settings.workspace_path", str(tmp_path))
    plugin = DockerOVSPlugin()

    plugin.lab_bridges["lab123"] = LabBridge(lab_id="lab123", bridge_name="ovs-lab123")
    plugin.networks["net123"] = NetworkState(
        network_id="net123",
        lab_id="lab123",
        interface_name="eth1",
        bridge_name="ovs-lab123",
    )

    updated = plugin._migrate_state_to_shared_bridge()

    assert updated is True
    assert plugin.lab_bridges["lab123"].bridge_name == "arch-ovs"
    assert plugin.networks["net123"].bridge_name == "arch-ovs"
