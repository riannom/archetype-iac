from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import agent.routers.interfaces as interfaces_mod
from agent.schemas import CarrierStateRequest, InterfaceProvisionRequest, PortRestoreRequest, SetMtuRequest


@pytest.fixture
def sync_to_thread(monkeypatch):
    async def _sync(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync)


@pytest.mark.asyncio
async def test_set_interface_carrier_libvirt_error_and_restore_exception(monkeypatch):
    monkeypatch.setattr(interfaces_mod.settings, "enable_ovs_plugin", True, raising=False)

    libvirt_provider = SimpleNamespace(
        get_node_kind_async=AsyncMock(return_value="iosv"),
        set_vm_link_state=AsyncMock(return_value=(False, "failed link")),
    )
    monkeypatch.setattr(interfaces_mod, "get_provider", lambda name: libvirt_provider if name == "libvirt" else None)

    out = await interfaces_mod.set_interface_carrier(
        "lab1", "r1", "eth1", CarrierStateRequest(state="off")
    )
    assert out.success is False
    assert out.error == "failed link"

    monkeypatch.setattr(
        interfaces_mod,
        "_get_docker_ovs_plugin",
        lambda: (_ for _ in ()).throw(RuntimeError("plugin down")),
    )
    monkeypatch.setattr(interfaces_mod, "get_provider", lambda name: None)
    out2 = await interfaces_mod.restore_interface(
        "lab1", "r1", "eth1", PortRestoreRequest(target_vlan=200)
    )
    assert out2.success is False
    assert "plugin down" in (out2.error or "")


@pytest.mark.asyncio
async def test_get_interface_vlan_ifindex_and_ovs_readback_paths(monkeypatch):
    monkeypatch.setattr(interfaces_mod.settings, "enable_ovs_plugin", True, raising=False)

    plugin = SimpleNamespace(get_endpoint_vlan=AsyncMock(return_value=None))
    docker_provider = SimpleNamespace(get_container_name=lambda lab_id, node: f"archetype-{lab_id}-{node}")
    libvirt_provider = SimpleNamespace(get_node_kind_async=AsyncMock(side_effect=RuntimeError("no kind")))

    def _get_provider(name: str):
        if name == "docker":
            return docker_provider
        if name == "libvirt":
            return libvirt_provider
        return None

    monkeypatch.setattr(interfaces_mod, "get_provider", _get_provider)
    monkeypatch.setattr(interfaces_mod, "_get_docker_ovs_plugin", lambda: plugin)
    monkeypatch.setattr(
        interfaces_mod,
        "_resolve_ovs_port_via_ifindex",
        AsyncMock(return_value=("vh-ifindex", 222)),
    )

    out_ifindex = await interfaces_mod.get_interface_vlan(
        "lab1", "r1", "eth1", read_from_ovs=True
    )
    assert out_ifindex.vlan_tag == 222

    monkeypatch.setattr(
        interfaces_mod,
        "_resolve_ovs_port_via_ifindex",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        interfaces_mod,
        "_resolve_ovs_port",
        AsyncMock(return_value=SimpleNamespace(port_name="vh-fallback", vlan_tag=300)),
    )

    proc = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"321\n", b"")))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))

    out_ovs = await interfaces_mod.get_interface_vlan(
        "lab1", "r1", "eth1", read_from_ovs=True
    )
    assert out_ovs.vlan_tag == 321


@pytest.mark.asyncio
async def test_list_interfaces_success_and_error(sync_to_thread, monkeypatch):
    def _run(args, **kwargs):
        if args == ["ip", "-j", "link", "show"]:
            data = [
                {"ifname": "lo"},
                {"ifname": "docker0"},
                {"ifname": "eth0", "operstate": "UP", "link_type": "ether", "address": "aa:bb:cc:dd:ee:ff"},
            ]
            return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")
        if args == ["ip", "-j", "addr", "show", "eth0"]:
            data = [{"addr_info": [{"family": "inet", "local": "192.0.2.10", "prefixlen": 24}]}]
            return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", _run)
    out = await interfaces_mod.list_interfaces()
    assert len(out["interfaces"]) == 1
    assert out["interfaces"][0]["name"] == "eth0"
    assert out["interfaces"][0]["ipv4_addresses"] == ["192.0.2.10/24"]

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("ip failed")))
    out_err = await interfaces_mod.list_interfaces()
    assert out_err["interfaces"] == []
    assert "ip failed" in out_err["error"]


