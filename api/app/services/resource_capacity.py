"""Resource capacity validation for multi-host deployments.

Validates that target agents have sufficient resources (CPU, memory, disk)
before deploying nodes. Uses agent heartbeat data and VendorConfig device
requirements to project resource usage. Includes a bin-packing placement
algorithm for capacity-aware node distribution across agents.
"""
from __future__ import annotations

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

# Vendor configs cache (populated on first use)
_vendor_configs_cache: dict | None = None


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
    """Import and return vendor configs from agent package (cached).

    Uses a lazy import since the agent package may not always be
    importable from the API (cross-package dependency). Falls back
    gracefully if unavailable. Result is cached after first call.
    """
    global _vendor_configs_cache
    if _vendor_configs_cache is not None:
        return _vendor_configs_cache
    try:
        from agent.vendors import VENDOR_CONFIGS
        _vendor_configs_cache = VENDOR_CONFIGS
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
    usage = host.get_resource_usage()

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
        caps = host.get_capabilities()
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


# ── Bin-Packing Placement ────────────────────────────────────────────


@dataclass
class NodeRequirement:
    """Resource requirements for a single node."""
    node_name: str
    device_type: str
    memory_mb: int
    cpu_cores: int


@dataclass
class AgentBucket:
    """Tracks remaining capacity for an agent during bin-packing."""
    agent_id: str
    agent_name: str
    memory_available_mb: float
    cpu_available_cores: float
    memory_total_mb: float
    cpu_total_cores: float
    assigned_nodes: list[str] = field(default_factory=list)


@dataclass
class PlacementPlan:
    """Result of bin-packing placement."""
    assignments: dict[str, str] = field(default_factory=dict)       # node_name -> agent_id
    unplaceable: list[str] = field(default_factory=list)            # nodes that couldn't fit
    per_agent: dict[str, list[str]] = field(default_factory=dict)   # agent_id -> [node_names]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_node_requirements(
    nodes: list[tuple[str, str]],
) -> list[NodeRequirement]:
    """Convert (node_name, device_type) tuples into NodeRequirement objects.

    Uses vendor configs + device overrides + minimum hardware constraints
    to determine per-node resource needs.
    """
    vendor_configs = _get_vendor_configs()
    overrides = _get_device_overrides()
    result = []
    for node_name, device_type in nodes:
        override = overrides.get(device_type, {})
        min_hw = minimum_hardware_for_device(device_type) or {}
        min_mem = min_hw.get("memory")
        min_cpu = min_hw.get("cpu")

        if device_type and device_type in vendor_configs:
            config = vendor_configs[device_type]
            memory = override.get("memory", getattr(config, "memory", DEFAULT_MEMORY_MB))
            cpu = override.get("cpu", getattr(config, "cpu", DEFAULT_CPU_CORES))
        else:
            memory = override.get("memory", DEFAULT_MEMORY_MB)
            cpu = override.get("cpu", DEFAULT_CPU_CORES)

        memory = max(memory, min_mem) if min_mem else memory
        cpu = max(cpu, min_cpu) if min_cpu else cpu

        result.append(NodeRequirement(
            node_name=node_name,
            device_type=device_type,
            memory_mb=memory,
            cpu_cores=cpu,
        ))
    return result


