"""Tests for agent_client operation timing instrumentation."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestAgentOperationHistogram:
    """Verify agent_operation_duration histogram recording."""

    @pytest.mark.asyncio
    async def test_agent_operation_histogram_on_success(self):
        """Successful agent call should observe agent_operation_duration."""
        mock_hist = MagicMock()
        mock_agent = MagicMock(id="agent-1", name="test-agent", address="10.0.0.1:8001")

        with patch("app.agent_client.agent_operation_duration", mock_hist), \
             patch("app.agent_client._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"success": True, "duration_ms": 500}
            from app.agent_client import start_node_on_agent
            result = await start_node_on_agent(mock_agent, "lab1", "node1")

        assert result["success"] is True
        mock_hist.labels.assert_called_with(
            operation="start_node",
            host_id="agent-1",
            status="success",
        )
        mock_hist.labels.return_value.observe.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_operation_histogram_on_failure(self):
        """Failed agent call should still observe histogram."""
        mock_hist = MagicMock()
        mock_agent = MagicMock(id="agent-1", name="test-agent", address="10.0.0.1:8001")

        with patch("app.agent_client.agent_operation_duration", mock_hist), \
             patch("app.agent_client._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = RuntimeError("connection refused")
            from app.agent_client import stop_node_on_agent
            result = await stop_node_on_agent(mock_agent, "lab1", "node1")

        assert result["success"] is False
        mock_hist.labels.assert_called_with(
            operation="stop_node",
            host_id="agent-1",
            status="error",
        )
        mock_hist.labels.return_value.observe.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_duration_ms_logged(self, caplog):
        """Both API-measured and agent-reported durations should be in log."""
        import logging
        mock_agent = MagicMock(id="agent-1", name="test-agent", address="10.0.0.1:8001")

        with caplog.at_level(logging.INFO, logger="app.agent_client"), \
             patch("app.agent_client.agent_operation_duration", MagicMock()), \
             patch("app.agent_client._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"success": True, "duration_ms": 123}
            from app.agent_client import create_node_on_agent
            await create_node_on_agent(mock_agent, "lab1", "node1", "ceos")

        response_logs = [r for r in caplog.records if getattr(r, "event", None) == "agent_response"]
        assert len(response_logs) >= 1
        log = response_logs[0]
        assert hasattr(log, "duration_ms")
        assert hasattr(log, "agent_duration_ms")
        assert log.agent_duration_ms == 123

    @pytest.mark.asyncio
    async def test_host_id_label_correct(self):
        """host_id label should match the agent's ID."""
        mock_hist = MagicMock()
        mock_agent = MagicMock(id="abc123", name="prod-agent", address="10.0.0.1:8001")

        with patch("app.agent_client.agent_operation_duration", mock_hist), \
             patch("app.agent_client._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"success": True}
            from app.agent_client import destroy_node_on_agent
            await destroy_node_on_agent(mock_agent, "lab1", "node1")

        mock_hist.labels.assert_called_with(
            operation="destroy_node",
            host_id="abc123",
            status="success",
        )

    @pytest.mark.asyncio
    async def test_all_operations_instrumented(self):
        """All 4 node operations should record to the histogram."""
        mock_hist = MagicMock()
        mock_agent = MagicMock(id="a1", name="agent", address="10.0.0.1:8001")

        with patch("app.agent_client.agent_operation_duration", mock_hist), \
             patch("app.agent_client._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"success": True}
            from app.agent_client import (
                create_node_on_agent,
                start_node_on_agent,
                stop_node_on_agent,
                destroy_node_on_agent,
            )
            await create_node_on_agent(mock_agent, "lab1", "n1", "ceos")
            await start_node_on_agent(mock_agent, "lab1", "n1")
            await stop_node_on_agent(mock_agent, "lab1", "n1")
            await destroy_node_on_agent(mock_agent, "lab1", "n1")

        operations = [c.kwargs.get("operation") or c.args[0] if c.args else None
                      for c in mock_hist.labels.call_args_list]
        # Check via keyword args
        operations = [c[1].get("operation", "") for c in mock_hist.labels.call_args_list]
        assert "create_node" in operations
        assert "start_node" in operations
        assert "stop_node" in operations
        assert "destroy_node" in operations
