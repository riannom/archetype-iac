"""Tests for OVSBackend delegation — each method calls the correct inner manager."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = REPO_ROOT / "agent"
for p in [str(REPO_ROOT), str(AGENT_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Patch all heavy dependencies before importing OVSBackend
# ---------------------------------------------------------------------------

_mock_overlay_cls = MagicMock()
_mock_ovs_cls = MagicMock()
_mock_plugin_fn = MagicMock()
_mock_settings = MagicMock(
    enable_vxlan=True,
    enable_ovs=True,
    enable_docker=False,
    enable_ovs_plugin=False,
)

with (
    patch("agent.network.overlay.OverlayManager", _mock_overlay_cls),
    patch("agent.network.ovs.OVSNetworkManager", _mock_ovs_cls),
    patch("agent.network.docker_plugin.get_docker_ovs_plugin", _mock_plugin_fn),
    patch("agent.network.backends.ovs_backend.settings", _mock_settings),
):
    from agent.network.backends.ovs_backend import OVSBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_backend():
    """Create an OVSBackend with mocked inner managers."""
    with (
        patch("agent.network.backends.ovs_backend.OverlayManager") as overlay_cls,
        patch("agent.network.backends.ovs_backend.OVSNetworkManager") as ovs_cls,
        patch("agent.network.backends.ovs_backend.get_docker_ovs_plugin") as plugin_fn,
    ):
        overlay_inst = MagicMock()
        ovs_inst = MagicMock()
        plugin_inst = MagicMock()
        overlay_cls.return_value = overlay_inst
        ovs_cls.return_value = ovs_inst
        plugin_fn.return_value = plugin_inst
        backend = OVSBackend()
    return backend, overlay_inst, ovs_inst, plugin_inst


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class TestProperties:
    def test_overlay_manager_property(self):
        backend, overlay, _ovs, _plugin = _make_backend()
        assert backend.overlay_manager is overlay

    def test_ovs_manager_property(self):
        backend, _overlay, ovs, _plugin = _make_backend()
        assert backend.ovs_manager is ovs

    def test_plugin_running_false_by_default(self):
        backend, *_ = _make_backend()
        assert backend.plugin_running is False

    def test_plugin_running_true_after_set(self):
        backend, *_ = _make_backend()
        backend._plugin_runner = MagicMock()
        assert backend.plugin_running is True


# ---------------------------------------------------------------------------
# OVS delegations (sync)
# ---------------------------------------------------------------------------

class TestOVSDelegation:
    def test_ovs_initialized(self):
        backend, _overlay, ovs, _plugin = _make_backend()
        ovs._initialized = True
        assert backend.ovs_initialized() is True
        ovs._initialized = False
        assert backend.ovs_initialized() is False

    def test_get_ovs_status(self):
        backend, _overlay, ovs, _plugin = _make_backend()
        ovs.get_status.return_value = {"bridge": "arch-ovs"}
        assert backend.get_ovs_status() == {"bridge": "arch-ovs"}
        ovs.get_status.assert_called_once()

    def test_get_links_for_lab(self):
        backend, _overlay, ovs, _plugin = _make_backend()
        ovs.get_links_for_lab.return_value = ["link1"]
        assert backend.get_links_for_lab("lab-1") == ["link1"]
        ovs.get_links_for_lab.assert_called_once_with("lab-1")


# ---------------------------------------------------------------------------
# OVS delegations (async)
# ---------------------------------------------------------------------------

class TestOVSAsyncDelegation:
    @pytest.mark.asyncio
    async def test_ensure_ovs_initialized(self):
        backend, _overlay, ovs, _plugin = _make_backend()
        ovs._initialized = False
        ovs.initialize = AsyncMock()
        await backend.ensure_ovs_initialized()
        ovs.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ensure_ovs_initialized_already_done(self):
        backend, _overlay, ovs, _plugin = _make_backend()
        ovs._initialized = True
        ovs.initialize = AsyncMock()
        await backend.ensure_ovs_initialized()
        ovs.initialize.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handle_container_restart_not_initialized(self):
        backend, _overlay, ovs, _plugin = _make_backend()
        ovs._initialized = False
        result = await backend.handle_container_restart("ctr-1", "lab-1")
        assert result == {"reprovisioned_ports": 0, "reconnected_links": 0, "errors": []}

    @pytest.mark.asyncio
    async def test_handle_container_restart_delegates(self):
        backend, _overlay, ovs, _plugin = _make_backend()
        ovs._initialized = True
        ovs.handle_container_restart = AsyncMock(return_value={"reprovisioned_ports": 2})
        result = await backend.handle_container_restart("ctr-1", "lab-1")
        assert result == {"reprovisioned_ports": 2}
        ovs.handle_container_restart.assert_awaited_once_with("ctr-1", "lab-1")

    @pytest.mark.asyncio
    async def test_connect_to_external(self):
        backend, _overlay, ovs, _plugin = _make_backend()
        ovs.connect_to_external = AsyncMock(return_value=100)
        result = await backend.connect_to_external("ctr", "eth1", "ens3", vlan_tag=10)
        assert result == 100
        ovs.connect_to_external.assert_awaited_once_with(
            container_name="ctr",
            interface_name="eth1",
            external_interface="ens3",
            vlan_tag=10,
        )

    @pytest.mark.asyncio
    async def test_detach_external_interface(self):
        backend, _overlay, ovs, _plugin = _make_backend()
        ovs.detach_external_interface = AsyncMock(return_value=True)
        assert await backend.detach_external_interface("ens3") is True
        ovs.detach_external_interface.assert_awaited_once_with("ens3")


# ---------------------------------------------------------------------------
# Overlay delegations
# ---------------------------------------------------------------------------

class TestOverlayDelegation:
    def test_overlay_status(self):
        backend, overlay, _ovs, _plugin = _make_backend()
        overlay.get_tunnel_status.return_value = {"tunnels": 3}
        assert backend.overlay_status() == {"tunnels": 3}

    def test_overlay_get_vtep(self):
        backend, overlay, _ovs, _plugin = _make_backend()
        sentinel = object()
        overlay.get_vtep.return_value = sentinel
        assert backend.overlay_get_vtep("10.0.0.1") is sentinel
        overlay.get_vtep.assert_called_once_with("10.0.0.1")

    @pytest.mark.asyncio
    async def test_overlay_create_link_tunnel(self):
        backend, overlay, _ovs, _plugin = _make_backend()
        overlay.create_link_tunnel = AsyncMock(return_value="tunnel-obj")
        result = await backend.overlay_create_link_tunnel(
            lab_id="lab1",
            link_id="lk1",
            vni=5000,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            local_vlan=3001,
            tenant_mtu=1400,
        )
        assert result == "tunnel-obj"
        overlay.create_link_tunnel.assert_awaited_once_with(
            lab_id="lab1",
            link_id="lk1",
            vni=5000,
            local_ip="10.0.0.1",
            remote_ip="10.0.0.2",
            local_vlan=3001,
            tenant_mtu=1400,
        )

    @pytest.mark.asyncio
    async def test_overlay_delete_link_tunnel(self):
        backend, overlay, _ovs, _plugin = _make_backend()
        overlay.delete_link_tunnel = AsyncMock(return_value=True)
        assert await backend.overlay_delete_link_tunnel("lk1", lab_id="lab1") is True
        overlay.delete_link_tunnel.assert_awaited_once_with(link_id="lk1", lab_id="lab1")

    @pytest.mark.asyncio
    async def test_overlay_cleanup_lab(self):
        backend, overlay, _ovs, _plugin = _make_backend()
        overlay.cleanup_lab = AsyncMock(return_value={"cleaned": 2})
        result = await backend.overlay_cleanup_lab("lab1")
        assert result == {"cleaned": 2}
        overlay.cleanup_lab.assert_awaited_once_with("lab1")


# ---------------------------------------------------------------------------
# check_port_exists
# ---------------------------------------------------------------------------

class TestCheckPortExists:
    def test_port_exists_returncode_zero(self):
        backend, *_ = _make_backend()
        with patch("agent.network.backends.ovs_backend.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert backend.check_port_exists("veth123") is True
            mock_run.assert_called_once_with(
                ["ovs-vsctl", "port-to-br", "veth123"],
                capture_output=True,
                text=True,
            )

    def test_port_exists_returncode_nonzero(self):
        backend, *_ = _make_backend()
        with patch("agent.network.backends.ovs_backend.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert backend.check_port_exists("nosuch") is False

    def test_port_exists_exception(self):
        backend, *_ = _make_backend()
        with patch("agent.network.backends.ovs_backend.subprocess.run") as mock_run:
            mock_run.side_effect = OSError("no such binary")
            assert backend.check_port_exists("port") is False


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------

class TestInitialize:
    @pytest.mark.asyncio
    async def test_initialize_vxlan_and_ovs(self):
        backend, overlay, ovs, _plugin = _make_backend()
        overlay.recover_link_tunnels = AsyncMock(return_value=5)
        ovs.initialize = AsyncMock()
        ovs.recover_allocations = AsyncMock(return_value=3)

        with patch("agent.network.backends.ovs_backend.settings", MagicMock(
            enable_vxlan=True, enable_ovs=True, enable_docker=False, enable_ovs_plugin=False,
        )):
            info = await backend.initialize()

        assert info["link_tunnels_recovered"] == 5
        assert info["ovs_initialized"] is True
        assert info["vlans_recovered"] == 3
        assert info["ovs_plugin_started"] is False

    @pytest.mark.asyncio
    async def test_initialize_nothing_enabled(self):
        backend, overlay, ovs, _plugin = _make_backend()
        overlay.recover_link_tunnels = AsyncMock()
        ovs.initialize = AsyncMock()

        with patch("agent.network.backends.ovs_backend.settings", MagicMock(
            enable_vxlan=False, enable_ovs=False, enable_docker=False, enable_ovs_plugin=False,
        )):
            info = await backend.initialize()

        assert info["ovs_initialized"] is False
        assert info["ovs_plugin_started"] is False
        overlay.recover_link_tunnels.assert_not_awaited()
        ovs.initialize.assert_not_awaited()


# ---------------------------------------------------------------------------
# shutdown
# ---------------------------------------------------------------------------

class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_no_runner(self):
        backend, _overlay, _ovs, plugin = _make_backend()
        # No runner set — should be a no-op
        await backend.shutdown()
        plugin.shutdown.assert_not_called()

    @pytest.mark.asyncio
    async def test_shutdown_with_runner(self):
        backend, _overlay, _ovs, plugin = _make_backend()
        runner = AsyncMock()
        backend._plugin_runner = runner
        plugin.shutdown = AsyncMock()
        await backend.shutdown()
        plugin.shutdown.assert_awaited_once()
        runner.cleanup.assert_awaited_once()
        assert backend._plugin_runner is None