def plan_placement(
    nodes: list[NodeRequirement],
    agents: list[AgentBucket],
    controller_reserve_mb: int = 0,
    local_agent_id: str | None = None,
) -> PlacementPlan:
    """Capacity-aware bin-packing placement for nodes across agents.

    Uses a greedy best-fit-decreasing approach: sorts nodes largest-first,
    then assigns each to the agent with the most remaining capacity
    (spreads load rather than packing tight).

    Args:
        nodes: Resource requirements per node.
        agents: Available agents with their remaining capacity.
        controller_reserve_mb: Memory to reserve on the local agent.
        local_agent_id: Agent ID for the controller (receives reserve).

    Returns:
        PlacementPlan with assignments, unplaceable nodes, and diagnostics.
    """
    plan = PlacementPlan()
    if not nodes:
        return plan
    if not agents:
        plan.unplaceable = [n.node_name for n in nodes]
        plan.errors.append("No agents available for placement.")
        return plan

    # Deep-copy buckets so we can mutate remaining capacity
    buckets = [
        AgentBucket(
            agent_id=a.agent_id,
            agent_name=a.agent_name,
            memory_available_mb=a.memory_available_mb,
            cpu_available_cores=a.cpu_available_cores,
            memory_total_mb=a.memory_total_mb,
            cpu_total_cores=a.cpu_total_cores,
            assigned_nodes=list(a.assigned_nodes),
        )
        for a in agents
    ]

    # Apply controller reserve
    if local_agent_id and controller_reserve_mb:
        for b in buckets:
            if b.agent_id == local_agent_id:
                b.memory_available_mb = max(0, b.memory_available_mb - controller_reserve_mb)
                break

    # Cluster pre-check
    total_avail_mem = sum(b.memory_available_mb for b in buckets)
    total_avail_cpu = sum(b.cpu_available_cores for b in buckets)
    total_need_mem = sum(n.memory_mb for n in nodes)
    total_need_cpu = sum(n.cpu_cores for n in nodes)

    if total_need_mem > total_avail_mem or total_need_cpu > total_avail_cpu:
        plan.unplaceable = [n.node_name for n in nodes]
        agent_lines = []
        for b in buckets:
            agent_lines.append(
                f"  - {b.agent_name}: "
                f"{b.memory_available_mb:.0f} MB / "
                f"{b.cpu_available_cores:.0f} vCPUs available"
            )
        node_lines = []
        for n in nodes:
            node_lines.append(
                f"  - {n.node_name} ({n.device_type}) needs "
                f"{n.memory_mb} MB / {n.cpu_cores} vCPUs"
            )
        deficit_parts = []
        if total_need_mem > total_avail_mem:
            deficit_parts.append(f"{total_need_mem - total_avail_mem:.0f} MB memory")
        if total_need_cpu > total_avail_cpu:
            deficit_parts.append(f"{total_need_cpu - total_avail_cpu:.0f} vCPUs")
        plan.errors.append(
            f"Cannot deploy {len(nodes)} node(s) — insufficient cluster resources:\n"
            + "\n".join(node_lines)
            + "\n\nCluster capacity:\n"
            + "\n".join(agent_lines)
            + f"\n\nTotal needed: {total_need_mem:.0f} MB / {total_need_cpu:.0f} vCPUs\n"
            f"Total available: {total_avail_mem:.0f} MB / {total_avail_cpu:.0f} vCPUs\n"
            f"Deficit: {', '.join(deficit_parts)}"
        )
        return plan

    # Sort nodes largest-first (deterministic: memory desc, cpu desc, name asc)
    sorted_nodes = sorted(
        nodes, key=lambda n: (-n.memory_mb, -n.cpu_cores, n.node_name)
    )

    # Greedy best-fit-decreasing: pick agent with most remaining memory
    for node in sorted_nodes:
        fitting = [
            b for b in buckets
            if b.memory_available_mb >= node.memory_mb
            and b.cpu_available_cores >= node.cpu_cores
        ]
        if not fitting:
            plan.unplaceable.append(node.node_name)
            continue
        # Pick agent with most remaining memory (spreads load)
        best = max(fitting, key=lambda b: (b.memory_available_mb, b.cpu_available_cores))
        best.memory_available_mb -= node.memory_mb
        best.cpu_available_cores -= node.cpu_cores
        best.assigned_nodes.append(node.node_name)
        plan.assignments[node.node_name] = best.agent_id

    # Build per-agent summary
    for b in buckets:
        if b.assigned_nodes:
            plan.per_agent[b.agent_id] = list(b.assigned_nodes)

    # Generate error for individually unplaceable nodes (cluster has capacity
    # overall but individual nodes don't fit any single agent)
    if plan.unplaceable:
        unplaceable_details = []
        for name in plan.unplaceable:
            node = next(n for n in nodes if n.node_name == name)
            unplaceable_details.append(
                f"  - {node.node_name} ({node.device_type}) needs "
                f"{node.memory_mb} MB / {node.cpu_cores} vCPUs"
            )
        agent_lines = []
        for b in buckets:
            agent_lines.append(
                f"  - {b.agent_name}: "
                f"{b.memory_available_mb:.0f} MB / "
                f"{b.cpu_available_cores:.0f} vCPUs remaining"
            )
        plan.errors.append(
            f"Cannot place {len(plan.unplaceable)} node(s) — "
            f"no single agent has enough capacity:\n"
            + "\n".join(unplaceable_details)
            + "\n\nAgent remaining capacity:\n"
            + "\n".join(agent_lines)
        )

    # Warn on tight fits (agent < 20% remaining after placement)
    for b in buckets:
        if b.assigned_nodes and b.memory_total_mb > 0:
            remaining_pct = b.memory_available_mb / b.memory_total_mb * 100
            if remaining_pct < 20:
                plan.warnings.append(
                    f"{b.agent_name}: only {remaining_pct:.0f}% memory remaining "
                    f"after placing {len(b.assigned_nodes)} node(s)"
                )

    return plan


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
