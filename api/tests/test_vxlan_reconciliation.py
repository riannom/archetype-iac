"""Tests for VXLAN port reconciliation and duplicate tunnel detection.

Covers:
- reconcile_agent_vxlan_ports(): API-driven port cleanup on agents
- detect_duplicate_tunnels(): duplicate VxlanTunnel dedup
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app import models
import app.tasks.link_reconciliation as link_reconciliation


# ─── Helpers ──────────────────────────────────────────────────────────────

def _make_link_state(
    test_db,
    lab_id: str,
    link_name: str,
    *,
    is_cross_host: bool = True,
    actual_state: str = "up",
    source_host_id: str | None = None,
    target_host_id: str | None = None,
) -> models.LinkState:
    ls = models.LinkState(
        lab_id=lab_id,
        link_name=link_name,
        source_node="R1",
        source_interface="eth1",
        target_node="R2",
        target_interface="eth1",
        is_cross_host=is_cross_host,
        actual_state=actual_state,
        source_host_id=source_host_id,
        target_host_id=target_host_id,
    )
    test_db.add(ls)
    test_db.flush()
    return ls


def _make_tunnel(
    test_db,
    lab_id: str,
    link_state_id: str | None,
    vni: int,
    agent_a_id: str,
    agent_b_id: str,
    *,
    status: str = "active",
    created_at: datetime | None = None,
) -> models.VxlanTunnel:
    t = models.VxlanTunnel(
        lab_id=lab_id,
        link_state_id=link_state_id,
        vni=vni,
        vlan_tag=0,
        agent_a_id=agent_a_id,
        agent_a_ip="10.0.0.1",
        agent_b_id=agent_b_id,
        agent_b_ip="10.0.0.2",
        status=status,
    )
    test_db.add(t)
    test_db.flush()
    if created_at:
        t.created_at = created_at
        test_db.flush()
    return t


# ─── reconcile_agent_vxlan_ports tests ────────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_removes_stale_port(test_db, sample_lab, multiple_hosts):
    """Agent with a stale VXLAN port not backed by DB gets it removed."""
    ls = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    _make_tunnel(
        test_db, sample_lab.id, ls.id, 5000,
        multiple_hosts[0].id, multiple_hosts[1].id,
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    mock_result = {"removed_ports": ["vxlan-stale123"], "valid_count": 1}
    with patch.object(
        link_reconciliation.agent_client,
        "reconcile_vxlan_ports_on_agent",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as mock_reconcile:
        result = await link_reconciliation.reconcile_agent_vxlan_ports(
            test_db, host_to_agent, cycle_count=5,
        )

    assert mock_reconcile.called
    # At least one agent had ports removed
    total_removed = sum(len(v) for v in result.values())
    assert total_removed >= 1


@pytest.mark.asyncio
async def test_reconcile_preserves_valid_ports(test_db, sample_lab, multiple_hosts):
    """Valid port names derived from DB are passed to agent — nothing removed."""
    ls = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    _make_tunnel(
        test_db, sample_lab.id, ls.id, 5000,
        multiple_hosts[0].id, multiple_hosts[1].id,
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    mock_result = {"removed_ports": [], "valid_count": 1}
    with patch.object(
        link_reconciliation.agent_client,
        "reconcile_vxlan_ports_on_agent",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as mock_reconcile:
        result = await link_reconciliation.reconcile_agent_vxlan_ports(
            test_db, host_to_agent, cycle_count=5,
        )

    assert mock_reconcile.called
    # Verify valid port names were passed
    call_args = mock_reconcile.call_args_list[0]
    valid_ports = call_args.kwargs.get("valid_port_names", call_args[1].get("valid_port_names", []))
    assert len(valid_ports) >= 1
    assert all(p.startswith("vxlan-") for p in valid_ports)
    assert result == {}  # Nothing removed


@pytest.mark.asyncio
async def test_reconcile_skips_offline_agents(test_db, sample_lab, multiple_hosts):
    """Only online agents in host_to_agent map get reconciled."""
    ls = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        source_host_id=multiple_hosts[2].id,  # agent-3 is offline
        target_host_id=multiple_hosts[0].id,
    )
    _make_tunnel(
        test_db, sample_lab.id, ls.id, 5000,
        multiple_hosts[2].id, multiple_hosts[0].id,
    )
    test_db.commit()

    # Only include online agents
    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    with patch.object(
        link_reconciliation.agent_client,
        "reconcile_vxlan_ports_on_agent",
        new_callable=AsyncMock,
        return_value={"removed_ports": [], "valid_count": 1},
    ) as mock_reconcile:
        await link_reconciliation.reconcile_agent_vxlan_ports(
            test_db, host_to_agent, cycle_count=5,
        )

    # Only agent-1 (online, has tunnel) gets called; agent-3 is offline
    for call in mock_reconcile.call_args_list:
        agent = call[0][0] if call[0] else call.kwargs.get("agent")
        assert agent.status == "online"


@pytest.mark.asyncio
async def test_reconcile_protects_in_progress_links(test_db, sample_lab, multiple_hosts):
    """Ports for in-progress (creating/connecting) links are whitelisted."""
    ls = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        actual_state="creating",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    # No VxlanTunnel yet (link is still being created)
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    from app.agent_client import compute_vxlan_port_name
    expected_port = compute_vxlan_port_name(sample_lab.id, "R1:eth1-R2:eth1")

    with patch.object(
        link_reconciliation.agent_client,
        "reconcile_vxlan_ports_on_agent",
        new_callable=AsyncMock,
        return_value={"removed_ports": [], "valid_count": 1},
    ) as mock_reconcile:
        await link_reconciliation.reconcile_agent_vxlan_ports(
            test_db, host_to_agent, cycle_count=5,
        )

    # Verify the in-progress link's port is in the valid set
    if mock_reconcile.called:
        for call in mock_reconcile.call_args_list:
            valid_ports = call.kwargs.get("valid_port_names", [])
            agent = call[0][0] if call[0] else call.kwargs.get("agent")
            if agent.id in (multiple_hosts[0].id, multiple_hosts[1].id):
                assert expected_port in valid_ports


@pytest.mark.asyncio
async def test_reconcile_agent_call_fails_continues(test_db, sample_lab, multiple_hosts):
    """If agent call fails, error is logged and other agents still proceed."""
    ls = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    _make_tunnel(
        test_db, sample_lab.id, ls.id, 5000,
        multiple_hosts[0].id, multiple_hosts[1].id,
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    with patch.object(
        link_reconciliation.agent_client,
        "reconcile_vxlan_ports_on_agent",
        new_callable=AsyncMock,
        side_effect=Exception("connection refused"),
    ):
        # Should not raise
        result = await link_reconciliation.reconcile_agent_vxlan_ports(
            test_db, host_to_agent, cycle_count=5,
        )

    assert result == {}


@pytest.mark.asyncio
async def test_reconcile_skips_non_5th_cycle(test_db, sample_lab, multiple_hosts):
    """Reconciliation only runs on every 5th cycle."""
    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    with patch.object(
        link_reconciliation.agent_client,
        "reconcile_vxlan_ports_on_agent",
        new_callable=AsyncMock,
    ) as mock_reconcile:
        for cycle in (1, 2, 3, 4, 6, 7):
            result = await link_reconciliation.reconcile_agent_vxlan_ports(
                test_db, host_to_agent, cycle_count=cycle,
            )
            assert result == {}

    mock_reconcile.assert_not_called()


# ─── detect_duplicate_tunnels tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_no_duplicates_noop(test_db, sample_lab, multiple_hosts):
    """No duplicate tunnels → no deletions."""
    ls = _make_link_state(test_db, sample_lab.id, "R1:eth1-R2:eth1")
    _make_tunnel(
        test_db, sample_lab.id, ls.id, 5000,
        multiple_hosts[0].id, multiple_hosts[1].id,
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    with patch.object(
        link_reconciliation.agent_client,
        "detach_overlay_interface_on_agent",
        new_callable=AsyncMock,
    ):
        removed = await link_reconciliation.detect_duplicate_tunnels(
            test_db, host_to_agent,
        )

    assert removed == 0
    assert test_db.query(models.VxlanTunnel).count() == 1


@pytest.mark.asyncio
async def test_same_direction_duplicates_keeps_active(test_db, sample_lab, multiple_hosts):
    """Two tunnels same direction — keeps one with active LinkState."""
    ls_active = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1", actual_state="up",
    )
    ls_dead = _make_link_state(
        test_db, sample_lab.id, "R3:eth1-R4:eth1", actual_state="deleted",
    )
    t_keep = _make_tunnel(
        test_db, sample_lab.id, ls_active.id, 5000,
        multiple_hosts[0].id, multiple_hosts[1].id,
    )
    _make_tunnel(
        test_db, sample_lab.id, ls_dead.id, 5000,
        multiple_hosts[0].id, multiple_hosts[1].id,
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    with patch.object(
        link_reconciliation.agent_client,
        "detach_overlay_interface_on_agent",
        new_callable=AsyncMock,
    ):
        removed = await link_reconciliation.detect_duplicate_tunnels(
            test_db, host_to_agent,
        )

    assert removed == 1
    remaining = test_db.query(models.VxlanTunnel).all()
    assert len(remaining) == 1
    assert remaining[0].id == t_keep.id


@pytest.mark.asyncio
async def test_reversed_direction_duplicates_detected(test_db, sample_lab, multiple_hosts):
    """Duplicates with (a,b) vs (b,a) agent ordering are detected."""
    ls1 = _make_link_state(test_db, sample_lab.id, "R1:eth1-R2:eth1", actual_state="up")
    ls2 = _make_link_state(test_db, sample_lab.id, "R3:eth1-R4:eth1", actual_state="deleted")

    _make_tunnel(
        test_db, sample_lab.id, ls1.id, 5000,
        multiple_hosts[0].id, multiple_hosts[1].id,  # (agent-1, agent-2)
    )
    _make_tunnel(
        test_db, sample_lab.id, ls2.id, 5000,
        multiple_hosts[1].id, multiple_hosts[0].id,  # (agent-2, agent-1) reversed!
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    with patch.object(
        link_reconciliation.agent_client,
        "detach_overlay_interface_on_agent",
        new_callable=AsyncMock,
    ):
        removed = await link_reconciliation.detect_duplicate_tunnels(
            test_db, host_to_agent,
        )

    assert removed == 1
    assert test_db.query(models.VxlanTunnel).count() == 1


@pytest.mark.asyncio
async def test_both_active_keeps_newest(test_db, sample_lab, multiple_hosts):
    """When both duplicates have active LinkStates, keep the newest."""
    ls1 = _make_link_state(test_db, sample_lab.id, "R1:eth1-R2:eth1", actual_state="up")
    ls2 = _make_link_state(test_db, sample_lab.id, "R3:eth1-R4:eth1", actual_state="up")

    old_time = datetime.now(timezone.utc) - timedelta(hours=1)
    _make_tunnel(
        test_db, sample_lab.id, ls1.id, 5000,
        multiple_hosts[0].id, multiple_hosts[1].id,
        created_at=old_time,
    )
    t_new = _make_tunnel(
        test_db, sample_lab.id, ls2.id, 5000,
        multiple_hosts[0].id, multiple_hosts[1].id,
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    with patch.object(
        link_reconciliation.agent_client,
        "detach_overlay_interface_on_agent",
        new_callable=AsyncMock,
    ):
        removed = await link_reconciliation.detect_duplicate_tunnels(
            test_db, host_to_agent,
        )

    assert removed == 1
    remaining = test_db.query(models.VxlanTunnel).all()
    assert len(remaining) == 1
    assert remaining[0].id == t_new.id


@pytest.mark.asyncio
async def test_teardown_fails_still_deletes_db_record(test_db, sample_lab, multiple_hosts):
    """If agent teardown fails, the DB record is still deleted."""
    ls1 = _make_link_state(test_db, sample_lab.id, "R1:eth1-R2:eth1", actual_state="up")
    ls2 = _make_link_state(test_db, sample_lab.id, "R3:eth1-R4:eth1", actual_state="deleted")

    _make_tunnel(
        test_db, sample_lab.id, ls1.id, 5000,
        multiple_hosts[0].id, multiple_hosts[1].id,
    )
    _make_tunnel(
        test_db, sample_lab.id, ls2.id, 5000,
        multiple_hosts[0].id, multiple_hosts[1].id,
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    with patch.object(
        link_reconciliation.agent_client,
        "detach_overlay_interface_on_agent",
        new_callable=AsyncMock,
        side_effect=Exception("agent down"),
    ):
        removed = await link_reconciliation.detect_duplicate_tunnels(
            test_db, host_to_agent,
        )

    assert removed == 1
    assert test_db.query(models.VxlanTunnel).count() == 1
