"""Tests for duplicate tunnel detection.

Covers:
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
