from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.network.overlay import (
    LinkTunnel,
    OverlayBridge,
    OverlayManager,
    Vtep,
    VxlanTunnel,
)
import agent.network.overlay as overlay_mod


def _make_overlay(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agent.network.overlay.settings",
        SimpleNamespace(
            ovs_bridge_name="arch-ovs",
            overlay_mtu=1450,
            workspace_path=str(tmp_path),
            vxlan_vni_base=100000,
            vxlan_vni_max=200000,
            overlay_clamp_host_mtu=True,
            overlay_preserve_container_mtu=False,
        ),
    )
    mgr = OverlayManager.__new__(OverlayManager)
    mgr._docker = None
    mgr._tunnels = {}
    mgr._bridges = {}
    mgr._vteps = {}
    mgr._link_tunnels = {}
    mgr._ovs_initialized = False
    mgr._bridge_name = "arch-ovs"
    mgr._mtu_cache = {}
    mgr._run_cmd = AsyncMock(return_value=(0, "", ""))
    mgr._ovs_vsctl = AsyncMock(return_value=(0, "", ""))
    mgr._ip_link_exists = AsyncMock(return_value=False)
    mgr._create_vxlan_device = AsyncMock()
    mgr._delete_vxlan_device = AsyncMock()
    return mgr


@pytest.mark.asyncio
async def test_overlay_docker_property_lazy_init(monkeypatch, tmp_path):
    mgr = _make_overlay(monkeypatch, tmp_path)
    mgr._docker = None
    sentinel = object()
    monkeypatch.setattr(overlay_mod.docker, "from_env", lambda: sentinel)
    assert mgr.docker is sentinel
    assert mgr.docker is sentinel


@pytest.mark.asyncio
async def test_ensure_ovs_bridge_failure_and_mode_update_paths(monkeypatch, tmp_path):
    mgr = _make_overlay(monkeypatch, tmp_path)
    mgr._ovs_vsctl = AsyncMock(return_value=(1, "", "ovs missing"))
    with pytest.raises(RuntimeError, match="OVS not available"):
        await mgr._ensure_ovs_bridge()

    mgr = _make_overlay(monkeypatch, tmp_path)
    mgr._ovs_vsctl = AsyncMock(
        side_effect=[
            (0, "", ""),         # --version
            (1, "", ""),         # br-exists
            (1, "", "create fail"),  # add-br
        ]
    )
    with pytest.raises(RuntimeError, match="Failed to create OVS bridge"):
        await mgr._ensure_ovs_bridge()

    mgr = _make_overlay(monkeypatch, tmp_path)
    mgr._ovs_vsctl = AsyncMock(
        side_effect=[
            (0, "", ""),               # --version
            (0, "", ""),               # br-exists
            (0, '"secure"\n', ""),      # get fail_mode
            (0, "", ""),               # set-fail-mode
        ]
    )
    await mgr._ensure_ovs_bridge()
    assert mgr._ovs_initialized is True
    mgr._ovs_vsctl.assert_any_await("set-fail-mode", "arch-ovs", "standalone")


@pytest.mark.asyncio
async def test_ovs_port_exists_error_path(monkeypatch, tmp_path):
    mgr = _make_overlay(monkeypatch, tmp_path)
    mgr._ovs_vsctl = AsyncMock(return_value=(1, "", "err"))
    assert await mgr._ovs_port_exists("vtep-a") is False


@pytest.mark.asyncio
async def test_ensure_vtep_existing_and_recreate_paths(monkeypatch, tmp_path):
    mgr = _make_overlay(monkeypatch, tmp_path)
    existing = Vtep(
        interface_name="vtep-10-0-0-2",
        vni=12345,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
    )
    mgr._vteps["10.0.0.2"] = existing
    out_existing = await mgr.ensure_vtep("10.0.0.1", "10.0.0.2")
    assert out_existing is existing

    mgr = _make_overlay(monkeypatch, tmp_path)
    mgr._ovs_port_exists = AsyncMock(return_value=True)
    mgr._ip_link_exists = AsyncMock(return_value=True)
    out = await mgr.ensure_vtep("10.0.0.1", "10.0.0.2", remote_host_id="host-b")
    assert out.remote_host_id == "host-b"
    assert out.remote_ip == "10.0.0.2"
    mgr._delete_vxlan_device.assert_awaited_once()
    mgr._run_cmd.assert_any_await(["ip", "link", "delete", out.interface_name])


