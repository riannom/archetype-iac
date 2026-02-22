"""Health and system info endpoints."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter

import agent.agent_state as _state
from agent.config import settings
from agent.helpers import get_agent_info, get_capabilities, get_resource_usage, _get_allocated_resources
from agent.version import get_commit
from agent.updater import detect_deployment_mode

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Detailed health endpoint for diagnostics and UI status."""
    return {
        "status": "ok",
        "agent_id": _state.AGENT_ID,
        "commit": get_commit(),
        "registered": _state._registered,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/healthz")
async def healthz():
    """Fast liveness endpoint used by container healthchecks."""
    return {
        "status": "ok",
    }


@router.get("/disk-usage")
async def disk_usage():
    """Return disk and memory usage stats."""
    import psutil
    disk_path = settings.workspace_path if settings.workspace_path else "/"
    disk = psutil.disk_usage(disk_path)
    memory = psutil.virtual_memory()
    return {
        "disk": {
            "total_gb": round(disk.total / (1024**3), 2),
            "used_gb": round(disk.used / (1024**3), 2),
            "free_gb": round(disk.free / (1024**3), 2),
            "percent": disk.percent,
        },
        "memory": {
            "total_gb": round(memory.total / (1024**3), 2),
            "used_gb": round(memory.used / (1024**3), 2),
            "percent": memory.percent,
        },
    }


@router.get("/capacity")
async def get_capacity():
    """Real-time capacity snapshot for placement decisions.

    Returns system resource stats plus allocated resources (sum of
    CPU/memory committed to running containers and VMs). Queried by
    the API during placement — fresher than heartbeat data.
    """
    usage = await get_resource_usage()
    if not usage:
        return {"error": "Failed to gather resource usage"}
    allocated = _get_allocated_resources(usage)
    return {
        **usage,
        "allocated_vcpus": allocated["vcpus"],
        "allocated_memory_mb": allocated["memory_mb"],
    }


@router.get("/metrics")
def metrics():
    """Prometheus metrics endpoint."""
    from starlette.responses import Response as StarletteResponse
    from agent.metrics import get_metrics
    body, content_type = get_metrics()
    return StarletteResponse(content=body, media_type=content_type)


@router.get("/info")
def info():
    """Return agent info and capabilities."""
    return get_agent_info().model_dump()


@router.get("/deployment-mode")
def get_deployment_mode() -> dict:
    """Get the agent's deployment mode.

    Used by controller to determine update strategy.
    """
    from agent.version import __version__
    mode = detect_deployment_mode()
    return {
        "mode": mode.value,
        "version": __version__,
    }
