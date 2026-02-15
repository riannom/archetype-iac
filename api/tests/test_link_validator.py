from __future__ import annotations

import pytest

from app import models
import app.services.link_validator as link_validator


@pytest.mark.asyncio
async def test_verify_same_host_link_updates_vlan(test_db, sample_lab, sample_host, monkeypatch) -> None:
    link_state = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="Ethernet1",
        target_node="r2",
        target_interface="Ethernet1",
        is_cross_host=False,
        source_host_id=sample_host.id,
        target_host_id=sample_host.id,
    )
    test_db.add(link_state)
    test_db.commit()

    async def fake_vlan(*args, **kwargs):
        return 200

    monkeypatch.setattr(
        "app.services.link_validator.agent_client.get_interface_vlan_from_agent",
        fake_vlan,
    )

    ok, error = await link_validator.verify_same_host_link(
        test_db, link_state, {sample_host.id: sample_host}
    )
    assert ok
    assert error is None
    assert link_state.vlan_tag == 200


@pytest.mark.asyncio
async def test_verify_cross_host_link_different_vlans_ok(test_db, sample_lab, multiple_hosts, monkeypatch) -> None:
    """Per-link VNI model: different local VLANs on each side is expected and valid."""
    host_a, host_b = multiple_hosts[:2]
    link_state = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="Ethernet1",
        target_node="r2",
        target_interface="Ethernet1",
        is_cross_host=True,
        source_host_id=host_a.id,
        target_host_id=host_b.id,
    )
    test_db.add(link_state)
    test_db.commit()

    async def fake_vlan(agent, lab_id, node, iface, read_from_ovs=True):
        return 100 if agent.id == host_a.id else 200

    async def fake_overlay_status(agent):
        return {"link_tunnels": [{"link_id": "r1:eth1-r2:eth1", "vni": 40001}]}

    monkeypatch.setattr(
        "app.services.link_validator.agent_client.get_interface_vlan_from_agent",
        fake_vlan,
    )
    monkeypatch.setattr(
        "app.services.link_validator.agent_client.get_overlay_status_from_agent",
        fake_overlay_status,
    )

    ok, error = await link_validator.verify_cross_host_link(
        test_db, link_state, {host_a.id: host_a, host_b.id: host_b}
    )
    assert ok
    assert error is None


@pytest.mark.asyncio
async def test_verify_cross_host_link_missing_tunnel(test_db, sample_lab, multiple_hosts, monkeypatch) -> None:
    """Per-link VNI model: missing VXLAN tunnel on an agent should fail."""
    host_a, host_b = multiple_hosts[:2]
    link_state = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="Ethernet1",
        target_node="r2",
        target_interface="Ethernet1",
        is_cross_host=True,
        source_host_id=host_a.id,
        target_host_id=host_b.id,
    )
    test_db.add(link_state)
    test_db.commit()

    async def fake_vlan(agent, lab_id, node, iface, read_from_ovs=True):
        return 100 if agent.id == host_a.id else 200

    async def fake_overlay_status(agent):
        # Tunnel exists on host_a but not host_b
        if agent.id == host_a.id:
            return {"link_tunnels": [{"link_id": "r1:eth1-r2:eth1", "vni": 40001}]}
        return {"link_tunnels": []}

    monkeypatch.setattr(
        "app.services.link_validator.agent_client.get_interface_vlan_from_agent",
        fake_vlan,
    )
    monkeypatch.setattr(
        "app.services.link_validator.agent_client.get_overlay_status_from_agent",
        fake_overlay_status,
    )

    ok, error = await link_validator.verify_cross_host_link(
        test_db, link_state, {host_a.id: host_a, host_b.id: host_b}
    )
    assert not ok
    assert error and "TUNNEL_MISSING" in error


@pytest.mark.asyncio
async def test_verify_cross_host_link_local_vlan_mismatch_fails(
    test_db,
    sample_lab,
    multiple_hosts,
    monkeypatch,
) -> None:
    host_a, host_b = multiple_hosts[:2]
    link_state = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="Ethernet1",
        target_node="r2",
        target_interface="Ethernet1",
        is_cross_host=True,
        source_host_id=host_a.id,
        target_host_id=host_b.id,
    )
    test_db.add(link_state)
    test_db.commit()

    async def fake_vlan(agent, lab_id, node, iface, read_from_ovs=True):
        return 100 if agent.id == host_a.id else 200

    async def fake_overlay_status(agent):
        # Source side has stale local VLAN on recovered tunnel.
        if agent.id == host_a.id:
            return {
                "link_tunnels": [{
                    "link_id": "r1:eth1-r2:eth1",
                    "local_vlan": 999,
                    "vni": 40001,
                }]
            }
        return {
            "link_tunnels": [{
                "link_id": "r1:eth1-r2:eth1",
                "local_vlan": 200,
                "vni": 40001,
            }]
        }

    monkeypatch.setattr(
        "app.services.link_validator.agent_client.get_interface_vlan_from_agent",
        fake_vlan,
    )
    monkeypatch.setattr(
        "app.services.link_validator.agent_client.get_overlay_status_from_agent",
        fake_overlay_status,
    )

    ok, error = await link_validator.verify_cross_host_link(
        test_db, link_state, {host_a.id: host_a, host_b.id: host_b}
    )
    assert not ok
    assert error and "VLAN_MISMATCH" in error


@pytest.mark.asyncio
async def test_verify_link_connected_missing_agent(test_db, sample_lab) -> None:
    link_state = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="eth1",
        target_node="r2",
        target_interface="eth1",
        is_cross_host=False,
        source_host_id="missing",
    )
    ok, error = await link_validator.verify_link_connected(test_db, link_state, {})
    assert not ok
    assert error and "Agent not found" in error
