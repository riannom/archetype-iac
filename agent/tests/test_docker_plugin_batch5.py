"""Batch 5 tests for DockerOVSPlugin — covers hot_connect, VXLAN tunnels,
external interfaces, cleanup, health check, and low-level helpers that were
previously only stubbed via AsyncMock.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.network.docker_plugin import (
    DockerOVSPlugin,
    EndpointState,
    LabBridge,
    NetworkState,
    LINKED_VLAN_START,
    LINKED_VLAN_END,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plugin(monkeypatch, tmp_path):
    """Create a DockerOVSPlugin with mocked I/O."""
    monkeypatch.setattr(
        "agent.network.docker_plugin.settings",
        SimpleNamespace(
            workspace_path=str(tmp_path),
            ovs_bridge_name="arch-ovs",
            local_mtu=0,
            lab_ttl_enabled=False,
            lab_ttl_seconds=3600,
            lab_ttl_check_interval=300,
            plugin_vxlan_dst_port=4789,
        ),
    )
    plugin = DockerOVSPlugin()
    plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    plugin._run_cmd = AsyncMock(return_value=(0, "", ""))
    plugin._mark_dirty_and_save = AsyncMock()
    plugin._validate_endpoint_exists = AsyncMock(return_value=True)
    plugin._bridge_name = "arch-ovs"
    return plugin


def _setup_lab(plugin, lab_id="lab1"):
    """Add a LabBridge and return it."""
    lb = LabBridge(lab_id=lab_id, bridge_name="arch-ovs")
    plugin.lab_bridges[lab_id] = lb
    return lb


def _add_endpoint(plugin, lab_id, container, iface, vlan, host_veth=None, net_id=None):
    """Register a network + endpoint for testing."""
    net_id = net_id or f"{lab_id}-{iface}"
    host_veth = host_veth or f"vh-{container[:4]}-{iface}"
    ep_id = f"ep-{container}-{iface}"

    plugin.networks[net_id] = NetworkState(
        network_id=net_id,
        lab_id=lab_id,
        interface_name=iface,
        bridge_name="arch-ovs",
    )
    lb = plugin.lab_bridges.get(lab_id)
    if lb:
        lb.network_ids.add(net_id)

    ep = EndpointState(
        endpoint_id=ep_id,
        network_id=net_id,
        host_veth=host_veth,
        cont_veth=f"vc-{container}-{iface}",
        vlan_tag=vlan,
        interface_name=iface,
        container_name=container,
    )
    plugin.endpoints[ep_id] = ep
    return ep


# ===========================================================================
# hot_connect
# ===========================================================================
class TestHotConnect:
    """Tests for DockerOVSPlugin.hot_connect()."""

    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_endpoint(plugin, "lab1", "r1", "eth1", vlan=100)
        _add_endpoint(plugin, "lab1", "r2", "eth1", vlan=101)

        result = await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")

        assert result is not None
        assert LINKED_VLAN_START <= result <= LINKED_VLAN_END
        # Both endpoints should share the same VLAN
        ep_a = [e for e in plugin.endpoints.values() if e.container_name == "r1"][0]
        ep_b = [e for e in plugin.endpoints.values() if e.container_name == "r2"][0]
        assert ep_a.vlan_tag == ep_b.vlan_tag == result

    @pytest.mark.asyncio
    async def test_missing_lab_bridge(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        result = await plugin.hot_connect("nonexistent", "r1", "eth1", "r2", "eth1")
        assert result is None

    @pytest.mark.asyncio
    async def test_endpoint_not_found(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        plugin._discover_endpoint = AsyncMock(return_value=None)

        result = await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")
        assert result is None

    @pytest.mark.asyncio
    async def test_validation_fails_port_a(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_endpoint(plugin, "lab1", "r1", "eth1", vlan=100)
        _add_endpoint(plugin, "lab1", "r2", "eth1", vlan=101)

        # First call fails (port A), second succeeds (port B)
        plugin._validate_endpoint_exists = AsyncMock(side_effect=[False, True])

        result = await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")
        assert result is None

    @pytest.mark.asyncio
    async def test_ovs_set_vlan_failure_port_a(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_endpoint(plugin, "lab1", "r1", "eth1", vlan=100)
        _add_endpoint(plugin, "lab1", "r2", "eth1", vlan=101)

        plugin._ovs_vsctl = AsyncMock(return_value=(1, "", "ovs error"))

        result = await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")
        assert result is None

    @pytest.mark.asyncio
    async def test_ovs_set_vlan_failure_port_b(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_endpoint(plugin, "lab1", "r1", "eth1", vlan=100)
        _add_endpoint(plugin, "lab1", "r2", "eth1", vlan=101)

        # First OVS call succeeds, second fails
        plugin._ovs_vsctl = AsyncMock(side_effect=[(0, "", ""), (1, "", "port B error")])

        result = await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")
        assert result is None

    @pytest.mark.asyncio
    async def test_lab_mismatch(self, monkeypatch, tmp_path):
        """Endpoints from different labs should be rejected."""
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _setup_lab(plugin, "lab2")
        _add_endpoint(plugin, "lab1", "r1", "eth1", vlan=100)
        _add_endpoint(plugin, "lab2", "r2", "eth1", vlan=101)

        result = await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")
        assert result is None

    @pytest.mark.asyncio
    async def test_discovers_missing_endpoints(self, monkeypatch, tmp_path):
        """When endpoints are not in memory, hot_connect discovers them."""
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")

        net1 = NetworkState(network_id="net1", lab_id="lab1", interface_name="eth1", bridge_name="arch-ovs")
        net2 = NetworkState(network_id="net2", lab_id="lab1", interface_name="eth1", bridge_name="arch-ovs")
        plugin.networks["net1"] = net1
        plugin.networks["net2"] = net2

        discovered_a = EndpointState(
            endpoint_id="ep-a", network_id="net1", host_veth="vh-r1-e1",
            cont_veth="vc1", vlan_tag=100, interface_name="eth1", container_name="r1",
        )
        discovered_b = EndpointState(
            endpoint_id="ep-b", network_id="net2", host_veth="vh-r2-e1",
            cont_veth="vc2", vlan_tag=101, interface_name="eth1", container_name="r2",
        )
        plugin._discover_endpoint = AsyncMock(side_effect=[discovered_a, discovered_b])

        result = await plugin.hot_connect("lab1", "r1", "eth1", "r2", "eth1")
        assert result is not None
        assert plugin._discover_endpoint.call_count == 2


# ===========================================================================
# _ensure_bridge
# ===========================================================================
class TestEnsureBridge:
    @pytest.mark.asyncio
    async def test_returns_cached(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        lb = await plugin._ensure_bridge("lab1")
        assert lb.lab_id == "lab1"
        # OVS should not be called since bridge was cached
        plugin._ovs_vsctl.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_bridge_when_missing(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        # br-exists returns nonzero (bridge doesn't exist)
        plugin._ovs_vsctl = AsyncMock(return_value=(1, "", ""))
        plugin._run_cmd = AsyncMock(return_value=(0, "", ""))

        lb = await plugin._ensure_bridge("new-lab")
        assert lb.lab_id == "new-lab"
        assert "new-lab" in plugin.lab_bridges
        # Should have called add-br
        calls = [str(c) for c in plugin._ovs_vsctl.call_args_list]
        assert any("add-br" in c for c in calls)

    @pytest.mark.asyncio
    async def test_reuses_existing_bridge(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        # br-exists returns 0 (bridge exists)
        plugin._ovs_vsctl = AsyncMock(return_value=(0, "", ""))

        lb = await plugin._ensure_bridge("lab-x")
        assert lb.bridge_name == "arch-ovs"

    @pytest.mark.asyncio
    async def test_bridge_creation_failure_raises(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        # br-exists fails, then add-br fails
        plugin._ovs_vsctl = AsyncMock(side_effect=[
            (1, "", ""),  # br-exists
            (1, "", "creation failed"),  # add-br
        ])
        with pytest.raises(RuntimeError, match="Failed to create OVS bridge"):
            await plugin._ensure_bridge("bad-lab")


# ===========================================================================
# _create_veth_pair / _attach_to_ovs / _delete_port
# ===========================================================================
class TestLowLevelHelpers:
    @pytest.mark.asyncio
    async def test_create_veth_pair_success(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        result = await plugin._create_veth_pair("vh-host", "vc-cont")
        assert result is True
        plugin._run_cmd.assert_called()

    @pytest.mark.asyncio
    async def test_create_veth_pair_failure(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        plugin._run_cmd = AsyncMock(return_value=(1, "", "RTNETLINK error"))
        result = await plugin._create_veth_pair("vh-host", "vc-cont")
        assert result is False

    @pytest.mark.asyncio
    async def test_create_veth_pair_sets_mtu(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "agent.network.docker_plugin.settings",
            SimpleNamespace(
                workspace_path=str(tmp_path),
                ovs_bridge_name="arch-ovs",
                local_mtu=9000,
                lab_ttl_enabled=False,
                lab_ttl_seconds=3600,
                lab_ttl_check_interval=300,
                plugin_vxlan_dst_port=4789,
            ),
        )
        plugin = DockerOVSPlugin()
        plugin._run_cmd = AsyncMock(return_value=(0, "", ""))
        result = await plugin._create_veth_pair("vh-host", "vc-cont")
        assert result is True
        # Should have calls with mtu=9000
        mtu_calls = [c for c in plugin._run_cmd.call_args_list if "9000" in str(c)]
        assert len(mtu_calls) == 2  # host side + container side

    @pytest.mark.asyncio
    async def test_attach_to_ovs_success(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        result = await plugin._attach_to_ovs("arch-ovs", "vh-port", 100)
        assert result is True
        plugin._ovs_vsctl.assert_awaited_once_with(
            "add-port",
            "arch-ovs",
            "vh-port",
            "tag=100",
            "--",
            "set",
            "interface",
            "vh-port",
            "type=system",
        )

    @pytest.mark.asyncio
    async def test_attach_to_ovs_writes_external_ids(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)

        result = await plugin._attach_to_ovs(
            "arch-ovs",
            "vh-port",
            100,
            external_ids={
                "archetype.endpoint_id": "ep-1",
                "archetype.lab_id": "lab1",
            },
        )

        assert result is True
        plugin._ovs_vsctl.assert_awaited_once_with(
            "add-port",
            "arch-ovs",
            "vh-port",
            "tag=100",
            "--",
            "set",
            "interface",
            "vh-port",
            "type=system",
            "external_ids:archetype.endpoint_id=ep-1",
            "external_ids:archetype.lab_id=lab1",
        )

    @pytest.mark.asyncio
    async def test_attach_to_ovs_failure(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        plugin._ovs_vsctl = AsyncMock(return_value=(1, "", "add-port failed"))
        result = await plugin._attach_to_ovs("arch-ovs", "vh-port", 100)
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_port(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        await plugin._delete_port("arch-ovs", "vh-port")
        assert plugin._ovs_vsctl.call_count == 1
        assert plugin._run_cmd.call_count == 1


# ===========================================================================
# create_vxlan_tunnel / delete_vxlan_tunnel
# ===========================================================================
class TestVxlanTunnel:
    @pytest.mark.asyncio
    async def test_create_success(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")

        port = await plugin.create_vxlan_tunnel(
            "lab1", "link-1", "10.0.0.1", "10.0.0.2", 100000, 2050,
        )
        assert port == "vx100000"
        assert 100000 in plugin.lab_bridges["lab1"].vxlan_tunnels

    @pytest.mark.asyncio
    async def test_create_idempotent(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        lb = _setup_lab(plugin, "lab1")
        lb.vxlan_tunnels[100000] = "vx100000"

        port = await plugin.create_vxlan_tunnel(
            "lab1", "link-1", "10.0.0.1", "10.0.0.2", 100000, 2050,
        )
        assert port == "vx100000"
        # No OVS calls since tunnel already exists
        plugin._run_cmd.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_no_lab_bridge(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        with pytest.raises(ValueError, match="Lab bridge not found"):
            await plugin.create_vxlan_tunnel(
                "missing", "link-1", "10.0.0.1", "10.0.0.2", 100000, 2050,
            )

    @pytest.mark.asyncio
    async def test_create_ip_link_failure(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        plugin._run_cmd = AsyncMock(return_value=(1, "", "some error"))

        with pytest.raises(RuntimeError, match="Failed to create VXLAN interface"):
            await plugin.create_vxlan_tunnel(
                "lab1", "link-1", "10.0.0.1", "10.0.0.2", 100000, 2050,
            )

    @pytest.mark.asyncio
    async def test_create_file_exists_is_tolerated(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        plugin._run_cmd = AsyncMock(return_value=(1, "", "File exists"))

        port = await plugin.create_vxlan_tunnel(
            "lab1", "link-1", "10.0.0.1", "10.0.0.2", 100000, 2050,
        )
        assert port == "vx100000"

    @pytest.mark.asyncio
    async def test_create_ovs_add_port_failure_cleans_up(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        # ip link succeeds, ovs-vsctl add-port fails
        plugin._run_cmd = AsyncMock(return_value=(0, "", ""))
        plugin._ovs_vsctl = AsyncMock(return_value=(1, "", "OVS error"))

        with pytest.raises(RuntimeError, match="Failed to add VXLAN port"):
            await plugin.create_vxlan_tunnel(
                "lab1", "link-1", "10.0.0.1", "10.0.0.2", 100000, 2050,
            )
        # Should have cleaned up the VXLAN interface
        cleanup_calls = [c for c in plugin._run_cmd.call_args_list if "delete" in str(c)]
        assert len(cleanup_calls) >= 1

    @pytest.mark.asyncio
    async def test_delete_success(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        lb = _setup_lab(plugin, "lab1")
        lb.vxlan_tunnels[100000] = "vx100000"

        result = await plugin.delete_vxlan_tunnel("lab1", 100000)
        assert result is True
        assert 100000 not in lb.vxlan_tunnels

    @pytest.mark.asyncio
    async def test_delete_not_found(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        result = await plugin.delete_vxlan_tunnel("lab1", 999)
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_no_lab(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        result = await plugin.delete_vxlan_tunnel("missing", 100000)
        assert result is False


# ===========================================================================
# attach_external_interface / detach_external_interface / connect_to_external
# ===========================================================================
class TestExternalInterface:
    @pytest.mark.asyncio
    async def test_attach_with_vlan(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")

        vlan = await plugin.attach_external_interface("lab1", "enp0s8", vlan_tag=500)
        assert vlan == 500
        assert plugin.lab_bridges["lab1"].external_ports["enp0s8"] == 500

    @pytest.mark.asyncio
    async def test_attach_trunk_mode(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")

        vlan = await plugin.attach_external_interface("lab1", "enp0s8")
        assert vlan == 0

    @pytest.mark.asyncio
    async def test_attach_already_attached(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        lb = _setup_lab(plugin, "lab1")
        lb.external_ports["enp0s8"] = 500

        vlan = await plugin.attach_external_interface("lab1", "enp0s8")
        assert vlan == 500

    @pytest.mark.asyncio
    async def test_attach_interface_not_found(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        plugin._run_cmd = AsyncMock(return_value=(1, "", "not found"))

        with pytest.raises(ValueError, match="not found"):
            await plugin.attach_external_interface("lab1", "bad-iface")

    @pytest.mark.asyncio
    async def test_attach_no_lab(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        with pytest.raises(ValueError, match="Lab bridge not found"):
            await plugin.attach_external_interface("missing", "enp0s8")

    @pytest.mark.asyncio
    async def test_detach_success(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        lb = _setup_lab(plugin, "lab1")
        lb.external_ports["enp0s8"] = 500

        result = await plugin.detach_external_interface("lab1", "enp0s8")
        assert result is True
        assert "enp0s8" not in lb.external_ports

    @pytest.mark.asyncio
    async def test_connect_to_external_happy_path(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        lb = _setup_lab(plugin, "lab1")
        lb.external_ports["enp0s8"] = 500
        _add_endpoint(plugin, "lab1", "r1", "eth1", vlan=100)

        vlan = await plugin.connect_to_external("lab1", "r1", "eth1", "enp0s8")
        assert vlan == 500

    @pytest.mark.asyncio
    async def test_connect_to_external_not_attached(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_endpoint(plugin, "lab1", "r1", "eth1", vlan=100)

        with pytest.raises(ValueError, match="not attached"):
            await plugin.connect_to_external("lab1", "r1", "eth1", "enp0s8")

    @pytest.mark.asyncio
    async def test_connect_to_external_endpoint_not_found(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        lb = _setup_lab(plugin, "lab1")
        lb.external_ports["enp0s8"] = 500

        with pytest.raises(ValueError, match="Endpoint not found"):
            await plugin.connect_to_external("lab1", "missing", "eth1", "enp0s8")


# ===========================================================================
# cleanup_stale_state
# ===========================================================================
class TestCleanupStaleState:
    @pytest.mark.asyncio
    async def test_removes_stale_networks(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_endpoint(plugin, "lab1", "r1", "eth1", vlan=100, net_id="stale-net-id")

        # Docker returns empty network list (all networks gone)
        plugin._run_cmd = AsyncMock(side_effect=[
            (0, "", ""),          # docker network ls (empty)
            (0, "", ""),          # docker ps (empty)
        ])
        plugin._delete_port = AsyncMock()

        stats = await plugin.cleanup_stale_state()
        assert stats["networks_removed"] == 1
        assert stats["endpoints_removed"] == 1

    @pytest.mark.asyncio
    async def test_preserves_live_networks(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        _setup_lab(plugin, "lab1")
        _add_endpoint(plugin, "lab1", "r1", "eth1", vlan=100, net_id="live-net-id")

        # Docker returns the network ID as live
        plugin._run_cmd = AsyncMock(side_effect=[
            (0, "live-net-id\n", ""),    # docker network ls
            (0, "r1\n", ""),             # docker ps
        ])

        stats = await plugin.cleanup_stale_state()
        assert stats["networks_removed"] == 0
        assert stats["endpoints_removed"] == 0

    @pytest.mark.asyncio
    async def test_docker_failure_returns_empty_stats(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        plugin._run_cmd = AsyncMock(return_value=(1, "", "docker error"))

        stats = await plugin.cleanup_stale_state()
        assert stats["networks_removed"] == 0
        assert stats["endpoints_removed"] == 0


# ===========================================================================
# _full_lab_cleanup
# ===========================================================================
class TestFullLabCleanup:
    @pytest.mark.asyncio
    async def test_cleans_all_resources(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        lb = _setup_lab(plugin, "lab1")
        lb.vxlan_tunnels[100000] = "vx100000"
        lb.external_ports["enp0s8"] = 500
        _add_endpoint(plugin, "lab1", "r1", "eth1", vlan=100)

        await plugin._full_lab_cleanup("lab1")

        assert "lab1" not in plugin.lab_bridges
        assert len([n for n in plugin.networks.values() if n.lab_id == "lab1"]) == 0
        assert len([e for e in plugin.endpoints.values()
                     if plugin.networks.get(e.network_id, SimpleNamespace(lab_id=None)).lab_id == "lab1"]) == 0

    @pytest.mark.asyncio
    async def test_noop_for_missing_lab(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        await plugin._full_lab_cleanup("nonexistent")
        plugin._ovs_vsctl.assert_not_called()


# ===========================================================================
# health_check
# ===========================================================================
class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        plugin._state_file = tmp_path / "state.json"
        plugin._state_file.touch()
        plugin._state_dirty = False
        plugin._started_at = datetime.now(timezone.utc)

        with patch("agent.network.docker_plugin.os.path.exists", return_value=True):
            result = await plugin.health_check()

        assert result["healthy"] is True
        assert result["checks"]["ovs_available"] is True
        assert result["checks"]["socket_exists"] is True
        assert "uptime_seconds" in result

    @pytest.mark.asyncio
    async def test_unhealthy_no_ovs(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        plugin._state_file = tmp_path / "state.json"
        plugin._started_at = datetime.now(timezone.utc)
        plugin._ovs_vsctl = AsyncMock(return_value=(1, "", "not found"))

        with patch("agent.network.docker_plugin.os.path.exists", return_value=True):
            result = await plugin.health_check()

        assert result["healthy"] is False
        assert result["checks"]["ovs_available"] is False


# ===========================================================================
# _maybe_delete_bridge
# ===========================================================================
class TestMaybeDeleteBridge:
    @pytest.mark.asyncio
    async def test_removes_empty_lab_tracking(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        lb = _setup_lab(plugin, "lab1")
        lb.network_ids = set()  # No networks left

        await plugin._maybe_delete_bridge("lab1")
        assert "lab1" not in plugin.lab_bridges

    @pytest.mark.asyncio
    async def test_keeps_lab_with_networks(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        lb = _setup_lab(plugin, "lab1")
        lb.network_ids = {"net1"}

        await plugin._maybe_delete_bridge("lab1")
        assert "lab1" in plugin.lab_bridges

    @pytest.mark.asyncio
    async def test_noop_for_missing_lab(self, monkeypatch, tmp_path):
        plugin = _make_plugin(monkeypatch, tmp_path)
        await plugin._maybe_delete_bridge("nonexistent")
