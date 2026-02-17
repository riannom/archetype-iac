"""System information endpoints including version and updates."""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import db, models
from app.auth import get_current_admin
from app.config import settings
from app.schemas import LoginDefaultsOut, UpdateInfo, VersionInfo
from app.services.link_reservations import get_link_endpoint_reservation_health_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])

# Cache for update check results
_update_cache: dict = {
    "data": None,
    "timestamp": 0,
}


def get_or_create_infra_settings(database: Session) -> models.InfraSettings:
    settings_row = database.get(models.InfraSettings, "global")
    if not settings_row:
        settings_row = models.InfraSettings(id="global")
        database.add(settings_row)
        database.commit()
        database.refresh(settings_row)
    return settings_row


def get_version() -> str:
    """Read version from VERSION file at repository root."""
    # Try multiple locations for VERSION file
    possible_paths = [
        Path(__file__).parent.parent.parent.parent / "VERSION",  # /api/../VERSION
        Path("/app/VERSION"),  # Docker container path
        Path("VERSION"),  # Current working directory
    ]

    for version_path in possible_paths:
        if version_path.exists():
            return version_path.read_text().strip()

    return "0.0.0"  # Fallback if VERSION file not found


def get_commit() -> str:
    """Read commit SHA from env or GIT_SHA file."""
    env_sha = os.getenv("ARCHETYPE_GIT_SHA", "").strip()
    if env_sha:
        return env_sha

    possible_paths = [
        Path(__file__).parent.parent.parent.parent / "GIT_SHA",
        Path("/app/GIT_SHA"),
        Path("GIT_SHA"),
    ]
    for commit_path in possible_paths:
        if commit_path.exists():
            try:
                return commit_path.read_text().strip()
            except Exception:
                pass
    return "unknown"


@router.get("/version", response_model=VersionInfo)
def get_version_info() -> VersionInfo:
    """Get current application version.

    Returns the version string read from the VERSION file at the repository root.
    """
    return VersionInfo(version=get_version(), commit=get_commit())


@router.get("/updates", response_model=UpdateInfo)
async def check_for_updates() -> UpdateInfo:
    """Check GitHub for available updates.

    Queries the GitHub releases API to check if a newer version is available.
    Results are cached for 1 hour to avoid rate limiting.

    Returns:
        UpdateInfo with current version, latest version, and release details
    """
    current_version = get_version()
    now = time.time()

    # Check cache
    if (
        _update_cache["data"] is not None
        and (now - _update_cache["timestamp"]) < settings.version_check_cache_ttl
    ):
        cached = _update_cache["data"]
        # Update current_version in case it changed (unlikely but possible)
        cached["current_version"] = current_version
        return UpdateInfo(**cached)

    # Fetch from GitHub
    github_url = f"https://api.github.com/repos/{settings.github_repo}/releases/latest"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                github_url,
                headers={
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": f"Archetype/{current_version}",
                },
            )

            if response.status_code == 404:
                # No releases yet
                result = {
                    "current_version": current_version,
                    "latest_version": None,
                    "update_available": False,
                    "release_url": None,
                    "release_notes": None,
                    "published_at": None,
                    "error": None,
                }
                _update_cache["data"] = result
                _update_cache["timestamp"] = now
                return UpdateInfo(**result)

            if response.status_code != 200:
                logger.warning(
                    f"GitHub API returned {response.status_code}: {response.text}"
                )
                return UpdateInfo(
                    current_version=current_version,
                    error=f"GitHub API error: {response.status_code}",
                )

            data = response.json()

    except httpx.ConnectError:
        logger.warning("Cannot connect to GitHub API")
        return UpdateInfo(
            current_version=current_version,
            error="Cannot connect to GitHub",
        )
    except httpx.TimeoutException:
        logger.warning("GitHub API request timed out")
        return UpdateInfo(
            current_version=current_version,
            error="GitHub API timeout",
        )
    except Exception as e:
        logger.error(f"Error checking for updates: {e}")
        return UpdateInfo(
            current_version=current_version,
            error=str(e),
        )

    # Parse release info
    tag_name = data.get("tag_name", "")
    # Strip 'v' prefix if present
    latest_version = tag_name.lstrip("v")

    # Compare versions (simple string comparison works for semver)
    update_available = _compare_versions(latest_version, current_version) > 0

    result = {
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
        "release_url": data.get("html_url"),
        "release_notes": data.get("body"),
        "published_at": data.get("published_at"),
        "error": None,
    }

    # Cache the result
    _update_cache["data"] = result
    _update_cache["timestamp"] = now

    return UpdateInfo(**result)


