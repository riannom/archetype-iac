"""Tests for the CarrierMonitor OVS polling and notification system."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.network.carrier_monitor import MonitoredPort


def _make_ports(*tuples):
    """Build a ports dict from (port_name, container, iface, lab_id) tuples.

    Returns ``{port_name: MonitoredPort}``, matching ``build_managed_ports`` output.
    """
    ports = {}
    for port_name, container, iface, lab_id in tuples:
        ports[port_name] = MonitoredPort(port_name, container, iface, lab_id)
    return ports


def _ovs_json(*rows):
    """Build OVS JSON output for ``list Interface`` with name,link_state cols."""
    import json

    return json.dumps({"data": list(rows)}).encode()


class TestCarrierMonitorPollAndDetect:
    """Poll and detect state changes."""

    @pytest.mark.asyncio
    async def test_detect_port_going_down(self):
        """When OVS reports link_state=down, notifier should fire with carrier=off."""
        from agent.network.carrier_monitor import CarrierMonitor

        ports = _make_ports(
            ("vh1234e1ab", "archetype-lab1-r1", "eth1", "lab1"),
        )
        notifier = AsyncMock(return_value=True)

        mon = CarrierMonitor(
            ovs_bridge="arch-ovs",
            get_managed_ports=lambda: ports,
            notifier=notifier,
        )

        # Seed: port is up initially
        mock_proc_up = AsyncMock()
        mock_proc_up.returncode = 0
        mock_proc_up.communicate = AsyncMock(
            return_value=(_ovs_json(["vh1234e1ab", "up"]), b""),
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc_up):
            await mon._seed_initial_state()

        assert mon._last_link_states.get("vh1234e1ab") == "up"

        # Poll: port goes down
        mock_proc_down = AsyncMock()
        mock_proc_down.returncode = 0
        mock_proc_down.communicate = AsyncMock(
            return_value=(_ovs_json(["vh1234e1ab", "down"]), b""),
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc_down):
            with patch("asyncio.create_task") as mock_task:
                await mon._poll_once()

        assert mon._last_link_states["vh1234e1ab"] == "down"
        # Notifier should have been dispatched
        mock_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_detect_port_going_up(self):
        """When OVS reports link_state=up after being down, notifier fires with carrier=on."""
        from agent.network.carrier_monitor import CarrierMonitor

        ports = _make_ports(
            ("vhAAAAe1cc", "archetype-lab2-r2", "eth1", "lab2"),
        )
        notifier = AsyncMock(return_value=True)

        mon = CarrierMonitor("arch-ovs", lambda: ports, notifier)

        # Seed: port starts down
        proc_down = AsyncMock()
        proc_down.returncode = 0
        proc_down.communicate = AsyncMock(
            return_value=(_ovs_json(["vhAAAAe1cc", "down"]), b""),
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc_down):
            await mon._seed_initial_state()

        assert mon._last_link_states["vhAAAAe1cc"] == "down"

        # Poll: port comes up
        proc_up = AsyncMock()
        proc_up.returncode = 0
        proc_up.communicate = AsyncMock(
            return_value=(_ovs_json(["vhAAAAe1cc", "up"]), b""),
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc_up):
            with patch("asyncio.create_task") as mock_task:
                await mon._poll_once()

        assert mon._last_link_states["vhAAAAe1cc"] == "up"
        mock_task.assert_called_once()


class TestCarrierMonitorManagedFiltering:
    """Only managed ports (in OVSNetworkManager._ports) are tracked."""

    @pytest.mark.asyncio
    async def test_unmanaged_ports_ignored(self):
        """VXLAN and management ports should be filtered out."""
        from agent.network.carrier_monitor import CarrierMonitor

        ports = _make_ports(
            ("vhXXXXe1ab", "archetype-lab1-r1", "eth1", "lab1"),
        )
        notifier = AsyncMock(return_value=True)
        mon = CarrierMonitor("arch-ovs", lambda: ports, notifier)

        # OVS returns managed port + unmanaged VXLAN port
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(
            return_value=(
                _ovs_json(
                    ["vhXXXXe1ab", "up"],
                    ["vxlan12345", "up"],
                    ["arch-ovs", "up"],
                ),
                b"",
            ),
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            await mon._seed_initial_state()

        # Only managed port tracked
        assert "vhXXXXe1ab" in mon._last_link_states
        assert "vxlan12345" not in mon._last_link_states
        assert "arch-ovs" not in mon._last_link_states


class TestCarrierMonitorStartupInit:
    """First poll should not fire spurious transitions."""

    @pytest.mark.asyncio
    async def test_no_spurious_on_startup(self):
        """Seeding initial state should not trigger notifications."""
        from agent.network.carrier_monitor import CarrierMonitor

        ports = _make_ports(
            ("vhAAAAe1ab", "archetype-lab1-r1", "eth1", "lab1"),
            ("vhBBBBe1ab", "archetype-lab1-r2", "eth1", "lab1"),
        )
        notifier = AsyncMock(return_value=True)
        mon = CarrierMonitor("arch-ovs", lambda: ports, notifier)

        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(
            return_value=(
                _ovs_json(
                    ["vhAAAAe1ab", "down"],
                    ["vhBBBBe1ab", "up"],
                ),
                b"",
            ),
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            await mon._seed_initial_state()

        # No notifications should have been fired
        notifier.assert_not_called()
        assert mon._last_link_states["vhAAAAe1ab"] == "down"
        assert mon._last_link_states["vhBBBBe1ab"] == "up"


class TestCarrierMonitorPortDisappears:
    """Port removed between polls should not crash."""

    @pytest.mark.asyncio
    async def test_port_removed_from_ovs(self):
        """If a port disappears from OVS, tracking is cleaned up silently."""
        from agent.network.carrier_monitor import CarrierMonitor

        ports = _make_ports(
            ("vhAAAAe1ab", "archetype-lab1-r1", "eth1", "lab1"),
        )
        notifier = AsyncMock(return_value=True)
        mon = CarrierMonitor("arch-ovs", lambda: ports, notifier)

        # Seed with port present
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(
            return_value=(_ovs_json(["vhAAAAe1ab", "up"]), b""),
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            await mon._seed_initial_state()

        assert "vhAAAAe1ab" in mon._last_link_states

        # Poll with port missing from OVS
        proc_empty = AsyncMock()
        proc_empty.returncode = 0
        proc_empty.communicate = AsyncMock(
            return_value=(_ovs_json(), b""),
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc_empty):
            with patch("asyncio.create_task") as mock_task:
                await mon._poll_once()

        # Port tracking cleaned up, no crash, no notification
        assert "vhAAAAe1ab" not in mon._last_link_states
        mock_task.assert_not_called()


class TestCarrierMonitorMultipleTransitions:
    """Several ports change state in one poll cycle."""

    @pytest.mark.asyncio
    async def test_multiple_ports_change(self):
        """When multiple ports transition in one poll, all are reported."""
        from agent.network.carrier_monitor import CarrierMonitor

        ports = _make_ports(
            ("vhAAAAe1ab", "archetype-lab1-r1", "eth1", "lab1"),
            ("vhBBBBe1ab", "archetype-lab1-r2", "eth1", "lab1"),
            ("vhCCCCe2ab", "archetype-lab1-r3", "eth2", "lab1"),
        )
        notifier = AsyncMock(return_value=True)
        mon = CarrierMonitor("arch-ovs", lambda: ports, notifier)

        # Seed all up
        proc_up = AsyncMock()
        proc_up.returncode = 0
        proc_up.communicate = AsyncMock(
            return_value=(
                _ovs_json(
                    ["vhAAAAe1ab", "up"],
                    ["vhBBBBe1ab", "up"],
                    ["vhCCCCe2ab", "up"],
                ),
                b"",
            ),
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc_up):
            await mon._seed_initial_state()

        # Two ports go down, one stays up
        proc_mixed = AsyncMock()
        proc_mixed.returncode = 0
        proc_mixed.communicate = AsyncMock(
            return_value=(
                _ovs_json(
                    ["vhAAAAe1ab", "down"],
                    ["vhBBBBe1ab", "up"],
                    ["vhCCCCe2ab", "down"],
                ),
                b"",
            ),
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc_mixed):
            with patch("asyncio.create_task") as mock_task:
                await mon._poll_once()

        # Two transitions detected
        assert mock_task.call_count == 2


class TestCarrierMonitorSteadyState:
    """No notification for steady state."""

    @pytest.mark.asyncio
    async def test_no_notification_for_steady_down(self):
        """Port stays down between polls → no re-notification."""
        from agent.network.carrier_monitor import CarrierMonitor

        ports = _make_ports(
            ("vhAAAAe1ab", "archetype-lab1-r1", "eth1", "lab1"),
        )
        notifier = AsyncMock(return_value=True)
        mon = CarrierMonitor("arch-ovs", lambda: ports, notifier)

        proc_down = AsyncMock()
        proc_down.returncode = 0
        proc_down.communicate = AsyncMock(
            return_value=(_ovs_json(["vhAAAAe1ab", "down"]), b""),
        )

        # Seed: down
        with patch("asyncio.create_subprocess_exec", return_value=proc_down):
            await mon._seed_initial_state()

        # Poll again: still down
        with patch("asyncio.create_subprocess_exec", return_value=proc_down):
            with patch("asyncio.create_task") as mock_task:
                await mon._poll_once()

        # No notification for steady state
        mock_task.assert_not_called()


class TestContainerToNodeResolution:
    """Verify container name to node name mapping."""

    def test_docker_container_name(self):
        """archetype-{lab_id}-{node} → node."""
        from agent.network.carrier_monitor import CarrierMonitor

        assert CarrierMonitor._container_to_node("archetype-lab123-r1", "lab123") == "r1"

    def test_libvirt_container_name(self):
        """arch-{lab_id}-{node} → node."""
        from agent.network.carrier_monitor import CarrierMonitor

        assert CarrierMonitor._container_to_node("arch-lab123-r1", "lab123") == "r1"

    def test_long_lab_id_truncation(self):
        """Lab IDs are truncated to 20 chars in container names."""
        from agent.network.carrier_monitor import CarrierMonitor

        lab_id = "a" * 30
        container = f"archetype-{'a' * 20}-node1"
        assert CarrierMonitor._container_to_node(container, lab_id) == "node1"

    def test_fallback_for_unknown_prefix(self):
        """Unknown prefix returns container name as-is."""
        from agent.network.carrier_monitor import CarrierMonitor

        assert CarrierMonitor._container_to_node("unknown-container", "lab1") == "unknown-container"

    def test_node_with_hyphens(self):
        """Node names with hyphens are preserved."""
        from agent.network.carrier_monitor import CarrierMonitor

        assert CarrierMonitor._container_to_node("archetype-lab1-my-node", "lab1") == "my-node"


class TestBuildManagedPorts:
    """Verify build_managed_ports merges OVS manager and Docker plugin ports."""

    def test_ovs_manager_ports_included(self):
        """OVSNetworkManager._ports are included in the result."""
        from agent.network.carrier_monitor import build_managed_ports

        class FakeOVSMgr:
            class Port:
                port_name = "vh1111e1ab"
                container_name = "archetype-lab1-r1"
                interface_name = "eth1"
                lab_id = "lab1"

            _ports = {"archetype-lab1-r1:eth1": Port()}

        result = build_managed_ports(ovs_mgr=FakeOVSMgr())
        assert "vh1111e1ab" in result
        p = result["vh1111e1ab"]
        assert p.container_name == "archetype-lab1-r1"
        assert p.interface_name == "eth1"
        assert p.lab_id == "lab1"

    def test_docker_plugin_endpoints_included(self):
        """DockerOVSPlugin endpoints (with host_veth) are included."""
        from agent.network.carrier_monitor import build_managed_ports

        class FakeNetwork:
            network_id = "net-1"
            lab_id = "lab2"
            interface_name = "eth1"

        class FakeEndpoint:
            endpoint_id = "ep-1"
            network_id = "net-1"
            interface_name = "eth1"
            host_veth = "vh2222e1ab"
            container_name = "archetype-lab2-r2"

        class FakePlugin:
            networks = {"net-1": FakeNetwork()}
            endpoints = {"ep-1": FakeEndpoint()}

        result = build_managed_ports(docker_plugin=FakePlugin())
        assert "vh2222e1ab" in result
        p = result["vh2222e1ab"]
        assert p.container_name == "archetype-lab2-r2"
        assert p.lab_id == "lab2"

    def test_both_sources_merged(self):
        """Ports from both OVS manager and Docker plugin are merged."""
        from agent.network.carrier_monitor import build_managed_ports

        class OVSPort:
            port_name = "vh1111e1ab"
            container_name = "archetype-lab1-r1"
            interface_name = "eth1"
            lab_id = "lab1"

        class FakeOVSMgr:
            _ports = {"archetype-lab1-r1:eth1": OVSPort()}

        class FakeNet:
            network_id = "net-2"
            lab_id = "lab2"
            interface_name = "eth1"

        class FakeEP:
            endpoint_id = "ep-2"
            network_id = "net-2"
            interface_name = "eth1"
            host_veth = "vh2222e1ab"
            container_name = "archetype-lab2-r2"

        class FakePlugin:
            networks = {"net-2": FakeNet()}
            endpoints = {"ep-2": FakeEP()}

        result = build_managed_ports(ovs_mgr=FakeOVSMgr(), docker_plugin=FakePlugin())
        assert len(result) == 2
        assert "vh1111e1ab" in result
        assert "vh2222e1ab" in result

    def test_endpoint_without_host_veth_skipped(self):
        """Endpoints without host_veth (not yet provisioned) are skipped."""
        from agent.network.carrier_monitor import build_managed_ports

        class FakeNet:
            network_id = "net-1"
            lab_id = "lab1"
            interface_name = "eth1"

        class FakeEP:
            endpoint_id = "ep-1"
            network_id = "net-1"
            interface_name = "eth1"
            host_veth = ""  # Not yet provisioned
            container_name = "archetype-lab1-r1"

        class FakePlugin:
            networks = {"net-1": FakeNet()}
            endpoints = {"ep-1": FakeEP()}

        result = build_managed_ports(docker_plugin=FakePlugin())
        assert len(result) == 0

    def test_endpoint_with_missing_network_skipped(self):
        """Endpoints whose network_id has no matching network are skipped."""
        from agent.network.carrier_monitor import build_managed_ports

        class FakeEP:
            endpoint_id = "ep-1"
            network_id = "orphan-net"
            interface_name = "eth1"
            host_veth = "vh3333e1ab"
            container_name = "archetype-lab1-r1"

        class FakePlugin:
            networks = {}  # No networks
            endpoints = {"ep-1": FakeEP()}

        result = build_managed_ports(docker_plugin=FakePlugin())
        assert len(result) == 0

    def test_none_sources_return_empty(self):
        """Passing None for both sources returns empty dict."""
        from agent.network.carrier_monitor import build_managed_ports

        result = build_managed_ports(ovs_mgr=None, docker_plugin=None)
        assert result == {}
