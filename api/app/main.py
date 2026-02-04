"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
import logging
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware

from app import db, models
from app.db import SessionLocal
from app.config import settings
from app.auth import get_current_user, hash_password
from app.catalog import list_devices as catalog_devices, list_images as catalog_images
from app.logging_config import (
    correlation_id_var,
    generate_correlation_id,
    set_correlation_id,
    setup_logging,
)
from app.middleware import CurrentUserMiddleware, DeprecationMiddleware
from app.routers.v1 import router as v1_router
from app.routers import admin, agents, auth, callbacks, console, events, images, infrastructure, iso, jobs, labs, permissions, state_ws, system, vendors, webhooks
from app.tasks.health import agent_health_monitor
from app.tasks.job_health import job_health_monitor
from app.tasks.reconciliation import state_reconciliation_monitor
from app.tasks.disk_cleanup import disk_cleanup_monitor
from app.tasks.image_reconciliation import image_reconciliation_monitor
from app.tasks.state_enforcement import state_enforcement_monitor
from app.tasks.link_reconciliation import link_reconciliation_monitor
from app.utils.async_tasks import setup_asyncio_exception_handler, safe_create_task
from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command

# Configure structured logging at module load
setup_logging()

logger = logging.getLogger(__name__)

# Background task handles
_agent_monitor_task: asyncio.Task | None = None
_reconciliation_task: asyncio.Task | None = None
_job_health_task: asyncio.Task | None = None
_disk_cleanup_task: asyncio.Task | None = None
_image_reconciliation_task: asyncio.Task | None = None
_state_enforcement_task: asyncio.Task | None = None
_link_reconciliation_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - start background tasks on startup, cleanup on shutdown."""
    global _agent_monitor_task, _reconciliation_task, _job_health_task, _disk_cleanup_task, _image_reconciliation_task, _state_enforcement_task, _link_reconciliation_task

    # Startup
    logger.info("Starting Archetype API controller")

    # Set up asyncio exception handler for catching unhandled exceptions in tasks
    setup_asyncio_exception_handler()

    # Run database migrations
    logger.info("Running database migrations")
    try:
        alembic_cfg = AlembicConfig("alembic.ini")
        alembic_command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations completed")
    except Exception as e:
        logger.error(f"Database migration failed: {e}")
        # Fall back to create_all for fresh installs without migrations
        logger.info("Falling back to create_all for database tables")
        models.Base.metadata.create_all(bind=db.engine)

    # Seed admin user if configured
    if settings.admin_email and settings.admin_password:
        session = SessionLocal()
        try:
            existing = session.query(models.User).filter(models.User.email == settings.admin_email).first()
            if not existing:
                if len(settings.admin_password.encode("utf-8")) > 72:
                    logger.warning("Skipping admin seed: ADMIN_PASSWORD must be 72 bytes or fewer")
                else:
                    admin_user = models.User(
                        email=settings.admin_email,
                        hashed_password=hash_password(settings.admin_password),
                        is_admin=True,
                    )
                    session.add(admin_user)
                    session.commit()
                    logger.info(f"Created admin user: {settings.admin_email}")
        finally:
            session.close()

    # Start background monitor tasks with proper exception handling
    _agent_monitor_task = safe_create_task(
        agent_health_monitor(), name="agent_health_monitor"
    )
    _reconciliation_task = safe_create_task(
        state_reconciliation_monitor(), name="state_reconciliation_monitor"
    )
    _job_health_task = safe_create_task(
        job_health_monitor(), name="job_health_monitor"
    )
    _disk_cleanup_task = safe_create_task(
        disk_cleanup_monitor(), name="disk_cleanup_monitor"
    )
    _image_reconciliation_task = safe_create_task(
        image_reconciliation_monitor(), name="image_reconciliation_monitor"
    )
    _state_enforcement_task = safe_create_task(
        state_enforcement_monitor(), name="state_enforcement_monitor"
    )
    _link_reconciliation_task = safe_create_task(
        link_reconciliation_monitor(), name="link_reconciliation_monitor"
    )

    yield

    # Shutdown
    logger.info("Shutting down Archetype API controller")

    if _agent_monitor_task:
        _agent_monitor_task.cancel()
        try:
            await _agent_monitor_task
        except asyncio.CancelledError:
            pass

    if _reconciliation_task:
        _reconciliation_task.cancel()
        try:
            await _reconciliation_task
        except asyncio.CancelledError:
            pass

    if _job_health_task:
        _job_health_task.cancel()
        try:
            await _job_health_task
        except asyncio.CancelledError:
            pass

    if _disk_cleanup_task:
        _disk_cleanup_task.cancel()
        try:
            await _disk_cleanup_task
        except asyncio.CancelledError:
            pass

    if _image_reconciliation_task:
        _image_reconciliation_task.cancel()
        try:
            await _image_reconciliation_task
        except asyncio.CancelledError:
            pass

    if _state_enforcement_task:
        _state_enforcement_task.cancel()
        try:
            await _state_enforcement_task
        except asyncio.CancelledError:
            pass

    if _link_reconciliation_task:
        _link_reconciliation_task.cancel()
        try:
            await _link_reconciliation_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Archetype API", version="0.1.0", lifespan=lifespan)


# Global exception handler for unhandled exceptions
# This prevents the API from crashing and provides detailed error logging
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle all unhandled exceptions with detailed logging.

    This ensures the API never crashes from unhandled exceptions, and provides
    detailed error information for troubleshooting.
    """
    # Get correlation ID for tracing
    correlation_id = correlation_id_var.get()

    # Format full traceback for logging
    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    tb_str = "".join(tb_lines)

    # Log the full error with context
    logger.error(
        f"Unhandled exception in request handler:\n"
        f"Correlation ID: {correlation_id}\n"
        f"Request: {request.method} {request.url.path}\n"
        f"Exception type: {type(exc).__name__}\n"
        f"Exception message: {exc}\n"
        f"Full traceback:\n{tb_str}"
    )

    # Return a structured error response
    # In production, we don't expose internal details to clients
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "error_type": type(exc).__name__,
            "correlation_id": correlation_id,
            "message": "An unexpected error occurred. Please check server logs for details.",
        },
        headers={"X-Correlation-ID": correlation_id} if correlation_id else {},
    )


