from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.routers.labs import _reconcile_single_node


@pytest.mark.asyncio
async def test_reconcile_stop_uses_shared_lab_cleanup_helper(tmp_path):
    target = SimpleNamespace(
        container_name="archetype-lab1-node1",
        desired_state="stopped",
    )

    container = MagicMock()
    container.status = "running"
    container.labels = {"archetype.node_name": "node1"}

    client = MagicMock()
    client.containers.get.return_value = container

    docker_provider = MagicMock()
    docker_provider.stop_node = AsyncMock(
        return_value=SimpleNamespace(success=True, error=None)
    )
    docker_provider.cleanup_lab_resources_if_empty = AsyncMock(
        return_value={"cleaned": True, "networks_deleted": 2}
    )

    with patch("agent.routers.labs.get_docker_client", return_value=client):
        with patch("agent.routers.labs.get_provider", return_value=docker_provider):
            result = await _reconcile_single_node("lab1", target, tmp_path)

    assert result.success is True
    assert result.action == "removed"
    docker_provider.stop_node.assert_awaited_once_with(
        lab_id="lab1",
        node_name="node1",
        workspace=tmp_path,
    )
    docker_provider.cleanup_lab_resources_if_empty.assert_awaited_once_with(
        "lab1",
        tmp_path,
    )
    container.stop.assert_not_called()
    container.remove.assert_not_called()
