"""Tests for agent/metrics.py — DummyMetric, get_metrics(), PROMETHEUS_AVAILABLE."""
from __future__ import annotations

from unittest.mock import patch

from agent.metrics import DummyMetric, get_metrics


# ---------------------------------------------------------------------------
# DummyMetric
# ---------------------------------------------------------------------------


def test_dummy_metric_labels_returns_self() -> None:
    """labels() returns self for chaining."""
    m = DummyMetric()
    result = m.labels(operation="create", status="ok")
    assert result is m


def test_dummy_metric_inc_noop() -> None:
    """inc() is a no-op that doesn't raise."""
    m = DummyMetric()
    m.inc()
    m.inc(amount=5)


def test_dummy_metric_observe_noop() -> None:
    """observe() is a no-op that doesn't raise."""
    m = DummyMetric()
    m.observe(1.5)
    m.observe(0.0)


def test_dummy_metric_chaining() -> None:
    """Full chaining pattern: labels().inc() and labels().observe()."""
    m = DummyMetric()
    m.labels(operation="deploy", status="success").inc()
    m.labels(operation="deploy", status="success").observe(2.5)


# ---------------------------------------------------------------------------
# get_metrics()
# ---------------------------------------------------------------------------


def test_get_metrics_without_prometheus() -> None:
    """When prometheus is not available, returns plain text fallback."""
    with patch("agent.metrics.PROMETHEUS_AVAILABLE", False):
        data, content_type = get_metrics()
        assert data == b"# prometheus_client not installed\n"
        assert content_type == "text/plain"


def test_get_metrics_with_prometheus() -> None:
    """When prometheus is available, returns generated metrics."""
    import agent.metrics as metrics_mod

    if not metrics_mod.PROMETHEUS_AVAILABLE:
        # Skip if prometheus_client is not actually installed
        return

    data, content_type = get_metrics()
    assert isinstance(data, bytes)
    assert len(data) > 0
    # Prometheus content type is a specific format
    assert "text/" in content_type


# ---------------------------------------------------------------------------
# Module-level metric instances
# ---------------------------------------------------------------------------


def test_module_metrics_are_functional() -> None:
    """Module-level metric instances work regardless of prometheus availability."""
    import agent.metrics as m

    # These should not raise regardless of PROMETHEUS_AVAILABLE
    m.docker_api_duration.labels(operation="inspect", status="ok").observe(0.1)
    m.ovs_operation_duration.labels(operation="add-port", status="ok").observe(0.05)
    m.node_operation_duration.labels(operation="create", status="ok").observe(1.0)
    m.node_operation_errors.labels(operation="create").inc()
    m.runtime_identity_skips.labels(
        resource_type="libvirt_domain",
        operation="recover_stale_network",
        reason="missing_runtime_metadata",
    ).inc()