@pytest.mark.asyncio
async def test_get_interface_details_success_and_error(sync_to_thread, monkeypatch):
    monkeypatch.setattr("agent.network.interface_config.get_default_route_interface", lambda: "eth0")
    monkeypatch.setattr("agent.network.interface_config.detect_network_manager", lambda: "networkd")
    monkeypatch.setattr("agent.network.interface_config.get_interface_mtu", lambda name: 9000 if name == "eth0" else None)
    monkeypatch.setattr("agent.network.interface_config.is_physical_interface", lambda name: name == "eth0")

    def _run(args, **kwargs):
        if args == ["ip", "-j", "link", "show"]:
            data = [
                {"ifname": "lo"},
                {"ifname": "eth0", "operstate": "UP", "address": "aa:bb:cc:dd:ee:ff", "mtu": 1500},
                {"ifname": "veth1", "operstate": "UP", "address": "00:00:00:00:00:01", "mtu": 1500},
            ]
            return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")
        if args[:4] == ["ip", "-j", "addr", "show"]:
            iface = args[4]
            if iface == "eth0":
                data = [{"addr_info": [{"family": "inet", "local": "198.51.100.5", "prefixlen": 25}]}]
                return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        raise AssertionError(f"unexpected args {args!r}")

    monkeypatch.setattr("subprocess.run", _run)
    out = await interfaces_mod.get_interface_details()
    assert out.default_route_interface == "eth0"
    assert out.network_manager == "networkd"
    assert any(i.name == "eth0" and i.mtu == 9000 for i in out.interfaces)

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    out_err = await interfaces_mod.get_interface_details()
    assert out_err.default_route_interface == "eth0"
    assert out_err.network_manager == "networkd"


