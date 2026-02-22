"""Dashboard metrics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import db, models
from app.auth import get_current_user
from app.state import HostStatus
from app.utils.cache import cache_get, cache_set
from app.utils.lab import find_lab_by_prefix, find_lab_with_name

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/metrics")
def get_dashboard_metrics(database: Session = Depends(db.get_db), _user: models.User = Depends(get_current_user)) -> dict:
    """Get aggregated system metrics for the dashboard.

    Returns agent counts, container counts, CPU/memory usage, and lab stats.
    Labs running count is based on actual container presence, not database state.
    """
    cached = cache_get("dashboard:metrics")
    if cached is not None:
        return cached

    # Get all hosts
    hosts = database.query(models.Host).all()
    online_agents = sum(1 for h in hosts if h.status == HostStatus.ONLINE)
    total_agents = len(hosts)

    # Get all labs for mapping
    all_labs = database.query(models.Lab).all()
    labs_by_id = {lab.id: lab for lab in all_labs}
    labs_by_prefix = {lab.id[:20]: lab.id for lab in all_labs}  # short prefix for matching

    # Aggregate resource usage from all online agents
    total_cpu = 0.0
    total_memory = 0.0
    total_memory_used = 0.0
    total_memory_total = 0.0
    total_disk_used = 0.0
    total_disk_total = 0.0
    total_containers_running = 0
    total_containers = 0
    total_vms_running = 0
    total_vms = 0
    online_count = 0
    labs_with_containers: set[str] = set()  # Track labs with running containers
    per_host: list[dict] = []  # Per-host breakdown for multi-host environments

    for host in hosts:
        if host.status != HostStatus.ONLINE:
            continue
        online_count += 1
        usage = host.get_resource_usage()
        host_cpu = usage.get("cpu_percent", 0)
        host_memory = usage.get("memory_percent", 0)
        host_memory_used = usage.get("memory_used_gb", 0)
        host_memory_total = usage.get("memory_total_gb", 0)
        host_disk_percent = usage.get("disk_percent", 0)
        host_disk_used = usage.get("disk_used_gb", 0)
        host_disk_total = usage.get("disk_total_gb", 0)
        host_containers = usage.get("containers_running", 0)

        total_cpu += host_cpu
        total_memory += host_memory
        total_memory_used += host_memory_used
        total_memory_total += host_memory_total
        total_disk_used += host_disk_used
        total_disk_total += host_disk_total
        total_containers_running += host_containers
        total_containers += usage.get("containers_total", 0)
        total_vms_running += usage.get("vms_running", 0)
        total_vms += usage.get("vms_total", 0)

        # Track per-host data
        per_host.append({
            "id": host.id,
            "name": host.name,
            "cpu_percent": round(host_cpu, 1),
            "memory_percent": round(host_memory, 1),
            "memory_used_gb": host_memory_used,
            "memory_total_gb": host_memory_total,
            "storage_percent": round(host_disk_percent, 1),
            "storage_used_gb": host_disk_used,
            "storage_total_gb": host_disk_total,
            "containers_running": host_containers,
            "vms_running": usage.get("vms_running", 0),
            "started_at": host.started_at.isoformat() if host.started_at else None,
        })

        # Track which labs have running containers
        for container in usage.get("container_details", []):
            if container.get("status") == "running" and not container.get("is_system"):
                lab_id = find_lab_by_prefix(
                    container.get("lab_prefix", ""), labs_by_id, labs_by_prefix
                )
                if lab_id:
                    labs_with_containers.add(lab_id)

    # Calculate averages
    avg_cpu = total_cpu / online_count if online_count > 0 else 0
    avg_memory = total_memory / online_count if online_count > 0 else 0

    # Storage: aggregate totals, calculate overall percent
    storage_percent = (total_disk_used / total_disk_total * 100) if total_disk_total > 0 else 0

    # Use container-based count as source of truth for running labs
    running_labs = len(labs_with_containers)

    # Determine if multi-host environment
    is_multi_host = total_agents > 1

    result = {
        "agents": {"online": online_agents, "total": total_agents},
        "containers": {"running": total_containers_running, "total": total_containers},
        "vms": {"running": total_vms_running, "total": total_vms},
        "cpu_percent": round(avg_cpu, 1),
        "memory_percent": round(avg_memory, 1),
        "memory": {
            "used_gb": round(total_memory_used, 2),
            "total_gb": round(total_memory_total, 2),
            "percent": round(avg_memory, 1),
        },
        "storage": {
            "used_gb": round(total_disk_used, 2),
            "total_gb": round(total_disk_total, 2),
            "percent": round(storage_percent, 1),
        },
        "labs_running": running_labs,
        "labs_total": len(all_labs),
        "per_host": per_host,
        "is_multi_host": is_multi_host,
    }
    cache_set("dashboard:metrics", result)
    return result


@router.get("/metrics/containers")
def get_containers_breakdown(database: Session = Depends(db.get_db), _user: models.User = Depends(get_current_user)) -> dict:
    """Get detailed container and VM breakdown by lab."""
    cached = cache_get("dashboard:containers")
    if cached is not None:
        return cached

    hosts = database.query(models.Host).filter(models.Host.status == HostStatus.ONLINE).all()
    all_labs = database.query(models.Lab).all()
    # Map both full ID and truncated prefix to lab info
    labs_by_id = {lab.id: lab.name for lab in all_labs}
    labs_by_prefix = {lab.id[:20]: (lab.id, lab.name) for lab in all_labs}  # short prefix for matching

    all_containers = []
    all_vms = []
    for host in hosts:
        usage = host.get_resource_usage()
        # Collect containers
        for container in usage.get("container_details", []):
            container["agent_name"] = host.name
            lab_id, lab_name = find_lab_with_name(
                container.get("lab_prefix", ""), labs_by_id, labs_by_prefix
            )
            container["lab_id"] = lab_id
            container["lab_name"] = lab_name
            all_containers.append(container)
        # Collect VMs
        for vm in usage.get("vm_details", []):
            vm["agent_name"] = host.name
            lab_id, lab_name = find_lab_with_name(
                vm.get("lab_prefix", ""), labs_by_id, labs_by_prefix
            )
            vm["lab_id"] = lab_id
            vm["lab_name"] = lab_name
            all_vms.append(vm)

    # Group containers by lab
    by_lab = {}
    system_containers = []
    for c in all_containers:
        if c.get("is_system"):
            system_containers.append(c)
        elif c.get("lab_id"):
            lab_id = c["lab_id"]
            if lab_id not in by_lab:
                by_lab[lab_id] = {"name": c["lab_name"], "containers": [], "vms": []}
            by_lab[lab_id]["containers"].append(c)
        else:
            # Orphan container (lab deleted but container still running)
            system_containers.append(c)

    # Add VMs to their labs
    for vm in all_vms:
        if vm.get("lab_id"):
            lab_id = vm["lab_id"]
            if lab_id not in by_lab:
                by_lab[lab_id] = {"name": vm["lab_name"], "containers": [], "vms": []}
            by_lab[lab_id]["vms"].append(vm)

    result = {
        "by_lab": by_lab,
        "system_containers": system_containers,
        "total_running": sum(1 for c in all_containers if c.get("status") == "running"),
        "total_stopped": sum(1 for c in all_containers if c.get("status") != "running"),
        "vms_running": sum(1 for vm in all_vms if vm.get("status") == "running"),
        "vms_stopped": sum(1 for vm in all_vms if vm.get("status") != "running"),
    }
    cache_set("dashboard:containers", result)
    return result


@router.get("/metrics/resources")
def get_resource_distribution(database: Session = Depends(db.get_db), _user: models.User = Depends(get_current_user)) -> dict:
    """Get resource usage distribution by agent and lab."""
    cached = cache_get("dashboard:resources")
    if cached is not None:
        return cached

    hosts = database.query(models.Host).filter(models.Host.status == HostStatus.ONLINE).all()
    all_labs = database.query(models.Lab).all()
    labs_by_id = {lab.id: lab.name for lab in all_labs}

    by_agent = []
    lab_containers = {}  # lab_id -> container count

    for host in hosts:
        usage = host.get_resource_usage()
        by_agent.append({
            "id": host.id,
            "name": host.name,
            "cpu_percent": usage.get("cpu_percent", 0),
            "memory_percent": usage.get("memory_percent", 0),
            "memory_used_gb": usage.get("memory_used_gb", 0),
            "memory_total_gb": usage.get("memory_total_gb", 0),
            "containers": usage.get("containers_running", 0),
        })

        # Count containers per lab (only non-system containers)
        for c in usage.get("container_details", []):
            if c.get("is_system"):
                continue
            lab_id = find_lab_by_prefix(c.get("lab_prefix", ""), labs_by_id)
            if lab_id:
                lab_containers[lab_id] = lab_containers.get(lab_id, 0) + 1

    # Estimate lab resource usage by container proportion
    total_containers = sum(lab_containers.values()) or 1
    by_lab = [
        {
            "id": lab_id,
            "name": labs_by_id[lab_id],
            "container_count": count,
            "estimated_percent": round(count / total_containers * 100, 1),
        }
        for lab_id, count in lab_containers.items()
    ]

    result = {"by_agent": by_agent, "by_lab": sorted(by_lab, key=lambda x: -x["container_count"])}
    cache_set("dashboard:resources", result)
    return result
