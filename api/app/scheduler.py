"""Standalone scheduler service for background monitor tasks.

This module runs as a separate process from the API, handling all periodic
background tasks (reconciliation, health checks, enforcement, etc.).
Separating monitors from the API ensures that:
1. A slow/blocked monitor can't make the API unresponsive
2. The API process only handles HTTP requests + WebSocket connections
3. Monitor crashes are isolated and auto-restarted via supervisor

Usage:
    python -m app.scheduler
"""
# ruff: noqa: E402  -- faulthandler setup must run before other imports
from __future__ import annotations

import asyncio
import faulthandler
import logging
import signal
import sys

faulthandler.enable()
faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)

from datetime import datetime, timezone

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from sqlalchemy import text

from app import db
from app.config import settings
from app.logging_config import setup_logging
from app.utils.async_tasks import setup_asyncio_exception_handler, safe_create_task
from app.utils.supervisor import supervised_task

# Monitor imports
from app.tasks.health import agent_health_monitor
from app.tasks.job_health import job_health_monitor
from app.tasks.reconciliation import state_reconciliation_monitor
from app.tasks.disk_cleanup import disk_cleanup_monitor
from app.tasks.image_reconciliation import image_reconciliation_monitor
from app.tasks.state_enforcement import state_enforcement_monitor
from app.tasks.link_reconciliation import link_reconciliation_monitor
from app.tasks.cleanup_handler import cleanup_event_monitor
from app.events.publisher import close_publisher

setup_logging()
logger = logging.getLogger(__name__)

# Track tasks for shutdown
_monitor_tasks: list[asyncio.Task] = []


async def healthz(request: Request) -> JSONResponse:
    """Scheduler health probe."""
    active = sum(1 for t in _monitor_tasks if not t.done())
    total = len(_monitor_tasks)
    result: dict = {
        "status": "ok",
        "service": "scheduler",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "monitors": {"active": active, "total": total},
    }
    try:
        pool = db.engine.pool
        result["db_pool"] = {
            "size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
        }
    except Exception:
        pass
    return JSONResponse(result)


async def metrics(_: Request) -> Response:
    """Prometheus metrics endpoint for scheduler process."""
    from app.metrics import get_metrics

    content, content_type = get_metrics()
    return Response(content=content, media_type=content_type)


async def startup():
    """Start all supervised monitor tasks."""
    global _monitor_tasks

    logger.info("Starting Archetype Scheduler")
    setup_asyncio_exception_handler()

    # Wait for database to be ready (migrations are handled by the API)
    logger.info("Verifying database connectivity")
    for attempt in range(30):
        try:
            with db.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection verified")
            break
        except Exception as e:
            if attempt == 29:
                logger.error(f"Database not reachable after 30 attempts: {e}")
                raise
            logger.warning(f"Database not ready (attempt {attempt + 1}/30), retrying in 2s...")
            await asyncio.sleep(2)

    # Start all monitors wrapped in supervisors
    monitors = [
        ("agent_health_monitor", agent_health_monitor),
        ("job_health_monitor", job_health_monitor),
        ("state_reconciliation_monitor", state_reconciliation_monitor),
        ("disk_cleanup_monitor", disk_cleanup_monitor),
        ("image_reconciliation_monitor", image_reconciliation_monitor),
        ("state_enforcement_monitor", state_enforcement_monitor),
        ("link_reconciliation_monitor", link_reconciliation_monitor),
    ]

    for name, monitor_fn in monitors:
        task = safe_create_task(
            supervised_task(monitor_fn, name=name),
            name=f"supervised_{name}",
        )
        _monitor_tasks.append(task)

    # Conditional monitors
    if settings.cleanup_event_driven_enabled:
        task = safe_create_task(
            supervised_task(cleanup_event_monitor, name="cleanup_event_monitor"),
            name="supervised_cleanup_event_monitor",
        )
        _monitor_tasks.append(task)

    logger.info(f"Started {len(_monitor_tasks)} supervised monitor tasks")


async def shutdown():
    """Cancel all monitor tasks on shutdown."""
    logger.info("Shutting down Archetype Scheduler")

    for task in _monitor_tasks:
        task.cancel()

    # Wait for all tasks to finish cancellation
    if _monitor_tasks:
        await asyncio.gather(*_monitor_tasks, return_exceptions=True)

    await close_publisher()
    logger.info("Scheduler shutdown complete")


app = Starlette(
    routes=[Route("/healthz", healthz), Route("/metrics", metrics)],
    on_startup=[startup],
    on_shutdown=[shutdown],
)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.scheduler:app",
        host="0.0.0.0",
        port=8002,
        log_level="info",
    )
