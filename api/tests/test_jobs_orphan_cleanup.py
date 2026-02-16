from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app import models
from app.tasks.jobs import _cleanup_orphan_containers


@pytest.mark.asyncio
async def test_cleanup_orphan_containers_uses_scoped_cleanup(
    test_db, sample_lab, multiple_hosts
) -> None:
    old_host = multiple_hosts[0]
    new_host = multiple_hosts[1]

    test_db.add_all([
        models.NodePlacement(
            lab_id=sample_lab.id,
            node_name="r1",
            host_id=old_host.id,
            status="deployed",
        ),
        models.NodePlacement(
            lab_id=sample_lab.id,
            node_name="r2",
            host_id=new_host.id,
            status="deployed",
        ),
    ])
    test_db.commit()

    log_parts: list[str] = []
    with patch(
        "app.tasks.jobs.agent_client.is_agent_online",
        return_value=True,
    ), patch(
        "app.tasks.jobs.agent_client.cleanup_lab_orphans",
        new_callable=AsyncMock,
        return_value={"removed_containers": ["stale-r3"], "errors": []},
    ) as mock_cleanup, patch(
        "app.tasks.jobs.agent_client.destroy_lab_on_agent",
        new_callable=AsyncMock,
    ) as mock_destroy:
        await _cleanup_orphan_containers(
            test_db,
            sample_lab.id,
            new_host.id,
            {old_host.id, new_host.id},
            log_parts,
        )

    mock_cleanup.assert_awaited_once()
    args = mock_cleanup.await_args.args
    assert args[0].id == old_host.id
    assert args[1] == sample_lab.id
    assert set(args[2]) == {"r1"}
    mock_destroy.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_orphan_containers_skips_offline_agents(
    test_db, sample_lab, multiple_hosts
) -> None:
    offline_host = multiple_hosts[2]
    new_host = multiple_hosts[0]

    log_parts: list[str] = []
    with patch(
        "app.tasks.jobs.agent_client.cleanup_lab_orphans",
        new_callable=AsyncMock,
    ) as mock_cleanup:
        await _cleanup_orphan_containers(
            test_db,
            sample_lab.id,
            new_host.id,
            {offline_host.id},
            log_parts,
        )

    mock_cleanup.assert_not_awaited()
    assert any("Skipped cleanup on offline agent" in line for line in log_parts)
