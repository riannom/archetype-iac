"""Timing utilities for operation performance measurement.

Provides context managers that record duration to both Prometheus histograms
and structured JSON logs. Exception-safe: metric/logging failures never break
the wrapped operation.

Usage:
    from app.timing import TimedOperation, AsyncTimedOperation
    from app.metrics import nlm_phase_duration

    with TimedOperation(
        histogram=nlm_phase_duration,
        labels={"phase": "container_deploy", "device_type": "ceos"},
        log_event="nlm_phase",
        log_extras={"lab_id": lab.id},
    ):
        do_work()

    async with AsyncTimedOperation(
        histogram=agent_operation_duration,
        labels={"operation": "start_node", "host_id": agent.id},
        log_event="agent_operation",
    ):
        await call_agent()
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class TimedOperation:
    """Synchronous context manager for timing operations.

    Records duration to a Prometheus histogram and emits a structured log.
    Both metric recording and logging are exception-guarded so failures
    in instrumentation never break the wrapped operation.
    """

    def __init__(
        self,
        *,
        histogram=None,
        labels: dict[str, str] | None = None,
        log_event: str = "timed_operation",
        log_extras: dict | None = None,
        log_level: int = logging.INFO,
    ):
        self.histogram = histogram
        self.labels = labels or {}
        self.log_event = log_event
        self.log_extras = log_extras or {}
        self.log_level = log_level
        self.duration_ms: int = 0
        self.success: bool = True
        self._start: float = 0.0

    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = time.monotonic() - self._start
        self.duration_ms = int(elapsed * 1000)
        self.success = exc_type is None

        # Record to Prometheus histogram (guarded)
        try:
            if self.histogram is not None:
                metric_labels = dict(self.labels)
                if metric_labels.get("status") in ("auto", "__auto__"):
                    metric_labels["status"] = "success" if self.success else "error"
                self.histogram.labels(**metric_labels).observe(elapsed)
        except Exception as e:
            logger.warning("Failed to record metric: %s", e)

        # Emit structured log (guarded)
        try:
            extra = {
                "event": self.log_event,
                "duration_ms": self.duration_ms,
                "success": self.success,
                **self.labels,
                **self.log_extras,
            }
            if exc_type is not None:
                extra["error"] = str(exc_val)
            logger.log(self.log_level, "%s completed", self.log_event, extra=extra)
        except Exception:
            pass

        return False  # Don't suppress exceptions


class AsyncTimedOperation:
    """Async context manager for timing operations.

    Same semantics as TimedOperation but for async with.
    """

    def __init__(
        self,
        *,
        histogram=None,
        labels: dict[str, str] | None = None,
        log_event: str = "timed_operation",
        log_extras: dict | None = None,
        log_level: int = logging.INFO,
    ):
        self.histogram = histogram
        self.labels = labels or {}
        self.log_event = log_event
        self.log_extras = log_extras or {}
        self.log_level = log_level
        self.duration_ms: int = 0
        self.success: bool = True
        self._start: float = 0.0

    async def __aenter__(self):
        self._start = time.monotonic()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        elapsed = time.monotonic() - self._start
        self.duration_ms = int(elapsed * 1000)
        self.success = exc_type is None

        try:
            if self.histogram is not None:
                metric_labels = dict(self.labels)
                if metric_labels.get("status") in ("auto", "__auto__"):
                    metric_labels["status"] = "success" if self.success else "error"
                self.histogram.labels(**metric_labels).observe(elapsed)
        except Exception as e:
            logger.warning("Failed to record metric: %s", e)

        try:
            extra = {
                "event": self.log_event,
                "duration_ms": self.duration_ms,
                "success": self.success,
                **self.labels,
                **self.log_extras,
            }
            if exc_type is not None:
                extra["error"] = str(exc_val)
            logger.log(self.log_level, "%s completed", self.log_event, extra=extra)
        except Exception:
            pass

        return False
