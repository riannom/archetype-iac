from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app import models
from app.tasks.jobs import _update_node_placements


@pytest.mark.asyncio
async def test_update_node_placements_emits_event_on_host_move(
    test_db, sample_lab, multiple_hosts,
) -> None:
    old_host, new_host = multiple_hosts[0], multiple_hosts[1]

    node = models.Node(
        lab_id=sample_lab.id,
        gui_id="node-r1",
        display_name="R1",
        container_name="r1",
    )
    test_db.add(node)
    test_db.flush()

    placement = models.NodePlacement(
        lab_id=sample_lab.id,
        node_name="r1",
        node_definition_id=node.id,
        host_id=old_host.id,
        status="deployed",
    )
    test_db.add(placement)
    test_db.commit()

    with patch(
        "app.tasks.jobs.emit_node_placement_changed",
        new_callable=AsyncMock,
    ) as mock_emit:
        await _update_node_placements(
            test_db,
            sample_lab.id,
            new_host.id,
            ["r1"],
            status="starting",
        )

    test_db.refresh(placement)
    assert placement.host_id == new_host.id
    assert placement.status == "starting"
    mock_emit.assert_awaited_once_with(
        lab_id=sample_lab.id,
        node_name="r1",
        agent_id=new_host.id,
        old_agent_id=old_host.id,
    )


@pytest.mark.asyncio
async def test_update_node_placements_no_event_when_host_unchanged(
    test_db, sample_lab, sample_host,
) -> None:
    node = models.Node(
        lab_id=sample_lab.id,
        gui_id="node-r1",
        display_name="R1",
        container_name="r1",
    )
    test_db.add(node)
    test_db.flush()

    placement = models.NodePlacement(
        lab_id=sample_lab.id,
        node_name="r1",
        node_definition_id=node.id,
        host_id=sample_host.id,
        status="deployed",
    )
    test_db.add(placement)
    test_db.commit()

    with patch(
        "app.tasks.jobs.emit_node_placement_changed",
        new_callable=AsyncMock,
    ) as mock_emit:
        await _update_node_placements(
            test_db,
            sample_lab.id,
            sample_host.id,
            ["r1"],
            status="deployed",
        )

    mock_emit.assert_not_awaited()
