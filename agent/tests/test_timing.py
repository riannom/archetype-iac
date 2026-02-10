"""Tests for agent.timing and agent.metrics."""
import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from agent.timing import AsyncTimedOperation, TimedOperation


class TestAgentTimedOperation:
    """Tests for the agent-side TimedOperation context manager."""

    def test_basic_duration(self):
        """duration_ms > 0 after exit."""
        with TimedOperation() as t:
            time.sleep(0.01)
        assert t.duration_ms > 0
        assert t.success is True

    def test_node_operation_histogram(self):
        """Histogram labels().observe() called with correct label."""
        mock_hist = MagicMock()
        with TimedOperation(histogram=mock_hist, labels={"operation": "start"}):
            pass
        mock_hist.labels.assert_called_once_with(operation="start")
        mock_hist.labels.return_value.observe.assert_called_once()

    def test_docker_api_histogram(self):
        """Docker API histogram records correctly."""
        mock_hist = MagicMock()
        with TimedOperation(histogram=mock_hist, labels={"operation": "create"}):
            time.sleep(0.01)
        call_args = mock_hist.labels.return_value.observe.call_args[0]
        assert call_args[0] > 0  # duration > 0

    def test_error_increments_tracking(self):
        """Exceptions set success=False and record error in log."""
        timer = TimedOperation(log_event="docker_op")
        with pytest.raises(RuntimeError):
            with timer:
                raise RuntimeError("container failed")
        assert timer.success is False

    @pytest.mark.asyncio
    async def test_async_timed_operation(self):
        """Async variant records duration."""
        async with AsyncTimedOperation() as t:
            await asyncio.sleep(0.01)
        assert t.duration_ms > 0
        assert t.success is True

    def test_prometheus_failure_guarded(self):
        """Metric failure doesn't break the operation."""
        mock_hist = MagicMock()
        mock_hist.labels.return_value.observe.side_effect = RuntimeError("prom down")
        with TimedOperation(histogram=mock_hist, labels={"operation": "x"}) as t:
            pass
        assert t.success is True


class TestAgentMetrics:
    """Tests for agent.metrics module."""

    def test_metrics_endpoint_returns_prometheus(self):
        """get_metrics() should return bytes with content type."""
        from agent.metrics import get_metrics
        body, content_type = get_metrics()
        assert isinstance(body, bytes)
        assert "text" in content_type

    def test_metric_definitions_exist(self):
        """All expected metrics should be importable."""
        from agent.metrics import (
            docker_api_duration,
            node_operation_duration,
            node_operation_errors,
            ovs_operation_duration,
        )
        # Verify they have the labels method (real or dummy)
        assert hasattr(docker_api_duration, "labels")
        assert hasattr(node_operation_duration, "labels")
        assert hasattr(node_operation_errors, "labels")
        assert hasattr(ovs_operation_duration, "labels")