@pytest.mark.asyncio
async def test_set_interface_mtu_persist_warning_paths(sync_to_thread, monkeypatch):
    monkeypatch.setattr("agent.network.interface_config.get_interface_mtu", lambda name: 1500 if name == "eth0" else 9000)
    monkeypatch.setattr("agent.network.interface_config.is_physical_interface", lambda _name: False)
    monkeypatch.setattr("agent.network.interface_config.detect_network_manager", lambda: "unknown")
    monkeypatch.setattr("agent.network.interface_config.set_mtu_runtime", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr("agent.network.interface_config.set_mtu_persistent", AsyncMock(return_value=(False, "persist fail")))

    out_unknown = await interfaces_mod.set_interface_mtu("eth0", SetMtuRequest(mtu=9000, persist=True))
    assert out_unknown.success is True
    assert "unknown network manager" in (out_unknown.error or "").lower()

    monkeypatch.setattr("agent.network.interface_config.detect_network_manager", lambda: "networkd")
    out_fail = await interfaces_mod.set_interface_mtu("eth0", SetMtuRequest(mtu=9000, persist=True))
    assert out_fail.success is True
    assert "persist fail" in (out_fail.error or "")


@pytest.mark.asyncio
async def test_list_bridges_success_fallback_and_errors(sync_to_thread, monkeypatch):
    def _run_primary(args, **kwargs):
        if args == ["bridge", "-j", "link", "show"]:
            data = [
                {"ifname": "eth0", "master": "sw-main"},
                {"ifname": "eth1", "master": "docker0"},
                {"ifname": "eth2", "master": "sw-main"},
            ]
            return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")
        raise AssertionError(args)

    monkeypatch.setattr("subprocess.run", _run_primary)
    out = await interfaces_mod.list_bridges()
    assert out["bridges"][0]["name"] == "sw-main"
    assert out["bridges"][0]["interfaces"] == ["eth0", "eth2"]

    calls = {"n": 0}

    def _run_fallback(args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FileNotFoundError("bridge not installed")
        if args == ["ip", "-j", "link", "show", "type", "bridge"]:
            data = [
                {"ifname": "sw-alt", "operstate": "UP"},
                {"ifname": "docker0", "operstate": "UP"},
            ]
            return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")
        raise AssertionError(args)

    monkeypatch.setattr("subprocess.run", _run_fallback)
    out_fallback = await interfaces_mod.list_bridges()
    assert out_fallback["bridges"] == [{"name": "sw-alt", "state": "UP", "interfaces": []}]

    def _run_fallback_error(args, **kwargs):
        raise FileNotFoundError("bridge missing")

    monkeypatch.setattr("subprocess.run", _run_fallback_error)
    out_err = await interfaces_mod.list_bridges()
    assert out_err["bridges"] == []

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fatal")))
    out_outer = await interfaces_mod.list_bridges()
    assert out_outer["bridges"] == []
    assert "fatal" in out_outer["error"]


class _ImmediateLoop:
    async def run_in_executor(self, _executor, fn):
        return fn()


@pytest.mark.asyncio
async def test_provision_interface_create_config_delete_and_errors(monkeypatch):
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: _ImmediateLoop())
    monkeypatch.setattr(interfaces_mod.settings, "ovs_bridge_name", "arch-ovs", raising=False)

    def _run_create(args, **kwargs):
        if args[:4] == ["ip", "link", "show", "ens5.100"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        if args[:4] == ["ip", "link", "add", "link"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ["cat", "/sys/class/net/ens5/mtu"]:
            return SimpleNamespace(returncode=0, stdout="1500\n", stderr="")
        if args[:4] == ["ip", "link", "set", "ens5"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:4] == ["ip", "link", "set", "ens5.100"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:4] == ["ovs-vsctl", "--may-exist", "add-port", "arch-ovs"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="ovs fail")
        if args[:2] == ["cat", "/sys/class/net/ens5.100/mtu"]:
            return SimpleNamespace(returncode=0, stdout="9000\n", stderr="")
        if args[:4] == ["ip", "-4", "addr", "show"]:
            stdout = "    inet 203.0.113.10/24 brd 203.0.113.255 scope global ens5.100\n"
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", _run_create)
    out_create = await interfaces_mod.provision_interface(
        InterfaceProvisionRequest(
            action="create_subinterface",
            parent_interface="ens5",
            vlan_id=100,
            attach_to_ovs=True,
            ovs_vlan_tag=2100,
        )
    )
    assert out_create.success is True
    assert out_create.interface_name == "ens5.100"
    assert out_create.ip_address == "203.0.113.10/24"

    def _run_create_ip_fail(args, **kwargs):
        if args[:4] == ["ip", "link", "show", "ens5.101"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:4] == ["ip", "link", "set", "ens5.101"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:4] == ["ip", "addr", "flush", "dev"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:4] == ["ip", "addr", "add", "10.0.0.1/24"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="bad ip")
        if args[:2] == ["cat", "/sys/class/net/ens5/mtu"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", _run_create_ip_fail)
    out_ip_fail = await interfaces_mod.provision_interface(
        InterfaceProvisionRequest(
            action="create_subinterface",
            parent_interface="ens5",
            name="ens5.101",
            vlan_id=101,
            ip_cidr="10.0.0.1/24",
        )
    )
    assert out_ip_fail.success is False
    assert "Failed to set IP" in (out_ip_fail.error or "")

    def _run_config_missing(args, **kwargs):
        if args[:4] == ["ip", "link", "show", "ens5.200"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", _run_config_missing)
    out_cfg_missing = await interfaces_mod.provision_interface(
        InterfaceProvisionRequest(action="configure", name="ens5.200")
    )
    assert out_cfg_missing.success is False

    def _run_delete_fail(args, **kwargs):
        if args[:3] == ["ovs-vsctl", "port-to-br", "ens5.300"]:
            return SimpleNamespace(returncode=0, stdout="arch-ovs\n", stderr="")
        if args[:4] == ["ovs-vsctl", "--if-exists", "del-port", "arch-ovs"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:4] == ["ip", "link", "delete", "ens5.300"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="no such dev")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", _run_delete_fail)
    out_delete_fail = await interfaces_mod.provision_interface(
        InterfaceProvisionRequest(action="delete", name="ens5.300")
    )
    assert out_delete_fail.success is False
    assert "Failed to delete interface" in (out_delete_fail.error or "")

    out_unknown = await interfaces_mod.provision_interface(
        SimpleNamespace(action="mystery", name=None, parent_interface=None, vlan_id=None)
    )
    assert out_unknown.success is False
    assert "Unknown action" in (out_unknown.error or "")

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("exec fail")))
    out_outer = await interfaces_mod.provision_interface(
        InterfaceProvisionRequest(action="configure", name="ens5.400")
    )
    assert out_outer.success is False
    assert "exec fail" in (out_outer.error or "")
