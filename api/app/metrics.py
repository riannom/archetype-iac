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
import re
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

    job_queue_wait = Histogram(
        "archetype_job_queue_wait_seconds",
        "Time jobs spend queued before execution",
        ["action"],
        buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, float("inf")),
    )

    job_failures = Counter(
        "archetype_job_failures_total",
        "Total failed jobs categorized by reason",
        ["action", "reason"],
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

    # --- NLM Phase Timing ---

    nlm_phase_duration = Histogram(
        "archetype_nlm_phase_duration_seconds",
        "Duration of NLM lifecycle phases",
        ["phase", "device_type", "status"],
        buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, float("inf")),
    )

    # --- Agent Operation Timing (API-measured round-trip) ---

    agent_operation_duration = Histogram(
        "archetype_agent_operation_duration_seconds",
        "API-to-agent operation round-trip duration",
        ["operation", "host_id", "status"],
        buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, float("inf")),
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

    # --- Link Operational State Metrics ---

    link_oper_transitions = Counter(
        "archetype_link_oper_transitions_total",
        "Total link endpoint operational-state transitions",
        ["endpoint", "old_state", "new_state", "reason", "is_cross_host"],
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

    # --- Queue Metrics ---

    job_queue_depth = Gauge(
        "archetype_job_queue_depth",
        "Number of jobs currently in the RQ queue",
    )

    # --- Reconciliation Metrics ---

    reconciliation_cycle_duration = Histogram(
        "archetype_reconciliation_cycle_seconds",
        "Duration of a full reconciliation cycle",
        buckets=(0.5, 1, 2, 5, 10, 30, 60, float("inf")),
    )
    reconciliation_labs_checked = Counter(
        "archetype_reconciliation_labs_checked_total",
        "Total labs checked during reconciliation cycles",
    )
    reconciliation_state_changes = Counter(
        "archetype_reconciliation_state_changes_total",
        "Total node state changes detected during reconciliation",
    )

    # --- State Flap Detection ---

    node_state_transitions = Counter(
        "archetype_node_state_transitions_total",
        "Total node state transitions",
        ["transition_type"],
    )

    # --- Circuit Breaker Metrics ---

    circuit_breaker_state = Gauge(
        "archetype_circuit_breaker_state",
        "Circuit breaker state (0=closed, 1=half-open, 2=open)",
        ["handler_type"],
    )

    # --- Broadcast Metrics ---

    broadcast_messages = Counter(
        "archetype_broadcast_messages_total",
        "Total broadcast messages published",
        ["message_type"],
    )
    broadcast_failures = Counter(
        "archetype_broadcast_failures_total",
        "Total broadcast publish failures",
        ["message_type"],
    )

    # --- Enforcement Timing ---

    enforcement_operation_duration = Histogram(
        "archetype_enforcement_operation_seconds",
        "Duration of enforcement operations",
        buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, float("inf")),
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
    job_queue_wait = DummyMetric()
    job_failures = DummyMetric()
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
    nlm_phase_duration = DummyMetric()
    agent_operation_duration = DummyMetric()
    labs_total = DummyMetric()
    labs_active = DummyMetric()
    link_oper_transitions = DummyMetric()
    db_connections_idle_in_transaction = DummyMetric()
    db_connections_total = DummyMetric()
    job_queue_depth = DummyMetric()
    reconciliation_cycle_duration = DummyMetric()
    reconciliation_labs_checked = DummyMetric()
    reconciliation_state_changes = DummyMetric()
    node_state_transitions = DummyMetric()
    circuit_breaker_state = DummyMetric()
    broadcast_messages = DummyMetric()
    broadcast_failures = DummyMetric()
    enforcement_operation_duration = DummyMetric()


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


def update_queue_metrics() -> None:
    """Update RQ queue depth metric."""
    if not PROMETHEUS_AVAILABLE:
        return

    try:
        from app.db import get_redis
        r = get_redis()
        depth = r.llen("rq:queue:archetype")
        job_queue_depth.set(depth)
    except Exception as e:
        logger.warning(f"Error updating queue metrics: {e}")


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
    update_queue_metrics()


def get_metrics() -> tuple[bytes, str]:
    """Generate Prometheus metrics output.

    Returns:
        Tuple of (metrics_bytes, content_type)
    """
    if not PROMETHEUS_AVAILABLE:
        return b"# Prometheus client not installed\n", "text/plain"

    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def _normalize_reason_label(value: str) -> str:
    lowered = value.strip().lower()
    lowered = re.sub(r"[^a-z0-9_]+", "_", lowered)
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    if not lowered:
        return "unknown"
    return lowered[:64]


def _normalize_action_label(action: str | None) -> str:
    """Normalize verbose action strings to bounded metric labels."""
    if not action:
        return "unknown"
    action = action.strip().lower()
    if not action:
        return "unknown"
    if action.startswith("sync:"):
        return "sync"
    if action.startswith("node:"):
        return "node"
    if action.startswith("links:"):
        return "links"
    return action.split(":")[0]


def infer_job_failure_reason(message: str | None) -> str:
    """Infer a bounded failure-reason label from log/error text."""
    if not message:
        return "unknown"

    text = message.lower()
    checks: list[tuple[str, str]] = [
        ("preflight connectivity check failed", "preflight_connectivity_failed"),
        ("preflight image check failed", "preflight_image_check_failed"),
        ("preflight image validation failed", "preflight_image_validation_failed"),
        ("job timed out after maximum retries", "timeout_retries_exhausted"),
        ("timed out after 1200s", "timeout_1200s"),
        ("timed out after 300s", "timeout_300s"),
        ("timed out after", "timeout"),
        ("retry failed: no healthy agent available", "no_healthy_agent"),
        ("no healthy agent available", "no_healthy_agent"),
        ("agent became unavailable", "agent_unavailable"),
        ("agent unavailable", "agent_unavailable"),
        ("connection refused", "agent_connection_refused"),
        ("name or service not known", "agent_dns_failure"),
        ("host unreachable", "agent_unreachable"),
        ("network is unreachable", "agent_unreachable"),
        ("cannot deploy - explicit host assignments failed", "host_assignment_failed"),
        ("missing or unhealthy agents for hosts", "host_assignment_failed"),
        ("assigned host", "host_assignment_offline"),
        ("no image found", "missing_image"),
        ("docker image not found", "missing_image"),
        ("required images not available on agent", "missing_image"),
        ("upload/sync required images", "missing_image"),
        ("parent job completed or missing", "orphaned_child"),
        ("insufficient resources", "insufficient_resources"),
        ("capacity", "capacity_check_failed"),
        ("link setup failed", "link_setup_failed"),
        ("deployment failed on one or more hosts", "deploy_partial_failure"),
        ("rollback failed", "deploy_rollback_failed"),
        ("per-link tunnel creation failed", "link_tunnel_creation_failed"),
        ("could not find ovs port", "ovs_port_missing"),
        ("stale - cleared after api restart", "stale_after_restart"),
        ("docker api error", "docker_api_error"),
        ("domain not found", "libvirt_domain_not_found"),
        ("unsupported configuration", "libvirt_unsupported_configuration"),
        ("libvirt error", "libvirt_error"),
        ("completed with 1 error", "partial_failure"),
        ("completed with ", "partial_failure"),
        ("container creation failed", "container_create_failed"),
        ("unknown action", "unknown_action"),
        ("job execution failed on agent", "agent_job_error"),
        ("unexpected error during job execution", "unexpected_job_error"),
        ("failed to create node", "create_node_failed"),
        ("failed to start node", "start_node_failed"),
        ("failed to stop node", "stop_node_failed"),
        ("failed to destroy node", "destroy_node_failed"),
        ("missing or unhealthy agents for hosts", "host_assignment_failed"),
        ("no agents found for multi-host destroy", "no_agents_for_multihost_destroy"),
    ]
    for needle, reason in checks:
        if needle in text:
            return reason
    return "unknown"


def record_job_started(action: str, queue_wait_seconds: float | None = None) -> None:
    """Record a job start event."""
    if not PROMETHEUS_AVAILABLE:
        return
    normalized_action = _normalize_action_label(action)
    jobs_total.labels(action=normalized_action, status="started").inc()
    if queue_wait_seconds is not None:
        job_queue_wait.labels(action=normalized_action).observe(max(0.0, queue_wait_seconds))


def record_job_completed(action: str, duration_seconds: float) -> None:
    """Record a job completion event with duration."""
    if not PROMETHEUS_AVAILABLE:
        return
    normalized_action = _normalize_action_label(action)
    jobs_total.labels(action=normalized_action, status="completed").inc()
    job_duration.labels(action=normalized_action).observe(duration_seconds)


def record_job_failed(
    action: str,
    duration_seconds: float | None = None,
    reason: str | None = None,
    failure_message: str | None = None,
) -> None:
    """Record a job failure event."""
    if not PROMETHEUS_AVAILABLE:
        return
    normalized_action = _normalize_action_label(action)
    jobs_total.labels(action=normalized_action, status="failed").inc()
    resolved_reason = reason or infer_job_failure_reason(failure_message)
    job_failures.labels(action=normalized_action, reason=_normalize_reason_label(resolved_reason)).inc()
    if duration_seconds is not None:
        job_duration.labels(action=normalized_action).observe(duration_seconds)


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


def record_link_oper_transition(
    endpoint: str,
    old_state: str | None,
    new_state: str | None,
    reason: str | None = None,
    is_cross_host: bool = False,
) -> None:
    """Record a link endpoint operational-state transition."""
    if not PROMETHEUS_AVAILABLE:
        return
    endpoint_label = "source" if endpoint == "source" else "target"
    old_label = _normalize_reason_label(old_state or "unknown")
    new_label = _normalize_reason_label(new_state or "unknown")
    reason_label = _normalize_reason_label(reason or "none")
    link_oper_transitions.labels(
        endpoint=endpoint_label,
        old_state=old_label,
        new_state=new_label,
        reason=reason_label,
        is_cross_host="true" if is_cross_host else "false",
    ).inc()


def record_reconciliation_cycle(
    duration: float, labs_checked: int, state_changes: int
) -> None:
    """Record metrics from a reconciliation cycle."""
    if not PROMETHEUS_AVAILABLE:
        return
    reconciliation_cycle_duration.observe(duration)
    reconciliation_labs_checked.inc(labs_checked)
    if state_changes:
        reconciliation_state_changes.inc(state_changes)


def record_node_state_transition(transition_type: str) -> None:
    """Record a node state transition for flap detection."""
    if not PROMETHEUS_AVAILABLE:
        return
    node_state_transitions.labels(transition_type=transition_type).inc()


def record_broadcast(message_type: str, success: bool) -> None:
    """Record a broadcast publish attempt."""
    if not PROMETHEUS_AVAILABLE:
        return
    if success:
        broadcast_messages.labels(message_type=message_type).inc()
    else:
        broadcast_failures.labels(message_type=message_type).inc()


def record_enforcement_duration(duration: float) -> None:
    """Record the duration of an enforcement cycle."""
    if not PROMETHEUS_AVAILABLE:
        return
    enforcement_operation_duration.observe(duration)
