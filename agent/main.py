"""Archetype Agent - Host-level orchestration agent.

This agent runs on each compute host and handles:
- Container lifecycle via DockerProvider
- VM lifecycle via LibvirtProvider
- Console access to running nodes
- Network overlay management
- Health reporting to controller
"""
# ruff: noqa: E402  -- agent setup and logging must run before other imports

from __future__ import annotations

import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from agent.config import settings
from agent.network.backends.registry import get_network_backend
from agent.version import __version__
from agent.updater import check_and_rollback
from agent.logging_config import setup_agent_logging

# Module-level imports for testability (patch targets need module-level attributes)
import docker  # noqa: F401
from agent.docker_client import get_docker_client  # noqa: F401
from agent.http_client import get_http_client, close_http_client, get_controller_auth_headers  # noqa: F401
from agent.console.docker_exec import DockerConsole  # noqa: F401
from agent.console.ssh_console import SSHConsole  # noqa: F401
from agent.readiness import get_probe_for_vendor, run_post_boot_commands  # noqa: F401
from agent.providers import get_provider, list_providers  # noqa: F401
from agent.version import get_commit  # noqa: F401

# Phase 7: Shared state, helpers, and registration extracted to dedicated modules
import agent.agent_state as _state
from agent.agent_state import (  # noqa: F401
    get_active_jobs, _increment_active_jobs, _decrement_active_jobs,
    get_overlay_manager, get_ovs_manager, get_event_listener, get_lock_manager,
    _SAFE_ID_RE, _PORT_NAME_RE, _CONTAINER_PREFIX_RE,
)
from agent.helpers import (  # noqa: F401
    get_workspace, get_provider_for_request, provider_status_to_schema,
    get_capabilities, get_resource_usage, get_agent_info,
    _sync_get_resource_usage,
    _validate_port_name, _validate_container_name,
    _get_allocated_resources, DEFAULT_CONTAINER_MEMORY_MB,
    _interface_name_to_index, _resolve_ovs_port, _resolve_ovs_port_via_ifindex,
    _resolve_ifindex_sync,
    _ovs_set_port_vlan, _ovs_get_port_vlan, _ovs_list_used_vlans,
    _ovs_allocate_link_vlan, _ovs_allocate_unique_vlan,
    _pick_free_vlan, _pick_isolation_vlan,
    OVSPortInfo, _load_node_startup_config, _render_n9kv_poap_script,
    _get_docker_images, _get_docker_ovs_plugin,
    _sync_prune_docker,
    _fix_running_interfaces, _cleanup_lingering_virsh_sessions,
)
from agent.registration import (  # noqa: F401
    forward_event_to_controller, register_with_controller,
    _bootstrap_transport_config, send_heartbeat, heartbeat_loop,
)

# Configure structured logging (uses AGENT_ID from agent_state)
setup_agent_logging(_state.AGENT_ID)

import logging

logger = logging.getLogger(__name__)

# Backward-compat aliases for code that reads module-level globals directly
AGENT_ID = _state.AGENT_ID
AGENT_STARTED_AT = _state.AGENT_STARTED_AT


def _parse_driver_status(driver_status: Any) -> dict[str, str]:
    """Normalize Docker DriverStatus into a key-value dict."""
    status: dict[str, str] = {}
    if not isinstance(driver_status, list):
        return status

    for item in driver_status:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            key, value = item
            status[str(key)] = str(value)
    return status


def _classify_docker_snapshotter_mode(
    driver: str,
    driver_type: str | None,
) -> str:
    """Classify Docker image-store mode for drift detection."""
    if driver_type and "io.containerd.snapshotter.v1" in driver_type:
        return "containerd"
    if not driver_type:
        # Legacy Docker image store typically reports no driver-type metadata.
        return "legacy"
    if driver in {"overlay2", "overlayfs"} and "snapshotter" not in driver_type:
        return "legacy"
    return "unknown"


