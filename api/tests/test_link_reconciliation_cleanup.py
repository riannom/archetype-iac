from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app import models
import app.tasks.link_reconciliation as link_reconciliation


@pytest.mark.asyncio
async def test_cleanup_orphaned_tunnels(test_db, sample_lab, multiple_hosts) -> None:
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)

    tunnel_orphan = models.VxlanTunnel(
        lab_id=sample_lab.id,
        link_state_id=None,
        vni=1000,
        vlan_tag=200,
        agent_a_id=multiple_hosts[0].id,
        agent_a_ip="192.168.1.1",
        agent_b_id=multiple_hosts[1].id,
        agent_b_ip="192.168.1.2",
        status="active",
        updated_at=stale_time,
    )
    tunnel_cleanup = models.VxlanTunnel(
        lab_id=sample_lab.id,
        link_state_id=None,
        vni=1001,
        vlan_tag=201,
        agent_a_id=multiple_hosts[0].id,
        agent_a_ip="192.168.1.1",
        agent_b_id=multiple_hosts[1].id,
        agent_b_ip="192.168.1.2",
        status="cleanup",
        updated_at=stale_time,
    )

    test_db.add(tunnel_orphan)
    test_db.add(tunnel_cleanup)
    test_db.commit()

    deleted = await link_reconciliation.cleanup_orphaned_tunnels(test_db)
    assert deleted == 2

    remaining = test_db.query(models.VxlanTunnel).count()
    assert remaining == 0


@pytest.mark.asyncio
async def test_cleanup_orphaned_tunnels_defers_when_agent_offline(
    test_db, sample_lab, multiple_hosts
) -> None:
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    online_host = multiple_hosts[0]
    offline_host = multiple_hosts[2]

    link_state = models.LinkState(
        lab_id=sample_lab.id,
        link_name="r1:eth1-r2:eth1",
        source_node="r1",
        source_interface="eth1",
        target_node="r2",
        target_interface="eth1",
        is_cross_host=True,
        desired_state="up",
        actual_state="error",
        source_host_id=online_host.id,
        target_host_id=offline_host.id,
    )
    test_db.add(link_state)
    test_db.flush()

    tunnel = models.VxlanTunnel(
        lab_id=sample_lab.id,
        link_state_id=link_state.id,
        vni=2000,
        vlan_tag=300,
        agent_a_id=online_host.id,
        agent_a_ip="10.1.1.1",
        agent_b_id=offline_host.id,
        agent_b_ip="10.1.1.2",
        status="cleanup",
        updated_at=stale_time,
    )
    test_db.add(tunnel)
    test_db.commit()

    with patch(
        "app.tasks.link_cleanup.agent_client.detach_overlay_interface_on_agent",
        new_callable=AsyncMock,
        return_value={"success": True},
    ) as mock_detach:
        deleted = await link_reconciliation.cleanup_orphaned_tunnels(test_db)

    assert deleted == 0
    # Only online endpoint can be attempted; offline endpoint defers deletion.
    assert mock_detach.await_count == 1

    remaining = test_db.query(models.VxlanTunnel).filter_by(id=tunnel.id).first()
    assert remaining is not None
    assert remaining.status == "cleanup"
    assert remaining.error_message is not None
    assert "offline" in remaining.error_message
