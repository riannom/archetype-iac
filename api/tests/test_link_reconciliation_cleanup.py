from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import models
import app.tasks.link_reconciliation as link_reconciliation


@pytest.mark.asyncio
async def test_cleanup_orphaned_tunnels(test_db, sample_lab) -> None:
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)

    tunnel_orphan = models.VxlanTunnel(
        lab_id=sample_lab.id,
        link_state_id=None,
        vni=1000,
        vlan_tag=200,
        status="active",
        updated_at=stale_time,
    )
    tunnel_cleanup = models.VxlanTunnel(
        lab_id=sample_lab.id,
        link_state_id=None,
        vni=1001,
        vlan_tag=201,
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
