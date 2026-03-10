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


# ---------------------------------------------------------------------------
# DummyMetric & factory helpers
# ---------------------------------------------------------------------------


class DummyMetric:
    """No-op metric used when prometheus_client is not installed."""

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


_DUMMY = DummyMetric()


def _make_gauge(name, description, labels=None):
    if PROMETHEUS_AVAILABLE:
        return Gauge(name, description, labels or [])
    return _DUMMY


def _make_counter(name, description, labels=None):
    if PROMETHEUS_AVAILABLE:
        return Counter(name, description, labels or [])
    return _DUMMY


def _make_histogram(name, description, labels=None, buckets=None):
    if PROMETHEUS_AVAILABLE:
        return Histogram(name, description, labels or [], buckets=buckets or Histogram.DEFAULT_BUCKETS)
    return _DUMMY


# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

# --- Node Metrics ---

nodes_total = _make_gauge(
    "archetype_nodes_total", "Total number of nodes", ["lab_id", "state"],
)
nodes_ready = _make_gauge(
    "archetype_nodes_ready", "Number of nodes in ready state", ["lab_id"],
)
nodes_by_host = _make_gauge(
    "archetype_nodes_by_host", "Number of nodes per host", ["host_id", "host_name"],
)

# --- Job Metrics ---

jobs_total = _make_counter(
    "archetype_jobs_total", "Total number of jobs created", ["action", "status"],
)
job_duration = _make_histogram(
    "archetype_job_duration_seconds", "Job execution duration in seconds",
    ["action"],
    buckets=(5, 10, 30, 60, 120, 300, 600, 900, 1200, 1800, float("inf")),
)
job_queue_wait = _make_histogram(
    "archetype_job_queue_wait_seconds", "Time jobs spend queued before execution",
    ["action"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, float("inf")),
)
job_failures = _make_counter(
    "archetype_job_failures_total", "Total failed jobs categorized by reason",
    ["action", "reason"],
)
jobs_active = _make_gauge(
    "archetype_jobs_active", "Number of currently active jobs", ["action"],
)

# --- Agent Metrics ---

agents_online = _make_gauge("archetype_agents_online", "Number of online agents")
agents_total = _make_gauge("archetype_agents_total", "Total number of registered agents")
agent_stale_images = _make_gauge(
    "archetype_agent_stale_images",
    "Number of stale image artifacts detected on an agent",
    ["host_id", "host_name"],
)

# --- Enforcement Metrics ---

enforcement_actions = _make_counter(
    "archetype_enforcement_total", "Total enforcement actions taken", ["result"],
)
enforcement_failures = _make_counter(
    "archetype_enforcement_failures_total",
    "Number of nodes that exceeded max enforcement retries",
)
enforcement_pending = _make_gauge(
    "archetype_enforcement_pending", "Number of nodes with pending enforcement",
)
enforcement_skip_reasons = _make_counter(
    "archetype_enforcement_skips_total", "Enforcement skips by reason", ["reason"],
)

# --- NLM Phase Timing ---

nlm_phase_duration = _make_histogram(
    "archetype_nlm_phase_duration_seconds", "Duration of NLM lifecycle phases",
    ["phase", "device_type", "status"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, float("inf")),
)

# --- Agent Operation Timing (API-measured round-trip) ---

agent_operation_duration = _make_histogram(
    "archetype_agent_operation_duration_seconds",
    "API-to-agent operation round-trip duration",
    ["operation", "host_id", "status"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, float("inf")),
)

# --- Lab Metrics ---

labs_total = _make_gauge("archetype_labs_total", "Total number of labs", ["state"])
labs_active = _make_gauge("archetype_labs_active", "Number of labs in running state")

# --- Link Operational State Metrics ---