@pytest.mark.asyncio
async def test_create_tunnel_core_paths(monkeypatch, tmp_path):
    mgr = _make_overlay(monkeypatch, tmp_path)
    existing = VxlanTunnel(
        vni=120001,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        interface_name="vxlan120001",
        lab_id="lab1",
        link_id="r1:eth1-r2:eth1",
        vlan_tag=2050,
    )
    mgr._tunnels[existing.key] = existing
    assert await mgr.create_tunnel("lab1", "r1:eth1-r2:eth1", "10.0.0.1", "10.0.0.2", 120001) is existing

    with pytest.raises(ValueError, match="VNI must be provided"):
        await mgr.create_tunnel("lab1", "r1:eth2-r2:eth2", "10.0.0.1", "10.0.0.2", None)

    mgr = _make_overlay(monkeypatch, tmp_path)
    mgr._ovs_port_exists = AsyncMock(return_value=True)
    mgr._ip_link_exists = AsyncMock(return_value=True)
    tunnel = await mgr.create_tunnel("lab1", "r1:eth3-r2:eth3", "10.0.0.1", "10.0.0.2", 120003)
    assert tunnel.key in mgr._tunnels
    mgr._delete_vxlan_device.assert_awaited_once()
    mgr._run_cmd.assert_any_await(["ip", "link", "delete", "vxlan120003"])


@pytest.mark.asyncio
async def test_delete_tunnel_and_bridge_error_paths(monkeypatch, tmp_path):
    mgr = _make_overlay(monkeypatch, tmp_path)
    tunnel = VxlanTunnel(
        vni=120004,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        interface_name="vxlan120004",
        lab_id="lab1",
        link_id="r1:eth4-r2:eth4",
        vlan_tag=2054,
    )
    mgr._tunnels[tunnel.key] = tunnel
    assert await mgr.delete_tunnel(tunnel) is True
    assert tunnel.key not in mgr._tunnels

    mgr._delete_vxlan_device = AsyncMock(side_effect=RuntimeError("no port"))
    assert await mgr.delete_tunnel(tunnel) is False

    bridge = OverlayBridge(
        name="arch-ovs",
        vni=120005,
        vlan_tag=2055,
        lab_id="lab1",
        link_id="r1:eth5-r2:eth5",
        veth_pairs=[("vh1", "eth1"), ("vh2", "eth2")],
    )
    mgr._bridges[bridge.key] = bridge
    assert await mgr.delete_bridge(bridge) is True
    assert bridge.key not in mgr._bridges

    mgr._run_cmd = AsyncMock(side_effect=RuntimeError("ip failed"))
    mgr._bridges[bridge.key] = bridge
    assert await mgr.delete_bridge(bridge) is False


