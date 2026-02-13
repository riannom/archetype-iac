"""Regression tests for create_node endpoint compatibility.

These tests ensure the agent does not crash when the controller sends a
CreateNodeRequest schema that does not include newer readiness override fields.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from agent.main import create_node
from agent.providers.base import NodeActionResult, NodeStatus
from agent.schemas import CreateNodeRequest


def _run(coro):
    return asyncio.run(coro)


def test_create_node_does_not_require_readiness_fields(tmp_path):
    """Old bug: request.readiness_probe raised AttributeError and returned 500."""
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-node1"
    provider.create_node = AsyncMock(
        return_value=NodeActionResult(success=True, node_name="node1", new_status=NodeStatus.STOPPED)
    )

    req = CreateNodeRequest(node_name="node1", kind="linux")

    with patch("agent.main.get_provider_for_request", return_value=provider):
        with patch("agent.main.get_workspace", return_value=tmp_path):
            res = _run(create_node("lab1", "node1", req, provider="docker"))

    assert res.success is True
    # The provider should be called even though readiness_* does not exist on the schema.
    provider.create_node.assert_awaited_once()
    kwargs = provider.create_node.await_args.kwargs
    assert "readiness_probe" in kwargs and kwargs["readiness_probe"] is None
    assert "readiness_pattern" in kwargs and kwargs["readiness_pattern"] is None
    assert "readiness_timeout" in kwargs and kwargs["readiness_timeout"] is None


def test_create_node_accepts_controller_payload_with_readiness_overrides(tmp_path):
    """Contract-style test: controller may send readiness_* keys.

    Even if the agent schema doesn't declare these fields, pydantic may allow
    them as extras, and the endpoint must not crash. If present, we forward
    them to the provider via getattr().
    """
    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-node1"
    provider.create_node = AsyncMock(
        return_value=NodeActionResult(success=True, node_name="node1", new_status=NodeStatus.STOPPED)
    )

    # Simulate an older agent schema receiving a newer controller payload.
    payload = {
        "node_name": "node1",
        "kind": "linux",
        "readiness_probe": "log_pattern",
        "readiness_pattern": "Press RETURN",
        "readiness_timeout": 600,
    }
    req = CreateNodeRequest.model_validate(payload)

    with patch("agent.main.get_provider_for_request", return_value=provider):
        with patch("agent.main.get_workspace", return_value=tmp_path):
            res = _run(create_node("lab1", "node1", req, provider="docker"))

    assert res.success is True
    provider.create_node.assert_awaited_once()
    kwargs = provider.create_node.await_args.kwargs
    assert kwargs["readiness_probe"] == "log_pattern"
    assert kwargs["readiness_pattern"] == "Press RETURN"
    assert kwargs["readiness_timeout"] == 600