async def _log_docker_snapshotter_mode_at_startup() -> None:
    """Log Docker image-store mode and warn on config drift."""
    if not settings.enable_docker:
        return

    expected_raw = (settings.docker_snapshotter_expected_mode or "any").strip().lower()
    if expected_raw not in {"legacy", "containerd", "any"}:
        logger.warning(
            f"Invalid docker_snapshotter_expected_mode={settings.docker_snapshotter_expected_mode!r}; "
            "using 'any'"
        )
        expected = "any"
    else:
        expected = expected_raw

    try:
        client = get_docker_client()
        info = await asyncio.to_thread(client.info)
        driver = str(info.get("Driver") or "unknown")
        driver_status = _parse_driver_status(info.get("DriverStatus"))
        driver_type = driver_status.get("driver-type")
        mode = _classify_docker_snapshotter_mode(driver, driver_type)

        logger.info(
            f"Docker snapshotter mode detected: mode={mode}, "
            f"driver={driver}, driver_type={driver_type or 'n/a'}, expected={expected}"
        )

        if expected != "any" and mode != expected:
            logger.warning(
                f"Docker snapshotter drift detected: expected={expected}, detected={mode} "
                f"(driver={driver}, driver_type={driver_type or 'n/a'})"
            )
            if expected == "legacy" and mode == "containerd":
                logger.warning(
                    "Containerd snapshotter is active while legacy mode is expected. "
                    "If image loads fail with 'wrong diff id' or 'content digest not found', "
                    "set containerd-snapshotter=false in docker daemon.json and reload images."
                )
    except Exception as e:
        logger.warning(f"Failed to inspect Docker snapshotter mode: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - register on startup, cleanup on shutdown."""
    logger.info(f"Agent {_state.AGENT_ID} starting...")

    if os.getenv("ARCHETYPE_AGENT_TESTING") == "1":
        logger.info("Testing mode enabled; skipping agent startup tasks")
        yield
        return

    # Check for pending rollback from a failed update
    check_and_rollback()

    logger.info(f"Controller URL: {settings.controller_url}")
    logger.info(f"Capabilities: {get_capabilities()}")
    logger.info(f"Network backend: {get_network_backend().name}")
    await _log_docker_snapshotter_mode_at_startup()

    # Initialize Redis lock manager
    from agent.locks import DeployLockManager, NoopDeployLockManager, set_lock_manager
    lm = DeployLockManager(
        redis_url=settings.redis_url,
        lock_ttl=settings.lock_ttl,
        agent_id=_state.AGENT_ID,
    )
    try:
        await lm.ping()
        _state.set_lock_manager(lm)
        set_lock_manager(lm)
        logger.info(f"Redis lock manager initialized (TTL: {settings.lock_ttl}s)")
    except Exception as e:
        logger.error(f"Redis unavailable ({e}); continuing without distributed locks")
        lm = NoopDeployLockManager(agent_id=_state.AGENT_ID)
        _state.set_lock_manager(lm)
        set_lock_manager(lm)

    # Clean up any orphaned locks from previous run (crash recovery)
    try:
        cleared_locks = await lm.clear_agent_locks()
        if cleared_locks:
            logger.warning(f"Cleared {len(cleared_locks)} orphaned locks from previous run: {cleared_locks}")
    except Exception as e:
        logger.error(f"Failed to clear orphaned locks: {e}")

    # Recover network allocations from system state (crash recovery)
    try:
        backend = get_network_backend()
        init_info = await backend.initialize()
        if init_info.get("vnis_recovered", 0) > 0:
            logger.info(
                f"Recovered {init_info['vnis_recovered']} VNI allocations from system state"
            )
        if init_info.get("vlans_recovered", 0) > 0:
            logger.info(
                f"Recovered {init_info['vlans_recovered']} VLAN allocations from OVS state"
            )
        if init_info.get("ovs_plugin_started"):
            logger.info("Docker OVS network plugin started")
            _state.set_fix_interfaces_task(asyncio.create_task(_fix_running_interfaces()))
    except Exception as e:
        logger.warning(f"Failed to recover network allocations: {e}")

    # Start periodic network cleanup task
    from agent.network.cleanup import get_cleanup_manager
    cleanup_mgr = get_cleanup_manager()
    try:
        # Run initial cleanup to clear any orphans from previous crash
        # Skip OVS cleanup at startup - overlay manager tracking is empty until
        # reconciliation runs, so VXLAN ports would be incorrectly seen as orphans
        initial_stats = await cleanup_mgr.run_full_cleanup(include_ovs=False)
        if initial_stats.veths_deleted > 0 or initial_stats.bridges_deleted > 0:
            logger.info(f"Initial network cleanup: {initial_stats.to_dict()}")

        # Start periodic cleanup (every 5 minutes)
        await cleanup_mgr.start_periodic_cleanup(interval_seconds=300)
        logger.info("Periodic network cleanup started (interval: 5 minutes)")
    except Exception as e:
        logger.warning(f"Failed to start network cleanup: {e}")

    # Recover interrupted transfer state from previous run
    from agent.routers.images import _load_persisted_transfer_state
    _load_persisted_transfer_state()

    # Start periodic image transfer cleanup task
    from agent.image_cleanup import get_image_cleanup_manager
    image_cleanup_mgr = get_image_cleanup_manager(settings.workspace_path)
    try:
        initial_image_stats = await image_cleanup_mgr.cleanup_stale_temp_files()
        if initial_image_stats.temp_files_deleted > 0 or initial_image_stats.partial_files_deleted > 0:
            logger.info(f"Initial image cleanup: temp={initial_image_stats.temp_files_deleted}, partial={initial_image_stats.partial_files_deleted}")
        await image_cleanup_mgr.start_periodic_cleanup(interval_seconds=300)
        logger.info("Periodic image cleanup started (interval: 5 minutes)")
    except Exception as e:
        logger.warning(f"Failed to start image cleanup: {e}")

    # Pre-detect local IP asynchronously (caches result for get_agent_info)
    await _state._async_detect_local_ip()

    # Try initial registration (will notify controller if this is a restart)
    await register_with_controller()

    # Phase 2 bootstrap: fetch and apply transport config from controller
    await _bootstrap_transport_config()

    # Start heartbeat background task
    _state.set_heartbeat_task(asyncio.create_task(heartbeat_loop()))

    # Start Docker event listener if docker provider is enabled
    if settings.enable_docker:
        try:
            listener = get_event_listener()
            _state.set_event_listener_task(asyncio.create_task(
                listener.start(forward_event_to_controller)
            ))
            logger.info("Docker event listener started")
        except Exception as e:
            logger.error(f"Failed to start Docker event listener: {e}")

    # Start carrier state monitor (OVS link_state polling)
    _carrier_monitor = None
    _vm_port_refresh_task = None
    if settings.enable_ovs:
        try:
            from agent.network.carrier_monitor import CarrierMonitor, build_managed_ports
            from agent.callbacks import report_carrier_state_change

            ovs_mgr = get_ovs_manager()
            # Include Docker OVS plugin endpoints when the plugin is enabled
            plugin = _get_docker_ovs_plugin() if settings.enable_ovs_plugin else None
            libvirt_prov = get_provider("libvirt")

            # Seed VM port cache before monitor start so existing VMs are tracked.
            if libvirt_prov is not None:
                try:
                    await libvirt_prov.refresh_vm_monitored_ports()
                except Exception as e:
                    logger.warning(f"Initial VM port cache refresh failed: {e}")

            _carrier_monitor = CarrierMonitor(
                ovs_bridge=settings.ovs_bridge_name,
                get_managed_ports=lambda: build_managed_ports(ovs_mgr, plugin, libvirt_prov),
                notifier=report_carrier_state_change,
            )
            await _carrier_monitor.start(interval=settings.carrier_monitor_interval)

            # Periodic VM port cache refresh (tap devices appear/disappear on deploy/destroy).
            if libvirt_prov is not None:
                async def _vm_port_refresh_loop():
                    while True:
                        try:
                            await asyncio.sleep(30)
                            await libvirt_prov.refresh_vm_monitored_ports()
                        except asyncio.CancelledError:
                            break
                        except Exception:
                            logger.debug("VM port cache refresh failed", exc_info=True)

                _vm_port_refresh_task = asyncio.create_task(_vm_port_refresh_loop())

        except Exception as e:
            logger.error(f"Failed to start carrier monitor: {e}")

    yield

    # Cleanup
    if _state._heartbeat_task:
        _state._heartbeat_task.cancel()
        try:
            await _state._heartbeat_task
        except asyncio.CancelledError:
            pass

    if _state._event_listener_task:
        try:
            listener = get_event_listener()
            await listener.stop()
        except Exception:
            pass
        _state._event_listener_task.cancel()
        try:
            await _state._event_listener_task
        except asyncio.CancelledError:
            pass

    if _state._fix_interfaces_task:
        _state._fix_interfaces_task.cancel()
        try:
            await _state._fix_interfaces_task
        except asyncio.CancelledError:
            pass
        finally:
            _state.set_fix_interfaces_task(None)

    # Stop VM port refresh task
    if _vm_port_refresh_task is not None:
        _vm_port_refresh_task.cancel()
        try:
            await _vm_port_refresh_task
        except asyncio.CancelledError:
            pass

    # Stop carrier monitor
    if _carrier_monitor is not None:
        _carrier_monitor.stop()

    # Terminate any lingering virsh console sessions before backend shutdown.
    await _cleanup_lingering_virsh_sessions()

    # Close network backend
    try:
        backend = get_network_backend()
        await backend.shutdown()
    except Exception as e:
        logger.error(f"Error stopping network backend: {e}")

    # Stop periodic network cleanup
    try:
        from agent.network.cleanup import get_cleanup_manager
        cleanup_mgr = get_cleanup_manager()
        await cleanup_mgr.stop_periodic_cleanup()
        logger.info("Periodic network cleanup stopped")
    except Exception as e:
        logger.warning(f"Error stopping network cleanup: {e}")

    # Stop periodic image cleanup
    try:
        from agent.image_cleanup import get_image_cleanup_manager
        image_cleanup_mgr = get_image_cleanup_manager()
        await image_cleanup_mgr.stop_periodic_cleanup()
        logger.info("Periodic image cleanup stopped")
    except Exception as e:
        logger.warning(f"Error stopping image cleanup: {e}")

    # Close lock manager
    if _state._lock_manager:
        await _state._lock_manager.close()
        logger.info("Redis lock manager closed")

    # Close shared HTTP client
    await close_http_client()

    logger.info(f"Agent {_state.AGENT_ID} shutting down")


# Create FastAPI app
app = FastAPI(
    title="Archetype Agent",
    version=__version__,
    lifespan=lifespan,
)

_cors_origins = [settings.controller_url] if settings.controller_url else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


class AgentAuthMiddleware(BaseHTTPMiddleware):
    """Validate pre-shared secret on inbound requests from controller."""

    EXEMPT_PATHS = {"/health", "/healthz", "/metrics"}

    async def dispatch(self, request, call_next):
        # Skip auth if no secret configured (backward compat)
        if not settings.controller_secret:
            return await call_next(request)

        # Skip health endpoints
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)
        # POAP bootstrap must be reachable by device DHCP/bootstrap clients.
        if request.url.path.startswith("/poap/"):
            return await call_next(request)

        # Skip WebSocket upgrades (handled in WS handlers)
        if request.headers.get("upgrade", "").lower() == "websocket":
            return await call_next(request)

        # Validate Bearer token
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(status_code=403, content={"detail": "Missing authorization"})

        token = auth.split(" ", 1)[1]
        if not hmac.compare_digest(token, settings.controller_secret):
            return JSONResponse(status_code=403, content={"detail": "Invalid authorization"})

        return await call_next(request)


