"""Tests for app.timing — TimedOperation and AsyncTimedOperation."""
import asyncio
import logging
import time
from unittest.mock import MagicMock, patch

import pytest

from app.timing import AsyncTimedOperation, TimedOperation


class TestTimedOperation:
    """Tests for the synchronous TimedOperation context manager."""

    def test_basic_duration_recording(self):
        """duration_ms should be > 0 after exit."""
        with TimedOperation() as t:
            time.sleep(0.01)
        assert t.duration_ms > 0

    def test_histogram_observe_called(self):
        """When a histogram is provided, labels().observe() must be called."""
        mock_hist = MagicMock()
        with TimedOperation(histogram=mock_hist, labels={"phase": "deploy"}):
            pass
        mock_hist.labels.assert_called_once_with(phase="deploy")
        mock_hist.labels.return_value.observe.assert_called_once()

    def test_labels_passed_correctly(self):
        """Labels dict should be forwarded to histogram.labels()."""
        mock_hist = MagicMock()
        labels = {"phase": "boot_wait", "device_type": "ceos"}
        with TimedOperation(histogram=mock_hist, labels=labels):
            pass
        mock_hist.labels.assert_called_once_with(**labels)

    def test_structured_log_emitted(self, caplog):
        """Structured log should contain event, duration_ms, success."""
        with caplog.at_level(logging.INFO, logger="app.timing"):
            with TimedOperation(log_event="test_event"):
                pass
        assert any("test_event completed" in r.message for r in caplog.records)
        log_record = next(r for r in caplog.records if "test_event" in r.message)
        assert log_record.event == "test_event"
        assert log_record.duration_ms >= 0
        assert log_record.success is True

    def test_exception_propagation(self):
        """Exceptions inside the block must propagate, success=False recorded."""
        timer = TimedOperation()
        with pytest.raises(ValueError, match="boom"):
            with timer:
                raise ValueError("boom")
        assert timer.success is False
        assert timer.duration_ms >= 0

    def test_error_captured_in_log(self, caplog):
        """When an exception occurs, the error field should be in the log."""
        with caplog.at_level(logging.INFO, logger="app.timing"):
            try:
                with TimedOperation(log_event="fail_event"):
                    raise RuntimeError("oops")
            except RuntimeError:
                pass
        log_record = next(r for r in caplog.records if "fail_event" in r.message)
        assert log_record.error == "oops"
        assert log_record.success is False

    def test_histogram_none_skips_metric(self):
        """histogram=None should not crash."""
        with TimedOperation(histogram=None) as t:
            pass
        assert t.success is True

    def test_prometheus_failure_guarded(self):
        """If histogram.observe() raises, the operation still completes."""
        mock_hist = MagicMock()
        mock_hist.labels.return_value.observe.side_effect = RuntimeError("prom down")
        # Should not raise
        with TimedOperation(histogram=mock_hist, labels={"x": "1"}) as t:
            pass
        assert t.success is True
        assert t.duration_ms >= 0

    def test_log_failure_guarded(self):
        """If logger.log raises, the operation still completes."""
        with patch("app.timing.logger") as mock_logger:
            mock_logger.log.side_effect = RuntimeError("log broken")
            mock_logger.warning = MagicMock()  # allow warning to pass
            with TimedOperation() as t:
                pass
            assert t.success is True

    def test_log_extras_merged(self, caplog):
        """Custom log_extras should appear in the log record."""
        with caplog.at_level(logging.INFO, logger="app.timing"):
            with TimedOperation(log_event="extra_test", log_extras={"lab_id": "abc123"}):
                pass
        log_record = next(r for r in caplog.records if "extra_test" in r.message)
        assert log_record.lab_id == "abc123"

    def test_duration_accuracy(self):
        """Sleep 0.1s should produce roughly 80-250ms."""
        with TimedOperation() as t:
            time.sleep(0.1)
        assert 80 <= t.duration_ms <= 250

    def test_nested_timers(self):
        """Two nested timers should record independently."""
        with TimedOperation() as outer:
            time.sleep(0.05)
            with TimedOperation() as inner:
                time.sleep(0.05)
        assert inner.duration_ms < outer.duration_ms
        assert inner.duration_ms >= 40


class TestAsyncTimedOperation:
    """Tests for the async variant."""

    @pytest.mark.asyncio
    async def test_async_variant_basic(self):
        """Basic async operation should record duration."""
        async with AsyncTimedOperation() as t:
            await asyncio.sleep(0.01)
        assert t.duration_ms > 0
        assert t.success is True

    @pytest.mark.asyncio
    async def test_async_exception_handling(self):
        """Async variant should capture exceptions and not suppress them."""
        timer = AsyncTimedOperation()
        with pytest.raises(ValueError, match="async boom"):
            async with timer:
                raise ValueError("async boom")
        assert timer.success is False
        assert timer.duration_ms >= 0
