"""Metrics service for dashboard and system statistics.

This service encapsulates metrics aggregation logic,
extracted from main.py to improve maintainability.

Usage:
    from app.services.metrics_service import MetricsService

    service = MetricsService(session)
    metrics = service.get_dashboard_metrics()
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class AgentMetrics:
    """Aggregated agent metrics."""
    total: int
    online: int
    offline: int
    degraded: int


@dataclass
class ContainerMetrics:
    """Aggregated container metrics."""
    running: int
    total: int
    by_lab: dict[str, int]


@dataclass
class ResourceMetrics:
    """Aggregated resource usage metrics."""
    avg_cpu_percent: float
    avg_memory_percent: float
    total_disk_used_gb: float
    total_disk_total_gb: float


@dataclass
class LabMetrics:
    """Aggregated lab metrics."""
    total: int
    running: int
    stopped: int
    error: int
    by_state: dict[str, int]


@dataclass
class DashboardMetrics:
    """Complete dashboard metrics."""
    agents: AgentMetrics
    containers: ContainerMetrics
    resources: ResourceMetrics
    labs: LabMetrics
    timestamp: str


class MetricsService:
    """Service for aggregating system metrics.

    This service provides methods for:
    - Dashboard metrics (agents, containers, resources)
    - Container breakdown by lab
    - Resource distribution across agents
    """

    def __init__(self, session: "Session"):
        self.session = session

    def get_dashboard_metrics(self) -> dict:
        """Get aggregated system metrics for the dashboard.

        Returns agent counts, container counts, CPU/memory usage, and lab stats.
        """
        from app import models
        from app import agent_client

        # Get all hosts
        hosts = self.session.query(models.Host).all()

        # Agent counts
        agents_total = len(hosts)
        agents_online = 0
        agents_offline = 0
        agents_degraded = 0

        # Resource aggregation
        total_cpu = 0.0
        total_memory = 0.0
        total_disk_used = 0.0
        total_disk_total = 0.0
        online_count = 0

        # Container aggregation
        containers_running = 0
        containers_total = 0
        containers_by_lab: dict[str, int] = {}

        for host in hosts:
            if agent_client.is_agent_online(host):
                agents_online += 1
                online_count += 1
            elif host.status == "degraded":
                agents_degraded += 1
            else:
                agents_offline += 1

            # Parse resource usage
            try:
                usage = json.loads(host.resource_usage) if host.resource_usage else {}
            except json.JSONDecodeError:
                usage = {}

            if agent_client.is_agent_online(host):
                total_cpu += usage.get("cpu_percent", 0)
                total_memory += usage.get("memory_percent", 0)
                total_disk_used += usage.get("disk_used_gb", 0)
                total_disk_total += usage.get("disk_total_gb", 0)

                containers_running += usage.get("containers_running", 0)
                containers_total += usage.get("containers_total", 0)

                # Aggregate containers by lab
                for container in usage.get("container_details", []):
                    if not container.get("is_system", False):
                        lab_prefix = container.get("lab_prefix", "unknown")
                        containers_by_lab[lab_prefix] = containers_by_lab.get(lab_prefix, 0) + 1

        # Calculate averages
        avg_cpu = total_cpu / online_count if online_count > 0 else 0
        avg_memory = total_memory / online_count if online_count > 0 else 0

        # Get lab stats from database
        labs = self.session.query(models.Lab).all()
        lab_state_counts = self._count_labs_by_state(labs)

        return {
            "agents": {
                "total": agents_total,
                "online": agents_online,
                "offline": agents_offline,
                "degraded": agents_degraded,
            },
            "containers": {
                "running": containers_running,
                "total": containers_total,
                "by_lab": containers_by_lab,
            },
            "resources": {
                "avg_cpu_percent": round(avg_cpu, 1),
                "avg_memory_percent": round(avg_memory, 1),
                "total_disk_used_gb": round(total_disk_used, 1),
                "total_disk_total_gb": round(total_disk_total, 1),
            },
            "labs": {
                "total": len(labs),
                "running": lab_state_counts.get("running", 0),
                "stopped": lab_state_counts.get("stopped", 0),
                "error": lab_state_counts.get("error", 0),
                "by_state": lab_state_counts,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _count_labs_by_state(self, labs) -> dict[str, int]:
        """Count labs by their state."""
        counts: dict[str, int] = {}
        for lab in labs:
            state = lab.state or "unknown"
            counts[state] = counts.get(state, 0) + 1
        return counts

    def get_containers_breakdown(self) -> dict:
        """Get detailed container breakdown by host and lab.

        Returns container counts and details grouped by host.
        """
        from app import models
        from app import agent_client

        hosts = self.session.query(models.Host).all()

        result = {
            "hosts": [],
            "totals": {
                "running": 0,
                "total": 0,
            },
        }

        for host in hosts:
            try:
                usage = json.loads(host.resource_usage) if host.resource_usage else {}
            except json.JSONDecodeError:
                usage = {}

            host_data = {
                "id": host.id,
                "name": host.name,
                "status": host.status,
                "is_online": agent_client.is_agent_online(host),
                "containers_running": usage.get("containers_running", 0),
                "containers_total": usage.get("containers_total", 0),
                "container_details": usage.get("container_details", []),
            }

            result["hosts"].append(host_data)
            result["totals"]["running"] += host_data["containers_running"]
            result["totals"]["total"] += host_data["containers_total"]

        return result

    def get_resource_distribution(self) -> dict:
        """Get resource usage distribution across agents.

        Returns CPU, memory, and disk usage per agent.
        """
        from app import models
        from app import agent_client

        hosts = self.session.query(models.Host).all()

        result = {
            "agents": [],
            "averages": {
                "cpu_percent": 0.0,
                "memory_percent": 0.0,
            },
        }

        online_count = 0
        total_cpu = 0.0
        total_memory = 0.0

        for host in hosts:
            try:
                usage = json.loads(host.resource_usage) if host.resource_usage else {}
            except json.JSONDecodeError:
                usage = {}

            is_online = agent_client.is_agent_online(host)

            agent_data = {
                "id": host.id,
                "name": host.name,
                "status": host.status,
                "is_online": is_online,
                "cpu_percent": usage.get("cpu_percent", 0),
                "memory_percent": usage.get("memory_percent", 0),
                "disk_percent": usage.get("disk_percent", 0),
                "disk_used_gb": usage.get("disk_used_gb", 0),
                "disk_total_gb": usage.get("disk_total_gb", 0),
            }

            if is_online:
                online_count += 1
                total_cpu += agent_data["cpu_percent"]
                total_memory += agent_data["memory_percent"]

            result["agents"].append(agent_data)

        if online_count > 0:
            result["averages"]["cpu_percent"] = round(total_cpu / online_count, 1)
            result["averages"]["memory_percent"] = round(total_memory / online_count, 1)

        return result

    def get_job_statistics(self, hours: int = 24) -> dict:
        """Get job statistics for the specified time period.

        Args:
            hours: Number of hours to look back

        Returns:
            Job counts by status and action
        """
        from app import models

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        jobs = (
            self.session.query(models.Job)
            .filter(models.Job.created_at >= cutoff)
            .all()
        )

        by_status: dict[str, int] = {}
        by_action: dict[str, int] = {}

        for job in jobs:
            status = job.status or "unknown"
            action = job.action.split(":")[0] if job.action else "unknown"

            by_status[status] = by_status.get(status, 0) + 1
            by_action[action] = by_action.get(action, 0) + 1

        return {
            "period_hours": hours,
            "total": len(jobs),
            "by_status": by_status,
            "by_action": by_action,
        }

    def get_node_state_summary(self) -> dict:
        """Get summary of node states across all labs.

        Returns:
            Node counts by state and readiness
        """
        from app import models

        node_states = self.session.query(models.NodeState).all()

        by_state: dict[str, int] = {}
        ready_count = 0
        total_count = len(node_states)

        for ns in node_states:
            state = ns.actual_state or "unknown"
            by_state[state] = by_state.get(state, 0) + 1
            if ns.is_ready:
                ready_count += 1

        return {
            "total": total_count,
            "ready": ready_count,
            "by_state": by_state,
        }


def get_metrics_service(session: "Session") -> MetricsService:
    """Create a metrics service instance with the given session."""
    return MetricsService(session)