@pytest.mark.asyncio
async def test_attach_container_plugin_and_fallback_paths(monkeypatch, tmp_path):
    mgr = _make_overlay(monkeypatch, tmp_path)
    mgr._ensure_ovs_bridge = AsyncMock()
    bridge = OverlayBridge(name="arch-ovs", vni=120006, vlan_tag=2060, lab_id="lab1", link_id="link-a")

    class _Plugin:
        async def get_endpoint_host_veth(self, lab_id, full_name, ifname):
            return "vh-plugin"

    monkeypatch.setattr("agent.network.docker_plugin.get_docker_ovs_plugin", lambda: _Plugin())
    assert await mgr.attach_container(bridge, "r1", "eth1") is True
    mgr._ovs_vsctl.assert_any_await("set", "port", "vh-plugin", "tag=2060")

    mgr = _make_overlay(monkeypatch, tmp_path)
    bridge = OverlayBridge(name="arch-ovs", vni=120007, vlan_tag=2061, lab_id="lab1", link_id="link-b")

    class _PluginNone:
        async def get_endpoint_host_veth(self, lab_id, full_name, ifname):
            return None

    monkeypatch.setattr("agent.network.docker_plugin.get_docker_ovs_plugin", lambda: _PluginNone())
    mgr._docker = SimpleNamespace(
        containers=SimpleNamespace(
            get=lambda _name: SimpleNamespace(status="exited", attrs={"State": {"Pid": 55}})
        )
    )

    async def _to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    assert await mgr.attach_container(bridge, "r2", "eth1") is False


@pytest.mark.asyncio
async def test_attach_container_notfound_and_veth_create_failure(monkeypatch, tmp_path):
    mgr = _make_overlay(monkeypatch, tmp_path)
    bridge = OverlayBridge(name="arch-ovs", vni=120008, vlan_tag=2062, lab_id="lab1", link_id="link-c")

    class _PluginNone:
        async def get_endpoint_host_veth(self, lab_id, full_name, ifname):
            return None

    monkeypatch.setattr("agent.network.docker_plugin.get_docker_ovs_plugin", lambda: _PluginNone())
    mgr._docker = SimpleNamespace(
        containers=SimpleNamespace(get=lambda _name: (_ for _ in ()).throw(overlay_mod.NotFound("missing")))
    )

    async def _to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    assert await mgr.attach_container(bridge, "r3", "eth1") is False

    mgr = _make_overlay(monkeypatch, tmp_path)
    monkeypatch.setattr("agent.network.docker_plugin.get_docker_ovs_plugin", lambda: _PluginNone())
    mgr._docker = SimpleNamespace(
        containers=SimpleNamespace(
            get=lambda _name: SimpleNamespace(status="running", attrs={"State": {"Pid": 77}})
        )
    )

    async def _run_cmd(cmd):
        if cmd[:5] == ["ip", "link", "add", cmd[3], "type"]:  # pragma: no cover
            return 1, "", "create failed"
        if cmd[:3] == ["ip", "link", "add"]:
            return 1, "", "create failed"
        return 0, "", ""

    mgr._run_cmd = AsyncMock(side_effect=_run_cmd)
    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    assert await mgr.attach_container(bridge, "r4", "eth1") is False


@pytest.mark.asyncio
async def test_attach_overlay_interface_plugin_and_fallback_paths(monkeypatch, tmp_path):
    mgr = _make_overlay(monkeypatch, tmp_path)
    mgr._ensure_ovs_bridge = AsyncMock()
    vtep = Vtep(interface_name="vtep-10-0-0-2", vni=12345, local_ip="10.0.0.1", remote_ip="10.0.0.2")
    mgr._vteps["10.0.0.2"] = vtep

    class _Plugin:
        async def get_endpoint_host_veth(self, lab_id, full_name, ifname):
            return "vh-ovl"

        async def set_endpoint_vlan(self, lab_id, full_name, ifname, vlan):
            return None

    monkeypatch.setattr("agent.network.docker_plugin.get_docker_ovs_plugin", lambda: _Plugin())
    ok = await mgr.attach_overlay_interface(
        lab_id="lab1",
        container_name="r1",
        interface_name="eth1",
        vlan_tag=2201,
        link_id="link-1",
        remote_ip="10.0.0.2",
    )
    assert ok is True
    assert "link-1" in mgr._vteps["10.0.0.2"].links

    mgr = _make_overlay(monkeypatch, tmp_path)

    class _PluginNone:
        async def get_endpoint_host_veth(self, lab_id, full_name, ifname):
            return None

    monkeypatch.setattr("agent.network.docker_plugin.get_docker_ovs_plugin", lambda: _PluginNone())
    mgr._docker = SimpleNamespace(
        containers=SimpleNamespace(
            get=lambda _name: SimpleNamespace(status="created", attrs={"State": {"Pid": 0}})
        )
    )

    async def _to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    assert await mgr.attach_overlay_interface("lab1", "r2", "eth1", 2202) is False


