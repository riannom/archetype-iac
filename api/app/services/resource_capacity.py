"""Resource capacity validation for multi-host deployments.

Validates that target agents have sufficient resources (CPU, memory, disk)
before deploying nodes. Uses agent heartbeat data and VendorConfig device
requirements to project resource usage.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.services.device_constraints import minimum_hardware_for_device

logger = logging.getLogger(__name__)

# Default resource requirements for unknown device types
DEFAULT_MEMORY_MB = 1024
DEFAULT_CPU_CORES = 1


@dataclass
class ResourceRequirements:
    """Aggregated resource requirements for a set of devices."""
    memory_mb: int = 0
    cpu_cores: int = 0
    node_count: int = 0


@dataclass
class AgentCapacity:
    """Structured capacity data parsed from agent heartbeat."""
    memory_total_mb: float = 0
    memory_used_mb: float = 0
    cpu_cores_total: float = 0
    cpu_used_cores: float = 0
    disk_total_gb: float = 0
    disk_used_gb: float = 0
    containers_running: int = 0

    @property
    def memory_available_mb(self) -> float:
        return max(0, self.memory_total_mb - self.memory_used_mb)

    @property
    def cpu_available_cores(self) -> float:
        return max(0, self.cpu_cores_total - self.cpu_used_cores)

    @property
    def disk_available_gb(self) -> float:
        return max(0, self.disk_total_gb - self.disk_used_gb)


@dataclass
class CapacityCheckResult:
    """Result of a resource capacity check for one agent."""
    fits: bool = True
    has_warnings: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    projected_memory_pct: float = 0
    projected_cpu_pct: float = 0
    projected_disk_pct: float = 0
    required_memory_mb: int = 0
    required_cpu_cores: int = 0
    available_memory_mb: float = 0
    available_cpu_cores: float = 0
    node_count: int = 0
    agent_name: str = ""


def _get_vendor_configs() -> dict:
    """Import and return vendor configs from agent package.

    Uses a lazy import since the agent package may not always be
    importable from the API (cross-package dependency). Falls back
    gracefully if unavailable.
    """
    try:
        from agent.vendors import VENDOR_CONFIGS
        return VENDOR_CONFIGS
    except ImportError:
        logger.warning("Could not import agent.vendors - using defaults for resource calculations")
        return {}


def _get_device_overrides() -> dict[str, dict]:
    """Load device config overrides (user customizations)."""
    try:
        from app.image_store import load_device_overrides
        return load_device_overrides()
    except Exception:
        return {}


def calculate_node_requirements(device_types: list[str]) -> ResourceRequirements:
    """Calculate total resource requirements for a list of device types.

    Looks up each device in VENDOR_CONFIGS for memory/cpu requirements,
    with user overrides taking precedence. Unknown devices get default
    values (1024 MB, 1 CPU).
    """
    vendor_configs = _get_vendor_configs()
    overrides = _get_device_overrides()
    reqs = ResourceRequirements(node_count=len(device_types))

    for device_type in device_types:
        override = overrides.get(device_type, {})
        min_hw = minimum_hardware_for_device(device_type) or {}
        min_mem = min_hw.get("memory")
        min_cpu = min_hw.get("cpu")
        if device_type and device_type in vendor_configs:
            config = vendor_configs[device_type]
            memory = override.get("memory", getattr(config, "memory", DEFAULT_MEMORY_MB))
            cpu = override.get("cpu", getattr(config, "cpu", DEFAULT_CPU_CORES))
            reqs.memory_mb += max(memory, min_mem) if min_mem else memory
            reqs.cpu_cores += max(cpu, min_cpu) if min_cpu else cpu
        else:
            memory = override.get("memory", DEFAULT_MEMORY_MB)
            cpu = override.get("cpu", DEFAULT_CPU_CORES)
            reqs.memory_mb += max(memory, min_mem) if min_mem else memory
            reqs.cpu_cores += max(cpu, min_cpu) if min_cpu else cpu

    return reqs


def get_agent_capacity(host: models.Host) -> AgentCapacity:
    """Parse agent heartbeat data into structured capacity info."""
    try:
        usage = json.loads(host.resource_usage) if host.resource_usage else {}
    except (json.JSONDecodeError, TypeError):
        usage = {}

    memory_total_gb = usage.get("memory_total_gb", 0)
    memory_used_gb = usage.get("memory_used_gb", 0)
    cpu_percent = usage.get("cpu_percent", 0)
    cpu_count = usage.get("cpu_count", 0)
    disk_total_gb = usage.get("disk_total_gb", 0)
    disk_used_gb = usage.get("disk_used_gb", 0)

    # cpu_count may not be in heartbeat - estimate from total and percent
    # The agent reports cpu_percent as overall utilization
    if not cpu_count:
        # Fallback: try to infer from capabilities
        try:
            caps = json.loads(host.capabilities) if host.capabilities else {}
        except (json.JSONDecodeError, TypeError):
            caps = {}
        cpu_count = caps.get("cpu_count", 0)

    return AgentCapacity(
        memory_total_mb=memory_total_gb * 1024,
        memory_used_mb=memory_used_gb * 1024,
        cpu_cores_total=cpu_count,
        cpu_used_cores=cpu_count * (cpu_percent / 100) if cpu_count else 0,
        disk_total_gb=disk_total_gb,
        disk_used_gb=disk_used_gb,
        containers_running=usage.get("containers_running", 0),
    )


@dataclass
class AgentScore:
    """Placement score for an agent, used for resource-aware scheduling."""
    score: float = 0.0
    available_memory_mb: float = 0
    available_cpu_cores: float = 0
    reason: str = ""


def score_agent(host: models.Host) -> AgentScore:
    """Compute a placement score in [0, 1] for an agent.

    Higher scores indicate more available resources. The score is a
    weighted sum of available-memory and available-CPU ratios, with a
    penalty applied for the local (controller) agent.

    Returns a minimum score of 0.1 for agents without heartbeat data
    so they remain selectable as a last resort.
    """
    capacity = get_agent_capacity(host)

    # No heartbeat data — low-priority fallback
    if capacity.memory_total_mb == 0 and capacity.cpu_cores_total == 0:
        return AgentScore(
            score=0.1,
            reason="no heartbeat data",
        )

    # Available memory, with controller reserve subtracted for local agents
    reserve_mb = settings.placement_controller_reserve_mb if host.is_local else 0
    usable_memory = max(0, capacity.memory_total_mb - reserve_mb)
    mem_available = max(0, usable_memory - capacity.memory_used_mb)
    mem_ratio = mem_available / usable_memory if usable_memory > 0 else 0

    # Available CPU ratio
    cpu_ratio = (
        capacity.cpu_available_cores / capacity.cpu_cores_total
        if capacity.cpu_cores_total > 0
        else 0
    )

    # Weighted sum
    raw_score = (
        settings.placement_weight_memory * mem_ratio
        + settings.placement_weight_cpu * cpu_ratio
    )

    # Local-agent penalty
    if host.is_local:
        raw_score *= settings.placement_local_penalty

    score = max(0.0, min(1.0, raw_score))

    reason = (
        f"mem={mem_available:.0f}/{usable_memory:.0f}MB "
        f"cpu={capacity.cpu_available_cores:.1f}/{capacity.cpu_cores_total:.0f} "
        f"raw={raw_score:.3f}"
    )
    if host.is_local:
        reason += f" local_penalty={settings.placement_local_penalty}"

    return AgentScore(
        score=score,
        available_memory_mb=mem_available,
        available_cpu_cores=capacity.cpu_available_cores,
        reason=reason,
    )


def distribute_nodes_by_score(
    node_names: list[str],
    agent_scores: dict[str, AgentScore],
) -> dict[str, str]:
    """Distribute nodes across agents proportionally to their scores.

    Returns a mapping of node_name -> agent_id. Uses a greedy approach:
    each node is assigned to the agent with the highest remaining
    fractional share, which produces a balanced distribution even for
    small numbers of nodes.
    """
    if not node_names or not agent_scores:
        return {}

    total_score = sum(s.score for s in agent_scores.values())
    if total_score == 0:
        return {}

    # Calculate fractional shares (how many nodes each agent "deserves")
    shares: dict[str, float] = {
        agent_id: (s.score / total_score) * len(node_names)
        for agent_id, s in agent_scores.items()
    }

    assignments: dict[str, str] = {}
    for node_name in node_names:
        # Pick agent with highest remaining share
        best_agent = max(shares, key=shares.get)  # type: ignore[arg-type]
        assignments[node_name] = best_agent
        shares[best_agent] -= 1.0

    return assignments


def check_capacity(
    host: models.Host,
    new_devices: list[str],
) -> CapacityCheckResult:
    """Check if a host can accommodate new devices.

    Calculates projected usage = current_used + new_requirements and
    compares against configured thresholds.

    Only validates NEW nodes - already-running containers are reflected
    in the heartbeat's memory_used_gb, avoiding double-counting.
    """
    if not settings.resource_validation_enabled:
        return CapacityCheckResult(agent_name=host.name or host.id)

    reqs = calculate_node_requirements(new_devices)
    capacity = get_agent_capacity(host)
    result = CapacityCheckResult(
        agent_name=host.name or host.id,
        required_memory_mb=reqs.memory_mb,
        required_cpu_cores=reqs.cpu_cores,
        available_memory_mb=capacity.memory_available_mb,
        available_cpu_cores=capacity.cpu_available_cores,
        node_count=reqs.node_count,
    )

    # Skip checks if we have no capacity data (agent hasn't sent heartbeat yet)
    if capacity.memory_total_mb == 0:
        logger.warning(f"No resource data for {host.name} - skipping capacity check")
        return result

    # Memory check
    buffer_mb = settings.resource_memory_buffer_mb
    usable_memory = capacity.memory_total_mb - buffer_mb
    if usable_memory > 0:
        projected_memory = capacity.memory_used_mb + reqs.memory_mb
        result.projected_memory_pct = (projected_memory / capacity.memory_total_mb) * 100

        if result.projected_memory_pct >= settings.resource_memory_error_pct:
            available = max(0, usable_memory - capacity.memory_used_mb)
            result.fits = False
            result.errors.append(
                f"Memory: Need {reqs.memory_mb} MB, only {available:.0f} MB available "
                f"(projected {result.projected_memory_pct:.0f}%)"
            )
        elif result.projected_memory_pct >= settings.resource_memory_warning_pct:
            result.has_warnings = True
            result.warnings.append(
                f"Memory: {result.projected_memory_pct:.0f}% projected "
                f"(currently {capacity.memory_used_mb:.0f} MB used, "
                f"adding {reqs.memory_mb} MB for {reqs.node_count} node(s))"
            )

    # CPU check
    if capacity.cpu_cores_total > 0:
        projected_cpu_cores = capacity.cpu_used_cores + reqs.cpu_cores
        result.projected_cpu_pct = (projected_cpu_cores / capacity.cpu_cores_total) * 100

        if result.projected_cpu_pct >= settings.resource_cpu_error_pct:
            result.fits = False
            result.errors.append(
                f"CPU: Need {reqs.cpu_cores} cores, projected {result.projected_cpu_pct:.0f}%"
            )
        elif result.projected_cpu_pct >= settings.resource_cpu_warning_pct:
            result.has_warnings = True
            result.warnings.append(
                f"CPU: {result.projected_cpu_pct:.0f}% projected "
                f"(adding {reqs.cpu_cores} core(s) for {reqs.node_count} node(s))"
            )

    # Disk check
    if capacity.disk_total_gb > 0:
        disk_pct = (capacity.disk_used_gb / capacity.disk_total_gb) * 100
        result.projected_disk_pct = disk_pct  # No per-node disk estimate

        if disk_pct >= settings.resource_disk_error_pct:
            result.fits = False
            result.errors.append(
                f"Disk: {disk_pct:.0f}% used ({capacity.disk_available_gb:.1f} GB free)"
            )
        elif disk_pct >= settings.resource_disk_warning_pct:
            result.has_warnings = True
            result.warnings.append(
                f"Disk: {disk_pct:.0f}% used ({capacity.disk_available_gb:.1f} GB free)"
            )

    return result


def check_multihost_capacity(
    placements: dict[str, list[str]],
    session: Session,
) -> dict[str, CapacityCheckResult]:
    """Check capacity across multiple hosts.

    Args:
        placements: dict of host_id -> list of device types to deploy
        session: Database session

    Returns:
        dict of host_id -> CapacityCheckResult
    """
    results: dict[str, CapacityCheckResult] = {}

    for host_id, device_types in placements.items():
        host = session.get(models.Host, host_id)
        if not host:
            result = CapacityCheckResult(
                fits=False,
                agent_name=host_id,
                errors=[f"Host {host_id} not found"],
            )
        else:
            result = check_capacity(host, device_types)
        results[host_id] = result

    return results


def format_capacity_error(results: dict[str, CapacityCheckResult]) -> str:
    """Format capacity check results into a human-readable error message."""
    lines = ["Insufficient resources for deployment:"]
    for host_id, result in results.items():
        if not result.fits:
            lines.append(f"\n  {result.agent_name}:")
            for error in result.errors:
                lines.append(f"    - {error}")

    lines.append("\nSuggestions:")
    lines.append("  - Assign some nodes to a different agent")
    lines.append("  - Stop unused labs to free resources")
    return "\n".join(lines)


def format_capacity_warnings(results: dict[str, CapacityCheckResult]) -> list[str]:
    """Collect warning messages from capacity check results."""
    warnings = []
    for host_id, result in results.items():
        for warning in result.warnings:
            warnings.append(f"{result.agent_name}: {warning}")
    return warnings