@router.get("/login-defaults", response_model=LoginDefaultsOut)
def get_login_defaults(database: Session = Depends(db.get_db)) -> LoginDefaultsOut:
    """Get public login screen defaults.

    This endpoint is intentionally unauthenticated so the login page can load
    global defaults before a user signs in.
    """
    settings_row = get_or_create_infra_settings(database)
    return LoginDefaultsOut(
        dark_theme_id=settings_row.login_dark_theme_id,
        dark_background_id=settings_row.login_dark_background_id,
        dark_background_opacity=settings_row.login_dark_background_opacity,
        light_theme_id=settings_row.login_light_theme_id,
        light_background_id=settings_row.login_light_background_id,
        light_background_opacity=settings_row.login_light_background_opacity,
    )


@router.get("/link-reservations/health")
def get_link_reservations_health(
    sample_limit: int = Query(
        20,
        ge=1,
        le=200,
        description="Maximum sample rows per drift category",
    ),
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_admin),
) -> dict[str, Any]:
    """Return DB-backed health snapshot for link endpoint reservations."""
    _ = current_user
    return get_link_endpoint_reservation_health_snapshot(database, sample_limit=sample_limit)


def _compare_versions(v1: str, v2: str) -> int:
    """Compare two semver version strings.

    Returns:
        1 if v1 > v2, -1 if v1 < v2, 0 if equal
    """
    if not v1 or not v2:
        return 0

    def parse_version(v: str) -> tuple[int, ...]:
        """Parse version string into tuple of integers."""
        # Handle pre-release suffixes like -alpha, -beta, -rc1
        base = v.split("-")[0]
        parts = []
        for part in base.split("."):
            try:
                parts.append(int(part))
            except ValueError:
                parts.append(0)
        # Pad to at least 3 parts
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts)

    try:
        v1_tuple = parse_version(v1)
        v2_tuple = parse_version(v2)

        if v1_tuple > v2_tuple:
            return 1
        elif v1_tuple < v2_tuple:
            return -1
        return 0
    except Exception:
        # Fallback to string comparison
        if v1 > v2:
            return 1
        elif v1 < v2:
            return -1
        return 0


# --- System Alerts ---

class AgentAlert(BaseModel):
    """Alert for an agent with an error."""
    agent_id: str
    agent_name: str
    error_message: str
    error_since: str  # ISO format timestamp


class SystemAlertsResponse(BaseModel):
    """Response containing active system alerts."""
    alerts: list[AgentAlert]
    agent_error_count: int


@router.get("/alerts", response_model=SystemAlertsResponse)
def get_system_alerts(
    database: Session = Depends(db.get_db),
) -> SystemAlertsResponse:
    """Get active system alerts.

    Returns a list of agents that currently have errors (e.g., Docker state
    corruption, unreachable agents). These alerts persist until the error
    condition is resolved.

    Used by the UI to display a prominent alert banner when infrastructure
    issues need attention.
    """
    agents_with_errors = (
        database.query(models.Host)
        .filter(models.Host.last_error.isnot(None))
        .all()
    )

    alerts = [
        AgentAlert(
            agent_id=agent.id,
            agent_name=agent.name,
            error_message=agent.last_error,
            error_since=agent.error_since.isoformat() if agent.error_since else "",
        )
        for agent in agents_with_errors
    ]

    return SystemAlertsResponse(
        alerts=alerts,
        agent_error_count=len(alerts),
    )


# --- Diagnostics ---

