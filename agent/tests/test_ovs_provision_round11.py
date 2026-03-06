"""Tests for agent/network/ovs_provision.py — discover, provision, container restart (round 11)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.network.ovs_provision import (
    discover_existing_state,
    generate_port_name,
    get_container_pid,
    provision_interface,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_manager():
    """Create a minimal mock OVSNetworkManager."""
    mgr = MagicMock()
    mgr._bridge_name = "arch-ovs"
    mgr._initialized = True
    mgr._ports = {}
    mgr._links = {}
    mgr._vlan_allocator = MagicMock()
    mgr._vlan_allocator.allocate.return_value = 100
    mgr._vlan_allocator._allocated = {}
    mgr._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    mgr._run_cmd = AsyncMock(return_value=(0, "", ""))
    mgr._ip_link_exists = AsyncMock(return_value=False)
    mgr.docker = MagicMock()
    return mgr


# ---------------------------------------------------------------------------
# generate_port_name
# ---------------------------------------------------------------------------


class TestGeneratePortName:

    def test_max_length(self):
        name = generate_port_name("archetype-test-router1", "eth1")
        assert len(name) <= 15

    def test_starts_with_vh(self):
        name = generate_port_name("my-container", "eth2")
        assert name.startswith("vh")

    def test_different_for_different_inputs(self):
        """Port names include random suffix, so different calls produce different names."""
        names = {generate_port_name("c1", "eth1") for _ in range(10)}
        # At least some should be different (random suffix)
        assert len(names) > 1


# ---------------------------------------------------------------------------
# get_container_pid
# ---------------------------------------------------------------------------


class TestGetContainerPid:

    def test_returns_pid_for_running_container(self):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.attrs = {"State": {"Pid": 12345}}
        mock_client.containers.get.return_value = mock_container

        pid = _run(get_container_pid(mock_client, "test-container"))
        assert pid == 12345

    def test_returns_none_for_stopped_container(self):
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_container.attrs = {"State": {"Pid": 0}}
        mock_client.containers.get.return_value = mock_container

        assert _run(get_container_pid(mock_client, "test")) is None

    def test_returns_none_on_not_found(self):
        from docker.errors import NotFound
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = NotFound("not found")

        assert _run(get_container_pid(mock_client, "missing")) is None

    def test_returns_none_on_exception(self):
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = RuntimeError("fail")

        assert _run(get_container_pid(mock_client, "test")) is None


# ---------------------------------------------------------------------------
# discover_existing_state
# ---------------------------------------------------------------------------


class TestDiscoverExistingState:

    def test_ovs_query_failure_early_return(self):
        mgr = _make_manager()
        mgr._ovs_vsctl = AsyncMock(return_value=(1, "", "error"))
        _run(discover_existing_state(mgr))
        assert len(mgr._ports) == 0

    def test_no_vh_ports_early_return(self):
        mgr = _make_manager()
        mgr._ovs_vsctl = AsyncMock(return_value=(0, "some-other-port\n", ""))
        _run(discover_existing_state(mgr))
        assert len(mgr._ports) == 0

    def test_empty_stdout_early_return(self):
        mgr = _make_manager()
        mgr._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
        _run(discover_existing_state(mgr))
        assert len(mgr._ports) == 0


# ---------------------------------------------------------------------------
# provision_interface
# ---------------------------------------------------------------------------


class TestProvisionInterface:

    def test_already_provisioned_returns_cached_vlan(self):
        from agent.network.ovs import OVSPort
        mgr = _make_manager()
        mgr._ports["container:eth1"] = OVSPort(
            port_name="vh1234", container_name="container",
            interface_name="eth1", vlan_tag=42, lab_id="lab1",
        )
        vlan = _run(provision_interface(mgr, "container", "eth1", "lab1"))
        assert vlan == 42

    def test_container_not_running_raises(self):
        mgr = _make_manager()
        with patch("agent.network.ovs_provision.get_container_pid", new_callable=AsyncMock) as mock_pid:
            mock_pid.return_value = None
            with pytest.raises(RuntimeError, match="not running"):
                _run(provision_interface(mgr, "container", "eth1", "lab1"))

    def test_namespace_rename_failure_logs_warning_but_succeeds(self):
        """When nsenter rename fails, the function logs a warning but still completes."""
        mgr = _make_manager()

        async def mock_run_cmd(args):
            # The rename step uses nsenter; fail it
            if "nsenter" in args:
                return (1, "", "namespace error")
            return (0, "", "")

        mgr._run_cmd = mock_run_cmd
        mgr._ovs_vsctl = AsyncMock(return_value=(0, "", ""))

        with patch("agent.network.ovs_provision.get_container_pid", new_callable=AsyncMock) as mock_pid, \
             patch("agent.network.ovs_provision.settings") as mock_settings:
            mock_pid.return_value = 1234
            mock_settings.local_mtu = 0
            vlan = _run(provision_interface(mgr, "container", "eth1", "lab1"))

        # Function succeeds despite rename failure (only a warning is logged)
        assert vlan == 100
        assert "container:eth1" in mgr._ports

    def test_happy_path_tracks_port(self):
        mgr = _make_manager()

        with patch("agent.network.ovs_provision.get_container_pid", new_callable=AsyncMock) as mock_pid, \
             patch("agent.network.ovs_provision.settings") as mock_settings:
            mock_pid.return_value = 1234
            mock_settings.local_mtu = 0
            vlan = _run(provision_interface(mgr, "container", "eth1", "lab1"))

        assert vlan == 100
        assert "container:eth1" in mgr._ports
