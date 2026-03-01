"""Additional branch coverage for agent.routers.interfaces."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.routers import interfaces as ifaces
from agent.schemas import InterfaceProvisionRequest, SetMtuRequest


class _Result:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeLoop:
    async def run_in_executor(self, _executor, fn):
        return fn()


@pytest.mark.asyncio
async def test_list_interfaces_and_details_paths(monkeypatch):
    async def _run_direct(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(ifaces.asyncio, "to_thread", _run_direct)

    link_payload = json.dumps(
        [
            {"ifname": "lo", "operstate": "UNKNOWN", "link_type": "loopback", "address": "00:00:00:00:00:00"},
            {"ifname": "docker0", "operstate": "UP", "link_type": "bridge", "address": "02:42:ac:11:00:01"},
            {"ifname": "ens5", "operstate": "UP", "link_type": "ether", "address": "aa:bb:cc:dd:ee:ff", "mtu": 1500},
            {"ifname": "ens5.100", "operstate": "UP", "link_type": "vlan", "address": "aa:bb:cc:dd:ee:ff", "mtu": 1500},
        ]
    )
    addr_payload = json.dumps(
        [
            {
                "ifname": "ens5",
                "addr_info": [
                    {"family": "inet", "local": "10.0.0.5", "prefixlen": 24},
                    {"family": "inet6", "local": "fe80::1", "prefixlen": 64},
                ],
            }
        ]
    )

    def _subprocess_run(cmd, **_kwargs):
        if cmd[:3] == ["ip", "-j", "link"]:
            return _Result(returncode=0, stdout=link_payload)
        if cmd[:4] == ["ip", "-j", "addr", "show"]:
            return _Result(returncode=0, stdout=addr_payload)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("subprocess.run", _subprocess_run)

    listed = await ifaces.list_interfaces()
    names = [entry["name"] for entry in listed["interfaces"]]
    assert "ens5" in names
    assert "docker0" not in names

    monkeypatch.setattr("agent.network.interface_config.get_default_route_interface", lambda: "ens5")
    monkeypatch.setattr("agent.network.interface_config.detect_network_manager", lambda: "systemd-networkd")
    monkeypatch.setattr("agent.network.interface_config.get_interface_mtu", lambda _name: 9000)
    monkeypatch.setattr("agent.network.interface_config.is_physical_interface", lambda name: not name.endswith(".100"))

    details = await ifaces.get_interface_details()
    assert details.default_route_interface == "ens5"
    assert details.network_manager == "systemd-networkd"
    assert any(item.name == "ens5" and item.is_default_route for item in details.interfaces)

    def _subprocess_error(_cmd, **_kwargs):
        raise RuntimeError("ip command failed")

    monkeypatch.setattr("subprocess.run", _subprocess_error)
    errored = await ifaces.list_interfaces()
    assert errored["interfaces"] == []
    assert "ip command failed" in errored["error"]


@pytest.mark.asyncio
async def test_set_interface_mtu_paths(monkeypatch):
    async def _run_direct(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(ifaces.asyncio, "to_thread", _run_direct)

    monkeypatch.setattr("agent.network.interface_config.get_interface_mtu", lambda _name: None)
    monkeypatch.setattr("agent.network.interface_config.is_physical_interface", lambda _name: True)
    monkeypatch.setattr("agent.network.interface_config.detect_network_manager", lambda: "unknown")
    missing = await ifaces.set_interface_mtu("eth99", SetMtuRequest(mtu=9000, persist=False))
    assert missing.success is False
    assert "not found" in (missing.error or "")

    monkeypatch.setattr("agent.network.interface_config.get_interface_mtu", lambda _name: 1500)
    monkeypatch.setattr("agent.network.interface_config.is_physical_interface", lambda _name: False)
    monkeypatch.setattr("agent.network.interface_config.detect_network_manager", lambda: "systemd-networkd")
    monkeypatch.setattr("agent.network.interface_config.set_mtu_runtime", AsyncMock(return_value=(False, "denied")))
    runtime_fail = await ifaces.set_interface_mtu("eth0", SetMtuRequest(mtu=9000, persist=False))
    assert runtime_fail.success is False
    assert runtime_fail.error == "denied"

    monkeypatch.setattr("agent.network.interface_config.set_mtu_runtime", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr("agent.network.interface_config.detect_network_manager", lambda: "unknown")
    unknown_mgr = await ifaces.set_interface_mtu("eth0", SetMtuRequest(mtu=9000, persist=True))
    assert unknown_mgr.success is True
    assert unknown_mgr.persisted is False
    assert "unknown network manager" in (unknown_mgr.error or "")

    monkeypatch.setattr("agent.network.interface_config.detect_network_manager", lambda: "netplan")
    monkeypatch.setattr(
        "agent.network.interface_config.set_mtu_persistent",
        AsyncMock(return_value=(True, None)),
    )
    monkeypatch.setattr("agent.network.interface_config.get_interface_mtu", lambda _name: 9200)
    persisted = await ifaces.set_interface_mtu("eth0", SetMtuRequest(mtu=9200, persist=True))
    assert persisted.success is True
    assert persisted.persisted is True
    assert persisted.new_mtu == 9200


@pytest.mark.asyncio
async def test_list_bridges_primary_and_fallback_paths(monkeypatch):
    async def _run_direct(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(ifaces.asyncio, "to_thread", _run_direct)

    bridge_payload = json.dumps(
        [
            {"ifname": "ens5", "master": "uplink"},
            {"ifname": "veth1", "master": "docker0"},
            {"ifname": "ens6", "master": "uplink"},
        ]
    )

    def _bridge_run(cmd, **_kwargs):
        if cmd[:3] == ["bridge", "-j", "link"]:
            return _Result(returncode=0, stdout=bridge_payload)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("subprocess.run", _bridge_run)
    bridges = await ifaces.list_bridges()
    assert bridges["bridges"] == [{"name": "uplink", "interfaces": ["ens5", "ens6"]}]

    ip_bridge_payload = json.dumps(
        [
            {"ifname": "uplink0", "operstate": "UP"},
            {"ifname": "docker0", "operstate": "UP"},
        ]
    )

    def _fallback_run(cmd, **_kwargs):
        if cmd[:3] == ["bridge", "-j", "link"]:
            raise FileNotFoundError("bridge command missing")
        if cmd[:5] == ["ip", "-j", "link", "show", "type"]:
            return _Result(returncode=0, stdout=ip_bridge_payload)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("subprocess.run", _fallback_run)
    fallback = await ifaces.list_bridges()
    assert fallback["bridges"] == [{"name": "uplink0", "state": "UP", "interfaces": []}]

    def _bridge_error(cmd, **_kwargs):  # noqa: ARG001
        raise RuntimeError("bridge listing failed")

    monkeypatch.setattr("subprocess.run", _bridge_error)
    errored = await ifaces.list_bridges()
    assert errored["bridges"] == []
    assert "failed" in (errored["error"] or "")


@pytest.mark.asyncio
async def test_provision_interface_create_configure_delete_paths(monkeypatch):
    monkeypatch.setattr(ifaces.asyncio, "get_event_loop", lambda: _FakeLoop())
    monkeypatch.setattr(ifaces.settings, "ovs_bridge_name", "arch-ovs")

    # create_subinterface happy path with OVS attach
    create_calls: list[list[str]] = []

    def _run_create(cmd, **_kwargs):
        create_calls.append(cmd)
        if cmd[:4] == ["ip", "link", "show", "ens5.100"]:
            return _Result(returncode=1)
        if cmd[:2] == ["cat", "/sys/class/net/ens5/mtu"]:
            return _Result(returncode=0, stdout="1500\n")
        if cmd[:2] == ["cat", "/sys/class/net/ens5.100/mtu"]:
            return _Result(returncode=0, stdout="9000\n")
        return _Result(returncode=0)

    monkeypatch.setattr("subprocess.run", _run_create)

    created = await ifaces.provision_interface(
        InterfaceProvisionRequest(
            action="create_subinterface",
            parent_interface="ens5",
            vlan_id=100,
            mtu=9000,
            ip_cidr="10.10.10.2/24",
            attach_to_ovs=True,
            ovs_vlan_tag=222,
        )
    )
    assert created.success is True
    assert created.interface_name == "ens5.100"
    assert created.mtu == 9000

    # create_subinterface fails on ip link add
    def _run_create_fail(cmd, **_kwargs):
        if cmd[:4] == ["ip", "link", "show", "ens5.101"]:
            return _Result(returncode=1)
        if cmd[:4] == ["ip", "link", "add", "link"]:
            return _Result(returncode=2, stderr="permission denied")
        return _Result(returncode=0)

    monkeypatch.setattr("subprocess.run", _run_create_fail)
    create_fail = await ifaces.provision_interface(
        InterfaceProvisionRequest(
            action="create_subinterface",
            parent_interface="ens5",
            vlan_id=101,
            name="ens5.101",
        )
    )
    assert create_fail.success is False
    assert "failed to create" in (create_fail.error or "").lower()

    # configure missing interface
    monkeypatch.setattr(
        "subprocess.run",
        lambda cmd, **_kwargs: _Result(returncode=1) if cmd[:4] == ["ip", "link", "show", "ens6.200"] else _Result(returncode=0),
    )
    missing_cfg = await ifaces.provision_interface(
        InterfaceProvisionRequest(action="configure", name="ens6.200", mtu=9100)
    )
    assert missing_cfg.success is False
    assert "does not exist" in (missing_cfg.error or "")

    # configure happy path
    def _run_configure(cmd, **_kwargs):
        if cmd[:4] == ["ip", "link", "show", "ens6.200"]:
            return _Result(returncode=0)
        if cmd[:2] == ["cat", "/sys/class/net/ens6.200/mtu"]:
            return _Result(returncode=0, stdout="9100\n")
        return _Result(returncode=0)

    monkeypatch.setattr("subprocess.run", _run_configure)
    configured = await ifaces.provision_interface(
        InterfaceProvisionRequest(action="configure", name="ens6.200", mtu=9100, ip_cidr="172.16.0.2/24")
    )
    assert configured.success is True
    assert configured.mtu == 9100

    # delete success path with OVS detachment
    def _run_delete(cmd, **_kwargs):
        if cmd[:3] == ["ovs-vsctl", "port-to-br", "ens6.200"]:
            return _Result(returncode=0, stdout="arch-ovs\n")
        if cmd[:4] == ["ip", "link", "delete", "ens6.200"]:
            return _Result(returncode=0)
        return _Result(returncode=0)

    monkeypatch.setattr("subprocess.run", _run_delete)
    deleted = await ifaces.provision_interface(
        InterfaceProvisionRequest(action="delete", name="ens6.200")
    )
    assert deleted.success is True

    # delete failure path
    def _run_delete_fail(cmd, **_kwargs):
        if cmd[:4] == ["ip", "link", "delete", "ens6.201"]:
            return _Result(returncode=1, stderr="busy")
        return _Result(returncode=1)

    monkeypatch.setattr("subprocess.run", _run_delete_fail)
    delete_fail = await ifaces.provision_interface(
        InterfaceProvisionRequest(action="delete", name="ens6.201")
    )
    assert delete_fail.success is False
    assert "failed to delete" in (delete_fail.error or "").lower()

    # Unknown action branch + exception wrapper branch
    unknown = await ifaces.provision_interface(SimpleNamespace(action="noop", name=None))
    assert unknown.success is False
    assert "unknown action" in (unknown.error or "").lower()

    def _run_raises(_cmd, **_kwargs):
        raise RuntimeError("subprocess down")

    monkeypatch.setattr("subprocess.run", _run_raises)
    exploded = await ifaces.provision_interface(
        InterfaceProvisionRequest(action="delete", name="ens6.300")
    )
    assert exploded.success is False
    assert "subprocess down" in (exploded.error or "")