link_oper_transitions = _make_counter(
    "archetype_link_oper_transitions_total",
    "Total link endpoint operational-state transitions",
    ["endpoint", "old_state", "new_state", "reason", "is_cross_host"],
)
link_endpoint_reservations_total = _make_gauge(
    "archetype_link_endpoint_reservations_total",
    "Total link endpoint reservation rows",
)
link_endpoint_reservation_missing = _make_gauge(
    "archetype_link_endpoint_reservation_missing",
    "Expected desired-up endpoint reservations missing from DB",
)
link_endpoint_reservation_orphaned = _make_gauge(
    "archetype_link_endpoint_reservation_orphaned",
    "Reservation rows not tied to desired-up links",
)
link_endpoint_reservation_conflicts = _make_gauge(
    "archetype_link_endpoint_reservation_conflicts",
    "Endpoints reserved by more than one link",
)

# --- Database Metrics ---

db_connections_idle_in_transaction = _make_gauge(
    "archetype_db_idle_in_transaction",
    "Number of database connections stuck idle in transaction",
)
db_connections_total = _make_gauge(
    "archetype_db_connections_total", "Total active database connections", ["state"],
)
db_transaction_issues = _make_counter(
    "archetype_db_transaction_issues_total",
    "Database transaction/rollback issues by issue type and phase",
    ["issue", "phase", "table"],
)
db_transaction_release_duration = _make_histogram(
    "archetype_db_transaction_release_seconds",
    "Duration spent releasing a DB transaction boundary before awaited I/O",
    ["phase", "table", "result"],
    buckets=(0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, float("inf")),
)

# --- Queue Metrics ---

job_queue_depth = _make_gauge(
    "archetype_job_queue_depth", "Number of jobs currently in the RQ queue",
)

# --- Reconciliation Metrics ---

reconciliation_cycle_duration = _make_histogram(
    "archetype_reconciliation_cycle_seconds",
    "Duration of a full reconciliation cycle",
    buckets=(0.5, 1, 2, 5, 10, 30, 60, float("inf")),
)
reconciliation_labs_checked = _make_counter(
    "archetype_reconciliation_labs_checked_total",
    "Total labs checked during reconciliation cycles",
)
reconciliation_state_changes = _make_counter(
    "archetype_reconciliation_state_changes_total",
    "Total node state changes detected during reconciliation",
)

# --- State Flap Detection ---

node_state_transitions = _make_counter(
    "archetype_node_state_transitions_total",
    "Total node state transitions",
    ["transition_type"],
)

# --- Circuit Breaker Metrics ---

circuit_breaker_state = _make_gauge(
    "archetype_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=half-open, 2=open)",
    ["handler_type"],
)

# --- Broadcast Metrics ---

broadcast_messages = _make_counter(
    "archetype_broadcast_messages_total",
    "Total broadcast messages published",
    ["message_type"],
)
broadcast_failures = _make_counter(
    "archetype_broadcast_failures_total",
    "Total broadcast publish failures",
    ["message_type"],
)

# --- Enforcement Timing ---

enforcement_operation_duration = _make_histogram(
    "archetype_enforcement_operation_seconds",
    "Duration of enforcement operations",
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, float("inf")),
)

# --- Runtime Identity Metrics ---

runtime_identity_events = _make_counter(
    "archetype_runtime_identity_events_total",
    "Runtime identity events observed during reconciliation",
    ["event"],
)
runtime_identity_missing_runtime_id_active_placements = _make_gauge(
    "archetype_runtime_identity_missing_runtime_id_active_placements",
    "Number of active node placements missing runtime_id",
)


# ---------------------------------------------------------------------------
# Periodic update functions
# ---------------------------------------------------------------------------


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

        missing_runtime_id_active = (
            session.query(func.count())
            .select_from(models.NodePlacement)
            .filter(
                models.NodePlacement.runtime_id.is_(None),
                models.NodePlacement.status.in_(("starting", "deployed")),
            )
            .scalar()
            or 0
        )
        runtime_identity_missing_runtime_id_active_placements.set(
            max(0, int(missing_runtime_id_active))
        )

    except Exception as e:
        logger.warning(f"Error updating node metrics: {e}")


