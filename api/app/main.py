"""FastAPI application entry point."""
# ruff: noqa: E402  -- faulthandler setup must run before other imports
from __future__ import annotations

import faulthandler
import logging
import signal
import sys
import traceback

# Enable faulthandler for SIGUSR1 stack dumps
faulthandler.enable()
faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware

from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse as StarletteJSONResponse
from starlette.routing import Route

from app import db, models
from app.config import settings
from app.auth import get_current_user, hash_password
from app.logging_config import (
    correlation_id_var,
    generate_correlation_id,
    setup_logging,
)
from app.middleware import CurrentUserMiddleware, DeprecationMiddleware
from app.routers.v1 import router as v1_router
from app.routers import admin, agents, auth, callbacks, console, dashboard, events, images, infrastructure, iso, jobs, lab_tests, labs, permissions, scenarios, state_ws, support, system, users, vendors, webhooks
from app.events.publisher import close_publisher
from app.utils.async_tasks import capture_event_loop, setup_asyncio_exception_handler
from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command

# Configure structured logging at module load
setup_logging()

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - run migrations and seed data on startup."""
    # Startup
    logger.info("Starting Archetype API controller")

    # JWT secret validation - must happen before anything else
    WEAK_SECRETS = {"", "changeme", "secret", "jwt_secret", "your-secret-key", "test"}
    if not settings.jwt_secret:
        logger.critical("JWT_SECRET is not configured - refusing to start")
        raise SystemExit("JWT_SECRET must be set")
    if settings.jwt_secret in WEAK_SECRETS:
        logger.warning("JWT_SECRET appears to be a weak/default value - change it in production!")

    # Capture event loop for sync→async task scheduling and set up exception handler
    capture_event_loop()
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

    # Keep catalog identity rows synchronized with runtime registry/custom devices.
    try:
        from app.services.catalog_service import ensure_catalog_identity_synced

        with db.get_session() as session:
            sync_result = ensure_catalog_identity_synced(
                session,
                source="runtime_identity_sync",
            )
            if sync_result.get("applied"):
                logger.info("Catalog identity sync applied at startup")
            else:
                logger.info(
                    "Catalog identity sync skipped at startup: %s",
                    sync_result.get("reason"),
                )
    except Exception:
        logger.exception("Catalog identity startup sync failed; registry fallback will be used")

    # Seed admin user if configured
    admin_username = settings.admin_username
    admin_email = settings.admin_email
    admin_password = settings.admin_password
    if admin_password and (admin_username or admin_email):
        with db.get_session() as session:
            # Check if admin already exists by username or email
            existing = None
            if admin_username:
                existing = session.query(models.User).filter(
                    models.User.username == admin_username.lower()
                ).first()
            if not existing and admin_email:
                existing = session.query(models.User).filter(
                    models.User.email == admin_email
                ).first()
            if not existing:
                if len(admin_password.encode("utf-8")) > 72:
                    logger.warning("Skipping admin seed: ADMIN_PASSWORD must be 72 bytes or fewer")
                else:
                    # Derive username if only email provided
                    username = admin_username.lower() if admin_username else admin_email.split("@")[0].lower()
                    admin_user = models.User(
                        username=username,
                        email=admin_email or f"{username}@local",
                        hashed_password=hash_password(admin_password),
                        global_role="super_admin",
                    )
                    session.add(admin_user)
                    session.commit()
                    logger.info(f"Created admin user: {username}")

    # Background monitors run in the separate scheduler service.
    # See app/scheduler.py and docker-compose scheduler service.

    yield

    # Shutdown
    logger.info("Shutting down Archetype API controller")
    from app.db import async_engine
    await async_engine.dispose()
    await close_publisher()


async def healthz(request: StarletteRequest) -> StarletteJSONResponse:
    """Lightweight health probe that bypasses all middleware.

    Always returns 200 to prove the event loop is alive.
    DB and Redis status are informational only — probes run in
    background threads so a slow DB/Redis never stalls the event loop.
    """
    import asyncio
    import time

    result: dict = {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    def _check_db() -> tuple[str, float | None]:
        start = time.monotonic()
        with db.get_session() as session:
            session.execute(sa_text("SELECT 1"))
        return "ok", round((time.monotonic() - start) * 1000, 1)

    def _check_redis() -> tuple[str, float | None]:
        start = time.monotonic()
        r = db.get_redis()
        r.ping()
        return "ok", round((time.monotonic() - start) * 1000, 1)

    # Run blocking probes in threads with a timeout
    for label, probe in [("db", _check_db), ("redis", _check_redis)]:
        try:
            status, ms = await asyncio.wait_for(
                asyncio.to_thread(probe), timeout=2.0
            )
            result[label] = status
            result[f"{label}_ms"] = ms
        except asyncio.TimeoutError:
            result[label] = "error: timeout"
        except Exception as e:
            result[label] = f"error: {type(e).__name__}"

    # Pool stats (informational, non-blocking)
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

    # Process memory (informational, single syscall)
    try:
        import resource as res_mod
        rusage = res_mod.getrusage(res_mod.RUSAGE_SELF)
        result["process_memory_mb"] = round(rusage.ru_maxrss / 1024, 1)
    except Exception:
        pass

    return StarletteJSONResponse(result)


app = FastAPI(
    title="Archetype API",
    version="0.5.0",
    lifespan=lifespan,
    routes=[Route("/healthz", healthz)],  # Bypass all middleware
)


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
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
)
app.add_middleware(CurrentUserMiddleware)
app.add_middleware(DeprecationMiddleware, sunset_date="2026-12-01")

# Include routers
app.include_router(auth.router)
app.include_router(agents.router)
app.include_router(labs.router)
app.include_router(jobs.router)
app.include_router(permissions.router)
app.include_router(users.router)
app.include_router(images.router)
app.include_router(support.router)
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
app.include_router(dashboard.router)
app.include_router(lab_tests.router)
app.include_router(scenarios.router)
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


@app.get("/disk-usage")
async def disk_usage(current_user: models.User = Depends(get_current_user)):
    """Resource pressure status (admin only)."""
    import asyncio
    from app.utils.http import require_admin
    require_admin(current_user)
    from app.services.resource_monitor import ResourceMonitor
    return await asyncio.to_thread(ResourceMonitor.get_status)


_metrics_last_update: float = 0.0
_METRICS_MIN_INTERVAL: float = 15.0  # seconds between recomputes


@app.get("/metrics")
def metrics(database: Session = Depends(db.get_db)):
    """Prometheus metrics endpoint.

    Returns metrics in Prometheus text format for scraping.
    Recomputes from database at most every 15 seconds.
    """
    import time
    from app.metrics import get_metrics, update_all_metrics

    global _metrics_last_update
    now = time.monotonic()
    if now - _metrics_last_update >= _METRICS_MIN_INTERVAL:
        update_all_metrics(database)
        _metrics_last_update = now

    content, content_type = get_metrics()
    return Response(content=content, media_type=content_type)