@pytest.mark.asyncio
async def test_detach_overlay_interface_and_delete_vtep_paths(monkeypatch, tmp_path):
    mgr = _make_overlay(monkeypatch, tmp_path)
    missing = await mgr.detach_overlay_interface("link-x", "10.0.0.9")
    assert missing["success"] is False
    assert "not found" in (missing["error"] or "").lower()

    vtep = Vtep(
        interface_name="vtep-10-0-0-2",
        vni=12345,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        links={"link-a", "link-b"},
    )
    mgr._vteps["10.0.0.2"] = vtep
    kept = await mgr.detach_overlay_interface("link-a", "10.0.0.2")
    assert kept["success"] is True
    assert kept["vtep_deleted"] is False
    assert kept["remaining_links"] == 1

    mgr._delete_vxlan_device = AsyncMock(return_value=None)
    deleted = await mgr.detach_overlay_interface("link-b", "10.0.0.2")
    assert deleted["success"] is True
    assert deleted["vtep_deleted"] is True

    assert await mgr.delete_vtep("10.0.0.99") is False


@pytest.mark.asyncio
async def test_delete_vtep_warning_and_exception_paths(monkeypatch, tmp_path):
    mgr = _make_overlay(monkeypatch, tmp_path)
    vtep = Vtep(
        interface_name="vtep-10-0-0-3",
        vni=12346,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.3",
        links={"l1"},
    )
    mgr._vteps["10.0.0.3"] = vtep
    mgr._delete_vxlan_device = AsyncMock(return_value=None)
    assert await mgr.delete_vtep("10.0.0.3") is True
    assert "10.0.0.3" not in mgr._vteps

    vtep2 = Vtep(
        interface_name="vtep-10-0-0-4",
        vni=12347,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.4",
    )
    mgr._vteps["10.0.0.4"] = vtep2
    mgr._delete_vxlan_device = AsyncMock(side_effect=RuntimeError("failed"))
    assert await mgr.delete_vtep("10.0.0.4") is False


@pytest.mark.asyncio
async def test_create_link_tunnel_existing_update_and_recreate(monkeypatch, tmp_path):
    mgr = _make_overlay(monkeypatch, tmp_path)
    existing = LinkTunnel(
        link_id="link-1",
        vni=130001,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=2100,
        interface_name="vxlan-aaaa1111",
        lab_id="lab1",
    )
    mgr._link_tunnels["link-1"] = existing
    mgr._ovs_port_exists = AsyncMock(return_value=True)
    updated = await mgr.create_link_tunnel("lab1", "link-1", 130001, "10.0.0.1", "10.0.0.2", 2200)
    assert updated is existing
    assert updated.local_vlan == 2200
    mgr._ovs_vsctl.assert_any_await("set", "port", "vxlan-aaaa1111", "tag=2200")

    mgr = _make_overlay(monkeypatch, tmp_path)
    stale = LinkTunnel(
        link_id="link-2",
        vni=130002,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=2101,
        interface_name="vxlan-bbbb2222",
        lab_id="lab1",
    )
    mgr._link_tunnels["link-2"] = stale
    mgr._ovs_port_exists = AsyncMock(side_effect=[False, False])
    mgr._ip_link_exists = AsyncMock(return_value=False)
    created = await mgr.create_link_tunnel("lab1", "link-2", 130002, "10.0.0.1", "10.0.0.2", 2101)
    assert created.link_id == "link-2"
    assert "link-2" in mgr._link_tunnels


