"""Prometheus metrics for Archetype Agent.

Exposes per-host performance metrics for Docker API calls, OVS operations,
and node lifecycle operations. The /metrics endpoint serves these in
Prometheus exposition format.
"""
from __future__ import annotations

import logging

try:
    from prometheus_client import (
        Counter,
        Histogram,
        generate_latest,
        CONTENT_TYPE_LATEST,
        REGISTRY,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

logger = logging.getLogger(__name__)


class DummyMetric:
    """No-op metric when prometheus_client is not installed."""
    def labels(self, *args, **kwargs):
        return self
    def inc(self, amount=1):
        pass
    def observe(self, value):
        pass


if PROMETHEUS_AVAILABLE:
    docker_api_duration = Histogram(
        "archetype_agent_docker_api_seconds",
        "Duration of Docker API calls",
        ["operation", "status"],
        buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, float("inf")),
    )

    ovs_operation_duration = Histogram(
        "archetype_agent_ovs_operation_seconds",
        "Duration of OVS operations",
        ["operation", "status"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, float("inf")),
    )

    node_operation_duration = Histogram(
        "archetype_agent_node_operation_seconds",
        "Duration of node lifecycle operations",
        ["operation", "status"],
        buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, float("inf")),
    )

    node_operation_errors = Counter(
        "archetype_agent_node_operation_errors_total",
        "Total node operation errors",
        ["operation"],
    )
else:
    docker_api_duration = DummyMetric()
    ovs_operation_duration = DummyMetric()
    node_operation_duration = DummyMetric()
    node_operation_errors = DummyMetric()


def get_metrics() -> tuple[bytes, str]:
    """Generate Prometheus metrics output."""
    if not PROMETHEUS_AVAILABLE:
        return b"# prometheus_client not installed\n", "text/plain"
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
