"""Comprehensive tests for agent/network/plugin_state.py.

Covers state persistence (save/load round-trips, atomic writes), garbage
collection of stale entries, reconciliation with OVS reality, recovery from
corrupt/missing state files, lab state tracking, container endpoint state
management, VLAN allocation state tracking, and edge cases.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.network import docker_plugin as plugin_mod
from agent.network.docker_plugin import (
    DockerOVSPlugin,
    EndpointState,
    LabBridge,
    LINKED_VLAN_START,
    NetworkState,
    OVS_BRIDGE_PREFIX,
    VLAN_RANGE_END,
    VLAN_RANGE_START,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plugin(monkeypatch, tmp_path):
    """Create a DockerOVSPlugin with mocked settings."""
    monkeypatch.setattr(plugin_mod.settings, "workspace_path", str(tmp_path))
    monkeypatch.setattr(plugin_mod.settings, "ovs_bridge_name", "arch-ovs")
    plugin = DockerOVSPlugin()
    return plugin


def _make_io_plugin(monkeypatch, tmp_path):
    """Create a DockerOVSPlugin with mocked I/O for async tests."""
    plugin = _make_plugin(monkeypatch, tmp_path)
    plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    plugin._run_cmd = AsyncMock(return_value=(0, "", ""))
    plugin._delete_port = AsyncMock()
    plugin._mark_dirty_and_save = AsyncMock()
    plugin._bridge_name = "arch-ovs"
    return plugin


def _setup_lab(plugin, lab_id="lab1"):
    """Add a LabBridge and return it."""
    lb = LabBridge(lab_id=lab_id, bridge_name="arch-ovs")
    plugin.lab_bridges[lab_id] = lb
    return lb


def _add_network(plugin, lab_id, net_id, iface="eth1"):
    """Add a NetworkState and register with lab bridge."""
    ns = NetworkState(
        network_id=net_id,
        lab_id=lab_id,
        interface_name=iface,
        bridge_name="arch-ovs",
    )
    plugin.networks[net_id] = ns
    lb = plugin.lab_bridges.get(lab_id)
    if lb:
        lb.network_ids.add(net_id)
    return ns


def _add_endpoint(plugin, ep_id, net_id, iface, vlan, host_veth="vh1",
                  cont_veth="vc1", container_name=None):
    """Add an EndpointState to the plugin."""
    ep = EndpointState(
        endpoint_id=ep_id,
        network_id=net_id,
        interface_name=iface,
        host_veth=host_veth,
        cont_veth=cont_veth,
        vlan_tag=vlan,
        container_name=container_name,
    )
    plugin.endpoints[ep_id] = ep
    plugin._allocated_vlans.add(vlan)
    return ep


# ===========================================================================
# State Persistence: Serialization
# ===========================================================================

class TestSerializeState:
    """Tests for _serialize_state."""

    def test_empty_state_serializes(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        result = plugin._serialize_state()

        assert result["version"] == 1
        assert "saved_at" in result
        assert result["lab_bridges"] == {}
        assert result["networks"] == {}
        assert result["endpoints"] == {}

    def test_populated_state_serializes(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        plugin._global_next_vlan = 200
        plugin._global_next_linked_vlan = 2100

        _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", "net1", "eth1")
        _add_endpoint(plugin, "ep1", "net1", "eth1", 200,
                      host_veth="vh-r1-eth1", cont_veth="vc-r1-eth1",
                      container_name="archetype-lab1-r1")

        result = plugin._serialize_state()

        assert result["global_next_vlan"] == 200
        assert result["global_next_linked_vlan"] == 2100
        assert "lab1" in result["lab_bridges"]
        assert result["lab_bridges"]["lab1"]["lab_id"] == "lab1"
        assert "net1" in result["lab_bridges"]["lab1"]["network_ids"]
        assert "net1" in result["networks"]
        assert result["networks"]["net1"]["interface_name"] == "eth1"
        assert "ep1" in result["endpoints"]
        assert result["endpoints"]["ep1"]["vlan_tag"] == 200
        assert result["endpoints"]["ep1"]["container_name"] == "archetype-lab1-r1"

    def test_serialize_preserves_vxlan_and_external(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        lb = _setup_lab(plugin, "lab1")
        lb.vxlan_tunnels = {5000: "vxlan5000"}
        lb.external_ports = {"ens5": 150}

        result = plugin._serialize_state()

        assert result["lab_bridges"]["lab1"]["vxlan_tunnels"] == {5000: "vxlan5000"}
        assert result["lab_bridges"]["lab1"]["external_ports"] == {"ens5": 150}

    def test_serialize_converts_network_ids_to_list(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        lb = _setup_lab(plugin, "lab1")
        lb.network_ids = {"net-a", "net-b"}

        result = plugin._serialize_state()
        # Should be a list for JSON compatibility
        assert isinstance(result["lab_bridges"]["lab1"]["network_ids"], list)
        assert set(result["lab_bridges"]["lab1"]["network_ids"]) == {"net-a", "net-b"}


# ===========================================================================
# State Persistence: Deserialization
# ===========================================================================

class TestDeserializeState:
    """Tests for _deserialize_state."""

    def test_empty_data_sets_defaults(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        plugin._deserialize_state({})

        assert plugin._global_next_vlan == VLAN_RANGE_START
        assert plugin._global_next_linked_vlan == LINKED_VLAN_START
        assert plugin.lab_bridges == {}
        assert plugin.networks == {}
        assert plugin.endpoints == {}

    def test_full_round_trip(self, monkeypatch, tmp_path):
        """Serialize and then deserialize produces identical logical state."""
        plugin = _make_plugin(monkeypatch, tmp_path)
        plugin._global_next_vlan = 250
        plugin._global_next_linked_vlan = 2080
        _setup_lab(plugin, "lab-x")
        _add_network(plugin, "lab-x", "net-x", "eth2")
        _add_endpoint(plugin, "ep-x", "net-x", "eth2", 250,
                      host_veth="vh-x", cont_veth="vc-x",
                      container_name="r1")

        state = plugin._serialize_state()

        plugin2 = _make_plugin(monkeypatch, tmp_path)
        plugin2._deserialize_state(state)

        assert plugin2._global_next_vlan == 250
        assert plugin2._global_next_linked_vlan == 2080
        assert "lab-x" in plugin2.lab_bridges
        assert "net-x" in plugin2.networks
        assert "ep-x" in plugin2.endpoints
        assert plugin2.endpoints["ep-x"].container_name == "r1"
        assert plugin2._allocated_vlans == {250}

    def test_unknown_version_still_loads(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        state = {
            "version": 99,
            "global_next_vlan": 300,
            "global_next_linked_vlan": 2200,
            "lab_bridges": {},
            "networks": {},
            "endpoints": {},
        }
        plugin._deserialize_state(state)
        assert plugin._global_next_vlan == 300

    def test_invalid_last_activity_falls_back(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        state = {
            "lab_bridges": {
                "lab1": {
                    "lab_id": "lab1",
                    "bridge_name": "arch-ovs",
                    "last_activity": "not-a-timestamp",
                    "network_ids": [],
                }
            },
            "networks": {},
            "endpoints": {},
        }
        plugin._deserialize_state(state)
        assert "lab1" in plugin.lab_bridges
        # Should fallback to now
        assert isinstance(plugin.lab_bridges["lab1"].last_activity, datetime)

    def test_missing_last_activity_falls_back(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        state = {
            "lab_bridges": {
                "lab1": {
                    "lab_id": "lab1",
                    "bridge_name": "arch-ovs",
                    "network_ids": [],
                }
            },
            "networks": {},
            "endpoints": {},
        }
        plugin._deserialize_state(state)
        assert isinstance(plugin.lab_bridges["lab1"].last_activity, datetime)

    def test_computed_next_vlan_wraps_on_overflow(self, monkeypatch, tmp_path):
        """When global_next_vlan is not saved and max vlan is at range end, it wraps."""
        plugin = _make_plugin(monkeypatch, tmp_path)
        state = {
            # No global_next_vlan key — triggers computed logic
            "lab_bridges": {},
            "networks": {},
            "endpoints": {
                "ep1": {
                    "endpoint_id": "ep1",
                    "network_id": "net1",
                    "interface_name": "eth1",
                    "host_veth": "vh1",
                    "cont_veth": "vc1",
                    "vlan_tag": VLAN_RANGE_END,
                }
            },
        }
        plugin._deserialize_state(state)
        assert plugin._global_next_vlan == VLAN_RANGE_START

    def test_computed_next_vlan_increments_normally(self, monkeypatch, tmp_path):
        """When global_next_vlan is missing, next vlan is max+1 if not wrapped."""
        plugin = _make_plugin(monkeypatch, tmp_path)
        state = {
            "lab_bridges": {},
            "networks": {},
            "endpoints": {
                "ep1": {
                    "endpoint_id": "ep1",
                    "network_id": "net1",
                    "interface_name": "eth1",
                    "host_veth": "vh1",
                    "cont_veth": "vc1",
                    "vlan_tag": 500,
                }
            },
        }
        plugin._deserialize_state(state)
        assert plugin._global_next_vlan == 501

    def test_endpoint_without_container_name(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        state = {
            "lab_bridges": {},
            "networks": {},
            "endpoints": {
                "ep1": {
                    "endpoint_id": "ep1",
                    "network_id": "net1",
                    "interface_name": "eth1",
                    "host_veth": "vh1",
                    "cont_veth": "vc1",
                    "vlan_tag": 100,
                    # no container_name key
                }
            },
        }
        plugin._deserialize_state(state)
        assert plugin.endpoints["ep1"].container_name is None


# ===========================================================================
# State Persistence: Save & Load
# ===========================================================================

class TestSaveAndLoad:
    """Tests for _save_state and _load_state."""

    @pytest.mark.asyncio
    async def test_save_creates_file(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")

        await plugin._save_state()

        assert plugin._state_file.exists()
        data = json.loads(plugin._state_file.read_text())
        assert data["version"] == 1
        assert "lab1" in data["lab_bridges"]

    @pytest.mark.asyncio
    async def test_save_clears_dirty_flag(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        plugin._state_dirty = True

        await plugin._save_state()

        assert plugin._state_dirty is False

    @pytest.mark.asyncio
    async def test_save_is_atomic(self, monkeypatch, tmp_path):
        """Save uses temp file + rename, no .tmp file remains after save."""
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")

        await plugin._save_state()

        # No .tmp file should remain
        tmp_file = plugin._state_file.with_suffix(".tmp")
        assert not tmp_file.exists()
        assert plugin._state_file.exists()

    @pytest.mark.asyncio
    async def test_save_handles_write_error(self, monkeypatch, tmp_path):
        """Save logs error but does not raise on I/O failure."""
        plugin = _make_plugin(monkeypatch, tmp_path)

        # Make the state file path unwritable
        async def _exploding_to_thread(fn, *args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(asyncio, "to_thread", _exploding_to_thread)
        plugin._state_dirty = True

        # Should not raise
        await plugin._save_state()

    @pytest.mark.asyncio
    async def test_load_returns_false_when_no_file(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        result = await plugin._load_state()
        assert result is False

    @pytest.mark.asyncio
    async def test_load_returns_true_on_success(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab-abc")
        await plugin._save_state()

        plugin2 = _make_plugin(monkeypatch, tmp_path)
        result = await plugin2._load_state()
        assert result is True
        assert "lab-abc" in plugin2.lab_bridges

    @pytest.mark.asyncio
    async def test_load_returns_false_on_corrupt_json(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        # Write corrupt JSON to state file
        plugin._state_file.write_text("{corrupt json!!! @@@")

        result = await plugin._load_state()
        assert result is False

    @pytest.mark.asyncio
    async def test_load_returns_false_on_unexpected_error(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        # Write valid JSON but with types that will fail deserialization
        plugin._state_file.write_text('{"lab_bridges": {"lab1": "not-a-dict"}}')

        result = await plugin._load_state()
        assert result is False

    @pytest.mark.asyncio
    async def test_mark_dirty_and_save(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        assert plugin._state_dirty is False

        await plugin._mark_dirty_and_save()

        assert plugin._state_dirty is False  # save clears it
        assert plugin._state_file.exists()


# ===========================================================================
# State Migration
# ===========================================================================

class TestMigrateStateToSharedBridge:
    """Tests for _migrate_state_to_shared_bridge."""

    def test_no_migration_needed(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", "net1", "eth1")

        updated = plugin._migrate_state_to_shared_bridge()

        assert updated is False

    def test_migrates_old_bridge_names(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        lb = _setup_lab(plugin, "lab1")
        lb.bridge_name = "ovs-lab1abcdef"

        ns = _add_network(plugin, "lab1", "net1", "eth1")
        ns.bridge_name = "ovs-lab1abcdef"

        updated = plugin._migrate_state_to_shared_bridge()

        assert updated is True
        assert plugin.lab_bridges["lab1"].bridge_name == "arch-ovs"
        assert plugin.networks["net1"].bridge_name == "arch-ovs"


# ===========================================================================
# Stale State Garbage Collection
# ===========================================================================

class TestCleanupStaleState:
    """Tests for cleanup_stale_state."""

    @pytest.mark.asyncio
    async def test_no_stale_entries(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", "net1", "eth1")
        _add_endpoint(plugin, "ep1", "net1", "eth1", 100,
                      container_name="archetype-lab1-r1")

        # Docker returns both the network and the container as live
        plugin._run_cmd = AsyncMock(side_effect=[
            (0, "net1\n", ""),  # docker network ls
            (0, "archetype-lab1-r1\n", ""),  # docker ps -a
        ])

        stats = await plugin.cleanup_stale_state()

        assert stats["networks_removed"] == 0
        assert stats["endpoints_removed"] == 0

    @pytest.mark.asyncio
    async def test_removes_stale_network(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", "net1", "eth1")

        # Docker shows no live networks or containers
        plugin._run_cmd = AsyncMock(side_effect=[
            (0, "\n", ""),  # docker network ls — empty
            (0, "\n", ""),  # docker ps -a — empty
        ])

        stats = await plugin.cleanup_stale_state()

        assert stats["networks_removed"] == 1
        assert "net1" not in plugin.networks

    @pytest.mark.asyncio
    async def test_removes_stale_endpoint_when_network_and_container_gone(
        self, monkeypatch, tmp_path
    ):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", "net1", "eth1")
        _add_endpoint(plugin, "ep1", "net1", "eth1", 100,
                      host_veth="vh-r1-eth1", container_name="c1")

        # network not live, container not live
        plugin._run_cmd = AsyncMock(side_effect=[
            (0, "\n", ""),  # docker network ls
            (0, "\n", ""),  # docker ps
        ])

        stats = await plugin.cleanup_stale_state()

        assert stats["endpoints_removed"] == 1
        assert "ep1" not in plugin.endpoints

    @pytest.mark.asyncio
    async def test_keeps_endpoint_if_container_still_alive(
        self, monkeypatch, tmp_path
    ):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", "net1", "eth1")
        _add_endpoint(plugin, "ep1", "net1", "eth1", 100,
                      host_veth="vh-r1-eth1", container_name="archetype-lab1-r1")

        # Network is gone but container is still alive
        plugin._run_cmd = AsyncMock(side_effect=[
            (0, "\n", ""),                        # docker network ls — network gone
            (0, "archetype-lab1-r1\n", ""),        # docker ps — container alive
        ])

        stats = await plugin.cleanup_stale_state()

        assert stats["networks_removed"] == 1  # network removed
        assert stats["endpoints_removed"] == 0  # endpoint kept (container alive)
        assert "ep1" in plugin.endpoints

    @pytest.mark.asyncio
    async def test_removes_empty_lab_bridge(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        # No networks, no tunnels, no external ports, no endpoints

        plugin._run_cmd = AsyncMock(side_effect=[
            (0, "\n", ""),  # docker network ls
            (0, "\n", ""),  # docker ps
        ])

        await plugin.cleanup_stale_state()

        assert "lab1" not in plugin.lab_bridges

    @pytest.mark.asyncio
    async def test_keeps_lab_bridge_with_vxlan_tunnels(
        self, monkeypatch, tmp_path
    ):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        lb = _setup_lab(plugin, "lab1")
        lb.vxlan_tunnels = {5000: "vxlan5000"}

        plugin._run_cmd = AsyncMock(side_effect=[
            (0, "\n", ""),
            (0, "\n", ""),
        ])

        await plugin.cleanup_stale_state()

        # Still present because vxlan_tunnels is non-empty
        assert "lab1" in plugin.lab_bridges

    @pytest.mark.asyncio
    async def test_skips_on_docker_network_ls_failure(
        self, monkeypatch, tmp_path
    ):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        plugin._run_cmd = AsyncMock(return_value=(1, "", "error"))

        stats = await plugin.cleanup_stale_state()

        assert stats["networks_removed"] == 0
        assert stats["endpoints_removed"] == 0

    @pytest.mark.asyncio
    async def test_skips_on_docker_ps_failure(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        plugin._run_cmd = AsyncMock(side_effect=[
            (0, "\n", ""),   # network ls ok
            (1, "", "err"),  # docker ps fails
        ])

        stats = await plugin.cleanup_stale_state()

        assert stats["networks_removed"] == 0
        assert stats["endpoints_removed"] == 0

    @pytest.mark.asyncio
    async def test_stale_endpoint_triggers_ovs_port_delete(
        self, monkeypatch, tmp_path
    ):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", "net1", "eth1")
        _add_endpoint(plugin, "ep1", "net1", "eth1", 100,
                      host_veth="vh-r1-eth1", container_name="gone-container")

        plugin._run_cmd = AsyncMock(side_effect=[
            (0, "\n", ""),  # network ls — empty
            (0, "\n", ""),  # ps — empty
        ])

        await plugin.cleanup_stale_state()

        plugin._delete_port.assert_awaited_once_with("arch-ovs", "vh-r1-eth1")

    @pytest.mark.asyncio
    async def test_prefix_match_keeps_network(self, monkeypatch, tmp_path):
        """Network IDs can match by prefix (Docker truncation)."""
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        full_id = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890ab"
        short_id = "abcdef12345678"
        _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", full_id, "eth1")

        plugin._run_cmd = AsyncMock(side_effect=[
            (0, f"{short_id}\n", ""),  # short ID returned by docker
            (0, "\n", ""),
        ])

        stats = await plugin.cleanup_stale_state()

        assert stats["networks_removed"] == 0
        assert full_id in plugin.networks


# ===========================================================================
# State Reconciliation
# ===========================================================================

class TestReconcileState:
    """Tests for _reconcile_state."""

    @pytest.mark.asyncio
    async def test_removes_endpoint_with_missing_veth(
        self, monkeypatch, tmp_path
    ):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", "net1", "eth1")
        _add_endpoint(plugin, "ep1", "net1", "eth1", 100,
                      host_veth="vh-missing")

        plugin._pending_endpoint_reconnects = []
        plugin._release_vlan = MagicMock()

        async def _ovs_vsctl(*args):
            if args[0] == "br-exists":
                return (0, "", "")
            if args[0] == "list-ports":
                return (0, "vh-missing\n", "")
            return (0, "", "")

        plugin._ovs_vsctl = _ovs_vsctl
        # veth does not exist
        plugin._run_cmd = AsyncMock(return_value=(1, "", "no such device"))

        stats = await plugin._reconcile_state()

        assert stats["endpoints_removed"] == 1
        assert "ep1" not in plugin.endpoints
        plugin._release_vlan.assert_called_once_with(100)

    @pytest.mark.asyncio
    async def test_queues_reconnect_when_container_known(
        self, monkeypatch, tmp_path
    ):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", "net1", "eth1")
        _add_endpoint(plugin, "ep1", "net1", "eth1", 100,
                      host_veth="vh-missing", container_name="r1")

        plugin._pending_endpoint_reconnects = []
        plugin._release_vlan = MagicMock()

        async def _ovs_vsctl(*args):
            if args[0] == "br-exists":
                return (0, "", "")
            if args[0] == "list-ports":
                return (0, "vh-missing\n", "")
            return (0, "", "")

        plugin._ovs_vsctl = _ovs_vsctl
        plugin._run_cmd = AsyncMock(return_value=(1, "", "no such device"))

        stats = await plugin._reconcile_state()

        assert stats["endpoints_queued"] == 1
        assert len(plugin._pending_endpoint_reconnects) == 1
        assert plugin._pending_endpoint_reconnects[0] == ("r1", "net1", "eth1")

    @pytest.mark.asyncio
    async def test_recreates_missing_bridge_with_networks(
        self, monkeypatch, tmp_path
    ):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        lb = _setup_lab(plugin, "lab1")
        lb.network_ids = {"net1"}
        plugin._ensure_bridge = AsyncMock()

        async def _ovs_vsctl(*args):
            if args[0] == "br-exists":
                return (1, "", "")  # Bridge missing
            return (0, "", "")

        plugin._ovs_vsctl = _ovs_vsctl

        stats = await plugin._reconcile_state()

        assert stats["bridges_recreated"] == 1
        plugin._ensure_bridge.assert_awaited_once_with("lab1")

    @pytest.mark.asyncio
    async def test_removes_orphan_bridge_with_no_networks(
        self, monkeypatch, tmp_path
    ):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        # No networks attached

        async def _ovs_vsctl(*args):
            if args[0] == "br-exists":
                return (1, "", "")
            return (0, "", "")

        plugin._ovs_vsctl = _ovs_vsctl

        stats = await plugin._reconcile_state()

        assert "lab1" not in plugin.lab_bridges

    @pytest.mark.asyncio
    async def test_no_changes_means_no_save(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        # Empty state: nothing to reconcile, nothing to save
        plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
        plugin._save_state = AsyncMock()

        stats = await plugin._reconcile_state()

        plugin._save_state.assert_not_awaited()
        assert all(v == 0 for v in stats.values())


# ===========================================================================
# Queue Missing Endpoint Reconnect
# ===========================================================================

class TestQueueMissingEndpointReconnect:
    """Tests for _queue_missing_endpoint_reconnect."""

    def test_returns_false_without_container_name(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        plugin._pending_endpoint_reconnects = []
        ep = EndpointState(
            endpoint_id="ep1", network_id="net1", interface_name="eth1",
            host_veth="vh1", cont_veth="vc1", vlan_tag=100,
            container_name=None,
        )
        assert plugin._queue_missing_endpoint_reconnect(ep) is False
        assert len(plugin._pending_endpoint_reconnects) == 0

    def test_returns_false_without_network_state(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        plugin._pending_endpoint_reconnects = []
        ep = EndpointState(
            endpoint_id="ep1", network_id="net-missing", interface_name="eth1",
            host_veth="vh1", cont_veth="vc1", vlan_tag=100,
            container_name="r1",
        )
        assert plugin._queue_missing_endpoint_reconnect(ep) is False

    def test_returns_true_and_queues(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        plugin._pending_endpoint_reconnects = []
        _add_network(plugin, "lab1", "net1", "eth1")
        ep = EndpointState(
            endpoint_id="ep1", network_id="net1", interface_name="eth1",
            host_veth="vh1", cont_veth="vc1", vlan_tag=100,
            container_name="r1",
        )
        assert plugin._queue_missing_endpoint_reconnect(ep) is True
        assert ("r1", "net1", "eth1") in plugin._pending_endpoint_reconnects


# ===========================================================================
# Reconnect Pending Endpoints
# ===========================================================================

class TestReconnectPendingEndpoints:
    """Tests for _reconnect_pending_endpoints."""

    @pytest.mark.asyncio
    async def test_noop_when_empty(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        plugin._pending_endpoint_reconnects = []
        plugin._reconnect_container_to_network = AsyncMock()

        await plugin._reconnect_pending_endpoints()

        plugin._reconnect_container_to_network.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reconnects_pending_entries(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _add_network(plugin, "lab1", "net1", "eth1")
        plugin._pending_endpoint_reconnects = [("r1", "net1", "eth1")]
        plugin._reconnect_container_to_network = AsyncMock(return_value=True)

        await plugin._reconnect_pending_endpoints()

        plugin._reconnect_container_to_network.assert_awaited_once_with(
            "r1", "net1", "eth1"
        )
        assert len(plugin._pending_endpoint_reconnects) == 0


# ===========================================================================
# State Recovery: Discover Existing State
# ===========================================================================

class TestDiscoverExistingState:
    """Tests for _discover_existing_state."""

    @pytest.mark.asyncio
    async def test_loads_persisted_and_reconciles(self, monkeypatch, tmp_path):
        # First save some state
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        await plugin._save_state()

        # Now create a new plugin and discover
        plugin2 = _make_io_plugin(monkeypatch, tmp_path)
        plugin2._reconcile_state = AsyncMock(return_value={
            "endpoints_removed": 0, "endpoints_recovered": 0,
            "endpoints_queued": 0, "bridges_recreated": 0, "ports_orphaned": 0,
        })
        plugin2._cleanup_orphaned_ovs_ports = AsyncMock(return_value=0)

        await plugin2._discover_existing_state()

        assert "lab1" in plugin2.lab_bridges
        plugin2._reconcile_state.assert_awaited_once()
        plugin2._cleanup_orphaned_ovs_ports.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_persisted_state_ensures_bridge(
        self, monkeypatch, tmp_path
    ):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        plugin._ensure_shared_bridge = AsyncMock()

        await plugin._discover_existing_state()

        plugin._ensure_shared_bridge.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_corrupt_state_falls_back_to_fresh(
        self, monkeypatch, tmp_path
    ):
        plugin = _make_plugin(monkeypatch, tmp_path)
        plugin._state_file.write_text("not valid json!!!")

        plugin2 = _make_io_plugin(monkeypatch, tmp_path)
        plugin2._ensure_shared_bridge = AsyncMock()

        await plugin2._discover_existing_state()

        # Should have fallen through to the fresh-start path
        plugin2._ensure_shared_bridge.assert_awaited_once()


# ===========================================================================
# Ensure Shared Bridge
# ===========================================================================

class TestEnsureSharedBridge:
    """Tests for _ensure_shared_bridge."""

    @pytest.mark.asyncio
    async def test_creates_bridge_when_missing(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        plugin._ovs_vsctl = AsyncMock(side_effect=[
            (1, "", ""),  # br-exists => missing
            (0, "", ""),  # add-br
            (0, "", ""),  # set-fail-mode
        ])

        await plugin._ensure_shared_bridge()

        calls = plugin._ovs_vsctl.await_args_list
        assert calls[1].args == ("add-br", "arch-ovs")
        assert calls[2].args == ("set-fail-mode", "arch-ovs", "standalone")

    @pytest.mark.asyncio
    async def test_skips_when_bridge_exists(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))

        await plugin._ensure_shared_bridge()

        assert plugin._ovs_vsctl.await_count == 1  # Only br-exists

    @pytest.mark.asyncio
    async def test_raises_on_creation_failure(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        plugin._ovs_vsctl = AsyncMock(side_effect=[
            (1, "", ""),           # br-exists => missing
            (1, "", "ovs error"),  # add-br fails
        ])

        with pytest.raises(RuntimeError, match="Failed to create shared OVS bridge"):
            await plugin._ensure_shared_bridge()


# ===========================================================================
# Cleanup Orphaned OVS Ports
# ===========================================================================

class TestCleanupOrphanedOVSPorts:
    """Tests for _cleanup_orphaned_ovs_ports."""

    @pytest.mark.asyncio
    async def test_removes_orphaned_vh_ports(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", "net1", "eth1")
        _add_endpoint(plugin, "ep1", "net1", "eth1", 100,
                      host_veth="vh-tracked")

        async def _ovs_vsctl(*args):
            if args[0] == "list-ports":
                return (0, "vh-tracked\nvh-orphan\nvxlan5000\n", "")
            return (0, "", "")

        plugin._ovs_vsctl = _ovs_vsctl

        cleaned = await plugin._cleanup_orphaned_ovs_ports()

        assert cleaned == 1
        plugin._delete_port.assert_awaited_once_with("arch-ovs", "vh-orphan")

    @pytest.mark.asyncio
    async def test_skips_non_vh_ports(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")

        async def _ovs_vsctl(*args):
            if args[0] == "list-ports":
                return (0, "vxlan5000\nens5\n", "")
            return (0, "", "")

        plugin._ovs_vsctl = _ovs_vsctl

        cleaned = await plugin._cleanup_orphaned_ovs_ports()
        assert cleaned == 0
        plugin._delete_port.assert_not_awaited()


# ===========================================================================
# Migrate Per-Lab Bridges
# ===========================================================================

class TestMigratePerLabBridges:
    """Tests for _migrate_per_lab_bridges."""

    @pytest.mark.asyncio
    async def test_moves_ports_with_vlan_tag(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)

        calls = []

        async def _ovs_vsctl(*args):
            calls.append(args)
            if args[0] == "list-br":
                return (0, f"arch-ovs\n{OVS_BRIDGE_PREFIX}lab1\n", "")
            if args[0] == "list-ports" and args[1] == f"{OVS_BRIDGE_PREFIX}lab1":
                return (0, "vh-port1\n", "")
            if args == ("get", "port", "vh-port1", "tag"):
                return (0, "200\n", "")
            return (0, "", "")

        plugin._ovs_vsctl = _ovs_vsctl

        await plugin._migrate_per_lab_bridges()

        # Should have removed from old bridge and added to new with tag
        add_port_calls = [c for c in calls if c[0] == "add-port"]
        assert len(add_port_calls) == 1
        assert add_port_calls[0] == ("add-port", "arch-ovs", "vh-port1", "tag=200")

    @pytest.mark.asyncio
    async def test_removes_empty_legacy_bridge(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)

        calls = []

        async def _ovs_vsctl(*args):
            calls.append(args)
            if args[0] == "list-br":
                return (0, f"arch-ovs\n{OVS_BRIDGE_PREFIX}lab1\n", "")
            if args[0] == "list-ports" and args[1] == f"{OVS_BRIDGE_PREFIX}lab1":
                return (0, "\n", "")  # empty
            return (0, "", "")

        plugin._ovs_vsctl = _ovs_vsctl

        await plugin._migrate_per_lab_bridges()

        del_br_calls = [c for c in calls if c[:2] == ("--if-exists", "del-br")]
        assert len(del_br_calls) == 1
        assert del_br_calls[0] == ("--if-exists", "del-br", f"{OVS_BRIDGE_PREFIX}lab1")

    @pytest.mark.asyncio
    async def test_skips_on_list_br_failure(self, monkeypatch, tmp_path):
        plugin = _make_io_plugin(monkeypatch, tmp_path)
        plugin._ovs_vsctl = AsyncMock(return_value=(1, "", ""))

        await plugin._migrate_per_lab_bridges()
        # Should return silently, no error


# ===========================================================================
# Find Lab ID From Containers
# ===========================================================================

class TestFindLabIdFromContainers:
    """Tests for _find_lab_id_from_containers."""

    @pytest.mark.asyncio
    async def test_finds_matching_container(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        c1 = SimpleNamespace(labels={"archetype.lab_id": "lab-full-uuid"})
        client = MagicMock()
        client.containers.list.return_value = [c1]

        with patch("docker.from_env", return_value=client):
            result = await plugin._find_lab_id_from_containers("lab-full")

        assert result == "lab-full-uuid"

    @pytest.mark.asyncio
    async def test_returns_none_on_no_match(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        c1 = SimpleNamespace(labels={"archetype.lab_id": "other-lab"})
        client = MagicMock()
        client.containers.list.return_value = [c1]

        with patch("docker.from_env", return_value=client):
            result = await plugin._find_lab_id_from_containers("lab-xyz")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_docker_error(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)

        with patch("docker.from_env", side_effect=RuntimeError("docker down")):
            result = await plugin._find_lab_id_from_containers("lab-")

        assert result is None


# ===========================================================================
# VLAN Allocation State Tracking
# ===========================================================================

class TestVlanAllocationState:
    """Tests for VLAN allocation tracking through serialization."""

    def test_allocated_vlans_populated_on_deserialize(
        self, monkeypatch, tmp_path
    ):
        plugin = _make_plugin(monkeypatch, tmp_path)
        state = {
            "global_next_vlan": 300,
            "lab_bridges": {},
            "networks": {},
            "endpoints": {
                "ep1": {
                    "endpoint_id": "ep1",
                    "network_id": "n1",
                    "interface_name": "eth1",
                    "host_veth": "vh1",
                    "cont_veth": "vc1",
                    "vlan_tag": 200,
                },
                "ep2": {
                    "endpoint_id": "ep2",
                    "network_id": "n2",
                    "interface_name": "eth2",
                    "host_veth": "vh2",
                    "cont_veth": "vc2",
                    "vlan_tag": 250,
                },
            },
        }
        plugin._deserialize_state(state)

        assert plugin._allocated_vlans == {200, 250}

    def test_empty_endpoints_yields_empty_allocated_vlans(
        self, monkeypatch, tmp_path
    ):
        plugin = _make_plugin(monkeypatch, tmp_path)
        plugin._deserialize_state({
            "global_next_vlan": 100,
            "lab_bridges": {},
            "networks": {},
            "endpoints": {},
        })
        assert plugin._allocated_vlans == set()

    @pytest.mark.asyncio
    async def test_vlan_state_survives_save_load(self, monkeypatch, tmp_path):
        """VLAN counters survive a full save/load cycle."""
        plugin = _make_plugin(monkeypatch, tmp_path)
        plugin._global_next_vlan = 350
        plugin._global_next_linked_vlan = 2150
        _setup_lab(plugin, "lab1")
        _add_network(plugin, "lab1", "net1", "eth1")
        _add_endpoint(plugin, "ep1", "net1", "eth1", 349)

        await plugin._save_state()

        plugin2 = _make_plugin(monkeypatch, tmp_path)
        await plugin2._load_state()

        assert plugin2._global_next_vlan == 350
        assert plugin2._global_next_linked_vlan == 2150
        assert plugin2._allocated_vlans == {349}
