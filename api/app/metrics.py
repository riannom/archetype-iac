"""Prometheus metrics for Archetype.

This module provides Prometheus metrics for monitoring the Archetype platform:
- Node metrics (total, ready, by state)
- Job metrics (total, duration, by status)
- Agent metrics (online count, health)
- Enforcement metrics (actions, failures)

Usage:
    from app.metrics import (
        nodes_total, nodes_ready, jobs_total, job_duration,
        agents_online, enforcement_actions, enforcement_failures,
        update_node_metrics, update_agent_metrics
    )

The /metrics endpoint exposes these in Prometheus format.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

try:
    from prometheus_client import (
        Counter,
        Gauge,
        Histogram,
        generate_latest,
        CONTENT_TYPE_LATEST,
        REGISTRY,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# --- Node Metrics ---

if PROMETHEUS_AVAILABLE:
    nodes_total = Gauge(
        "archetype_nodes_total",
        "Total number of nodes",
        ["lab_id", "state"],
    )

    nodes_ready = Gauge(
        "archetype_nodes_ready",
        "Number of nodes in ready state",
        ["lab_id"],
    )

    nodes_by_host = Gauge(
        "archetype_nodes_by_host",
        "Number of nodes per host",
        ["host_id", "host_name"],
    )

    # --- Job Metrics ---

    jobs_total = Counter(
        "archetype_jobs_total",
        "Total number of jobs created",
        ["action", "status"],
    )

    job_duration = Histogram(
        "archetype_job_duration_seconds",
        "Job execution duration in seconds",
        ["action"],
        buckets=(5, 10, 30, 60, 120, 300, 600, 900, 1200, 1800, float("inf")),
    )

    jobs_active = Gauge(
        "archetype_jobs_active",
        "Number of currently active jobs",
        ["action"],
    )

    # --- Agent Metrics ---

    agents_online = Gauge(
        "archetype_agents_online",
        "Number of online agents",
    )

    agents_total = Gauge(
        "archetype_agents_total",
        "Total number of registered agents",
    )

    agent_cpu_percent = Gauge(
        "archetype_agent_cpu_percent",
        "Agent CPU usage percentage",
        ["host_id", "host_name"],
    )

    agent_memory_percent = Gauge(
        "archetype_agent_memory_percent",
        "Agent memory usage percentage",
        ["host_id", "host_name"],
    )

    agent_containers_running = Gauge(
        "archetype_agent_containers_running",
        "Number of running containers on agent",
        ["host_id", "host_name"],
    )

    agent_vms_running = Gauge(
        "archetype_agent_vms_running",
        "Number of running VMs on agent",
        ["host_id", "host_name"],
    )

    # --- Enforcement Metrics ---

    enforcement_actions = Counter(
        "archetype_enforcement_total",
        "Total enforcement actions taken",
        ["result"],  # success, failed, skipped
    )

    enforcement_failures = Counter(
        "archetype_enforcement_failures_total",
        "Number of nodes that exceeded max enforcement retries",
    )

    enforcement_pending = Gauge(
        "archetype_enforcement_pending",
        "Number of nodes with pending enforcement",
    )

    # --- Lab Metrics ---

    labs_total = Gauge(
        "archetype_labs_total",
        "Total number of labs",
        ["state"],
    )

    labs_active = Gauge(
        "archetype_labs_active",
        "Number of labs in running state",
    )

    # --- Database Metrics ---

    db_connections_idle_in_transaction = Gauge(
        "archetype_db_idle_in_transaction",
        "Number of database connections stuck idle in transaction",
    )

    db_connections_total = Gauge(
        "archetype_db_connections_total",
        "Total active database connections",
        ["state"],
    )

else:
    # Dummy implementations when prometheus_client is not installed
    class DummyMetric:
        def labels(self, *args, **kwargs):
            return self
        def inc(self, amount=1):
            pass
        def dec(self, amount=1):
            pass
        def set(self, value):
            pass
        def observe(self, value):
            pass

    nodes_total = DummyMetric()
    nodes_ready = DummyMetric()
    nodes_by_host = DummyMetric()
    jobs_total = DummyMetric()
    job_duration = DummyMetric()
    jobs_active = DummyMetric()
    agents_online = DummyMetric()
    agents_total = DummyMetric()
    agent_cpu_percent = DummyMetric()
    agent_memory_percent = DummyMetric()
    agent_containers_running = DummyMetric()
    agent_vms_running = DummyMetric()
    enforcement_actions = DummyMetric()
    enforcement_failures = DummyMetric()
    enforcement_pending = DummyMetric()
    labs_total = DummyMetric()
    labs_active = DummyMetric()
    db_connections_idle_in_transaction = DummyMetric()
    db_connections_total = DummyMetric()


def update_node_metrics(session: "Session") -> None:
    """Update node-related metrics from database.

    Uses SQL GROUP BY to aggregate counts in the database instead of
    loading all rows into Python.
    """
    if not PROMETHEUS_AVAILABLE:
        return

    try:
        from sqlalchemy import func
        from app import models

        # Clear existing lab-specific metrics
        nodes_total._metrics.clear()
        nodes_ready._metrics.clear()

        # Aggregate node counts by lab and state in SQL
        state_counts = (
            session.query(
                models.NodeState.lab_id,
                models.NodeState.actual_state,
                func.count(),
            )
            .group_by(models.NodeState.lab_id, models.NodeState.actual_state)
            .all()
        )
        for lab_id, state, count in state_counts:
            nodes_total.labels(lab_id=lab_id, state=state).set(count)

        # Aggregate ready counts by lab in SQL
        ready_counts = (
            session.query(
                models.NodeState.lab_id,
                func.count(),
            )
            .filter(models.NodeState.is_ready)
            .group_by(models.NodeState.lab_id)
            .all()
        )
        for lab_id, count in ready_counts:
            nodes_ready.labels(lab_id=lab_id).set(count)

    except Exception as e:
        logger.warning(f"Error updating node metrics: {e}")


def update_agent_metrics(session: "Session") -> None:
    """Update agent-related metrics from database.

    Call this periodically to keep agent metrics current.
    """
    if not PROMETHEUS_AVAILABLE:
        return

    try:
        import json
        from app import models
        from app import agent_client

        hosts = session.query(models.Host).all()

        online_count = 0
        total_count = len(hosts)

        # Clear host-specific metrics
        agent_cpu_percent._metrics.clear()
        agent_memory_percent._metrics.clear()
        agent_containers_running._metrics.clear()
        agent_vms_running._metrics.clear()
        nodes_by_host._metrics.clear()

        for host in hosts:
            if agent_client.is_agent_online(host):
                online_count += 1

            # Parse resource usage
            try:
                usage = json.loads(host.resource_usage) if host.resource_usage else {}
            except json.JSONDecodeError:
                usage = {}

            host_name = host.name or host.id

            if "cpu_percent" in usage:
                agent_cpu_percent.labels(
                    host_id=host.id, host_name=host_name
                ).set(usage["cpu_percent"])

            if "memory_percent" in usage:
                agent_memory_percent.labels(
                    host_id=host.id, host_name=host_name
                ).set(usage["memory_percent"])

            if "containers_running" in usage:
                agent_containers_running.labels(
                    host_id=host.id, host_name=host_name
                ).set(usage["containers_running"])

            if "vms_running" in usage:
                agent_vms_running.labels(
                    host_id=host.id, host_name=host_name
                ).set(usage["vms_running"])

        # Count nodes per host using SQL GROUP BY
        from sqlalchemy import func
        placement_counts = (
            session.query(
                models.NodePlacement.host_id,
                func.count(),
            )
            .group_by(models.NodePlacement.host_id)
            .all()
        )
        host_node_counts = {host_id: count for host_id, count in placement_counts}

        for host in hosts:
            host_name = host.name or host.id
            count = host_node_counts.get(host.id, 0)
            nodes_by_host.labels(host_id=host.id, host_name=host_name).set(count)

        agents_online.set(online_count)
        agents_total.set(total_count)

    except Exception as e:
        logger.warning(f"Error updating agent metrics: {e}")


def update_job_metrics(session: "Session") -> None:
    """Update job-related metrics from database.

    Uses SQL GROUP BY to count active jobs by action.
    """
    if not PROMETHEUS_AVAILABLE:
        return

    try:
        from sqlalchemy import func
        from app import models

        # Clear action-specific active job counts
        jobs_active._metrics.clear()

        # Count active jobs by action in SQL
        action_counts = (
            session.query(
                models.Job.action,
                func.count(),
            )
            .filter(models.Job.status.in_(["queued", "running"]))
            .group_by(models.Job.action)
            .all()
        )
        for action, count in action_counts:
            # Normalize action (strip sub-action after colon)
            jobs_active.labels(action=action.split(":")[0]).set(count)

    except Exception as e:
        logger.warning(f"Error updating job metrics: {e}")


def update_lab_metrics(session: "Session") -> None:
    """Update lab-related metrics from database.

    Uses SQL GROUP BY to count labs by state.
    """
    if not PROMETHEUS_AVAILABLE:
        return

    try:
        from sqlalchemy import func
        from app import models

        labs_total._metrics.clear()

        state_counts = (
            session.query(
                models.Lab.state,
                func.count(),
            )
            .group_by(models.Lab.state)
            .all()
        )

        active_count = 0
        for state, count in state_counts:
            labs_total.labels(state=state).set(count)
            if state == "running":
                active_count = count

        labs_active.set(active_count)

    except Exception as e:
        logger.warning(f"Error updating lab metrics: {e}")


def update_enforcement_metrics(session: "Session") -> None:
    """Update enforcement-related metrics from database."""
    if not PROMETHEUS_AVAILABLE:
        return

    try:
        from app import models

        # Count nodes with pending enforcement (desired != actual)
        pending = (
            session.query(models.NodeState)
            .filter(models.NodeState.desired_state != models.NodeState.actual_state)
            .count()
        )
        enforcement_pending.set(pending)

    except Exception as e:
        logger.warning(f"Error updating enforcement metrics: {e}")


def update_db_metrics(session: "Session") -> None:
    """Update database connection health metrics.

    Queries pg_stat_activity to track connection states, especially
    idle-in-transaction connections which can indicate connection leaks.
    """
    if not PROMETHEUS_AVAILABLE:
        return

    try:
        from sqlalchemy import text

        # Query PostgreSQL for connection states
        result = session.execute(text("""
            SELECT state, count(*)
            FROM pg_stat_activity
            WHERE datname = current_database()
            GROUP BY state
        """))

        db_connections_total._metrics.clear()
        idle_in_transaction_count = 0

        for row in result:
            state, count = row
            if state:
                db_connections_total.labels(state=state).set(count)
                if state == "idle in transaction":
                    idle_in_transaction_count = count

        db_connections_idle_in_transaction.set(idle_in_transaction_count)

        # Log warning if idle-in-transaction connections are accumulating
        if idle_in_transaction_count > 2:
            logger.warning(
                f"Database health: {idle_in_transaction_count} idle-in-transaction "
                "connections detected - possible connection leak"
            )

    except Exception as e:
        logger.warning(f"Error updating db metrics: {e}")


def update_all_metrics(session: "Session") -> None:
    """Update all metrics from database.

    Call this periodically in a background task.
    """
    update_node_metrics(session)
    update_agent_metrics(session)
    update_job_metrics(session)
    update_lab_metrics(session)
    update_enforcement_metrics(session)
    update_db_metrics(session)


def get_metrics() -> tuple[bytes, str]:
    """Generate Prometheus metrics output.

    Returns:
        Tuple of (metrics_bytes, content_type)
    """
    if not PROMETHEUS_AVAILABLE:
        return b"# Prometheus client not installed\n", "text/plain"

    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def record_job_started(action: str) -> None:
    """Record a job start event."""
    if not PROMETHEUS_AVAILABLE:
        return
    jobs_total.labels(action=action, status="started").inc()


def record_job_completed(action: str, duration_seconds: float) -> None:
    """Record a job completion event with duration."""
    if not PROMETHEUS_AVAILABLE:
        return
    jobs_total.labels(action=action, status="completed").inc()
    job_duration.labels(action=action).observe(duration_seconds)


def record_job_failed(action: str, duration_seconds: float | None = None) -> None:
    """Record a job failure event."""
    if not PROMETHEUS_AVAILABLE:
        return
    jobs_total.labels(action=action, status="failed").inc()
    if duration_seconds is not None:
        job_duration.labels(action=action).observe(duration_seconds)


def record_enforcement_action(result: str) -> None:
    """Record an enforcement action.

    Args:
        result: One of 'success', 'failed', 'skipped'
    """
    if not PROMETHEUS_AVAILABLE:
        return
    enforcement_actions.labels(result=result).inc()


def record_enforcement_exhausted() -> None:
    """Record a node that exceeded max enforcement retries."""
    if not PROMETHEUS_AVAILABLE:
        return
    enforcement_failures.inc()
