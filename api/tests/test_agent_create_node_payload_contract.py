"""Contract tests for API -> agent create-node payload.

These are fast unit tests that assert the API includes optional readiness
override keys when provided.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_client import create_node_on_agent


@pytest.mark.asyncio
async def test_create_node_on_agent_includes_readiness_override_fields_when_set():
    agent = MagicMock()
    agent.id = "agent1"
    agent.name = "agent1"
    agent.address = "http://localhost:8001"

    with patch("app.agent_client._agent_request", new_callable=AsyncMock) as req:
        req.return_value = {"success": True}
        await create_node_on_agent(
            agent,
            "lab1",
            "node1",
            "cat9000v-q200",
            provider="libvirt",
            readiness_probe="log_pattern",
            readiness_pattern="Press RETURN",
            readiness_timeout=600,
        )

    _, kwargs = req.await_args
    payload = kwargs["json_body"]
    assert payload["node_name"] == "node1"
    assert payload["kind"] == "cat9000v-q200"
    assert payload["readiness_probe"] == "log_pattern"
    assert payload["readiness_pattern"] == "Press RETURN"
    assert payload["readiness_timeout"] == 600


@pytest.mark.asyncio
async def test_create_node_on_agent_omits_readiness_override_fields_when_unset():
    agent = MagicMock()
    agent.id = "agent1"
    agent.name = "agent1"
    agent.address = "http://localhost:8001"

    with patch("app.agent_client._agent_request", new_callable=AsyncMock) as req:
        req.return_value = {"success": True}
        await create_node_on_agent(
            agent,
            "lab1",
            "node1",
            "cat9000v-q200",
            provider="libvirt",
        )

    _, kwargs = req.await_args
    payload = kwargs["json_body"]
    assert "readiness_probe" not in payload
    assert "readiness_pattern" not in payload
    assert "readiness_timeout" not in payload

