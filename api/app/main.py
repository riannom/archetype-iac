"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware

from app import db, models
from app.db import SessionLocal
from app.config import settings
from app.auth import get_current_user, hash_password
from app.catalog import list_devices as catalog_devices, list_images as catalog_images
from app.middleware import CurrentUserMiddleware
from app.routers import admin, agents, auth, console, images, jobs, labs, permissions
from app.tasks.health import agent_health_monitor

logger = logging.getLogger(__name__)

# Background task handle
_agent_monitor_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - start background tasks on startup, cleanup on shutdown."""
    global _agent_monitor_task

    # Startup
    logger.info("Starting Aura API controller")

    # Create database tables
    logger.info("Creating database tables")
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

    # Start agent health monitor background task
    _agent_monitor_task = asyncio.create_task(agent_health_monitor())

    yield

    # Shutdown
    logger.info("Shutting down Aura API controller")

    if _agent_monitor_task:
        _agent_monitor_task.cancel()
        try:
            await _agent_monitor_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Netlab GUI API", version="0.1.0", lifespan=lifespan)

# Middleware
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

# Include routers
app.include_router(auth.router)
app.include_router(agents.router)
app.include_router(labs.router)
app.include_router(jobs.router)
app.include_router(permissions.router)
app.include_router(images.router)
app.include_router(console.router)
app.include_router(admin.router)


# Simple endpoints that remain in main.py
@app.get("/health")
def health(request: Request) -> dict[str, str]:
    user = request.state.user
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "user": user.email if user else "",
    }


@app.get("/devices")
def list_devices() -> dict[str, object]:
    data = catalog_devices()
    if data.get("error"):
        raise HTTPException(status_code=500, detail=data["error"])
    return data


@app.get("/images")
def list_images() -> dict[str, object]:
    data = catalog_images()
    if data.get("error"):
        raise HTTPException(status_code=500, detail=data["error"])
    return data


@app.get("/vendors")
def list_vendors() -> list[dict]:
    """Return vendor configurations for frontend device catalog.

    This endpoint provides a unified view of all supported network devices,
    including their categories, icons, versions, and availability status.
    Data is sourced from the centralized vendor registry in agent/vendors.py.
    """
    from agent.vendors import get_vendors_for_ui
    return get_vendors_for_ui()


@app.get("/dashboard/metrics")
def get_dashboard_metrics(database: Session = Depends(db.get_db)) -> dict:
    """Get aggregated system metrics for the dashboard.

    Returns agent counts, container counts, CPU/memory usage, and lab stats.
    """
    import json

    # Get all hosts
    hosts = database.query(models.Host).all()
    online_agents = sum(1 for h in hosts if h.status == "online")
    total_agents = len(hosts)

    # Aggregate resource usage from all online agents
    total_cpu = 0.0
    total_memory = 0.0
    total_containers_running = 0
    total_containers = 0
    online_count = 0

    for host in hosts:
        if host.status != "online":
            continue
        online_count += 1
        try:
            usage = json.loads(host.resource_usage) if host.resource_usage else {}
            total_cpu += usage.get("cpu_percent", 0)
            total_memory += usage.get("memory_percent", 0)
            total_containers_running += usage.get("containers_running", 0)
            total_containers += usage.get("containers_total", 0)
        except (json.JSONDecodeError, TypeError):
            pass

    # Calculate averages
    avg_cpu = total_cpu / online_count if online_count > 0 else 0
    avg_memory = total_memory / online_count if online_count > 0 else 0

    # Get lab counts
    all_labs = database.query(models.Lab).all()
    running_labs = sum(1 for lab in all_labs if lab.state in ("running", "starting"))

    return {
        "agents": {"online": online_agents, "total": total_agents},
        "containers": {"running": total_containers_running, "total": total_containers},
        "cpu_percent": round(avg_cpu, 1),
        "memory_percent": round(avg_memory, 1),
        "labs_running": running_labs,
        "labs_total": len(all_labs),
    }