app.add_middleware(AgentAuthMiddleware)


# --- Include domain routers (images MUST be last — /images/{reference:path} is a catch-all) ---

from agent.routers.health import router as health_router
from agent.routers.admin import router as admin_router
from agent.routers.jobs import router as jobs_router
from agent.routers.labs import router as labs_router
from agent.routers.nodes import router as nodes_router
from agent.routers.links import router as links_router
from agent.routers.overlay import router as overlay_router
from agent.routers.ovs_plugin import router as ovs_plugin_router
from agent.routers.interfaces import router as interfaces_router
from agent.routers.console import router as console_router
from agent.routers.images import router as images_router

app.include_router(health_router)
app.include_router(admin_router)
app.include_router(jobs_router)
app.include_router(labs_router)
app.include_router(nodes_router)
app.include_router(links_router)
app.include_router(overlay_router)
app.include_router(ovs_plugin_router)
app.include_router(interfaces_router)
app.include_router(console_router)
app.include_router(images_router)


# --- Backward-compat re-exports (test files import these from agent.main) ---

from agent.routers.jobs import deploy_lab, _execute_deploy_with_callback  # noqa: F401
from agent.routers.labs import prune_docker, remove_container, remove_container_for_lab  # noqa: F401
from agent.routers.nodes import create_node, verify_node_cli  # noqa: F401
from agent.routers.overlay import (  # noqa: F401
    get_lab_port_state, declare_port_state, attach_overlay_interface,
)
from agent.routers.links import create_link, delete_link  # noqa: F401
from agent.routers.console import console_websocket  # noqa: F401


# --- Entry point ---

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "agent.main:app",
        host=settings.agent_host,
        port=settings.agent_port,
        reload=False,  # Disable reload to prevent connection drops during long operations
        timeout_keep_alive=300,  # Keep connections alive for deploy operations
    )
