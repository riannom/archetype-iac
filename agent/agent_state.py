"""Shared mutable state for the Archetype agent.

This module holds all module-level globals, accessor functions, and mutators
that are shared across agent modules. Callers should use the module-attribute
access pattern:

    import agent.agent_state as _state
    _state.AGENT_ID        # always reads current value
    _state.set_agent_id(x) # cross-module mutation
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone

from agent.config import settings

# ---------------------------------------------------------------------------
# Identity & Lifecycle
# ---------------------------------------------------------------------------

AGENT_ID: str = settings.agent_id or str(uuid.uuid4())[:8]
AGENT_STARTED_AT: datetime = datetime.now(timezone.utc)


def set_agent_id(new_id: str) -> None:
    """Set the agent ID (used when controller assigns an existing ID)."""
    global AGENT_ID
    AGENT_ID = new_id


# ---------------------------------------------------------------------------
# Registration & Background Tasks
# ---------------------------------------------------------------------------

_registered: bool = False
_heartbeat_task: asyncio.Task | None = None
_event_listener_task: asyncio.Task | None = None
_fix_interfaces_task: asyncio.Task | None = None


def set_registered(value: bool) -> None:
    global _registered
    _registered = value


def set_heartbeat_task(task: asyncio.Task | None) -> None:
    global _heartbeat_task
    _heartbeat_task = task


def set_event_listener_task(task: asyncio.Task | None) -> None:
    global _event_listener_task
    _event_listener_task = task


def set_fix_interfaces_task(task: asyncio.Task | None) -> None:
    global _fix_interfaces_task
    _fix_interfaces_task = task


# ---------------------------------------------------------------------------
# Deploy Results Cache
# ---------------------------------------------------------------------------

_deploy_results: dict[str, asyncio.Future] = {}

# ---------------------------------------------------------------------------
# Lock Manager
# ---------------------------------------------------------------------------

_lock_manager = None


def get_lock_manager():
    """Get the deploy lock manager."""
    return _lock_manager


def set_lock_manager(mgr) -> None:
    global _lock_manager
    _lock_manager = mgr


# ---------------------------------------------------------------------------
# Event Listener
# ---------------------------------------------------------------------------

_event_listener = None


def get_event_listener():
    """Lazy-initialize Docker event listener."""
    global _event_listener
    if _event_listener is None:
        from agent.events import DockerEventListener
        _event_listener = DockerEventListener()
    return _event_listener


# ---------------------------------------------------------------------------
# Active Jobs Counter
# ---------------------------------------------------------------------------

_active_jobs: int = 0


def get_active_jobs() -> int:
    """Get the current count of active jobs."""
    return _active_jobs


def _increment_active_jobs() -> None:
    """Increment the active jobs counter."""
    global _active_jobs
    _active_jobs += 1


def _decrement_active_jobs() -> None:
    """Decrement the active jobs counter."""
    global _active_jobs
    _active_jobs = max(0, _active_jobs - 1)


# ---------------------------------------------------------------------------
# Network Manager Accessors
# ---------------------------------------------------------------------------

def get_overlay_manager():
    """Lazy-initialize overlay manager."""
    from agent.network.backends.registry import get_network_backend
    return get_network_backend().overlay_manager


def get_ovs_manager():
    """Lazy-initialize OVS network manager."""
    from agent.network.backends.registry import get_network_backend
    return get_network_backend().ovs_manager


# ---------------------------------------------------------------------------
# Local IP Detection
# ---------------------------------------------------------------------------

_cached_local_ip: str | None = None
_local_ip_detected: bool = False


def _detect_local_ip() -> str | None:
    """Return cached auto-detected local IP (populated at startup)."""
    return _cached_local_ip


async def _async_detect_local_ip() -> str | None:
    """Auto-detect local IP address from default route interface (non-blocking)."""
    global _cached_local_ip, _local_ip_detected
    if _local_ip_detected:
        return _cached_local_ip
    try:
        from agent.network.cmd import run_cmd as _async_run_cmd
        code, stdout, _ = await _async_run_cmd(["ip", "route", "get", "1.1.1.1"])
        if code == 0:
            # Output: "1.1.1.1 via X.X.X.X dev ethX src Y.Y.Y.Y uid 0"
            parts = stdout.split()
            if "src" in parts:
                src_idx = parts.index("src")
                if src_idx + 1 < len(parts):
                    import logging
                    ip = parts[src_idx + 1]
                    logging.getLogger(__name__).info(f"Auto-detected local IP: {ip}")
                    _cached_local_ip = ip
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to auto-detect local IP: {e}")
    _local_ip_detected = True
    return _cached_local_ip


# ---------------------------------------------------------------------------
# Compiled Regexes (validation)
# ---------------------------------------------------------------------------

_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
_PORT_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]+$")
_CONTAINER_PREFIX_RE = re.compile(r"^(archetype-|arch-)")