# Correlation ID middleware for request tracing
class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware to handle correlation IDs for request tracing.

    Extracts X-Correlation-ID from incoming requests or generates a new one.
    Sets the correlation ID in context for use by loggers throughout the request.
    Returns the correlation ID in the response header.
    """

    async def dispatch(self, request: Request, call_next):
        # Extract correlation ID from header or generate new one
        correlation_id = request.headers.get("X-Correlation-ID")
        if not correlation_id:
            correlation_id = generate_correlation_id()

        # Set in context for logging
        token = correlation_id_var.set(correlation_id)

        try:
            response = await call_next(request)
            # Add correlation ID to response headers
            response.headers["X-Correlation-ID"] = correlation_id
            return response
        finally:
            # Reset context
            correlation_id_var.reset(token)


# Middleware (order matters - correlation ID should be early)
app.add_middleware(CorrelationIdMiddleware)
if settings.session_secret:
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(CurrentUserMiddleware)
app.add_middleware(DeprecationMiddleware, sunset_date="2026-12-01")

# Include routers
app.include_router(auth.router)
app.include_router(agents.router)
app.include_router(labs.router)
app.include_router(jobs.router)
app.include_router(permissions.router)
app.include_router(images.router)
app.include_router(console.router)
app.include_router(admin.router)
app.include_router(callbacks.router)
app.include_router(events.router)
app.include_router(iso.router)
app.include_router(webhooks.router)
app.include_router(system.router)
app.include_router(state_ws.router)
app.include_router(infrastructure.router)
app.include_router(vendors.router)
app.include_router(v1_router, prefix="/api/v1")


# Simple endpoints that remain in main.py
@app.get("/health")
def health(request: Request) -> dict[str, str]:
    user = request.state.user
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user": user.email if user else "",
    }


@app.get("/metrics")
def metrics(database: Session = Depends(db.get_db)):
    """Prometheus metrics endpoint.

    Returns metrics in Prometheus text format for scraping.
    Automatically updates all metrics from database before returning.
    """
    from app.metrics import get_metrics, update_all_metrics

    # Update all metrics from current database state
    update_all_metrics(database)

    # Generate and return metrics
    content, content_type = get_metrics()
    return Response(content=content, media_type=content_type)


@app.get("/devices", deprecated=True)
def list_devices() -> dict[str, object]:
    """List devices from netlab CLI.

    DEPRECATED: Use GET /vendors instead. This endpoint requires netlab CLI
    to be installed and is kept for backward compatibility only.
    """
    data = catalog_devices()
    if data.get("error"):
        raise HTTPException(status_code=500, detail=data["error"])
    return data


@app.get("/images", deprecated=True)
def list_images() -> dict[str, object]:
    """List images from netlab CLI.

    DEPRECATED: Use GET /images/library instead. This endpoint requires netlab CLI
    to be installed and is kept for backward compatibility only.
    """
    data = catalog_images()
    if data.get("error"):
        raise HTTPException(status_code=500, detail=data["error"])
    return data


@app.get("/dashboard/metrics")
def get_dashboard_metrics(database: Session = Depends(db.get_db)) -> dict:
    """Get aggregated system metrics for the dashboard.

    Returns agent counts, container counts, CPU/memory usage, and lab stats.
    Labs running count is based on actual container presence, not database state.
    """
    import json
    from app.utils.lab import find_lab_by_prefix

    # Get all hosts
    hosts = database.query(models.Host).all()
    online_agents = sum(1 for h in hosts if h.status == "online")
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
        if host.status != "online":
            continue
        online_count += 1
        try:
            usage = json.loads(host.resource_usage) if host.resource_usage else {}
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
        except (json.JSONDecodeError, TypeError):
            pass

    # Calculate averages
    avg_cpu = total_cpu / online_count if online_count > 0 else 0
    avg_memory = total_memory / online_count if online_count > 0 else 0

    # Storage: aggregate totals, calculate overall percent
    storage_percent = (total_disk_used / total_disk_total * 100) if total_disk_total > 0 else 0

    # Use container-based count as source of truth for running labs
    running_labs = len(labs_with_containers)

    # Determine if multi-host environment
    is_multi_host = total_agents > 1

    return {
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


@app.get("/dashboard/metrics/containers")
def get_containers_breakdown(database: Session = Depends(db.get_db)) -> dict:
    """Get detailed container and VM breakdown by lab."""
    import json
    from app.utils.lab import find_lab_with_name

    hosts = database.query(models.Host).filter(models.Host.status == "online").all()
    all_labs = database.query(models.Lab).all()
    # Map both full ID and truncated prefix to lab info
    labs_by_id = {lab.id: lab.name for lab in all_labs}
    labs_by_prefix = {lab.id[:20]: (lab.id, lab.name) for lab in all_labs}  # short prefix for matching

    all_containers = []
    all_vms = []
    for host in hosts:
        try:
            usage = json.loads(host.resource_usage) if host.resource_usage else {}
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
        except (json.JSONDecodeError, TypeError):
            pass

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

    return {
        "by_lab": by_lab,
        "system_containers": system_containers,
        "total_running": sum(1 for c in all_containers if c.get("status") == "running"),
        "total_stopped": sum(1 for c in all_containers if c.get("status") != "running"),
        "vms_running": sum(1 for vm in all_vms if vm.get("status") == "running"),
        "vms_stopped": sum(1 for vm in all_vms if vm.get("status") != "running"),
    }


@app.get("/dashboard/metrics/resources")
def get_resource_distribution(database: Session = Depends(db.get_db)) -> dict:
    """Get resource usage distribution by agent and lab."""
    import json
    from app.utils.lab import find_lab_by_prefix

    hosts = database.query(models.Host).filter(models.Host.status == "online").all()
    all_labs = database.query(models.Lab).all()
    labs_by_id = {lab.id: lab.name for lab in all_labs}

    by_agent = []
    lab_containers = {}  # lab_id -> container count

    for host in hosts:
        usage = json.loads(host.resource_usage) if host.resource_usage else {}
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

    return {"by_agent": by_agent, "by_lab": sorted(by_lab, key=lambda x: -x["container_count"])}
