from __future__ import annotations

import pytest

from agent.network.overlay import LinkTunnel, OverlayManager


@pytest.mark.asyncio
async def test_create_link_tunnel_rebinds_recovered_entry(monkeypatch) -> None:
    manager = OverlayManager()

    async def _noop() -> None:
        return None

    async def _ovs_port_exists(name: str) -> bool:
        return True

    calls = {"set_vlan": 0, "deleted": 0}

    async def _ovs_vsctl(*args: str) -> tuple[int, str, str]:
        if args and args[0] == "set":
            calls["set_vlan"] += 1
        return 0, "", ""

    async def _delete_vxlan_device(*args: str, **kwargs: str) -> None:
        calls["deleted"] += 1

    monkeypatch.setattr(manager, "_ensure_ovs_bridge", _noop)
    monkeypatch.setattr(manager, "_ovs_port_exists", _ovs_port_exists)
    monkeypatch.setattr(manager, "_ovs_vsctl", _ovs_vsctl)
    monkeypatch.setattr(manager, "_delete_vxlan_device", _delete_vxlan_device)

    lab_id = "lab123"
    link_id = "r1:eth1-r2:eth1"
    interface_name = manager._link_tunnel_interface_name(lab_id, link_id)

    recovered = LinkTunnel(
        link_id=interface_name,  # placeholder key from recovery
        vni=5000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=100,
        interface_name=interface_name,
        lab_id="recovered",
        tenant_mtu=1400,
    )
    manager._link_tunnels[interface_name] = recovered

    result = await manager.create_link_tunnel(
        lab_id=lab_id,
        link_id=link_id,
        vni=5000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=200,
        tenant_mtu=1400,
    )

    assert result is recovered
    assert result.link_id == link_id
    assert result.lab_id == lab_id
    assert link_id in manager._link_tunnels
    assert interface_name not in manager._link_tunnels
    assert calls["deleted"] == 0
    assert calls["set_vlan"] >= 1


@pytest.mark.asyncio
async def test_create_link_tunnel_adopts_existing_port(monkeypatch) -> None:
    manager = OverlayManager()

    async def _noop() -> None:
        return None

    async def _ovs_port_exists(name: str) -> bool:
        return True

    async def _read_vxlan_link_info(name: str) -> tuple[int, str, str]:
        return 6000, "10.0.0.2", "10.0.0.1"

    calls = {"deleted": 0}

    async def _delete_vxlan_device(*args: str, **kwargs: str) -> None:
        calls["deleted"] += 1

    monkeypatch.setattr(manager, "_ensure_ovs_bridge", _noop)
    monkeypatch.setattr(manager, "_ovs_port_exists", _ovs_port_exists)
    monkeypatch.setattr(manager, "_read_vxlan_link_info", _read_vxlan_link_info)
    monkeypatch.setattr(manager, "_delete_vxlan_device", _delete_vxlan_device)

    lab_id = "lab999"
    link_id = "r2:eth2-r3:eth2"

    result = await manager.create_link_tunnel(
        lab_id=lab_id,
        link_id=link_id,
        vni=6000,
        local_ip="10.0.0.1",
        remote_ip="10.0.0.2",
        local_vlan=300,
        tenant_mtu=1400,
    )

    assert result.link_id == link_id
    assert result.vni == 6000
    assert calls["deleted"] == 0
