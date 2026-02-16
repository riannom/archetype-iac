"""Tests for same-host port convergence and InterfaceMapping refresh.

Covers:
- refresh_interface_mappings(): bulk upsert with last_verified_at
- run_same_host_convergence(): builds pairings from same-host links, converges
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app import models
import app.tasks.link_reconciliation as link_reconciliation


# ─── Helpers ──────────────────────────────────────────────────────────────


def _make_same_host_link(
    test_db,
    lab_id: str,
    link_name: str,
    *,
    host_id: str,
    source_node: str = "R1",
    source_interface: str = "eth1",
    target_node: str = "R2",
    target_interface: str = "eth1",
    vlan_tag: int | None = 100,
    desired_state: str = "up",
    actual_state: str = "up",
) -> models.LinkState:
    ls = models.LinkState(
        lab_id=lab_id,
        link_name=link_name,
        source_node=source_node,
        source_interface=source_interface,
        target_node=target_node,
        target_interface=target_interface,
        is_cross_host=False,
        desired_state=desired_state,
        actual_state=actual_state,
        source_host_id=host_id,
        target_host_id=host_id,
        vlan_tag=vlan_tag,
    )
    test_db.add(ls)
    test_db.flush()
    return ls


def _make_cross_host_link(
    test_db,
    lab_id: str,
    link_name: str,
    *,
    source_host_id: str,
    target_host_id: str,
    source_node: str = "r1",
    source_interface: str = "eth1",
    target_node: str = "r2",
    target_interface: str = "eth1",
    source_vlan_tag: int = 100,
    target_vlan_tag: int = 200,
    desired_state: str = "up",
    actual_state: str = "up",
) -> models.LinkState:
    ls = models.LinkState(
        lab_id=lab_id,
        link_name=link_name,
        source_node=source_node,
        source_interface=source_interface,
        target_node=target_node,
        target_interface=target_interface,
        is_cross_host=True,
        desired_state=desired_state,
        actual_state=actual_state,
        source_host_id=source_host_id,
        target_host_id=target_host_id,
        source_vlan_tag=source_vlan_tag,
        target_vlan_tag=target_vlan_tag,
    )
    test_db.add(ls)
    test_db.flush()
    return ls


def _make_node(
    test_db,
    lab_id: str,
    display_name: str,
    *,
    host_id: str,
    container_name: str | None = None,
) -> models.Node:
    node = models.Node(
        id=str(uuid4()),
        lab_id=lab_id,
        gui_id=display_name.lower(),
        display_name=display_name,
        container_name=container_name or f"archetype-test-{display_name.lower()}",
        device="linux",
        host_id=host_id,
    )
    test_db.add(node)
    test_db.flush()
    return node


def _make_interface_mapping(
    test_db,
    lab_id: str,
    node_id: str,
    linux_interface: str,
    ovs_port: str,
    vlan_tag: int = 100,
) -> models.InterfaceMapping:
    mapping = models.InterfaceMapping(
        id=str(uuid4()),
        lab_id=lab_id,
        node_id=node_id,
        ovs_port=ovs_port,
        ovs_bridge="arch-ovs",
        vlan_tag=vlan_tag,
        linux_interface=linux_interface,
    )
    test_db.add(mapping)
    test_db.flush()
    return mapping


# ─── InterfaceMapping refresh tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_creates_new_mappings(test_db, sample_lab, sample_host):
    """New ports from agent create InterfaceMapping records."""
    node = _make_node(test_db, sample_lab.id, "R1", host_id=sample_host.id)
    _make_same_host_link(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        host_id=sample_host.id,
    )
    test_db.commit()

    host_to_agent = {sample_host.id: sample_host}

    mock_ports = [
        {
            "node_name": "R1",
            "interface_name": "eth1",
            "ovs_port_name": "vh-abc123",
            "vlan_tag": 100,
        },
    ]

    with patch.object(
        link_reconciliation.agent_client,
        "get_lab_port_state",
        new_callable=AsyncMock,
        return_value=mock_ports,
    ):
        result = await link_reconciliation.refresh_interface_mappings(
            test_db, host_to_agent
        )

    assert result["created"] == 1
    mapping = (
        test_db.query(models.InterfaceMapping)
        .filter(
            models.InterfaceMapping.lab_id == sample_lab.id,
            models.InterfaceMapping.node_id == node.id,
        )
        .first()
    )
    assert mapping is not None
    assert mapping.ovs_port == "vh-abc123"
    assert mapping.vlan_tag == 100
    assert mapping.last_verified_at is not None


@pytest.mark.asyncio
async def test_refresh_updates_existing_mappings(test_db, sample_lab, sample_host):
    """Existing mappings get updated with fresh data and timestamp."""
    node = _make_node(test_db, sample_lab.id, "R1", host_id=sample_host.id)
    mapping = _make_interface_mapping(
        test_db, sample_lab.id, node.id, "eth1", "vh-old123", vlan_tag=50,
    )
    _make_same_host_link(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        host_id=sample_host.id,
    )
    test_db.commit()

    host_to_agent = {sample_host.id: sample_host}

    mock_ports = [
        {
            "node_name": "R1",
            "interface_name": "eth1",
            "ovs_port_name": "vh-new456",
            "vlan_tag": 200,
        },
    ]

    with patch.object(
        link_reconciliation.agent_client,
        "get_lab_port_state",
        new_callable=AsyncMock,
        return_value=mock_ports,
    ):
        result = await link_reconciliation.refresh_interface_mappings(
            test_db, host_to_agent
        )

    assert result["updated"] == 1
    test_db.refresh(mapping)
    assert mapping.ovs_port == "vh-new456"
    assert mapping.vlan_tag == 200
    assert mapping.last_verified_at is not None


@pytest.mark.asyncio
async def test_refresh_handles_offline_agent(test_db, sample_lab, multiple_hosts):
    """Offline agents are skipped gracefully."""
    _make_same_host_link(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        host_id=multiple_hosts[2].id,  # offline
    )
    test_db.commit()

    # Only online agents
    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    with patch.object(
        link_reconciliation.agent_client,
        "get_lab_port_state",
        new_callable=AsyncMock,
    ) as mock_get:
        result = await link_reconciliation.refresh_interface_mappings(
            test_db, host_to_agent
        )

    mock_get.assert_not_called()
    assert result == {"updated": 0, "created": 0}


@pytest.mark.asyncio
async def test_refresh_no_same_host_links_noop(test_db, sample_lab, multiple_hosts):
    """No same-host links → no agent calls."""
    test_db.commit()
    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    with patch.object(
        link_reconciliation.agent_client,
        "get_lab_port_state",
        new_callable=AsyncMock,
    ) as mock_get:
        result = await link_reconciliation.refresh_interface_mappings(
            test_db, host_to_agent
        )

    mock_get.assert_not_called()
    assert result == {"updated": 0, "created": 0}


# ─── Same-host convergence tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_host_convergence_sends_pairings(test_db, sample_lab, sample_host):
    """Same-host links with InterfaceMappings produce port pairings."""
    node_r1 = _make_node(test_db, sample_lab.id, "R1", host_id=sample_host.id)
    node_r2 = _make_node(test_db, sample_lab.id, "R2", host_id=sample_host.id)

    _make_interface_mapping(
        test_db, sample_lab.id, node_r1.id, "eth1", "vh-r1eth1", vlan_tag=100,
    )
    _make_interface_mapping(
        test_db, sample_lab.id, node_r2.id, "eth1", "vh-r2eth1", vlan_tag=100,
    )

    _make_same_host_link(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        host_id=sample_host.id,
        vlan_tag=100,
    )
    test_db.commit()

    host_to_agent = {sample_host.id: sample_host}
    captured_pairings = {}

    async def _capture(agent, pairings):
        captured_pairings[agent.id] = pairings
        return {
            "results": [
                {"link_name": p["link_name"], "lab_id": p["lab_id"],
                 "status": "converged", "actual_vlan": p["vlan_tag"]}
                for p in pairings
            ],
        }

    with patch.object(
        link_reconciliation,
        "declare_port_state_on_agent",
        side_effect=_capture,
    ):
        await link_reconciliation.run_same_host_convergence(
            test_db, host_to_agent
        )

    assert sample_host.id in captured_pairings
    pairings = captured_pairings[sample_host.id]
    assert len(pairings) == 1
    assert pairings[0]["port_a"] == "vh-r1eth1"
    assert pairings[0]["port_b"] == "vh-r2eth1"
    assert pairings[0]["vlan_tag"] == 100


@pytest.mark.asyncio
async def test_same_host_convergence_resolves_container_name_endpoints(
    test_db, sample_lab, sample_host
):
    """Endpoint names keyed by container_name still resolve Node records."""
    node_r1 = _make_node(
        test_db,
        sample_lab.id,
        "Router One",
        host_id=sample_host.id,
        container_name="r1",
    )
    node_r2 = _make_node(
        test_db,
        sample_lab.id,
        "Router Two",
        host_id=sample_host.id,
        container_name="r2",
    )

    _make_interface_mapping(
        test_db, sample_lab.id, node_r1.id, "eth1", "vh-r1eth1", vlan_tag=100,
    )
    _make_interface_mapping(
        test_db, sample_lab.id, node_r2.id, "eth1", "vh-r2eth1", vlan_tag=100,
    )
    _make_same_host_link(
        test_db,
        sample_lab.id,
        "r1:eth1-r2:eth1",
        host_id=sample_host.id,
        source_node="r1",
        target_node="r2",
        vlan_tag=100,
    )
    test_db.commit()

    host_to_agent = {sample_host.id: sample_host}
    with patch.object(
        link_reconciliation,
        "declare_port_state_on_agent",
        new_callable=AsyncMock,
        return_value={"results": [{"status": "converged"}]},
    ) as mock_declare:
        await link_reconciliation.run_same_host_convergence(test_db, host_to_agent)

    mock_declare.assert_called_once()


@pytest.mark.asyncio
async def test_same_host_convergence_skips_missing_mappings(test_db, sample_lab, sample_host):
    """Links without InterfaceMappings are skipped."""
    _make_node(test_db, sample_lab.id, "R1", host_id=sample_host.id)
    _make_node(test_db, sample_lab.id, "R2", host_id=sample_host.id)
    # No InterfaceMapping records

    _make_same_host_link(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        host_id=sample_host.id,
        vlan_tag=100,
    )
    test_db.commit()

    host_to_agent = {sample_host.id: sample_host}

    with patch.object(
        link_reconciliation,
        "declare_port_state_on_agent",
        new_callable=AsyncMock,
    ) as mock_declare:
        result = await link_reconciliation.run_same_host_convergence(
            test_db, host_to_agent
        )

    mock_declare.assert_not_called()
    assert result == {}


@pytest.mark.asyncio
async def test_same_host_convergence_handles_agent_error(test_db, sample_lab, sample_host):
    """Agent call failure is handled gracefully."""
    node_r1 = _make_node(test_db, sample_lab.id, "R1", host_id=sample_host.id)
    node_r2 = _make_node(test_db, sample_lab.id, "R2", host_id=sample_host.id)

    _make_interface_mapping(
        test_db, sample_lab.id, node_r1.id, "eth1", "vh-r1eth1", vlan_tag=100,
    )
    _make_interface_mapping(
        test_db, sample_lab.id, node_r2.id, "eth1", "vh-r2eth1", vlan_tag=100,
    )

    _make_same_host_link(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        host_id=sample_host.id,
        vlan_tag=100,
    )
    test_db.commit()

    host_to_agent = {sample_host.id: sample_host}

    with patch.object(
        link_reconciliation,
        "declare_port_state_on_agent",
        new_callable=AsyncMock,
        side_effect=Exception("connection refused"),
    ):
        result = await link_reconciliation.run_same_host_convergence(
            test_db, host_to_agent
        )

    assert sample_host.id in result
    assert "error" in result[sample_host.id]


@pytest.mark.asyncio
async def test_same_host_no_links_noop(test_db, sample_lab, sample_host):
    """No same-host links → no agent calls."""
    test_db.commit()
    host_to_agent = {sample_host.id: sample_host}

    with patch.object(
        link_reconciliation,
        "declare_port_state_on_agent",
        new_callable=AsyncMock,
    ) as mock_declare:
        result = await link_reconciliation.run_same_host_convergence(
            test_db, host_to_agent
        )

    mock_declare.assert_not_called()
    assert result == {}


@pytest.mark.asyncio
async def test_same_host_skips_zero_vlan(test_db, sample_lab, sample_host):
    """Links with vlan_tag=0 are skipped (no valid VLAN)."""
    node_r1 = _make_node(test_db, sample_lab.id, "R1", host_id=sample_host.id)
    node_r2 = _make_node(test_db, sample_lab.id, "R2", host_id=sample_host.id)

    _make_interface_mapping(
        test_db, sample_lab.id, node_r1.id, "eth1", "vh-r1eth1", vlan_tag=0,
    )
    _make_interface_mapping(
        test_db, sample_lab.id, node_r2.id, "eth1", "vh-r2eth1", vlan_tag=0,
    )

    _make_same_host_link(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        host_id=sample_host.id,
        vlan_tag=0,
    )
    test_db.commit()

    host_to_agent = {sample_host.id: sample_host}

    with patch.object(
        link_reconciliation,
        "declare_port_state_on_agent",
        new_callable=AsyncMock,
    ) as mock_declare:
        result = await link_reconciliation.run_same_host_convergence(
            test_db, host_to_agent
        )

    mock_declare.assert_not_called()
    assert result == {}


@pytest.mark.asyncio
async def test_cross_host_convergence_resolves_container_name_endpoints(
    test_db, sample_lab, multiple_hosts
):
    """Cross-host convergence resolves endpoint names by container_name."""
    host_a, host_b = multiple_hosts[0], multiple_hosts[1]
    node_r1 = _make_node(
        test_db,
        sample_lab.id,
        "Router One",
        host_id=host_a.id,
        container_name="r1",
    )
    node_r2 = _make_node(
        test_db,
        sample_lab.id,
        "Router Two",
        host_id=host_b.id,
        container_name="r2",
    )

    # Deliberately mismatch InterfaceMapping VLAN vs DB truth to trigger correction.
    _make_interface_mapping(
        test_db, sample_lab.id, node_r1.id, "eth1", "vh-r1eth1", vlan_tag=999,
    )
    _make_interface_mapping(
        test_db, sample_lab.id, node_r2.id, "eth1", "vh-r2eth1", vlan_tag=998,
    )
    _make_cross_host_link(
        test_db,
        sample_lab.id,
        "r1:eth1-r2:eth1",
        source_host_id=host_a.id,
        target_host_id=host_b.id,
        source_node="r1",
        target_node="r2",
        source_vlan_tag=100,
        target_vlan_tag=200,
    )
    test_db.commit()

    host_to_agent = {host_a.id: host_a, host_b.id: host_b}

    with patch.object(
        link_reconciliation.agent_client,
        "set_port_vlan_on_agent",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_set_vlan:
        result = await link_reconciliation.run_cross_host_port_convergence(
            test_db, host_to_agent
        )

    assert result == {"updated": 2, "errors": 0}
    assert mock_set_vlan.await_count == 2