def update_agent_metrics(session: "Session") -> None:
    """Update agent-related metrics from database.

    Call this periodically to keep agent metrics current.
    """
    if not PROMETHEUS_AVAILABLE:
        return

    try:
        from app import models
        from app import agent_client

        hosts = session.query(models.Host).all()

        online_count = 0
        total_count = len(hosts)

        # Clear host-specific metrics
        nodes_by_host._metrics.clear()

        for host in hosts:
            if agent_client.is_agent_online(host):
                online_count += 1

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


# ---------------------------------------------------------------------------
# Label normalization helpers
# ---------------------------------------------------------------------------


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
        ("this session's transaction has been rolled back", "db_session_invalidated"),
        ("session is in 'inactive' state", "db_session_invalidated"),
        ("can't reconnect until invalid transaction is rolled back", "db_session_invalidated"),
        ("idle-in-transaction timeout", "db_idle_transaction_timeout"),
        ("terminating connection due to idle-in-transaction timeout", "db_idle_transaction_timeout"),
        ("server closed the connection unexpectedly", "db_connection_closed"),
        ("connection not open", "db_connection_closed"),
        ("row is otherwise not present", "orm_row_stale"),
        ("objectdeletederror", "orm_row_stale"),
        ("staledataerror", "orm_row_stale"),
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


# ---------------------------------------------------------------------------
# Recording helpers
# ---------------------------------------------------------------------------


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


def record_enforcement_skip(reason: str) -> None:
    """Record an enforcement skip reason."""
    if not PROMETHEUS_AVAILABLE:
        return
    enforcement_skip_reasons.labels(reason=_normalize_reason_label(reason)).inc()


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


def set_link_endpoint_reservation_metrics(
    *,
    total: int,
    missing: int,
    orphaned: int,
    conflicts: int,
) -> None:
    """Set gauges for link endpoint reservation health."""
    if not PROMETHEUS_AVAILABLE:
        return
    link_endpoint_reservations_total.set(total)
    link_endpoint_reservation_missing.set(missing)
    link_endpoint_reservation_orphaned.set(orphaned)
    link_endpoint_reservation_conflicts.set(conflicts)


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


def record_db_transaction_issue(
    *,
    issue: str,
    phase: str,
    table: str = "unknown",
) -> None:
    """Record DB transaction boundary failures with bounded labels."""
    if not PROMETHEUS_AVAILABLE:
        return
    db_transaction_issues.labels(
        issue=_normalize_reason_label(issue),
        phase=_normalize_reason_label(phase),
        table=_normalize_reason_label(table),
    ).inc()


def record_db_transaction_release_duration(
    *,
    duration_seconds: float,
    phase: str,
    table: str = "unknown",
    result: str = "success",
) -> None:
    """Record time spent releasing a DB transaction boundary."""
    if not PROMETHEUS_AVAILABLE:
        return
    db_transaction_release_duration.labels(
        phase=_normalize_reason_label(phase),
        table=_normalize_reason_label(table),
        result=_normalize_reason_label(result),
    ).observe(max(0.0, float(duration_seconds)))


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


def set_agent_stale_image_count(host_id: str, host_name: str, count: int) -> None:
    """Set the current stale-image count for an agent."""
    if not PROMETHEUS_AVAILABLE:
        return
    agent_stale_images.labels(host_id=host_id, host_name=host_name).set(max(0, count))


def record_enforcement_duration(duration: float) -> None:
    """Record the duration of an enforcement cycle."""
    if not PROMETHEUS_AVAILABLE:
        return
    enforcement_operation_duration.observe(duration)


def record_runtime_identity_event(event: str) -> None:
    """Record a runtime identity event observed during reconciliation."""
    if not PROMETHEUS_AVAILABLE:
        return
    runtime_identity_events.labels(event=_normalize_reason_label(event)).inc()