@pytest.mark.asyncio
async def test_delete_link_tunnel_untracked_and_notfound(monkeypatch, tmp_path):
    mgr = _make_overlay(monkeypatch, tmp_path)
    mgr._ovs_port_exists = AsyncMock(return_value=True)
    mgr._delete_vxlan_device = AsyncMock(return_value=None)
    assert await mgr.delete_link_tunnel("r1:eth1-r2:eth1", lab_id="lab1") is True

    mgr = _make_overlay(monkeypatch, tmp_path)
    mgr._ovs_port_exists = AsyncMock(return_value=False)
    assert await mgr.delete_link_tunnel("missing-link", lab_id="lab1") is True

    tunnel = LinkTunnel(
        link_id="tracked",
        vni=130003,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=2103,
        interface_name="vxlan-cccc3333",
        lab_id="lab1",
    )
    mgr._link_tunnels["tracked"] = tunnel
    mgr._delete_vxlan_device = AsyncMock(side_effect=RuntimeError("cannot delete"))
    assert await mgr.delete_link_tunnel("tracked") is False


@pytest.mark.asyncio
async def test_cleanup_lab_counts_and_errors(monkeypatch, tmp_path):
    mgr = _make_overlay(monkeypatch, tmp_path)
    tunnel = VxlanTunnel(
        vni=130004,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        interface_name="vxlan130004",
        lab_id="lab1",
        link_id="l-a",
        vlan_tag=2104,
    )
    bridge = OverlayBridge(name="arch-ovs", vni=130004, vlan_tag=2104, lab_id="lab1", link_id="l-a")
    link_tunnel = LinkTunnel(
        link_id="l-a",
        vni=130004,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=2104,
        interface_name="vxlan-dddd4444",
        lab_id="lab1",
    )
    mgr._tunnels[tunnel.key] = tunnel
    mgr._bridges[bridge.key] = bridge
    mgr._link_tunnels[link_tunnel.link_id] = link_tunnel
    mgr.delete_bridge = AsyncMock(return_value=True)
    mgr.delete_tunnel = AsyncMock(side_effect=RuntimeError("boom tunnel"))
    mgr.delete_link_tunnel = AsyncMock(return_value=True)

    result = await mgr.cleanup_lab("lab1")
    assert result["bridges_deleted"] == 1
    assert result["link_tunnels_deleted"] == 1
    assert any("Tunnel" in e for e in result["errors"])


def test_get_tunnel_status_covers_all_models(monkeypatch, tmp_path):
    mgr = _make_overlay(monkeypatch, tmp_path)
    mgr._mtu_cache = {"10.0.0.2": 1450}
    mgr._vteps["10.0.0.2"] = Vtep(
        interface_name="vtep-10-0-0-2",
        vni=130005,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        remote_host_id="host-b",
        tenant_mtu=1400,
        links={"link-1"},
    )
    t = VxlanTunnel(
        vni=130006,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        interface_name="vxlan130006",
        lab_id="lab1",
        link_id="link-2",
        vlan_tag=2206,
        tenant_mtu=1400,
    )
    b = OverlayBridge(
        name="arch-ovs",
        vni=130006,
        vlan_tag=2206,
        lab_id="lab1",
        link_id="link-2",
        tenant_mtu=1400,
        veth_pairs=[("vh1", "eth1")],
    )
    lt = LinkTunnel(
        link_id="link-3",
        vni=130007,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=2207,
        interface_name="vxlan-eeee5555",
        lab_id="lab1",
        tenant_mtu=1400,
    )
    mgr._tunnels[t.key] = t
    mgr._bridges[b.key] = b
    mgr._link_tunnels[lt.link_id] = lt

    status = mgr.get_tunnel_status()
    assert status["ovs_bridge"] == "arch-ovs"
    assert status["mtu_cache"]["10.0.0.2"] == 1450
    assert status["vteps"][0]["remote_host_id"] == "host-b"
    assert status["tunnels"][0]["vlan_tag"] == 2206
    assert status["bridges"][0]["veth_pairs"] == [("vh1", "eth1")]
    assert status["link_tunnels"][0]["local_vlan"] == 2207