class TaskInfo(BaseModel):
    """Information about a running async task."""
    name: str
    state: str
    done: bool
    cancelled: bool
    has_exception: bool
    exception_type: str | None = None
    exception_message: str | None = None


class RecentJobInfo(BaseModel):
    """Recent job for diagnostics."""
    job_id: str
    lab_id: str
    action: str
    status: str
    created_at: str
    started_at: str | None
    completed_at: str | None
    error_summary: str | None = None


class DiagnosticsResponse(BaseModel):
    """System diagnostics information for troubleshooting."""
    timestamp: str
    python_version: str
    asyncio_tasks: list[TaskInfo]
    task_count: int
    recent_failed_jobs: list[RecentJobInfo]
    recent_jobs: list[RecentJobInfo]
    event_loop_running: bool
    memory_info: dict[str, Any] | None = None


@router.get("/diagnostics", response_model=DiagnosticsResponse)
async def get_diagnostics(
    database: Session = Depends(db.get_db),
) -> DiagnosticsResponse:
    """Get system diagnostics for troubleshooting.

    Returns information about running asyncio tasks, recent jobs, and system state.
    Useful for debugging crashes and performance issues.

    Note: This endpoint is primarily for debugging. In production, you may want
    to restrict access to admins only.
    """
    # Get all running asyncio tasks
    tasks_info = []
    all_tasks = asyncio.all_tasks()

    for task in all_tasks:
        task_state = "running"
        if task.done():
            task_state = "done"
        elif task.cancelled():
            task_state = "cancelled"

        exception_type = None
        exception_message = None
        has_exception = False

        if task.done() and not task.cancelled():
            try:
                exc = task.exception()
                if exc:
                    has_exception = True
                    exception_type = type(exc).__name__
                    exception_message = str(exc)[:200]
            except asyncio.CancelledError:
                pass
            except asyncio.InvalidStateError:
                pass

        tasks_info.append(TaskInfo(
            name=task.get_name(),
            state=task_state,
            done=task.done(),
            cancelled=task.cancelled(),
            has_exception=has_exception,
            exception_type=exception_type,
            exception_message=exception_message,
        ))

    # Get recent failed jobs
    failed_jobs = (
        database.query(models.Job)
        .filter(models.Job.status == "failed")
        .order_by(models.Job.created_at.desc())
        .limit(10)
        .all()
    )

    # Get most recent jobs regardless of status
    recent_jobs = (
        database.query(models.Job)
        .order_by(models.Job.created_at.desc())
        .limit(20)
        .all()
    )

    def job_to_info(job: models.Job) -> RecentJobInfo:
        error_summary = None
        if job.status == "failed" and job.log_path:
            # Extract first line of error
            error_summary = job.log_path.split('\n')[0][:200] if job.log_path else None
        return RecentJobInfo(
            job_id=job.id,
            lab_id=job.lab_id,
            action=job.action,
            status=job.status,
            created_at=job.created_at.isoformat() if job.created_at else "",
            started_at=job.started_at.isoformat() if job.started_at else None,
            completed_at=job.completed_at.isoformat() if job.completed_at else None,
            error_summary=error_summary,
        )

    # Try to get memory info
    memory_info = None
    try:
        import resource
        rusage = resource.getrusage(resource.RUSAGE_SELF)
        memory_info = {
            "max_rss_mb": rusage.ru_maxrss / 1024,  # Convert to MB
            "user_time_s": rusage.ru_utime,
            "system_time_s": rusage.ru_stime,
        }
    except ImportError:
        pass

    # Check if event loop is running
    try:
        loop = asyncio.get_running_loop()
        event_loop_running = loop.is_running()
    except RuntimeError:
        event_loop_running = False

    return DiagnosticsResponse(
        timestamp=datetime.now(timezone.utc).isoformat(),
        python_version=sys.version,
        asyncio_tasks=tasks_info,
        task_count=len(all_tasks),
        recent_failed_jobs=[job_to_info(j) for j in failed_jobs],
        recent_jobs=[job_to_info(j) for j in recent_jobs],
        event_loop_running=event_loop_running,
        memory_info=memory_info,
    )
