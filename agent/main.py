"""Archetype Agent - Host-level orchestration agent.

This agent runs on each compute host and handles:
- Container lifecycle via DockerProvider
- VM lifecycle via LibvirtProvider
- Console access to running nodes
- Network overlay management
- Health reporting to controller
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from agent.config import settings
from agent.providers import NodeStatus as ProviderNodeStatus, get_provider, list_providers
from agent.providers.base import Provider
from agent.schemas import (
    AgentCapabilities,
    AgentInfo,
    AgentStatus,
    AttachContainerRequest,
    AttachContainerResponse,
    CleanupLabOrphansRequest,
    CleanupLabOrphansResponse,
    CleanupOrphansRequest,
    CleanupOrphansResponse,
    CleanupOverlayRequest,
    CleanupOverlayResponse,
    ConsoleRequest,
    CreateTunnelRequest,
    CreateTunnelResponse,
    DeployRequest,
    DeployTopology,
    DestroyRequest,
    DiscoveredLab,
    DiscoverLabsResponse,
    DockerImageInfo,
    ExtractConfigsRequest,
    ExtractConfigsResponse,
    ExtractedConfig,
    FixInterfacesResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    ImageExistsResponse,
    ImageInventoryResponse,
    ImagePullProgress,
    ImagePullRequest,
    ImagePullResponse,
    ImageReceiveRequest,
    ImageReceiveResponse,
    JobResult,
    JobStatus,
    LabStatusResponse,
    MtuTestRequest,
    MtuTestResponse,
    NodeReconcileRequest,
    NodeReconcileResponse,
    NodeReconcileResult,
    LinkCreate,
    LinkCreateResponse,
    LinkDeleteResponse,
    LinkInfo,
    LinkListResponse,
    LinkState,
    NodeInfo,
    NodeStatus,
    OVSPortInfo,
    OVSStatusResponse,
    OverlayStatusResponse,
    # New VTEP model schemas
    VtepInfo,
    EnsureVtepRequest,
    EnsureVtepResponse,
    AttachOverlayInterfaceRequest,
    AttachOverlayInterfaceResponse,
    DetachOverlayInterfaceRequest,
    DetachOverlayInterfaceResponse,
    Provider,
    ExternalConnectRequest,
    ExternalConnectResponse,
    ExternalDisconnectRequest,
    ExternalDisconnectResponse,
    ExternalConnectionInfo,
    ExternalListResponse,
    BridgePatchRequest,
    BridgePatchResponse,
    BridgeDeletePatchRequest,
    BridgeDeletePatchResponse,
    RegistrationRequest,
    RegistrationResponse,
    TunnelInfo,
    UpdateConfigRequest,
    UpdateConfigResponse,
    UpdateRequest,
    UpdateResponse,
    DockerPruneRequest,
    DockerPruneResponse,
    # Docker OVS Plugin schemas
    PluginHealthResponse,
    PluginBridgeInfo,
    PluginStatusResponse,
    PluginPortInfo,
    PluginLabPortsResponse,
    PluginFlowsResponse,
    PluginVxlanRequest,
    PluginVxlanResponse,
    PluginExternalAttachRequest,
    PluginExternalAttachResponse,
    PluginExternalInfo,
    PluginExternalListResponse,
    PluginMgmtNetworkInfo,
    PluginMgmtNetworkResponse,
    PluginMgmtAttachRequest,
    PluginMgmtAttachResponse,
    CarrierStateRequest,
    CarrierStateResponse,
    PortIsolateResponse,
    PortRestoreRequest,
    PortRestoreResponse,
    PortVlanResponse,
    # Host interface configuration
    InterfaceDetail,
    InterfaceDetailsResponse,
    SetMtuRequest,
    SetMtuResponse,
)
from agent.version import __version__, get_commit
from agent.updater import (
    DeploymentMode,
    detect_deployment_mode,
    perform_docker_update,
    perform_systemd_update,
)
from agent.logging_config import setup_agent_logging

# Generate agent ID if not configured
AGENT_ID = settings.agent_id or str(uuid.uuid4())[:8]

# Capture agent start time (used for uptime tracking)
AGENT_STARTED_AT = datetime.now(timezone.utc)

# Configure structured logging
setup_agent_logging(AGENT_ID)

import logging

logger = logging.getLogger(__name__)

# Track registration state
_registered = False
_heartbeat_task: asyncio.Task | None = None
_event_listener_task: asyncio.Task | None = None

# Overlay network manager (lazy initialized)
_overlay_manager = None

# Deploy results cache for concurrent request deduplication
_deploy_results: dict[str, asyncio.Future] = {}

# Redis lock manager (initialized on startup)
_lock_manager = None

# Event listener instance (lazy initialized)
_event_listener = None

# Docker OVS plugin runner (initialized on startup if enabled)
_docker_plugin_runner = None

# Active job counter for heartbeat reporting
_active_jobs = 0


def get_active_jobs() -> int:
    """Get the current count of active jobs."""
    return _active_jobs


def _increment_active_jobs():
    """Increment the active jobs counter."""
    global _active_jobs
    _active_jobs += 1


def _decrement_active_jobs():
    """Decrement the active jobs counter."""
    global _active_jobs
    _active_jobs = max(0, _active_jobs - 1)


def get_overlay_manager():
    """Lazy-initialize overlay manager."""
    global _overlay_manager
    if _overlay_manager is None:
        from agent.network.overlay import OverlayManager
        _overlay_manager = OverlayManager()
    return _overlay_manager


# OVS network manager (lazy initialized)
_ovs_manager = None


def get_ovs_manager():
    """Lazy-initialize OVS network manager."""
    global _ovs_manager
    if _ovs_manager is None:
        from agent.network.ovs import OVSNetworkManager
        _ovs_manager = OVSNetworkManager()
    return _ovs_manager


def get_event_listener():
    """Lazy-initialize Docker event listener."""
    global _event_listener
    if _event_listener is None:
        from agent.events import DockerEventListener
        _event_listener = DockerEventListener()
    return _event_listener


def get_lock_manager():
    """Get the deploy lock manager."""
    global _lock_manager
    return _lock_manager


async def forward_event_to_controller(event):
    """Forward a node event to the controller.

    This function is called by the event listener when a container
    state change is detected. It POSTs the event to the controller's
    /events/node endpoint for real-time state synchronization.
    """
    from agent.events.base import NodeEvent, NodeEventType

    if not isinstance(event, NodeEvent):
        return

    # Handle container restart - reprovision OVS interfaces if needed
    if event.event_type == NodeEventType.STARTED:
        container_name = event.attributes.get("container_name") if event.attributes else None
        if container_name:
            try:
                ovs = get_ovs_manager()
                if ovs._initialized:
                    await ovs.handle_container_restart(container_name, event.lab_id)
            except Exception as e:
                logger.warning(f"Failed to reprovision interfaces for {container_name}: {e}")

    payload = {
        "agent_id": AGENT_ID,
        "lab_id": event.lab_id,
        "node_name": event.node_name,
        "container_id": event.container_id,
        "event_type": event.event_type.value,
        "timestamp": event.timestamp.isoformat(),
        "status": event.status,
        "attributes": event.attributes,
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.controller_url}/events/node",
                json=payload,
                timeout=5.0,
            )
            if response.status_code == 200:
                logger.debug(f"Forwarded event: {event.event_type.value} for {event.log_name()}")
            else:
                logger.warning(f"Failed to forward event: HTTP {response.status_code}")
    except Exception as e:
        logger.error(f"Error forwarding event to controller: {e}")


def get_workspace(lab_id: str) -> Path:
    """Get workspace directory for a lab."""
    workspace = Path(settings.workspace_path) / lab_id
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def get_provider_for_request(provider_name: str = "docker") -> Provider:
    """Get a provider instance for handling a request.

    Args:
        provider_name: Name of the provider to use (default: docker)

    Returns:
        Provider instance

    Raises:
        HTTPException: If the requested provider is not available
    """
    provider = get_provider(provider_name)
    if provider is None:
        available = list_providers()
        raise HTTPException(
            status_code=503,
            detail=f"Provider '{provider_name}' not available. Available: {available}"
        )
    return provider


async def _fix_running_interfaces() -> None:
    """Fix interface naming/attachments for already-running containers after agent restart."""
    # Allow plugin/network reconciliation to complete before renaming.
    await asyncio.sleep(5)

    provider = get_provider("docker")
    if provider is None:
        return

    for attempt in range(2):
        try:
            containers = await asyncio.to_thread(
                provider.docker.containers.list,
                all=True,
                filters={"label": "archetype.lab_id"},
            )
        except Exception as e:
            logger.warning(f"Failed to list containers for interface fixup: {e}")
            return

        for container in containers:
            lab_id = container.labels.get("archetype.lab_id")
            if not lab_id:
                continue
            try:
                await provider._fix_interface_names(container.name, lab_id)
            except Exception as e:
                logger.warning(
                    f"Failed to fix interfaces for {container.name}: {e}"
                )

        if attempt == 0:
            await asyncio.sleep(5)


def provider_status_to_schema(status: ProviderNodeStatus) -> NodeStatus:
    """Convert provider NodeStatus to schema NodeStatus."""
    mapping = {
        ProviderNodeStatus.PENDING: NodeStatus.PENDING,
        ProviderNodeStatus.STARTING: NodeStatus.STARTING,
        ProviderNodeStatus.RUNNING: NodeStatus.RUNNING,
        ProviderNodeStatus.STOPPING: NodeStatus.STOPPING,
        ProviderNodeStatus.STOPPED: NodeStatus.STOPPED,
        ProviderNodeStatus.ERROR: NodeStatus.ERROR,
        ProviderNodeStatus.UNKNOWN: NodeStatus.UNKNOWN,
    }
    return mapping.get(status, NodeStatus.UNKNOWN)


def get_capabilities() -> AgentCapabilities:
    """Determine agent capabilities based on config and available tools."""
    providers = []
    if settings.enable_docker:
        providers.append(Provider.DOCKER)
    if settings.enable_libvirt:
        providers.append(Provider.LIBVIRT)

    features = ["console", "status"]
    if settings.enable_vxlan:
        features.append("vxlan")

    return AgentCapabilities(
        providers=providers,
        max_concurrent_jobs=settings.max_concurrent_jobs,
        features=features,
    )


def _sync_get_resource_usage() -> dict:
    """Gather system resource metrics (synchronous implementation)."""
    import psutil

    try:
        # CPU usage (average across all cores)
        cpu_percent = psutil.cpu_percent(interval=0.1)

        # Memory usage
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        memory_used_gb = round(memory.used / (1024 ** 3), 2)
        memory_total_gb = round(memory.total / (1024 ** 3), 2)

        # Disk usage for workspace partition
        disk_path = settings.workspace_path if settings.workspace_path else "/"
        disk = psutil.disk_usage(disk_path)
        disk_percent = disk.percent
        disk_used_gb = round(disk.used / (1024 ** 3), 2)
        disk_total_gb = round(disk.total / (1024 ** 3), 2)

        # Docker container counts and details
        # Only count Archetype-managed containers (node containers and system containers)
        containers_running = 0
        containers_total = 0
        container_details = []
        try:
            import docker
            from docker.errors import NotFound, APIError
            client = docker.from_env()

            # Use low-level API to get container list, then fetch each individually
            # This handles "Dead" or corrupted containers gracefully
            container_list = client.api.containers(all=True)
            all_containers = []
            for container_info in container_list:
                try:
                    c = client.containers.get(container_info["Id"])
                    all_containers.append(c)
                except (NotFound, APIError) as e:
                    # Skip dead/corrupted containers that can't be inspected
                    container_name = container_info.get("Names", ["unknown"])[0].lstrip("/")
                    logger.warning(f"Skipping corrupted/dead container {container_name}: {e}")
                    continue

            # Collect detailed container info with lab associations
            # Include Archetype node containers and system containers
            for c in all_containers:
                labels = c.labels
                is_archetype_node = bool(labels.get("archetype.node_name"))
                # System containers (e.g., archetype-api, archetype-worker)
                is_archetype_system = c.name.startswith("archetype-") and not is_archetype_node

                # Only include Archetype-related containers
                if not is_archetype_node and not is_archetype_system:
                    continue

                # Count only Archetype-related containers
                containers_total += 1
                if c.status == "running":
                    containers_running += 1

                lab_prefix = labels.get("archetype.lab_id", "")
                node_name = labels.get("archetype.node_name")
                node_kind = labels.get("archetype.node_kind")

                # Get image name/tag (handle deleted images gracefully)
                try:
                    image_name = c.image.tags[0] if c.image.tags else c.image.short_id
                except Exception:
                    image_name = "unknown"

                container_details.append({
                    "name": c.name,
                    "status": c.status,
                    "lab_prefix": lab_prefix,
                    "node_name": node_name,
                    "node_kind": node_kind,
                    "image": image_name,
                    "is_system": is_archetype_system,
                })
        except Exception as e:
            logger.warning(f"Docker container collection failed: {type(e).__name__}: {e}")

        return {
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "memory_used_gb": memory_used_gb,
            "memory_total_gb": memory_total_gb,
            "disk_percent": disk_percent,
            "disk_used_gb": disk_used_gb,
            "disk_total_gb": disk_total_gb,
            "containers_running": containers_running,
            "containers_total": containers_total,
            "container_details": container_details,
        }
    except Exception as e:
        logger.warning(f"Failed to gather resource usage: {e}")
        return {}


async def get_resource_usage() -> dict:
    """Gather system resource metrics for heartbeat (async wrapper)."""
    return await asyncio.to_thread(_sync_get_resource_usage)


def _detect_local_ip() -> str | None:
    """Auto-detect local IP address from default route interface."""
    import subprocess
    try:
        # Get the IP of the interface with the default route
        result = subprocess.run(
            ["ip", "route", "get", "1.1.1.1"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Output: "1.1.1.1 via X.X.X.X dev ethX src Y.Y.Y.Y uid 0"
            parts = result.stdout.split()
            if "src" in parts:
                src_idx = parts.index("src")
                if src_idx + 1 < len(parts):
                    ip = parts[src_idx + 1]
                    logger.info(f"Auto-detected local IP: {ip}")
                    return ip
    except Exception as e:
        logger.warning(f"Failed to auto-detect local IP: {e}")
    return None


def get_agent_info() -> AgentInfo:
    """Build agent info for registration."""
    # Determine the host to advertise to the controller.
    # Priority: advertise_host > agent_host (if not 0.0.0.0) > local_ip > auto-detect > agent_name
    advertise_host = settings.advertise_host
    if not advertise_host:
        if settings.agent_host != "0.0.0.0":
            advertise_host = settings.agent_host
        elif settings.local_ip:
            advertise_host = settings.local_ip
        else:
            # Try to auto-detect the local IP
            detected_ip = _detect_local_ip()
            if detected_ip:
                advertise_host = detected_ip
            else:
                advertise_host = settings.agent_name

    address = f"{advertise_host}:{settings.agent_port}"

    return AgentInfo(
        agent_id=AGENT_ID,
        name=settings.agent_name,
        address=address,
        capabilities=get_capabilities(),
        started_at=AGENT_STARTED_AT,
        is_local=settings.is_local,
        deployment_mode=detect_deployment_mode().value,
    )


async def register_with_controller() -> bool:
    """Register this agent with the controller."""
    global _registered, AGENT_ID

    request = RegistrationRequest(
        agent=get_agent_info(),
        token=settings.registration_token or None,
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.controller_url}/agents/register",
                json=request.model_dump(mode='json'),
                timeout=settings.registration_timeout,
            )
            if response.status_code == 200:
                result = RegistrationResponse(**response.json())
                if result.success:
                    _registered = True
                    # Use the assigned ID from controller (may differ if we're
                    # re-registering an existing agent with a new generated ID)
                    if result.assigned_id and result.assigned_id != AGENT_ID:
                        logger.info(f"Controller assigned existing ID: {result.assigned_id}")
                        AGENT_ID = result.assigned_id
                    logger.info(f"Registered with controller as {AGENT_ID}")
                    return True
                else:
                    logger.warning(f"Registration rejected: {result.message}")
                    return False
            else:
                logger.error(f"Registration failed: HTTP {response.status_code}")
                return False
    except httpx.ConnectError:
        logger.warning(f"Cannot connect to controller at {settings.controller_url}")
        return False
    except Exception as e:
        logger.error(f"Registration error: {e}")
        return False


async def send_heartbeat() -> HeartbeatResponse | None:
    """Send heartbeat to controller."""
    request = HeartbeatRequest(
        agent_id=AGENT_ID,
        status=AgentStatus.ONLINE,
        active_jobs=get_active_jobs(),
        resource_usage=await get_resource_usage(),
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.controller_url}/agents/{AGENT_ID}/heartbeat",
                json=request.model_dump(),
                timeout=settings.heartbeat_timeout,
            )
            if response.status_code == 200:
                return HeartbeatResponse(**response.json())
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")
    return None


async def heartbeat_loop():
    """Background task to send periodic heartbeats."""
    global _registered

    while True:
        await asyncio.sleep(settings.heartbeat_interval)

        if not _registered:
            # Try to register again
            await register_with_controller()
            continue

        response = await send_heartbeat()
        if response is None:
            # Controller unreachable, mark as unregistered to retry
            _registered = False
            logger.warning("Lost connection to controller, will retry registration")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - register on startup, cleanup on shutdown."""
    global _heartbeat_task, _event_listener_task, _lock_manager

    logger.info(f"Agent {AGENT_ID} starting...")
    logger.info(f"Controller URL: {settings.controller_url}")
    logger.info(f"Capabilities: {get_capabilities()}")

    # Initialize Redis lock manager
    from agent.locks import DeployLockManager, NoopDeployLockManager, set_lock_manager
    _lock_manager = DeployLockManager(
        redis_url=settings.redis_url,
        lock_ttl=settings.lock_ttl,
        agent_id=AGENT_ID,
    )
    try:
        await _lock_manager.ping()
        set_lock_manager(_lock_manager)
        logger.info(f"Redis lock manager initialized (TTL: {settings.lock_ttl}s)")
    except Exception as e:
        logger.error(f"Redis unavailable ({e}); continuing without distributed locks")
        _lock_manager = NoopDeployLockManager(agent_id=AGENT_ID)
        set_lock_manager(_lock_manager)

    # Clean up any orphaned locks from previous run (crash recovery)
    try:
        cleared_locks = await _lock_manager.clear_agent_locks()
        if cleared_locks:
            logger.warning(f"Cleared {len(cleared_locks)} orphaned locks from previous run: {cleared_locks}")
    except Exception as e:
        logger.error(f"Failed to clear orphaned locks: {e}")

    # Recover network allocations from system state (crash recovery)
    try:
        if settings.enable_vxlan:
            overlay_mgr = get_overlay_manager()
            vnis_recovered = await overlay_mgr.recover_allocations()
            if vnis_recovered > 0:
                logger.info(f"Recovered {vnis_recovered} VNI allocations from system state")

        if settings.enable_ovs:
            ovs_mgr = get_ovs_manager()
            await ovs_mgr.initialize()
            vlans_recovered = await ovs_mgr.recover_allocations()
            if vlans_recovered > 0:
                logger.info(f"Recovered {vlans_recovered} VLAN allocations from OVS state")
    except Exception as e:
        logger.warning(f"Failed to recover network allocations: {e}")

    # Start periodic network cleanup task
    from agent.network.cleanup import get_cleanup_manager
    cleanup_mgr = get_cleanup_manager()
    try:
        # Run initial cleanup to clear any orphans from previous crash
        initial_stats = await cleanup_mgr.run_full_cleanup()
        if initial_stats.veths_deleted > 0 or initial_stats.bridges_deleted > 0:
            logger.info(f"Initial network cleanup: {initial_stats.to_dict()}")

        # Start periodic cleanup (every 5 minutes)
        await cleanup_mgr.start_periodic_cleanup(interval_seconds=300)
        logger.info("Periodic network cleanup started (interval: 5 minutes)")
    except Exception as e:
        logger.warning(f"Failed to start network cleanup: {e}")

    # Try initial registration (will notify controller if this is a restart)
    await register_with_controller()

    # Start heartbeat background task
    _heartbeat_task = asyncio.create_task(heartbeat_loop())

    # Start Docker event listener if docker provider is enabled
    if settings.enable_docker:
        try:
            listener = get_event_listener()
            _event_listener_task = asyncio.create_task(
                listener.start(forward_event_to_controller)
            )
            logger.info("Docker event listener started")
        except Exception as e:
            logger.error(f"Failed to start Docker event listener: {e}")

    # Start Docker OVS network plugin if docker provider is enabled
    global _docker_plugin_runner
    if settings.enable_docker:
        try:
            from agent.network.docker_plugin import get_docker_ovs_plugin
            plugin = get_docker_ovs_plugin()
            _docker_plugin_runner = await plugin.start()
            logger.info("Docker OVS network plugin started")
            asyncio.create_task(_fix_running_interfaces())
        except Exception as e:
            logger.error(f"Failed to start Docker OVS plugin: {e}")

    yield

    # Cleanup
    if _heartbeat_task:
        _heartbeat_task.cancel()
        try:
            await _heartbeat_task
        except asyncio.CancelledError:
            pass

    if _event_listener_task:
        try:
            listener = get_event_listener()
            await listener.stop()
        except Exception:
            pass
        _event_listener_task.cancel()
        try:
            await _event_listener_task
        except asyncio.CancelledError:
            pass

    # Close Docker OVS plugin
    if _docker_plugin_runner:
        try:
            # Call shutdown first to save state
            from agent.network.docker_plugin import get_docker_ovs_plugin
            plugin = get_docker_ovs_plugin()
            await plugin.shutdown()
            await _docker_plugin_runner.cleanup()
            logger.info("Docker OVS network plugin stopped")
        except Exception as e:
            logger.error(f"Error stopping Docker OVS plugin: {e}")

    # Stop periodic network cleanup
    try:
        from agent.network.cleanup import get_cleanup_manager
        cleanup_mgr = get_cleanup_manager()
        await cleanup_mgr.stop_periodic_cleanup()
        logger.info("Periodic network cleanup stopped")
    except Exception as e:
        logger.warning(f"Error stopping network cleanup: {e}")

    # Close lock manager
    if _lock_manager:
        await _lock_manager.close()
        logger.info("Redis lock manager closed")

    logger.info(f"Agent {AGENT_ID} shutting down")


# Create FastAPI app
app = FastAPI(
    title="Archetype Agent",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Health Endpoints ---

@app.get("/health")
def health():
    """Basic health check."""
    return {
        "status": "ok",
        "agent_id": AGENT_ID,
        "commit": get_commit(),
        "registered": _registered,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/info")
def info():
    """Return agent info and capabilities."""
    return get_agent_info().model_dump()


@app.get("/callbacks/dead-letters")
def get_dead_letters():
    """Get failed callbacks that couldn't be delivered.

    Returns the dead letter queue contents for monitoring/debugging.
    """
    from agent.callbacks import get_dead_letters as fetch_dead_letters
    return {"dead_letters": fetch_dead_letters()}


# --- Lock Status Endpoints ---

@app.get("/locks/status")
async def get_lock_status():
    """Get status of all deploy locks on this agent.

    Returns information about currently held locks including:
    - lab_id: The lab holding the lock
    - ttl: Remaining time-to-live in seconds
    - age_seconds: How long the lock has been held
    - is_stuck: Whether the lock exceeds the stuck threshold
    - owner: Agent ID that owns the lock

    Used by controller to detect and clean up stuck locks.
    """
    now = datetime.now(timezone.utc)
    lock_manager = get_lock_manager()

    if lock_manager is None:
        return {"locks": [], "timestamp": now.isoformat(), "error": "Lock manager not initialized"}

    try:
        locks = await lock_manager.get_all_locks()
        # Add is_stuck flag based on controller threshold
        for lock in locks:
            lock["is_stuck"] = lock.get("age_seconds", 0) > settings.lock_stuck_threshold
        return {"locks": locks, "timestamp": now.isoformat()}
    except Exception as e:
        logger.error(f"Failed to get lock status: {e}")
        return {"locks": [], "timestamp": now.isoformat(), "error": str(e)}


@app.post("/locks/{lab_id}/release")
async def release_lock(lab_id: str):
    """Force release a stuck deploy lock for a lab.

    This uses Redis to forcibly release the lock, allowing new deploys
    to proceed immediately. The lock manager handles ownership checks
    and logs appropriate warnings.

    Returns:
        status: "cleared" if lock was released, "not_found" if no lock existed
    """
    lock_manager = get_lock_manager()

    if lock_manager is None:
        return {"status": "error", "lab_id": lab_id, "error": "Lock manager not initialized"}

    try:
        # Force release via Redis
        released = await lock_manager.force_release(lab_id)

        # Also clear cached results
        _deploy_results.pop(lab_id, None)

        if released:
            logger.info(f"Force-released lock for lab {lab_id}")
            return {"status": "cleared", "lab_id": lab_id}
        else:
            return {"status": "not_found", "lab_id": lab_id}
    except Exception as e:
        logger.error(f"Failed to release lock for lab {lab_id}: {e}")
        return {"status": "error", "lab_id": lab_id, "error": str(e)}


# --- Agent Update Endpoint ---

@app.post("/update")
async def trigger_update(request: UpdateRequest) -> UpdateResponse:
    """Receive update command from controller.

    Detects deployment mode and initiates appropriate update procedure:
    - Systemd mode: git pull + pip install + systemctl restart
    - Docker mode: Reports back - controller handles container restart

    The agent reports progress via callbacks to the callback_url.
    """
    logger.info(f"Update request received: job={request.job_id}, target={request.target_version}")

    # Detect deployment mode
    mode = detect_deployment_mode()
    logger.info(f"Detected deployment mode: {mode.value}")

    if mode == DeploymentMode.SYSTEMD:
        # Start async update process
        asyncio.create_task(
            perform_systemd_update(
                job_id=request.job_id,
                agent_id=AGENT_ID,
                target_version=request.target_version,
                callback_url=request.callback_url,
            )
        )
        return UpdateResponse(
            accepted=True,
            message="Update initiated",
            deployment_mode=mode.value,
        )

    elif mode == DeploymentMode.DOCKER:
        # Docker update needs external handling
        asyncio.create_task(
            perform_docker_update(
                job_id=request.job_id,
                agent_id=AGENT_ID,
                target_version=request.target_version,
                callback_url=request.callback_url,
            )
        )
        return UpdateResponse(
            accepted=False,
            message="Docker deployment detected. Update must be performed externally.",
            deployment_mode=mode.value,
        )

    else:
        # Unknown deployment mode
        return UpdateResponse(
            accepted=False,
            message="Unknown deployment mode. Cannot perform automatic update.",
            deployment_mode=mode.value,
        )


@app.get("/deployment-mode")
def get_deployment_mode() -> dict:
    """Get the agent's deployment mode.

    Used by controller to determine update strategy.
    """
    mode = detect_deployment_mode()
    return {
        "mode": mode.value,
        "version": __version__,
    }


# --- Job Execution Endpoints (called by controller) ---

@app.post("/jobs/deploy")
async def deploy_lab(request: DeployRequest) -> JobResult:
    """Deploy a lab topology.

    Uses Redis-based per-lab locking to prevent concurrent deploys for the same lab.
    Locks automatically expire via TTL if agent crashes, ensuring recovery.

    Accepts topology in JSON format only.

    If callback_url is provided, returns 202 Accepted immediately and executes
    the deploy in the background, POSTing the result to the callback URL when done.
    """
    from agent.locks import LockAcquisitionTimeout

    lab_id = request.lab_id
    logger.info(f"Deploy request: lab={lab_id}, job={request.job_id}, provider={request.provider.value}")
    if request.callback_url:
        logger.debug(f"  Async mode with callback: {request.callback_url}")

    # Validate that JSON topology is provided
    if not request.topology:
        raise HTTPException(
            status_code=400,
            detail="No topology provided. Deploy requires 'topology' (JSON)."
        )

    lock_manager = get_lock_manager()
    if lock_manager is None:
        raise HTTPException(
            status_code=503,
            detail="Lock manager not initialized"
        )

    # Async callback mode - return immediately and execute in background
    if request.callback_url:
        # Start async execution
        asyncio.create_task(
            _execute_deploy_with_callback(
                request.job_id,
                lab_id,
                request.topology,
                request.provider.value,
                request.callback_url,
            )
        )
        return JobResult(
            job_id=request.job_id,
            status=JobStatus.ACCEPTED,
            stdout="Deploy accepted for async execution",
        )

    # Synchronous mode - acquire Redis lock with heartbeat and execute
    try:
        async with lock_manager.acquire_with_heartbeat(
            lab_id,
            timeout=settings.lock_acquire_timeout,
            extend_interval=settings.lock_extend_interval,
        ):
            provider = get_provider_for_request(request.provider.value)
            workspace = get_workspace(lab_id)
            logger.info(f"Deploy starting: lab={lab_id}, workspace={workspace}")

            result = await provider.deploy(
                lab_id=lab_id,
                topology=request.topology,
                workspace=workspace,
            )

            logger.info(f"Deploy finished: lab={lab_id}, success={result.success}")

            if result.success:
                job_result = JobResult(
                    job_id=request.job_id,
                    status=JobStatus.COMPLETED,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            else:
                job_result = JobResult(
                    job_id=request.job_id,
                    status=JobStatus.FAILED,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    error_message=result.error,
                )

            # Cache result briefly for concurrent requests
            _deploy_results[lab_id] = job_result
            asyncio.create_task(_cleanup_deploy_cache(lab_id, delay=5.0))

            return job_result

    except LockAcquisitionTimeout as e:
        logger.warning(f"Timeout waiting for deploy lock on lab {lab_id}: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Deploy already in progress for lab {lab_id}, try again later"
        )
    except Exception as e:
        logger.error(f"Deploy error for lab {lab_id}: {e}", exc_info=True)
        job_result = JobResult(
            job_id=request.job_id,
            status=JobStatus.FAILED,
            error_message=str(e),
        )
        _deploy_results[lab_id] = job_result
        asyncio.create_task(_cleanup_deploy_cache(lab_id, delay=5.0))
        return job_result


async def _execute_deploy_with_callback(
    job_id: str,
    lab_id: str,
    topology: "DeployTopology | None",
    provider_name: str,
    callback_url: str,
) -> None:
    """Execute deploy in background and send result via callback.

    This function handles the async deploy execution pattern:
    1. Acquire the lab lock via Redis with heartbeat (prevents concurrent deploys)
    2. Periodically extend the lock TTL while deploy is running
    3. Execute the deploy operation
    4. POST the result to the callback URL
    5. Handle callback delivery failures with retry

    The Redis lock has a short TTL (2 min) for fast crash recovery, but is
    extended every 30s while the deploy is actively running.

    Args:
        topology: Structured JSON topology (preferred for multi-host)
    """
    from agent.callbacks import CallbackPayload, deliver_callback
    from agent.locks import LockAcquisitionTimeout
    from datetime import datetime, timezone

    _increment_active_jobs()
    try:
        started_at = datetime.now(timezone.utc)
        lock_manager = get_lock_manager()

        if lock_manager is None:
            logger.error(f"Lock manager not initialized for async deploy of lab {lab_id}")
            payload = CallbackPayload(
                job_id=job_id,
                agent_id=AGENT_ID,
                status="failed",
                error_message="Lock manager not initialized",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            await deliver_callback(callback_url, payload)
            return

        try:
            async with lock_manager.acquire_with_heartbeat(
                lab_id,
                timeout=settings.lock_acquire_timeout,
                extend_interval=settings.lock_extend_interval,
            ):
                try:
                    from agent.callbacks import HeartbeatSender

                    provider = get_provider_for_request(provider_name)
                    workspace = get_workspace(lab_id)
                    logger.info(f"Async deploy starting: lab={lab_id}, workspace={workspace}")

                    # Send heartbeats during deploy to prove job is active
                    async with HeartbeatSender(callback_url, job_id, interval=30.0):
                        result = await provider.deploy(
                            lab_id=lab_id,
                            topology=topology,
                            workspace=workspace,
                        )

                    logger.info(f"Async deploy finished: lab={lab_id}, success={result.success}")

                    # Build callback payload
                    payload = CallbackPayload(
                        job_id=job_id,
                        agent_id=AGENT_ID,
                        status="completed" if result.success else "failed",
                        stdout=result.stdout or "",
                        stderr=result.stderr or "",
                        error_message=result.error if not result.success else None,
                        started_at=started_at,
                        completed_at=datetime.now(timezone.utc),
                    )

                except Exception as e:
                    logger.error(f"Async deploy error for lab {lab_id}: {e}", exc_info=True)

                    payload = CallbackPayload(
                        job_id=job_id,
                        agent_id=AGENT_ID,
                        status="failed",
                        error_message=str(e),
                        started_at=started_at,
                        completed_at=datetime.now(timezone.utc),
                    )

        except LockAcquisitionTimeout:
            logger.warning(f"Async deploy timeout waiting for lock on lab {lab_id}")
            payload = CallbackPayload(
                job_id=job_id,
                agent_id=AGENT_ID,
                status="failed",
                error_message=f"Deploy already in progress for lab {lab_id}, timed out waiting for lock",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )

        # Deliver callback (outside the lock)
        await deliver_callback(callback_url, payload)
    finally:
        _decrement_active_jobs()


async def _cleanup_deploy_cache(lab_id: str, delay: float = 5.0):
    """Clean up cached deploy result after a delay."""
    await asyncio.sleep(delay)
    _deploy_results.pop(lab_id, None)


@app.post("/jobs/destroy")
async def destroy_lab(request: DestroyRequest) -> JobResult:
    """Tear down a lab.

    If callback_url is provided, returns 202 Accepted immediately and executes
    the destroy in the background, POSTing the result to the callback URL when done.
    """
    from agent.locks import LockAcquisitionTimeout

    logger.info(f"Destroy request: lab={request.lab_id}, job={request.job_id}")
    if request.callback_url:
        logger.debug(f"  Async mode with callback: {request.callback_url}")

    lock_manager = get_lock_manager()
    if lock_manager is None:
        raise HTTPException(
            status_code=503,
            detail="Lock manager not initialized"
        )

    # Async callback mode - return immediately and execute in background
    if request.callback_url:
        asyncio.create_task(
            _execute_destroy_with_callback(
                request.job_id,
                request.lab_id,
                request.provider.value,
                request.callback_url,
            )
        )
        return JobResult(
            job_id=request.job_id,
            status=JobStatus.ACCEPTED,
            stdout="Destroy accepted for async execution",
        )

    # Synchronous mode - acquire lock first
    try:
        async with lock_manager.acquire_with_heartbeat(
            request.lab_id,
            timeout=settings.lock_acquire_timeout,
            extend_interval=settings.lock_extend_interval,
        ):
            provider = get_provider_for_request(request.provider.value)
            workspace = get_workspace(request.lab_id)
            result = await provider.destroy(
                lab_id=request.lab_id,
                workspace=workspace,
            )

            if result.success:
                return JobResult(
                    job_id=request.job_id,
                    status=JobStatus.COMPLETED,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            else:
                return JobResult(
                    job_id=request.job_id,
                    status=JobStatus.FAILED,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    error_message=result.error,
                )
    except LockAcquisitionTimeout:
        logger.warning(f"Timeout waiting for lock on lab {request.lab_id} for destroy")
        raise HTTPException(
            status_code=503,
            detail=f"Another operation is in progress for lab {request.lab_id}, try again later"
        )


async def _execute_destroy_with_callback(
    job_id: str,
    lab_id: str,
    provider_name: str,
    callback_url: str,
) -> None:
    """Execute destroy in background and send result via callback."""
    from agent.callbacks import CallbackPayload, deliver_callback, HeartbeatSender
    from agent.locks import LockAcquisitionTimeout
    from datetime import datetime, timezone

    _increment_active_jobs()
    try:
        started_at = datetime.now(timezone.utc)
        lock_manager = get_lock_manager()

        if lock_manager is None:
            logger.error(f"Lock manager not initialized for async destroy of lab {lab_id}")
            payload = CallbackPayload(
                job_id=job_id,
                agent_id=AGENT_ID,
                status="failed",
                error_message="Lock manager not initialized",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )
            await deliver_callback(callback_url, payload)
            return

        try:
            async with lock_manager.acquire_with_heartbeat(
                lab_id,
                timeout=settings.lock_acquire_timeout,
                extend_interval=settings.lock_extend_interval,
            ):
                provider = get_provider_for_request(provider_name)
                workspace = get_workspace(lab_id)
                logger.info(f"Async destroy starting: lab={lab_id}, workspace={workspace}")

                # Send heartbeats during destroy to prove job is active
                async with HeartbeatSender(callback_url, job_id, interval=30.0):
                    result = await provider.destroy(
                        lab_id=lab_id,
                        workspace=workspace,
                    )

                logger.info(f"Async destroy finished: lab={lab_id}, success={result.success}")

                payload = CallbackPayload(
                    job_id=job_id,
                    agent_id=AGENT_ID,
                    status="completed" if result.success else "failed",
                    stdout=result.stdout or "",
                    stderr=result.stderr or "",
                    error_message=result.error if not result.success else None,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                )

        except LockAcquisitionTimeout:
            logger.warning(f"Lock timeout for async destroy of lab {lab_id}")
            payload = CallbackPayload(
                job_id=job_id,
                agent_id=AGENT_ID,
                status="failed",
                error_message=f"Another operation is in progress for lab {lab_id}",
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )

        except Exception as e:
            logger.error(f"Async destroy error for lab {lab_id}: {e}", exc_info=True)

            payload = CallbackPayload(
                job_id=job_id,
                agent_id=AGENT_ID,
                status="failed",
                error_message=str(e),
                started_at=started_at,
                completed_at=datetime.now(timezone.utc),
            )

        await deliver_callback(callback_url, payload)
    finally:
        _decrement_active_jobs()


# --- Status Endpoints ---

@app.get("/labs/{lab_id}/status")
async def lab_status(lab_id: str) -> LabStatusResponse:
    """Get status of all nodes in a lab.

    Queries both Docker and libvirt providers and merges the results.
    This supports mixed labs with both containers and VMs.
    """
    logger.debug(f"Status request: lab={lab_id}")

    workspace = get_workspace(lab_id)
    all_nodes: list[NodeInfo] = []
    errors: list[str] = []

    # Query Docker provider
    docker_provider = get_provider("docker")
    if docker_provider:
        try:
            docker_result = await docker_provider.status(
                lab_id=lab_id,
                workspace=workspace,
            )
            for node in docker_result.nodes:
                all_nodes.append(NodeInfo(
                    name=node.name,
                    status=provider_status_to_schema(node.status),
                    container_id=node.container_id,
                    image=node.image,
                    ip_addresses=node.ip_addresses,
                ))
            if docker_result.error:
                errors.append(f"Docker: {docker_result.error}")
        except Exception as e:
            errors.append(f"Docker query failed: {e}")

    # Query libvirt provider for VMs
    libvirt_provider = get_provider("libvirt")
    if libvirt_provider:
        try:
            libvirt_result = await libvirt_provider.status(
                lab_id=lab_id,
                workspace=workspace,
            )
            for node in libvirt_result.nodes:
                all_nodes.append(NodeInfo(
                    name=node.name,
                    status=provider_status_to_schema(node.status),
                    container_id=node.container_id,
                    image=node.image,
                    ip_addresses=node.ip_addresses,
                ))
            if libvirt_result.error:
                errors.append(f"Libvirt: {libvirt_result.error}")
        except Exception as e:
            logger.debug(f"Libvirt query failed (may not have VMs): {e}")

    return LabStatusResponse(
        lab_id=lab_id,
        nodes=all_nodes,
        error="; ".join(errors) if errors else None,
    )


@app.post("/labs/{lab_id}/nodes/reconcile")
async def reconcile_nodes(
    lab_id: str,
    request: NodeReconcileRequest,
) -> NodeReconcileResponse:
    """Reconcile nodes to their desired states.

    For each node in the request, this endpoint will:
    - Start the container/VM if desired_state is "running" and it's stopped
    - Stop the container/VM if desired_state is "stopped" and it's running
    - Skip if already in the desired state

    Supports both Docker containers and libvirt VMs.
    """
    logger.info(f"Reconcile request: lab={lab_id}, nodes={len(request.nodes)}")

    results: list[NodeReconcileResult] = []
    workspace = get_workspace(lab_id)

    for target in request.nodes:
        container_name = target.container_name
        desired = target.desired_state

        # Try Docker first
        try:
            container = await asyncio.to_thread(
                lambda: docker_client.containers.get(container_name)
            )
            current_status = container.status

            if desired == "running":
                if current_status == "running":
                    results.append(NodeReconcileResult(
                        container_name=container_name,
                        action="already_running",
                        success=True,
                    ))
                else:
                    await asyncio.to_thread(container.start)
                    logger.info(f"Started container {container_name}")
                    results.append(NodeReconcileResult(
                        container_name=container_name,
                        action="started",
                        success=True,
                    ))

            elif desired == "stopped":
                if current_status in ("exited", "created", "dead"):
                    results.append(NodeReconcileResult(
                        container_name=container_name,
                        action="already_stopped",
                        success=True,
                    ))
                else:
                    await asyncio.to_thread(container.stop, timeout=30)
                    logger.info(f"Stopped container {container_name}")
                    results.append(NodeReconcileResult(
                        container_name=container_name,
                        action="stopped",
                        success=True,
                    ))
            continue

        except docker.errors.NotFound:
            # Docker container not found, try libvirt
            pass
        except Exception as e:
            logger.error(f"Error reconciling Docker container {container_name}: {e}")
            results.append(NodeReconcileResult(
                container_name=container_name,
                action="error",
                success=False,
                error=str(e),
            ))
            continue

        # Try libvirt for VMs
        libvirt_provider = get_provider("libvirt")
        if libvirt_provider:
            try:
                # Extract node name from container_name (format: arch-{lab_id}-{node_name})
                # For VMs, we use the container_name directly as it should match the domain pattern
                node_name = container_name

                if desired == "running":
                    result = await libvirt_provider.start_node(lab_id, node_name, workspace)
                    if result.success:
                        action = "started" if result.new_status == NodeStatus.RUNNING else "already_running"
                        results.append(NodeReconcileResult(
                            container_name=container_name,
                            action=action,
                            success=True,
                        ))
                    else:
                        results.append(NodeReconcileResult(
                            container_name=container_name,
                            action="error",
                            success=False,
                            error=result.error or "Failed to start VM",
                        ))

                elif desired == "stopped":
                    result = await libvirt_provider.stop_node(lab_id, node_name, workspace)
                    if result.success:
                        action = "stopped" if result.new_status == NodeStatus.STOPPED else "already_stopped"
                        results.append(NodeReconcileResult(
                            container_name=container_name,
                            action=action,
                            success=True,
                        ))
                    else:
                        results.append(NodeReconcileResult(
                            container_name=container_name,
                            action="error",
                            success=False,
                            error=result.error or "Failed to stop VM",
                        ))
                continue

            except Exception as e:
                logger.error(f"Error reconciling libvirt VM {container_name}: {e}")

        # Neither Docker nor libvirt found the node
        logger.warning(f"Node {container_name} not found in Docker or libvirt")
        results.append(NodeReconcileResult(
            container_name=container_name,
            action="error",
            success=False,
            error=f"Node not found: {container_name}",
        ))

    return NodeReconcileResponse(
        lab_id=lab_id,
        results=results,
    )


@app.post("/labs/{lab_id}/extract-configs")
async def extract_configs(lab_id: str) -> ExtractConfigsResponse:
    """Extract running configs from all cEOS nodes in a lab.

    This extracts the running-config from all running cEOS containers
    and saves them to the workspace as startup-config files for persistence.
    Returns both the count and the actual config content for each node.
    """
    logger.info(f"Extract configs request: lab={lab_id}")

    try:
        provider = get_provider_for_request()
        workspace = get_workspace(lab_id)

        # Call the provider's extract method - now returns list of (node_name, content) tuples
        extracted_configs = await provider._extract_all_ceos_configs(lab_id, workspace)

        # Convert to response format
        configs = [
            ExtractedConfig(node_name=node_name, content=content)
            for node_name, content in extracted_configs
        ]

        return ExtractConfigsResponse(
            success=True,
            extracted_count=len(configs),
            configs=configs,
        )

    except Exception as e:
        logger.error(f"Extract configs error for lab {lab_id}: {e}", exc_info=True)
        return ExtractConfigsResponse(
            success=False,
            extracted_count=0,
            error=str(e),
        )


@app.put("/labs/{lab_id}/nodes/{node_name}/config")
async def update_node_config(
    lab_id: str,
    node_name: str,
    request: UpdateConfigRequest,
) -> UpdateConfigResponse:
    """Update the startup config for a node.

    This saves the config to the agent's workspace so it will be used
    on next container restart/redeploy. Called by the API after extracting
    configs to sync the agent's workspace with the API's workspace.
    """
    logger.info(f"Update config request: lab={lab_id} node={node_name}")

    try:
        workspace = get_workspace(lab_id)
        config_dir = workspace / "configs" / node_name
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "startup-config"
        config_file.write_text(request.content)
        logger.info(f"Saved startup config for {node_name} ({len(request.content)} bytes)")
        return UpdateConfigResponse(success=True)
    except Exception as e:
        logger.error(f"Update config error for {node_name}: {e}", exc_info=True)
        return UpdateConfigResponse(success=False, error=str(e))


# --- Container Control Endpoints ---

@app.post("/containers/{container_name}/start")
async def start_container(container_name: str) -> dict:
    """Start a stopped container.

    Used by the sync system to start individual nodes without redeploying.
    Uses asyncio.to_thread() to avoid blocking the event loop.
    """
    logger.info(f"Starting container: {container_name}")

    try:
        import docker
        client = docker.from_env()
        container = await asyncio.to_thread(client.containers.get, container_name)

        if container.status == "running":
            return {"success": True, "message": "Container already running"}

        await asyncio.to_thread(container.start)
        return {"success": True, "message": "Container started"}

    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Container '{container_name}' not found")
    except docker.errors.APIError as e:
        logger.error(f"Docker API error starting {container_name}: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Error starting container {container_name}: {e}")
        return {"success": False, "error": str(e)}


@app.post("/containers/{container_name}/stop")
async def stop_container(container_name: str) -> dict:
    """Stop a running container.

    Used by the sync system to stop individual nodes without destroying the lab.
    Uses asyncio.to_thread() to avoid blocking the event loop.
    """
    logger.info(f"Stopping container: {container_name}")

    try:
        import docker
        client = docker.from_env()
        container = await asyncio.to_thread(client.containers.get, container_name)

        if container.status != "running":
            return {"success": True, "message": "Container already stopped"}

        await asyncio.to_thread(container.stop, timeout=settings.container_stop_timeout)
        return {"success": True, "message": "Container stopped"}

    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Container '{container_name}' not found")
    except docker.errors.APIError as e:
        logger.error(f"Docker API error stopping {container_name}: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Error stopping container {container_name}: {e}")
        return {"success": False, "error": str(e)}


@app.delete("/containers/{container_name}")
async def remove_container(container_name: str, force: bool = False) -> dict:
    """Remove a container.

    Used to clean up orphan containers or containers that need to be recreated.
    Uses asyncio.to_thread() to avoid blocking the event loop.
    """
    logger.info(f"Removing container: {container_name} (force={force})")

    try:
        import docker
        client = docker.from_env()
        container = await asyncio.to_thread(client.containers.get, container_name)

        await asyncio.to_thread(container.remove, force=force)
        return {"success": True, "message": "Container removed"}

    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Container '{container_name}' not found")
    except docker.errors.APIError as e:
        logger.error(f"Docker API error removing {container_name}: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Error removing container {container_name}: {e}")
        return {"success": False, "error": str(e)}


@app.delete("/containers/{lab_id}/{container_name}")
async def remove_container_for_lab(
    lab_id: str, container_name: str, force: bool = False
) -> dict:
    """Remove a specific container for a lab.

    This endpoint is used for live node removal when a user deletes a node
    from the canvas. The lab_id is used for logging and validation.

    Args:
        lab_id: Lab identifier (for logging/validation)
        container_name: Name of the container to remove
        force: Whether to force removal of running container

    Returns:
        Dict with success status and message/error
    """
    logger.info(f"Removing container {container_name} for lab {lab_id} (force={force})")

    try:
        import docker

        client = docker.from_env()
        container = await asyncio.to_thread(client.containers.get, container_name)

        # Validate container belongs to the lab (optional safety check)
        labels = container.labels or {}
        container_lab_id = labels.get("archetype.lab_id")
        if container_lab_id and container_lab_id != lab_id:
            logger.warning(
                f"Container {container_name} belongs to lab {container_lab_id}, "
                f"not {lab_id} - proceeding anyway"
            )

        # Stop first if running, then remove
        if container.status == "running":
            logger.info(f"Stopping running container {container_name} before removal")
            await asyncio.to_thread(container.stop, timeout=10)

        await asyncio.to_thread(container.remove, force=force)
        logger.info(f"Successfully removed container {container_name} for lab {lab_id}")
        return {"success": True, "message": "Container removed"}

    except docker.errors.NotFound:
        logger.info(f"Container {container_name} not found (already removed)")
        return {"success": True, "message": "Container not found (already removed)"}
    except docker.errors.APIError as e:
        logger.error(f"Docker API error removing {container_name}: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Error removing container {container_name}: {e}")
        return {"success": False, "error": str(e)}


# --- Reconciliation Endpoints ---

@app.get("/discover-labs")
async def discover_labs() -> DiscoverLabsResponse:
    """Discover all running labs by inspecting containers.

    Used by controller to reconcile state after restart.
    """
    logger.info("Discovering running labs...")

    # Use default provider for discovery
    provider = get_provider_for_request()
    discovered = await provider.discover_labs()

    labs = [
        DiscoveredLab(
            lab_id=lab_id,
            nodes=[
                NodeInfo(
                    name=node.name,
                    status=provider_status_to_schema(node.status),
                    container_id=node.container_id,
                    image=node.image,
                    ip_addresses=node.ip_addresses,
                )
                for node in nodes
            ],
        )
        for lab_id, nodes in discovered.items()
    ]

    return DiscoverLabsResponse(labs=labs)


@app.post("/cleanup-orphans")
async def cleanup_orphans(request: CleanupOrphansRequest) -> CleanupOrphansResponse:
    """Remove containers for labs that no longer exist.

    Args:
        request: Contains list of valid lab IDs to keep

    Returns:
        List of removed container names
    """
    logger.info(f"Cleaning up orphan containers, keeping {len(request.valid_lab_ids)} valid labs")

    # Use default provider for cleanup
    provider = get_provider_for_request()
    valid_ids = set(request.valid_lab_ids)
    removed = await provider.cleanup_orphan_containers(valid_ids)

    return CleanupOrphansResponse(
        removed_containers=removed,
        errors=[],
    )


@app.post("/cleanup-lab-orphans")
async def cleanup_lab_orphans(request: CleanupLabOrphansRequest) -> CleanupLabOrphansResponse:
    """Remove orphaned containers for a specific lab.

    Used when nodes are migrated between agents. Removes containers for
    nodes that are no longer assigned to this agent.

    Args:
        request: Contains lab_id and list of node_names to keep

    Returns:
        Lists of removed and kept containers
    """
    import docker
    from docker.errors import APIError

    logger.info(f"Cleaning up orphan containers for lab {request.lab_id}, keeping {len(request.keep_node_names)} nodes")

    removed = []
    kept = []
    errors = []
    keep_set = set(request.keep_node_names)

    try:
        client = docker.from_env()

        # Find all containers for this lab
        containers = client.containers.list(all=True, filters={
            "label": f"archetype.lab_id={request.lab_id}"
        })

        for container in containers:
            labels = container.labels
            node_name = labels.get("archetype.node_name")

            if not node_name:
                continue

            if node_name in keep_set:
                kept.append(container.name)
                logger.debug(f"Keeping container {container.name} (node {node_name} assigned to this agent)")
            else:
                # This container is for a node not assigned to this agent - remove it
                try:
                    logger.info(f"Removing orphan container {container.name} (node {node_name} not assigned to this agent)")
                    container.remove(force=True)
                    removed.append(container.name)
                except APIError as e:
                    error_msg = f"Failed to remove {container.name}: {e}"
                    logger.warning(error_msg)
                    errors.append(error_msg)

    except Exception as e:
        error_msg = f"Error during lab orphan cleanup: {e}"
        logger.error(error_msg)
        errors.append(error_msg)

    return CleanupLabOrphansResponse(
        removed_containers=removed,
        kept_containers=kept,
        errors=errors,
    )


@app.post("/prune-docker")
async def prune_docker(request: DockerPruneRequest) -> DockerPruneResponse:
    """Prune Docker resources to reclaim disk space.

    This endpoint cleans up:
    - Dangling images (images not tagged and not used by containers)
    - Build cache (if enabled)
    - Unused volumes (if enabled, conservative by default)

    Images used by containers from valid labs are protected.

    Args:
        request: Contains valid_lab_ids and flags for what to prune

    Returns:
        Counts of removed resources and space reclaimed
    """
    logger.info(
        f"Docker prune request: dangling_images={request.prune_dangling_images}, "
        f"build_cache={request.prune_build_cache}, unused_volumes={request.prune_unused_volumes}"
    )

    images_removed = 0
    build_cache_removed = 0
    volumes_removed = 0
    space_reclaimed = 0
    errors = []

    try:
        import docker
        client = docker.from_env()

        # Get images used by running containers (to protect them)
        protected_image_ids = set()
        try:
            containers = client.containers.list(all=True)
            for container in containers:
                # Check if container belongs to a valid lab
                labels = container.labels
                lab_id = labels.get("archetype.lab_id", "")

                # Protect images from valid labs
                is_valid_lab = lab_id in request.valid_lab_ids if lab_id else False

                if is_valid_lab or container.status == "running":
                    if container.image:
                        protected_image_ids.add(container.image.id)

        except Exception as e:
            errors.append(f"Error getting container info: {e}")
            logger.warning(f"Error getting container info for protection: {e}")

        # Prune dangling images
        if request.prune_dangling_images:
            try:
                # Use filters to only prune dangling images
                result = client.images.prune(filters={"dangling": True})
                deleted = result.get("ImagesDeleted") or []
                images_removed = len([d for d in deleted if d.get("Deleted")])
                space_reclaimed += result.get("SpaceReclaimed", 0)
                logger.info(f"Pruned {images_removed} dangling images, reclaimed {result.get('SpaceReclaimed', 0)} bytes")
            except Exception as e:
                errors.append(f"Error pruning images: {e}")
                logger.warning(f"Error pruning dangling images: {e}")

        # Prune build cache
        if request.prune_build_cache:
            try:
                # Use the low-level API for build cache pruning
                result = client.api.prune_builds()
                build_cache_removed = len(result.get("CachesDeleted") or [])
                space_reclaimed += result.get("SpaceReclaimed", 0)
                logger.info(f"Pruned {build_cache_removed} build cache entries, reclaimed {result.get('SpaceReclaimed', 0)} bytes")
            except Exception as e:
                errors.append(f"Error pruning build cache: {e}")
                logger.warning(f"Error pruning build cache: {e}")

        # Prune unused volumes (conservative - disabled by default)
        if request.prune_unused_volumes:
            try:
                result = client.volumes.prune()
                deleted = result.get("VolumesDeleted") or []
                volumes_removed = len(deleted)
                space_reclaimed += result.get("SpaceReclaimed", 0)
                logger.info(f"Pruned {volumes_removed} volumes, reclaimed {result.get('SpaceReclaimed', 0)} bytes")
            except Exception as e:
                errors.append(f"Error pruning volumes: {e}")
                logger.warning(f"Error pruning volumes: {e}")

        return DockerPruneResponse(
            success=True,
            images_removed=images_removed,
            build_cache_removed=build_cache_removed,
            volumes_removed=volumes_removed,
            space_reclaimed=space_reclaimed,
            errors=errors,
        )

    except Exception as e:
        logger.error(f"Docker prune failed: {e}")
        return DockerPruneResponse(
            success=False,
            errors=[str(e)],
        )


# --- Overlay Networking Endpoints ---

@app.post("/overlay/tunnel")
async def create_tunnel(request: CreateTunnelRequest) -> CreateTunnelResponse:
    """Create a VXLAN tunnel to another host.

    This creates a VXLAN interface and associated bridge for
    connecting lab nodes across hosts.
    """
    if not settings.enable_vxlan:
        return CreateTunnelResponse(
            success=False,
            error="VXLAN overlay not enabled on this agent",
        )

    logger.info(f"Creating tunnel: lab={request.lab_id}, link={request.link_id}, remote={request.remote_ip}")

    try:
        overlay = get_overlay_manager()

        # Create VXLAN tunnel
        tunnel = await overlay.create_tunnel(
            lab_id=request.lab_id,
            link_id=request.link_id,
            local_ip=request.local_ip,
            remote_ip=request.remote_ip,
            vni=request.vni,
        )

        # Create bridge and attach VXLAN
        await overlay.create_bridge(tunnel)

        return CreateTunnelResponse(
            success=True,
            tunnel=TunnelInfo(
                vni=tunnel.vni,
                interface_name=tunnel.interface_name,
                local_ip=tunnel.local_ip,
                remote_ip=tunnel.remote_ip,
                lab_id=tunnel.lab_id,
                link_id=tunnel.link_id,
                vlan_tag=tunnel.vlan_tag,
            ),
        )

    except Exception as e:
        logger.error(f"Tunnel creation failed: {e}")
        return CreateTunnelResponse(
            success=False,
            error=str(e),
        )


@app.post("/overlay/attach")
async def attach_container(request: AttachContainerRequest) -> AttachContainerResponse:
    """Attach a container to an overlay bridge.

    This creates a veth pair, moves one end into the container,
    and attaches the other to the overlay bridge.
    """
    if not settings.enable_vxlan:
        return AttachContainerResponse(
            success=False,
            error="VXLAN overlay not enabled on this agent",
        )

    # Convert short container name to full Docker container name
    # The API sends short names like "eos_1", but Docker needs the full name
    # like "archetype-d35ec857-eos_1"
    provider = get_provider("docker")
    if provider is None:
        return AttachContainerResponse(
            success=False,
            error="Docker provider not available",
        )
    full_container_name = provider.get_container_name(request.lab_id, request.container_name)

    # Convert interface name for cEOS containers
    # cEOS uses INTFTYPE=eth, meaning CLI "Ethernet1" maps to Linux "eth1"
    interface_name = request.interface_name
    try:
        container = provider.docker.containers.get(full_container_name)
        env_vars = container.attrs.get("Config", {}).get("Env", [])
        intftype = None
        for env in env_vars:
            if env.startswith("INTFTYPE="):
                intftype = env.split("=", 1)[1]
                break
        if intftype == "eth" and interface_name.startswith("Ethernet"):
            # Convert Ethernet1 -> eth1, Ethernet2 -> eth2, etc.
            import re
            match = re.match(r"Ethernet(\d+)", interface_name)
            if match:
                interface_name = f"eth{match.group(1)}"
                logger.info(f"Converted interface name: {request.interface_name} -> {interface_name}")
    except Exception as e:
        logger.warning(f"Could not check container env for interface conversion: {e}")

    logger.info(f"Attaching container: {full_container_name} to bridge for {request.link_id}")

    try:
        overlay = get_overlay_manager()

        # Get the bridge for this link
        bridges = await overlay.get_bridges_for_lab(request.lab_id)
        bridge = None
        for b in bridges:
            if b.link_id == request.link_id:
                bridge = b
                break

        if not bridge:
            return AttachContainerResponse(
                success=False,
                error=f"No bridge found for link {request.link_id}",
            )

        # Attach container
        success = await overlay.attach_container(
            bridge=bridge,
            container_name=full_container_name,
            interface_name=interface_name,
            ip_address=request.ip_address,
        )

        if success:
            return AttachContainerResponse(success=True)
        else:
            return AttachContainerResponse(
                success=False,
                error="Failed to attach container to bridge",
            )

    except Exception as e:
        logger.error(f"Container attachment failed: {e}")
        return AttachContainerResponse(
            success=False,
            error=str(e),
        )


@app.post("/overlay/cleanup")
async def cleanup_overlay(request: CleanupOverlayRequest) -> CleanupOverlayResponse:
    """Clean up all overlay networking for a lab."""
    if not settings.enable_vxlan:
        return CleanupOverlayResponse()

    logger.info(f"Cleaning up overlay for lab: {request.lab_id}")

    try:
        overlay = get_overlay_manager()
        result = await overlay.cleanup_lab(request.lab_id)

        return CleanupOverlayResponse(
            tunnels_deleted=result["tunnels_deleted"],
            bridges_deleted=result["bridges_deleted"],
            errors=result["errors"],
        )

    except Exception as e:
        logger.error(f"Overlay cleanup failed: {e}")
        return CleanupOverlayResponse(errors=[str(e)])


@app.get("/overlay/status")
async def overlay_status() -> OverlayStatusResponse:
    """Get status of all overlay networks on this agent."""
    if not settings.enable_vxlan:
        return OverlayStatusResponse()

    try:
        overlay = get_overlay_manager()
        status = overlay.get_tunnel_status()

        tunnels = [
            TunnelInfo(
                vni=t["vni"],
                interface_name=t["interface"],
                local_ip=t["local_ip"],
                remote_ip=t["remote_ip"],
                lab_id=t["lab_id"],
                link_id=t["link_id"],
            )
            for t in status["tunnels"]
        ]

        return OverlayStatusResponse(
            vteps=status.get("vteps", []),
            tunnels=tunnels,
            bridges=status["bridges"],
        )

    except Exception as e:
        logger.error(f"Overlay status failed: {e}")
        return OverlayStatusResponse()


@app.post("/overlay/vtep")
async def ensure_vtep(request: EnsureVtepRequest) -> EnsureVtepResponse:
    """Ensure a VTEP exists to the remote host.

    This implements the new trunk VTEP model where there is one VTEP per
    remote host (not one per link). The VTEP is created in trunk mode
    (no VLAN tag) and all cross-host links to that remote host share it.

    If a VTEP already exists to the remote host, it is returned without
    creating a new one.
    """
    if not settings.enable_vxlan:
        return EnsureVtepResponse(
            success=False,
            error="VXLAN overlay is disabled on this agent",
        )

    try:
        overlay = get_overlay_manager()

        # Check if VTEP already exists
        existing = overlay.get_vtep(request.remote_ip)
        if existing:
            return EnsureVtepResponse(
                success=True,
                vtep=VtepInfo(
                    interface_name=existing.interface_name,
                    vni=existing.vni,
                    local_ip=existing.local_ip,
                    remote_ip=existing.remote_ip,
                    remote_host_id=existing.remote_host_id,
                    tenant_mtu=existing.tenant_mtu,
                ),
                created=False,
            )

        # Create new VTEP
        vtep = await overlay.ensure_vtep(
            local_ip=request.local_ip,
            remote_ip=request.remote_ip,
            remote_host_id=request.remote_host_id,
        )

        return EnsureVtepResponse(
            success=True,
            vtep=VtepInfo(
                interface_name=vtep.interface_name,
                vni=vtep.vni,
                local_ip=vtep.local_ip,
                remote_ip=vtep.remote_ip,
                remote_host_id=vtep.remote_host_id,
                tenant_mtu=vtep.tenant_mtu,
            ),
            created=True,
        )

    except Exception as e:
        logger.error(f"Ensure VTEP failed: {e}")
        return EnsureVtepResponse(success=False, error=str(e))


@app.post("/overlay/attach-link")
async def attach_overlay_interface(
    request: AttachOverlayInterfaceRequest,
) -> AttachOverlayInterfaceResponse:
    """Attach a container interface to the overlay with a specific VLAN tag.

    This is the new model where the VLAN tag is specified by the controller
    (coordinated across agents) rather than derived from a per-link tunnel.
    The VTEP should already exist (via /overlay/vtep) in trunk mode.
    """
    if not settings.enable_vxlan:
        return AttachOverlayInterfaceResponse(
            success=False,
            error="VXLAN overlay is disabled on this agent",
        )

    try:
        overlay = get_overlay_manager()

        success = await overlay.attach_overlay_interface(
            lab_id=request.lab_id,
            container_name=request.container_name,
            interface_name=request.interface_name,
            vlan_tag=request.vlan_tag,
            tenant_mtu=request.tenant_mtu,
            link_id=request.link_id,
            remote_ip=request.remote_ip,
        )

        if success:
            return AttachOverlayInterfaceResponse(success=True)
        else:
            return AttachOverlayInterfaceResponse(
                success=False,
                error="Failed to attach interface",
            )

    except Exception as e:
        logger.error(f"Attach overlay interface failed: {e}")
        return AttachOverlayInterfaceResponse(success=False, error=str(e))


@app.post("/overlay/detach-link")
async def detach_overlay_interface(
    request: DetachOverlayInterfaceRequest,
) -> DetachOverlayInterfaceResponse:
    """Detach a link from the overlay network.

    This performs a complete detach:
    1. Isolates the container interface by assigning a unique VLAN tag
    2. Removes the link from VTEP reference counting
    3. Optionally deletes the VTEP if no more links use it

    This ensures the interface no longer participates in the cross-host
    link's L2 domain while preserving the underlying network infrastructure
    for other links.
    """
    if not settings.enable_vxlan:
        return DetachOverlayInterfaceResponse(
            success=False,
            error="VXLAN overlay is disabled on this agent",
        )

    logger.info(
        f"Detach overlay interface: lab={request.lab_id}, "
        f"container={request.container_name}, interface={request.interface_name}, "
        f"link_id={request.link_id}"
    )

    interface_isolated = False
    new_vlan = None

    try:
        # Step 1: Isolate the interface by assigning a unique VLAN
        # This prevents traffic flow through the overlay for this link
        try:
            plugin = _get_docker_ovs_plugin()
            if plugin is None:
                logger.warning("Docker OVS plugin not available, skipping interface isolation")
            else:
                # Build full container name
                provider = get_provider_for_request()
                container_name = provider.get_container_name(request.lab_id, request.container_name)

                new_vlan = await plugin.isolate_port(
                    request.lab_id,
                    container_name,
                    request.interface_name,
                )
                if new_vlan is not None:
                    interface_isolated = True
                    logger.info(
                        f"Interface {container_name}:{request.interface_name} "
                        f"isolated to VLAN {new_vlan}"
                    )
                else:
                    logger.warning(
                        f"Failed to isolate {container_name}:{request.interface_name}"
                    )
        except Exception as e:
            logger.warning(f"Interface isolation failed (continuing with VTEP cleanup): {e}")

        # Step 2: Handle VTEP reference counting
        overlay = get_overlay_manager()

        result = await overlay.detach_overlay_interface(
            link_id=request.link_id,
            remote_ip=request.remote_ip,
            delete_vtep_if_unused=request.delete_vtep_if_unused,
        )

        return DetachOverlayInterfaceResponse(
            success=result["success"],
            interface_isolated=interface_isolated,
            new_vlan=new_vlan,
            vtep_deleted=result["vtep_deleted"],
            remaining_links=result["remaining_links"],
            error=result["error"],
        )

    except Exception as e:
        logger.error(f"Detach overlay interface failed: {e}")
        return DetachOverlayInterfaceResponse(
            success=False,
            interface_isolated=interface_isolated,
            new_vlan=new_vlan,
            error=str(e),
        )


@app.post("/network/test-mtu")
async def test_mtu(request: MtuTestRequest) -> MtuTestResponse:
    """Test MTU to a target IP address.

    Runs ping with DF (Don't Fragment) bit set to verify the network path
    supports the requested MTU. Also detects link type (direct/routed) via
    TTL analysis.

    Link type detection:
    - TTL >= 64: Direct/switched (L2 adjacent)
    - TTL < 64: Routed (TTL decremented by intermediate hops)

    Args:
        request: Target IP and MTU to test

    Returns:
        MtuTestResponse with test results
    """
    target_ip = request.target_ip
    mtu = request.mtu

    # Calculate ping payload size: MTU - 20 (IP header) - 8 (ICMP header)
    payload_size = mtu - 28

    if payload_size < 0:
        return MtuTestResponse(
            success=False,
            error=f"MTU {mtu} too small (minimum 28 bytes for IP + ICMP headers)",
        )

    logger.info(f"Testing MTU {mtu} to {target_ip} (payload size: {payload_size})")

    try:
        # Run ping with DF bit set (-M do = don't fragment)
        # -c 3: send 3 pings
        # -W 5: 5 second timeout
        # -s: payload size
        process = await asyncio.create_subprocess_exec(
            "ping",
            "-M", "do",  # Don't fragment
            "-c", "3",
            "-W", "5",
            "-s", str(payload_size),
            target_ip,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=20.0,
        )
        stdout_text = stdout.decode(errors="replace")
        stderr_text = stderr.decode(errors="replace")

        if process.returncode != 0:
            # Check for "message too long" which indicates MTU issue
            combined = stdout_text + stderr_text
            if "message too long" in combined.lower() or "frag needed" in combined.lower():
                return MtuTestResponse(
                    success=False,
                    error=f"Path MTU too small for {mtu} bytes",
                )
            return MtuTestResponse(
                success=False,
                error=f"Ping failed: {stderr_text.strip() or stdout_text.strip() or 'Unknown error'}",
            )

        # Parse ping output for TTL and latency
        ttl = None
        latency_ms = None
        link_type = "unknown"

        # Parse TTL from "ttl=64" pattern
        import re
        ttl_match = re.search(r"ttl=(\d+)", stdout_text, re.IGNORECASE)
        if ttl_match:
            ttl = int(ttl_match.group(1))
            # Determine link type based on TTL
            # Common default TTLs: Linux=64, Windows=128, Cisco=255
            # If TTL >= 64, likely direct; lower values suggest routing hops
            if ttl >= 64:
                link_type = "direct"
            else:
                link_type = "routed"

        # Parse latency from rtt summary or individual ping
        # Format: "rtt min/avg/max/mdev = 0.123/0.456/0.789/0.111 ms"
        rtt_match = re.search(r"rtt min/avg/max/mdev = [\d.]+/([\d.]+)/", stdout_text)
        if rtt_match:
            latency_ms = float(rtt_match.group(1))
        else:
            # Try to get from individual ping line: "time=0.123 ms"
            time_match = re.search(r"time=([\d.]+)\s*ms", stdout_text)
            if time_match:
                latency_ms = float(time_match.group(1))

        logger.info(
            f"MTU test to {target_ip}: success, "
            f"mtu={mtu}, ttl={ttl}, latency={latency_ms}ms, type={link_type}"
        )

        return MtuTestResponse(
            success=True,
            tested_mtu=mtu,
            link_type=link_type,
            latency_ms=latency_ms,
            ttl=ttl,
        )

    except asyncio.TimeoutError:
        return MtuTestResponse(
            success=False,
            error="Ping timed out",
        )
    except Exception as e:
        logger.error(f"MTU test failed: {e}")
        return MtuTestResponse(
            success=False,
            error=str(e),
        )


# --- OVS Interface Management ---


@app.post("/labs/{lab_id}/nodes/{node_name}/fix-interfaces")
async def fix_node_interfaces(lab_id: str, node_name: str) -> FixInterfacesResponse:
    """Fix interface names for a running container.

    Docker may assign interface names based on network attachment order rather
    than the intended names. This endpoint renames interfaces to match their
    intended OVS network names (eth1, eth2, etc.).

    Useful for:
    - Containers restarted outside normal deployment flow
    - Recovering from agent restart that lost plugin state
    - Manual troubleshooting

    Args:
        lab_id: Lab identifier
        node_name: Node name (display name, not container name)

    Returns:
        FixInterfacesResponse with counts of fixed interfaces
    """
    provider = get_provider_for_request()
    container_name = provider.get_container_name(lab_id, node_name)

    logger.info(f"Fixing interface names for {container_name}")

    try:
        result = await provider._fix_interface_names(container_name, lab_id)
        return FixInterfacesResponse(
            success=True,
            node=node_name,
            fixed=result.get("fixed", 0),
            already_correct=result.get("already_correct", 0),
            errors=result.get("errors", []),
        )
    except Exception as e:
        logger.error(f"Failed to fix interfaces for {node_name}: {e}")
        return FixInterfacesResponse(
            success=False,
            node=node_name,
            errors=[str(e)],
        )


@app.get("/labs/{lab_id}/nodes/{node_name}/linux-interfaces")
async def list_node_linux_interfaces(lab_id: str, node_name: str) -> dict:
    """List Linux interface names inside a container network namespace."""
    try:
        provider = get_provider_for_request()
        container_name = provider.get_container_name(lab_id, node_name)
        container = provider.docker.containers.get(container_name)
        pid = container.attrs.get("State", {}).get("Pid")
        if not pid:
            return {"container": container_name, "interfaces": [], "error": "Container not running"}

        proc = await asyncio.create_subprocess_exec(
            "nsenter", "-t", str(pid), "-n",
            "ip", "-o", "link", "show",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return {
                "container": container_name,
                "interfaces": [],
                "error": stderr.decode().strip() or "Failed to list interfaces",
            }

        interfaces = []
        for line in stdout.decode().strip().split("\n"):
            parts = line.split(":", 2)
            if len(parts) >= 2:
                name = parts[1].strip().split("@")[0]
                interfaces.append(name)

        return {"container": container_name, "interfaces": interfaces, "error": None}
    except Exception as e:
        return {"container": node_name, "interfaces": [], "error": str(e)}


# --- OVS Hot-Connect Link Management ---

@app.post("/labs/{lab_id}/links")
async def create_link(lab_id: str, link: LinkCreate) -> LinkCreateResponse:
    """Hot-connect two interfaces in a running lab.

    This creates a Layer 2 link between two container interfaces by
    assigning them the same VLAN tag on the OVS bridge. Uses the Docker
    OVS plugin which manages the shared OVS bridge.

    Args:
        lab_id: Lab identifier
        link: Link creation request with source/target nodes and interfaces

    Returns:
        LinkCreateResponse with link details or error
    """
    if not settings.enable_ovs:
        return LinkCreateResponse(
            success=False,
            error="OVS networking not enabled on this agent",
        )

    logger.info(
        f"Hot-connect request: lab={lab_id}, "
        f"{link.source_node}:{link.source_interface} <-> "
        f"{link.target_node}:{link.target_interface}"
    )

    try:
        # Get container names from provider
        provider = get_provider_for_request()
        container_a = provider.get_container_name(lab_id, link.source_node)
        container_b = provider.get_container_name(lab_id, link.target_node)

        # Hot-connect via Docker OVS plugin (uses shared OVS bridge)
        plugin = _get_docker_ovs_plugin()
        vlan_tag = await plugin.hot_connect(
            lab_id=lab_id,
            container_a=container_a,
            iface_a=link.source_interface,
            container_b=container_b,
            iface_b=link.target_interface,
        )

        if vlan_tag is None:
            return LinkCreateResponse(
                success=False,
                error="hot_connect failed - endpoints not found",
            )

        link_id = f"{link.source_node}:{link.source_interface}-{link.target_node}:{link.target_interface}"

        return LinkCreateResponse(
            success=True,
            link=LinkInfo(
                link_id=link_id,
                lab_id=lab_id,
                source_node=link.source_node,
                source_interface=link.source_interface,
                target_node=link.target_node,
                target_interface=link.target_interface,
                state=LinkState.CONNECTED,
                vlan_tag=vlan_tag,
            ),
        )

    except Exception as e:
        logger.error(f"Hot-connect failed: {e}")
        return LinkCreateResponse(
            success=False,
            error=str(e),
        )


@app.delete("/labs/{lab_id}/links/{link_id}")
async def delete_link(lab_id: str, link_id: str) -> LinkDeleteResponse:
    """Hot-disconnect a link in a running lab.

    This breaks a Layer 2 link between two container interfaces by
    assigning them separate VLAN tags. Uses the Docker OVS plugin.

    Args:
        lab_id: Lab identifier
        link_id: Link identifier (format: "node1:iface1-node2:iface2")

    Returns:
        LinkDeleteResponse with success status
    """
    if not settings.enable_ovs:
        return LinkDeleteResponse(
            success=False,
            error="OVS networking not enabled on this agent",
        )

    logger.info(f"Hot-disconnect request: lab={lab_id}, link={link_id}")

    try:
        # Parse link_id to get endpoints
        # Format: "node1:iface1-node2:iface2"
        parts = link_id.split("-")
        if len(parts) != 2:
            return LinkDeleteResponse(
                success=False,
                error=f"Invalid link_id format: {link_id}",
            )

        ep_a = parts[0].split(":")
        ep_b = parts[1].split(":")

        if len(ep_a) != 2 or len(ep_b) != 2:
            return LinkDeleteResponse(
                success=False,
                error=f"Invalid link_id format: {link_id}",
            )

        node_a, iface_a = ep_a
        node_b, iface_b = ep_b

        # Get container names from provider
        provider = get_provider_for_request()
        container_a = provider.get_container_name(lab_id, node_a)
        container_b = provider.get_container_name(lab_id, node_b)

        # Hot-disconnect via Docker OVS plugin
        # Disconnect both endpoints by giving each a unique VLAN
        plugin = _get_docker_ovs_plugin()
        await plugin.hot_disconnect(lab_id, container_a, iface_a)
        await plugin.hot_disconnect(lab_id, container_b, iface_b)

        return LinkDeleteResponse(success=True)

    except Exception as e:
        logger.error(f"Hot-disconnect failed: {e}")
        return LinkDeleteResponse(
            success=False,
            error=str(e),
        )


@app.get("/labs/{lab_id}/links")
async def list_links(lab_id: str) -> LinkListResponse:
    """List all links and their connection states for a lab.

    Returns all OVS-managed links for the specified lab, including
    their VLAN tags and connection state.

    Args:
        lab_id: Lab identifier

    Returns:
        LinkListResponse with list of links
    """
    if not settings.enable_ovs:
        return LinkListResponse(links=[])

    try:
        ovs = get_ovs_manager()
        if not ovs._initialized:
            return LinkListResponse(links=[])

        # Get provider for container name resolution
        provider = get_provider_for_request()

        links = []
        for ovs_link in ovs.get_links_for_lab(lab_id):
            # Parse port keys to get node/interface names
            # Format: "container_name:interface_name"
            port_a_parts = ovs_link.port_a.rsplit(":", 1)
            port_b_parts = ovs_link.port_b.rsplit(":", 1)

            # Extract node names from container names
            # Container format: "archetype-{lab_id}-{node_name}"
            source_node = port_a_parts[0].split("-")[-1] if port_a_parts else ""
            target_node = port_b_parts[0].split("-")[-1] if port_b_parts else ""
            source_interface = port_a_parts[1] if len(port_a_parts) > 1 else ""
            target_interface = port_b_parts[1] if len(port_b_parts) > 1 else ""

            links.append(LinkInfo(
                link_id=ovs_link.link_id,
                lab_id=ovs_link.lab_id,
                source_node=source_node,
                source_interface=source_interface,
                target_node=target_node,
                target_interface=target_interface,
                state=LinkState.CONNECTED,
                vlan_tag=ovs_link.vlan_tag,
            ))

        return LinkListResponse(links=links)

    except Exception as e:
        logger.error(f"List links failed: {e}")
        return LinkListResponse(links=[])


# --- OVS Status Endpoint ---

@app.get("/ovs/status")
async def ovs_status() -> OVSStatusResponse:
    """Get status of OVS networking on this agent.

    Returns information about the OVS bridge, provisioned ports,
    and active links.
    """
    if not settings.enable_ovs:
        return OVSStatusResponse(
            bridge_name="",
            initialized=False,
        )

    try:
        ovs = get_ovs_manager()
        status = ovs.get_status()

        ports = [
            OVSPortInfo(
                port_name=p["port_name"],
                container_name=p["container"],
                interface_name=p["interface"],
                vlan_tag=p["vlan_tag"],
                lab_id=p["lab_id"],
            )
            for p in status["ports"]
        ]

        links = [
            LinkInfo(
                link_id=l["link_id"],
                lab_id=l["lab_id"],
                source_node=l["port_a"].rsplit(":", 1)[0].split("-")[-1],
                source_interface=l["port_a"].rsplit(":", 1)[1] if ":" in l["port_a"] else "",
                target_node=l["port_b"].rsplit(":", 1)[0].split("-")[-1],
                target_interface=l["port_b"].rsplit(":", 1)[1] if ":" in l["port_b"] else "",
                state=LinkState.CONNECTED,
                vlan_tag=l["vlan_tag"],
            )
            for l in status["links"]
        ]

        return OVSStatusResponse(
            bridge_name=status["bridge"],
            initialized=status["initialized"],
            ports=ports,
            links=links,
            vlan_allocations=status["vlan_allocations"],
        )

    except Exception as e:
        logger.error(f"OVS status failed: {e}")
        return OVSStatusResponse(
            bridge_name="",
            initialized=False,
        )


# --- Docker OVS Plugin Endpoints ---

def _get_docker_ovs_plugin():
    """Get the Docker OVS plugin instance."""
    from agent.network.docker_plugin import get_docker_ovs_plugin
    return get_docker_ovs_plugin()


@app.get("/ovs-plugin/health")
async def ovs_plugin_health() -> PluginHealthResponse:
    """Check Docker OVS plugin health.

    Returns health status including socket availability, OVS accessibility,
    and resource counts.
    """
    if not settings.enable_ovs_plugin:
        return PluginHealthResponse(healthy=False)

    try:
        plugin = _get_docker_ovs_plugin()
        health = await plugin.health_check()

        return PluginHealthResponse(
            healthy=health["healthy"],
            checks=health["checks"],
            uptime_seconds=health["uptime_seconds"],
            started_at=health.get("started_at"),
        )

    except Exception as e:
        logger.error(f"OVS plugin health check failed: {e}")
        return PluginHealthResponse(healthy=False)


@app.get("/ovs-plugin/status")
async def ovs_plugin_status() -> PluginStatusResponse:
    """Get comprehensive Docker OVS plugin status.

    Returns detailed information about all lab bridges, endpoints,
    and management networks.
    """
    if not settings.enable_ovs_plugin:
        return PluginStatusResponse(healthy=False)

    try:
        plugin = _get_docker_ovs_plugin()
        status = await plugin.get_plugin_status()

        bridges = [
            PluginBridgeInfo(
                lab_id=b["lab_id"],
                bridge_name=b["bridge_name"],
                port_count=b["port_count"],
                vlan_range_used=tuple(b["vlan_range_used"]),
                vxlan_tunnels=b["vxlan_tunnels"],
                external_interfaces=b["external_interfaces"],
                last_activity=b["last_activity"],
            )
            for b in status["bridges"]
        ]

        return PluginStatusResponse(
            healthy=status["healthy"],
            labs_count=status["labs_count"],
            endpoints_count=status["endpoints_count"],
            networks_count=status["networks_count"],
            management_networks_count=status["management_networks_count"],
            bridges=bridges,
            uptime_seconds=status["uptime_seconds"],
        )

    except Exception as e:
        logger.error(f"OVS plugin status failed: {e}")
        return PluginStatusResponse(healthy=False)


@app.get("/ovs-plugin/labs/{lab_id}")
async def ovs_plugin_lab_status(lab_id: str) -> PluginBridgeInfo | dict:
    """Get status of a specific lab's OVS bridge.

    Returns detailed information about the lab's bridge, ports,
    VXLAN tunnels, and external interfaces.
    """
    if not settings.enable_ovs_plugin:
        return {"error": "OVS plugin not enabled"}

    try:
        plugin = _get_docker_ovs_plugin()
        status = plugin.get_lab_status(lab_id)

        if not status:
            return {"error": f"Lab {lab_id} not found"}

        # Get bridge info
        lab_bridge = plugin.lab_bridges.get(lab_id)
        if not lab_bridge:
            return {"error": f"Lab bridge not found for {lab_id}"}

        return PluginBridgeInfo(
            lab_id=lab_id,
            bridge_name=lab_bridge.bridge_name,
            port_count=len(status.get("endpoints", [])),
            vlan_range_used=(100, lab_bridge.next_vlan - 1),
            vxlan_tunnels=len(lab_bridge.vxlan_tunnels),
            external_interfaces=list(lab_bridge.external_ports.keys()),
            last_activity=lab_bridge.last_activity.isoformat(),
        )

    except Exception as e:
        logger.error(f"OVS plugin lab status failed: {e}")
        return {"error": str(e)}


@app.get("/ovs-plugin/labs/{lab_id}/ports")
async def ovs_plugin_lab_ports(lab_id: str) -> PluginLabPortsResponse:
    """Get detailed port information for a lab.

    Returns all ports including VLAN tags and traffic statistics.
    """
    if not settings.enable_ovs_plugin:
        return PluginLabPortsResponse(lab_id=lab_id, ports=[])

    try:
        plugin = _get_docker_ovs_plugin()
        ports_data = await plugin.get_lab_ports(lab_id)

        ports = [
            PluginPortInfo(
                port_name=p["port_name"],
                bridge_name=p.get("bridge_name"),
                container=p.get("container"),
                interface=p["interface"],
                vlan_tag=p["vlan_tag"],
                rx_bytes=p.get("rx_bytes", 0),
                tx_bytes=p.get("tx_bytes", 0),
            )
            for p in ports_data
        ]

        return PluginLabPortsResponse(lab_id=lab_id, ports=ports)

    except Exception as e:
        logger.error(f"OVS plugin lab ports failed: {e}")
        return PluginLabPortsResponse(lab_id=lab_id, ports=[])


@app.get("/ovs-plugin/labs/{lab_id}/flows")
async def ovs_plugin_lab_flows(lab_id: str) -> PluginFlowsResponse:
    """Get OVS flow information for a lab.

    Returns OpenFlow rules for debugging network connectivity.
    """
    if not settings.enable_ovs_plugin:
        return PluginFlowsResponse(error="OVS plugin not enabled")

    try:
        plugin = _get_docker_ovs_plugin()
        flows_data = await plugin.get_lab_flows(lab_id)

        if "error" in flows_data:
            return PluginFlowsResponse(error=flows_data["error"])

        return PluginFlowsResponse(
            bridge=flows_data.get("bridge"),
            flow_count=flows_data.get("flow_count", 0),
            flows=flows_data.get("flows", []),
        )

    except Exception as e:
        logger.error(f"OVS plugin lab flows failed: {e}")
        return PluginFlowsResponse(error=str(e))


@app.post("/ovs-plugin/labs/{lab_id}/vxlan")
async def create_plugin_vxlan(lab_id: str, request: PluginVxlanRequest) -> PluginVxlanResponse:
    """Create VXLAN tunnel on a lab's OVS bridge.

    Used for multi-host connectivity between lab bridges on different agents.
    """
    if not settings.enable_ovs_plugin:
        return PluginVxlanResponse(success=False, error="OVS plugin not enabled")

    logger.info(
        f"Creating plugin VXLAN: lab={lab_id}, vni={request.vni}, "
        f"remote={request.remote_ip}"
    )

    try:
        plugin = _get_docker_ovs_plugin()
        port_name = await plugin.create_vxlan_tunnel(
            lab_id=lab_id,
            link_id=request.link_id,
            local_ip=request.local_ip,
            remote_ip=request.remote_ip,
            vni=request.vni,
            vlan_tag=request.vlan_tag,
        )

        return PluginVxlanResponse(success=True, port_name=port_name)

    except Exception as e:
        logger.error(f"Plugin VXLAN creation failed: {e}")
        return PluginVxlanResponse(success=False, error=str(e))


@app.delete("/ovs-plugin/labs/{lab_id}/vxlan/{vni}")
async def delete_plugin_vxlan(lab_id: str, vni: int) -> PluginVxlanResponse:
    """Delete VXLAN tunnel from a lab's OVS bridge.
    """
    if not settings.enable_ovs_plugin:
        return PluginVxlanResponse(success=False, error="OVS plugin not enabled")

    logger.info(f"Deleting plugin VXLAN: lab={lab_id}, vni={vni}")

    try:
        plugin = _get_docker_ovs_plugin()
        success = await plugin.delete_vxlan_tunnel(lab_id, vni)

        if success:
            return PluginVxlanResponse(success=True)
        else:
            return PluginVxlanResponse(success=False, error="Tunnel not found")

    except Exception as e:
        logger.error(f"Plugin VXLAN deletion failed: {e}")
        return PluginVxlanResponse(success=False, error=str(e))


@app.post("/ovs-plugin/labs/{lab_id}/external")
async def attach_plugin_external(
    lab_id: str, request: PluginExternalAttachRequest
) -> PluginExternalAttachResponse:
    """Attach external host interface to lab's OVS bridge.

    Enables connectivity between lab containers and external networks.
    """
    if not settings.enable_ovs_plugin:
        return PluginExternalAttachResponse(success=False, error="OVS plugin not enabled")

    logger.info(
        f"Attaching external interface: lab={lab_id}, "
        f"interface={request.external_interface}"
    )

    try:
        plugin = _get_docker_ovs_plugin()
        vlan_tag = await plugin.attach_external_interface(
            lab_id=lab_id,
            external_interface=request.external_interface,
            vlan_tag=request.vlan_tag,
        )

        return PluginExternalAttachResponse(success=True, vlan_tag=vlan_tag)

    except Exception as e:
        logger.error(f"External interface attachment failed: {e}")
        return PluginExternalAttachResponse(success=False, error=str(e))


@app.delete("/ovs-plugin/labs/{lab_id}/external/{interface}")
async def detach_plugin_external(lab_id: str, interface: str) -> PluginExternalAttachResponse:
    """Detach external interface from lab's OVS bridge.
    """
    if not settings.enable_ovs_plugin:
        return PluginExternalAttachResponse(success=False, error="OVS plugin not enabled")

    logger.info(f"Detaching external interface: lab={lab_id}, interface={interface}")

    try:
        plugin = _get_docker_ovs_plugin()
        success = await plugin.detach_external_interface(lab_id, interface)

        if success:
            return PluginExternalAttachResponse(success=True)
        else:
            return PluginExternalAttachResponse(success=False, error="Interface not found")

    except Exception as e:
        logger.error(f"External interface detachment failed: {e}")
        return PluginExternalAttachResponse(success=False, error=str(e))


@app.get("/ovs-plugin/labs/{lab_id}/external")
async def list_plugin_external(lab_id: str) -> PluginExternalListResponse:
    """List external interfaces attached to a lab's OVS bridge.
    """
    if not settings.enable_ovs_plugin:
        return PluginExternalListResponse(lab_id=lab_id, interfaces=[])

    try:
        plugin = _get_docker_ovs_plugin()
        external_ports = plugin.list_external_interfaces(lab_id)

        interfaces = [
            PluginExternalInfo(interface=iface, vlan_tag=vlan)
            for iface, vlan in external_ports.items()
        ]

        return PluginExternalListResponse(lab_id=lab_id, interfaces=interfaces)

    except Exception as e:
        logger.error(f"List external interfaces failed: {e}")
        return PluginExternalListResponse(lab_id=lab_id, interfaces=[])


@app.post("/ovs-plugin/labs/{lab_id}/mgmt")
async def create_plugin_mgmt_network(lab_id: str) -> PluginMgmtNetworkResponse:
    """Create management network for a lab.

    Creates a Docker bridge network with NAT for container management access.
    """
    if not settings.enable_ovs_plugin:
        return PluginMgmtNetworkResponse(success=False, error="OVS plugin not enabled")

    logger.info(f"Creating management network for lab {lab_id}")

    try:
        plugin = _get_docker_ovs_plugin()
        mgmt_net = await plugin.create_management_network(lab_id)

        return PluginMgmtNetworkResponse(
            success=True,
            network=PluginMgmtNetworkInfo(
                lab_id=mgmt_net.lab_id,
                network_id=mgmt_net.network_id,
                network_name=mgmt_net.network_name,
                subnet=mgmt_net.subnet,
                gateway=mgmt_net.gateway,
            ),
        )

    except Exception as e:
        logger.error(f"Management network creation failed: {e}")
        return PluginMgmtNetworkResponse(success=False, error=str(e))


@app.post("/ovs-plugin/labs/{lab_id}/mgmt/attach")
async def attach_to_plugin_mgmt(
    lab_id: str, request: PluginMgmtAttachRequest
) -> PluginMgmtAttachResponse:
    """Attach container to management network.

    Returns the assigned IP address for the container's management interface.
    """
    if not settings.enable_ovs_plugin:
        return PluginMgmtAttachResponse(success=False, error="OVS plugin not enabled")

    logger.info(f"Attaching {request.container_id} to management network for lab {lab_id}")

    try:
        plugin = _get_docker_ovs_plugin()
        ip_address = await plugin.attach_to_management(request.container_id, lab_id)

        if ip_address:
            return PluginMgmtAttachResponse(success=True, ip_address=ip_address)
        else:
            return PluginMgmtAttachResponse(success=False, error="Failed to get IP address")

    except Exception as e:
        logger.error(f"Management network attachment failed: {e}")
        return PluginMgmtAttachResponse(success=False, error=str(e))


@app.delete("/ovs-plugin/labs/{lab_id}/mgmt")
async def delete_plugin_mgmt_network(lab_id: str) -> PluginMgmtNetworkResponse:
    """Delete management network for a lab.
    """
    if not settings.enable_ovs_plugin:
        return PluginMgmtNetworkResponse(success=False, error="OVS plugin not enabled")

    logger.info(f"Deleting management network for lab {lab_id}")

    try:
        plugin = _get_docker_ovs_plugin()
        success = await plugin.delete_management_network(lab_id)

        if success:
            return PluginMgmtNetworkResponse(success=True)
        else:
            return PluginMgmtNetworkResponse(success=False, error="Network not found")

    except Exception as e:
        logger.error(f"Management network deletion failed: {e}")
        return PluginMgmtNetworkResponse(success=False, error=str(e))


# --- Carrier State and Port Control Endpoints ---


@app.post("/labs/{lab_id}/interfaces/{node}/{interface}/carrier")
async def set_interface_carrier(
    lab_id: str,
    node: str,
    interface: str,
    request: CarrierStateRequest,
) -> CarrierStateResponse:
    """Set the carrier state of a container interface.

    This uses `ip link set carrier on/off` to simulate physical link up/down.
    When carrier is off, the interface cannot send or receive traffic but
    remains configured in the container.

    Args:
        lab_id: Lab identifier
        node: Node name (container name or node name)
        interface: Interface name in the container (e.g., "eth1")
        request: Contains "on" or "off" state

    Returns:
        CarrierStateResponse with success status
    """
    if not settings.enable_ovs_plugin:
        return CarrierStateResponse(
            success=False,
            container=node,
            interface=interface,
            state=request.state,
            error="OVS plugin not enabled",
        )

    logger.info(f"Set carrier {request.state}: lab={lab_id}, node={node}, interface={interface}")

    try:
        plugin = _get_docker_ovs_plugin()

        # Resolve container name - might be node name or already container name
        provider = get_provider_for_request()
        container_name = provider.get_container_name(lab_id, node)

        success = await plugin.set_carrier_state(lab_id, container_name, interface, request.state)

        return CarrierStateResponse(
            success=success,
            container=container_name,
            interface=interface,
            state=request.state,
            error=None if success else "Failed to set carrier state",
        )

    except Exception as e:
        logger.error(f"Set carrier state failed: {e}")
        return CarrierStateResponse(
            success=False,
            container=node,
            interface=interface,
            state=request.state,
            error=str(e),
        )


@app.post("/labs/{lab_id}/interfaces/{node}/{interface}/isolate")
async def isolate_interface(
    lab_id: str,
    node: str,
    interface: str,
) -> PortIsolateResponse:
    """Isolate a container interface from its L2 domain.

    This assigns the interface a unique VLAN tag and sets carrier off,
    effectively disconnecting it from any other interface.

    Args:
        lab_id: Lab identifier
        node: Node name (container name or node name)
        interface: Interface name in the container

    Returns:
        PortIsolateResponse with new VLAN tag
    """
    if not settings.enable_ovs_plugin:
        return PortIsolateResponse(
            success=False,
            container=node,
            interface=interface,
            error="OVS plugin not enabled",
        )

    logger.info(f"Isolate port: lab={lab_id}, node={node}, interface={interface}")

    try:
        plugin = _get_docker_ovs_plugin()

        # Resolve container name
        provider = get_provider_for_request()
        container_name = provider.get_container_name(lab_id, node)

        vlan_tag = await plugin.isolate_port(lab_id, container_name, interface)

        return PortIsolateResponse(
            success=vlan_tag is not None,
            container=container_name,
            interface=interface,
            vlan_tag=vlan_tag,
            error=None if vlan_tag is not None else "Failed to isolate port",
        )

    except Exception as e:
        logger.error(f"Port isolation failed: {e}")
        return PortIsolateResponse(
            success=False,
            container=node,
            interface=interface,
            error=str(e),
        )


@app.post("/labs/{lab_id}/interfaces/{node}/{interface}/restore")
async def restore_interface(
    lab_id: str,
    node: str,
    interface: str,
    request: PortRestoreRequest,
) -> PortRestoreResponse:
    """Restore a container interface to a specific VLAN and enable carrier.

    This reconnects the interface to the specified L2 domain (VLAN) and
    simulates physical link restoration.

    Args:
        lab_id: Lab identifier
        node: Node name (container name or node name)
        interface: Interface name in the container
        request: Contains target VLAN to restore to

    Returns:
        PortRestoreResponse with success status
    """
    if not settings.enable_ovs_plugin:
        return PortRestoreResponse(
            success=False,
            container=node,
            interface=interface,
            vlan_tag=request.target_vlan,
            error="OVS plugin not enabled",
        )

    logger.info(f"Restore port: lab={lab_id}, node={node}, interface={interface}, vlan={request.target_vlan}")

    try:
        plugin = _get_docker_ovs_plugin()

        # Resolve container name
        provider = get_provider_for_request()
        container_name = provider.get_container_name(lab_id, node)

        success = await plugin.restore_port(lab_id, container_name, interface, request.target_vlan)

        return PortRestoreResponse(
            success=success,
            container=container_name,
            interface=interface,
            vlan_tag=request.target_vlan,
            error=None if success else "Failed to restore port",
        )

    except Exception as e:
        logger.error(f"Port restore failed: {e}")
        return PortRestoreResponse(
            success=False,
            container=node,
            interface=interface,
            vlan_tag=request.target_vlan,
            error=str(e),
        )


@app.get("/labs/{lab_id}/interfaces/{node}/{interface}/vlan")
async def get_interface_vlan(
    lab_id: str,
    node: str,
    interface: str,
    read_from_ovs: bool = False,
) -> PortVlanResponse:
    """Get the current VLAN tag for a container interface.

    Args:
        lab_id: Lab identifier
        node: Node name (container name or node name)
        interface: Interface name in the container
        read_from_ovs: If True, read directly from OVS instead of in-memory state.
                       Use this for verification to get ground truth.

    Returns:
        PortVlanResponse with current VLAN tag
    """
    if not settings.enable_ovs_plugin:
        return PortVlanResponse(
            container=node,
            interface=interface,
            error="OVS plugin not enabled",
        )

    try:
        plugin = _get_docker_ovs_plugin()

        # Resolve container name
        provider = get_provider_for_request()
        container_name = provider.get_container_name(lab_id, node)

        vlan_tag = await plugin.get_endpoint_vlan(
            lab_id, container_name, interface, read_from_ovs=read_from_ovs
        )

        return PortVlanResponse(
            container=container_name,
            interface=interface,
            vlan_tag=vlan_tag,
            error=None if vlan_tag is not None else "Endpoint not found",
        )

    except Exception as e:
        logger.error(f"Get VLAN failed: {e}")
        return PortVlanResponse(
            container=node,
            interface=interface,
            error=str(e),
        )


# --- External Connectivity Endpoints ---

@app.post("/labs/{lab_id}/external/connect")
async def connect_to_external(
    lab_id: str,
    request: ExternalConnectRequest,
) -> ExternalConnectResponse:
    """Connect a container interface to an external network.

    This establishes connectivity between a container interface and an
    external host interface (e.g., for internet access, management network,
    or physical lab equipment).

    Args:
        lab_id: Lab identifier
        request: Connection request with container/interface and external interface

    Returns:
        ExternalConnectResponse with VLAN tag or error
    """
    if not settings.enable_ovs:
        return ExternalConnectResponse(
            success=False,
            error="OVS networking not enabled on this agent",
        )

    logger.info(
        f"External connect request: lab={lab_id}, "
        f"node={request.node_name}, interface={request.interface_name}, "
        f"external={request.external_interface}"
    )

    try:
        ovs = get_ovs_manager()
        if not ovs._initialized:
            await ovs.initialize()

        # Resolve container name
        if request.container_name:
            container_name = request.container_name
        elif request.node_name:
            provider = get_provider_for_request()
            container_name = provider.get_container_name(lab_id, request.node_name)
        else:
            return ExternalConnectResponse(
                success=False,
                error="Either container_name or node_name must be provided",
            )

        # Connect to external network
        vlan_tag = await ovs.connect_to_external(
            container_name=container_name,
            interface_name=request.interface_name,
            external_interface=request.external_interface,
            vlan_tag=request.vlan_tag,
        )

        return ExternalConnectResponse(
            success=True,
            vlan_tag=vlan_tag,
        )

    except Exception as e:
        logger.error(f"External connect failed: {e}")
        return ExternalConnectResponse(
            success=False,
            error=str(e),
        )


@app.post("/ovs/patch")
async def create_bridge_patch(request: BridgePatchRequest) -> BridgePatchResponse:
    """Create a patch connection to another OVS or Linux bridge.

    This establishes connectivity between the arch-ovs bridge and another
    bridge (e.g., libvirt virbr0, Docker bridge, or physical bridge).

    Args:
        request: Patch request with target bridge name and optional VLAN

    Returns:
        BridgePatchResponse with patch port name or error
    """
    if not settings.enable_ovs:
        return BridgePatchResponse(
            success=False,
            error="OVS networking not enabled on this agent",
        )

    logger.info(f"Bridge patch request: target={request.target_bridge}")

    try:
        ovs = get_ovs_manager()
        if not ovs._initialized:
            await ovs.initialize()

        patch_port = await ovs.create_patch_to_bridge(
            target_bridge=request.target_bridge,
            vlan_tag=request.vlan_tag,
        )

        return BridgePatchResponse(
            success=True,
            patch_port=patch_port,
        )

    except Exception as e:
        logger.error(f"Bridge patch failed: {e}")
        return BridgePatchResponse(
            success=False,
            error=str(e),
        )


@app.delete("/ovs/patch")
async def delete_bridge_patch(request: BridgeDeletePatchRequest) -> BridgeDeletePatchResponse:
    """Delete a patch connection to another bridge.

    This removes connectivity between the arch-ovs bridge and another bridge.

    Args:
        request: Delete request with target bridge name

    Returns:
        BridgeDeletePatchResponse with success status
    """
    if not settings.enable_ovs:
        return BridgeDeletePatchResponse(
            success=False,
            error="OVS networking not enabled on this agent",
        )

    logger.info(f"Bridge patch delete request: target={request.target_bridge}")

    try:
        ovs = get_ovs_manager()
        if not ovs._initialized:
            return BridgeDeletePatchResponse(
                success=False,
                error="OVS not initialized",
            )

        success = await ovs.delete_patch_to_bridge(request.target_bridge)
        return BridgeDeletePatchResponse(success=success)

    except Exception as e:
        logger.error(f"Bridge patch delete failed: {e}")
        return BridgeDeletePatchResponse(
            success=False,
            error=str(e),
        )


@app.post("/labs/{lab_id}/external/disconnect")
async def disconnect_from_external(
    lab_id: str,
    request: ExternalDisconnectRequest,
) -> ExternalDisconnectResponse:
    """Disconnect an external network interface.

    This detaches an external host interface from the OVS bridge,
    breaking connectivity to any container interfaces that were connected.

    Args:
        lab_id: Lab identifier
        request: Disconnect request with external interface name

    Returns:
        ExternalDisconnectResponse with success status
    """
    if not settings.enable_ovs:
        return ExternalDisconnectResponse(
            success=False,
            error="OVS networking not enabled on this agent",
        )

    logger.info(f"External disconnect request: lab={lab_id}, interface={request.external_interface}")

    try:
        ovs = get_ovs_manager()
        if not ovs._initialized:
            return ExternalDisconnectResponse(
                success=False,
                error="OVS not initialized",
            )

        success = await ovs.detach_external_interface(request.external_interface)
        return ExternalDisconnectResponse(success=success)

    except Exception as e:
        logger.error(f"External disconnect failed: {e}")
        return ExternalDisconnectResponse(
            success=False,
            error=str(e),
        )


@app.get("/labs/{lab_id}/external")
async def list_external_connections(lab_id: str) -> ExternalListResponse:
    """List all external network connections.

    Returns all external interfaces attached to the OVS bridge and their
    connected container interfaces.

    Args:
        lab_id: Lab identifier (used for filtering, currently returns all)

    Returns:
        ExternalListResponse with list of external connections
    """
    if not settings.enable_ovs:
        return ExternalListResponse(connections=[])

    try:
        ovs = get_ovs_manager()
        if not ovs._initialized:
            return ExternalListResponse(connections=[])

        connections_data = await ovs.list_external_connections()

        connections = [
            ExternalConnectionInfo(
                external_interface=c["external_interface"],
                vlan_tag=c["vlan_tag"],
                connected_ports=c["connected_ports"],
            )
            for c in connections_data
        ]

        return ExternalListResponse(connections=connections)

    except Exception as e:
        logger.error(f"List external connections failed: {e}")
        return ExternalListResponse(connections=[])


# --- Node Readiness Endpoint ---

@app.get("/labs/{lab_id}/nodes/{node_name}/ready")
async def check_node_ready(
    lab_id: str,
    node_name: str,
    provider_type: str | None = None,
    kind: str | None = None,
) -> dict:
    """Check if a node has completed its boot sequence.

    Returns readiness status based on vendor-specific probes that check
    container logs or CLI output for boot completion patterns.

    When a node first becomes ready, any configured post-boot commands
    are executed (e.g., cEOS iptables fixes).

    Args:
        lab_id: Lab identifier
        node_name: Node name within the lab
        provider_type: Optional provider type ("docker" or "libvirt").
                       If not specified, tries Docker first, then libvirt.
        kind: Optional device kind. Required for libvirt VMs if not
              auto-detected.
    """
    from agent.readiness import (
        get_probe_for_vendor,
        get_readiness_timeout,
        run_post_boot_commands,
    )

    # Try libvirt if explicitly requested or if Docker fails
    if provider_type == "libvirt":
        return await _check_libvirt_readiness(lab_id, node_name, kind)

    # Try Docker first
    docker_provider = get_provider("docker")
    if docker_provider is not None:
        container_name = docker_provider.get_container_name(lab_id, node_name)

        # Get the node kind to determine appropriate probe
        try:
            import docker
            client = docker.from_env()
            container = client.containers.get(container_name)
            detected_kind = container.labels.get("archetype.node_kind", "")
            kind = kind or detected_kind

            # Get and run the appropriate probe
            probe = get_probe_for_vendor(kind)
            result = await probe.check(container_name)

            # If ready, run post-boot commands (idempotent - only runs once per container)
            if result.is_ready:
                await run_post_boot_commands(container_name, kind)

            return {
                "is_ready": result.is_ready,
                "message": result.message,
                "progress_percent": result.progress_percent,
                "timeout": get_readiness_timeout(kind),
                "provider": "docker",
            }
        except Exception:
            # Docker container not found, try libvirt if no provider specified
            if provider_type is None:
                return await _check_libvirt_readiness(lab_id, node_name, kind)
            return {
                "is_ready": False,
                "message": "Container not found",
                "progress_percent": 0,
                "provider": "docker",
            }

    # No Docker provider, try libvirt
    return await _check_libvirt_readiness(lab_id, node_name, kind)


async def _check_libvirt_readiness(
    lab_id: str,
    node_name: str,
    kind: str | None,
) -> dict:
    """Check readiness for a libvirt VM.

    Args:
        lab_id: Lab identifier
        node_name: Node name
        kind: Device kind for vendor config lookup

    Returns:
        Readiness status dict
    """
    from agent.readiness import get_readiness_timeout

    libvirt_provider = get_provider("libvirt")
    if libvirt_provider is None:
        return {
            "is_ready": False,
            "message": "Libvirt provider not available",
            "progress_percent": None,
            "provider": "libvirt",
        }

    if kind is None:
        return {
            "is_ready": False,
            "message": "Device kind required for VM readiness check",
            "progress_percent": None,
            "provider": "libvirt",
        }

    result = await libvirt_provider.check_readiness(lab_id, node_name, kind)

    return {
        "is_ready": result.is_ready,
        "message": result.message,
        "progress_percent": result.progress_percent,
        "timeout": get_readiness_timeout(kind),
        "provider": "libvirt",
    }


# --- Network Interface Discovery Endpoints ---

@app.get("/interfaces")
async def list_interfaces() -> dict:
    """List available network interfaces on this host.

    Returns physical interfaces that can be used for VLAN sub-interfaces
    or external network connections.
    """
    import subprocess

    def _sync_list_interfaces() -> dict:
        interfaces = []
        try:
            # Get list of interfaces using ip command
            result = subprocess.run(
                ["ip", "-j", "link", "show"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                import json
                link_data = json.loads(result.stdout)

                for link in link_data:
                    name = link.get("ifname", "")
                    # Skip loopback, docker, and veth interfaces
                    if name in ("lo",) or name.startswith(("docker", "veth", "br-", "clab")):
                        continue

                    # Get interface state and type
                    operstate = link.get("operstate", "unknown")
                    link_type = link.get("link_type", "")

                    # Get IP addresses for this interface
                    addr_result = subprocess.run(
                        ["ip", "-j", "addr", "show", name],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    ipv4_addresses = []
                    if addr_result.returncode == 0:
                        addr_data = json.loads(addr_result.stdout)
                        for iface in addr_data:
                            for addr_info in iface.get("addr_info", []):
                                if addr_info.get("family") == "inet":
                                    ipv4_addresses.append(f"{addr_info['local']}/{addr_info.get('prefixlen', 24)}")

                    interfaces.append({
                        "name": name,
                        "state": operstate,
                        "type": link_type,
                        "ipv4_addresses": ipv4_addresses,
                        "mac": link.get("address"),
                        # Indicate if this is a VLAN sub-interface
                        "is_vlan": "." in name,
                    })

        except Exception as e:
            logger.error(f"Error listing interfaces: {e}")
            return {"interfaces": [], "error": str(e)}

        return {"interfaces": interfaces}

    return await asyncio.to_thread(_sync_list_interfaces)


@app.get("/interfaces/details")
async def get_interface_details() -> InterfaceDetailsResponse:
    """Get detailed interface info including MTU and default route detection.

    Returns all physical interfaces with their current MTU, identifies the
    default route interface, and detects which network manager is in use.
    """
    import json as json_module
    import subprocess

    from agent.network.interface_config import (
        detect_network_manager,
        get_default_route_interface,
        get_interface_mtu,
        is_physical_interface,
    )

    def _sync_get_details() -> InterfaceDetailsResponse:
        interfaces: list[InterfaceDetail] = []
        default_route_iface = get_default_route_interface()
        network_mgr = detect_network_manager()

        try:
            # Get list of all interfaces
            result = subprocess.run(
                ["ip", "-j", "link", "show"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                link_data = json_module.loads(result.stdout)

                for link in link_data:
                    name = link.get("ifname", "")
                    if not name or name == "lo":
                        continue

                    # Check if physical
                    is_physical = is_physical_interface(name)

                    # Get MTU
                    mtu = get_interface_mtu(name) or link.get("mtu", 1500)

                    # Get state
                    operstate = link.get("operstate", "unknown")

                    # Get MAC address
                    mac = link.get("address")

                    # Get IP addresses
                    addr_result = subprocess.run(
                        ["ip", "-j", "addr", "show", name],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    ipv4_addresses = []
                    if addr_result.returncode == 0:
                        addr_data = json_module.loads(addr_result.stdout)
                        for iface in addr_data:
                            for addr_info in iface.get("addr_info", []):
                                if addr_info.get("family") == "inet":
                                    ipv4_addresses.append(
                                        f"{addr_info['local']}/{addr_info.get('prefixlen', 24)}"
                                    )

                    interfaces.append(InterfaceDetail(
                        name=name,
                        mtu=mtu,
                        is_physical=is_physical,
                        is_default_route=(name == default_route_iface),
                        mac=mac,
                        ipv4_addresses=ipv4_addresses,
                        state=operstate,
                    ))

        except Exception as e:
            logger.error(f"Error getting interface details: {e}")

        return InterfaceDetailsResponse(
            interfaces=interfaces,
            default_route_interface=default_route_iface,
            network_manager=network_mgr,
        )

    return await asyncio.to_thread(_sync_get_details)


@app.post("/interfaces/{interface_name}/mtu")
async def set_interface_mtu(interface_name: str, request: SetMtuRequest) -> SetMtuResponse:
    """Set MTU on a physical interface with optional persistence.

    Args:
        interface_name: Name of the interface to configure
        request: MTU value and persistence settings

    Returns:
        Result of the MTU configuration operation
    """
    from agent.network.interface_config import (
        detect_network_manager,
        get_interface_mtu,
        is_physical_interface,
        set_mtu_persistent,
        set_mtu_runtime,
    )

    # Validate interface exists
    previous_mtu = get_interface_mtu(interface_name)
    if previous_mtu is None:
        return SetMtuResponse(
            success=False,
            interface=interface_name,
            previous_mtu=0,
            new_mtu=request.mtu,
            error=f"Interface {interface_name} not found",
        )

    # Warn if not a physical interface
    if not is_physical_interface(interface_name):
        logger.warning(f"Setting MTU on non-physical interface {interface_name}")

    network_mgr = detect_network_manager()

    # Apply runtime MTU first
    success, error = await set_mtu_runtime(interface_name, request.mtu)
    if not success:
        return SetMtuResponse(
            success=False,
            interface=interface_name,
            previous_mtu=previous_mtu,
            new_mtu=request.mtu,
            network_manager=network_mgr,
            error=error,
        )

    # Persist if requested
    persisted = False
    persist_error = None
    if request.persist:
        if network_mgr == "unknown":
            persist_error = "Cannot persist: unknown network manager"
            logger.warning(f"MTU set on {interface_name} but persistence unavailable: {persist_error}")
        else:
            persisted, persist_error = await set_mtu_persistent(interface_name, request.mtu, network_mgr)
            if not persisted:
                logger.warning(f"MTU set on {interface_name} but persistence failed: {persist_error}")

    # Verify the MTU was applied
    new_mtu = get_interface_mtu(interface_name) or request.mtu

    return SetMtuResponse(
        success=True,
        interface=interface_name,
        previous_mtu=previous_mtu,
        new_mtu=new_mtu,
        persisted=persisted,
        network_manager=network_mgr,
        error=persist_error if not persisted and request.persist else None,
    )


@app.get("/bridges")
async def list_bridges() -> dict:
    """List available Linux bridges on this host.

    Returns bridges that can be used for external network connections.
    """
    import subprocess

    def _sync_list_bridges() -> dict:
        bridges = []
        try:
            # Get list of bridges using bridge command
            result = subprocess.run(
                ["bridge", "-j", "link", "show"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                import json
                bridge_data = json.loads(result.stdout)

                # Extract unique bridge names (master field)
                seen_bridges = set()
                for link in bridge_data:
                    master = link.get("master")
                    if master and master not in seen_bridges:
                        seen_bridges.add(master)

                # Get details for each bridge
                for bridge_name in sorted(seen_bridges):
                    # Skip docker-managed bridges
                    if bridge_name.startswith(("docker", "br-")):
                        continue

                    bridge_info = {"name": bridge_name, "interfaces": []}

                    # Get interfaces attached to this bridge
                    for link in bridge_data:
                        if link.get("master") == bridge_name:
                            bridge_info["interfaces"].append(link.get("ifname"))

                    bridges.append(bridge_info)

        except FileNotFoundError:
            # bridge command not available, try ip command
            try:
                result = subprocess.run(
                    ["ip", "-j", "link", "show", "type", "bridge"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )

                if result.returncode == 0:
                    import json
                    link_data = json.loads(result.stdout)

                    for link in link_data:
                        name = link.get("ifname", "")
                        # Skip docker-managed bridges
                        if name.startswith(("docker", "br-")):
                            continue

                        bridges.append({
                            "name": name,
                            "state": link.get("operstate", "unknown"),
                            "interfaces": [],  # Would need additional queries
                        })

            except Exception as e:
                logger.error(f"Error listing bridges: {e}")
                return {"bridges": [], "error": str(e)}

        except Exception as e:
            logger.error(f"Error listing bridges: {e}")
            return {"bridges": [], "error": str(e)}

        return {"bridges": bridges}

    return await asyncio.to_thread(_sync_list_bridges)


# --- Image Synchronization Endpoints ---

# Track active image pull jobs
_image_pull_jobs: dict[str, ImagePullProgress] = {}


def _get_docker_images() -> list[DockerImageInfo]:
    """Get list of Docker images on this agent."""
    try:
        import docker
        client = docker.from_env()
        images = []

        for img in client.images.list():
            # Get image details
            image_id = img.id
            tags = img.tags or []
            size_bytes = img.attrs.get("Size", 0)
            created = img.attrs.get("Created", None)

            images.append(DockerImageInfo(
                id=image_id,
                tags=tags,
                size_bytes=size_bytes,
                created=created,
            ))

        return images
    except Exception as e:
        logger.error(f"Error listing Docker images: {e}")
        return []


@app.get("/images")
def list_images() -> ImageInventoryResponse:
    """List all Docker images on this agent.

    Returns a list of images with their tags, sizes, and IDs.
    Used by controller to check image availability before deployment.
    """
    images = _get_docker_images()
    return ImageInventoryResponse(images=images)


@app.get("/images/{reference:path}")
def check_image(reference: str) -> ImageExistsResponse:
    """Check if a specific image exists on this agent.

    Args:
        reference: Docker image reference (e.g., "ceos:4.28.0F")

    Returns:
        Whether the image exists and its details if found.
    """
    try:
        import docker
        client = docker.from_env()

        # Try to get the image
        try:
            img = client.images.get(reference)
            return ImageExistsResponse(
                exists=True,
                image=DockerImageInfo(
                    id=img.id,
                    tags=img.tags or [],
                    size_bytes=img.attrs.get("Size", 0),
                    created=img.attrs.get("Created", None),
                ),
            )
        except docker.errors.ImageNotFound:
            return ImageExistsResponse(exists=False)

    except Exception as e:
        logger.error(f"Error checking image {reference}: {e}")
        return ImageExistsResponse(exists=False)


@app.post("/images/receive")
async def receive_image(
    file: UploadFile,
    image_id: str = "",
    reference: str = "",
    total_bytes: int = 0,
    job_id: str = "",
) -> ImageReceiveResponse:
    """Receive a streamed Docker image tar from controller.

    This endpoint accepts a Docker image tar file (from `docker save`)
    and loads it into the local Docker daemon.

    Args:
        file: The image tar file
        image_id: Library image ID for tracking
        reference: Docker reference (e.g., "ceos:4.28.0F")
        total_bytes: Expected size for progress
        job_id: Sync job ID for progress reporting

    Returns:
        Result of loading the image
    """
    import os
    import subprocess
    import tempfile

    logger.info(f"Receiving image: {reference} ({total_bytes} bytes)")

    # Update progress if job_id provided
    if job_id:
        _image_pull_jobs[job_id] = ImagePullProgress(
            job_id=job_id,
            status="transferring",
            progress_percent=0,
            bytes_transferred=0,
            total_bytes=total_bytes,
        )

    try:
        # Save uploaded file to temp
        with tempfile.NamedTemporaryFile(delete=False, suffix=".tar") as tmp_file:
            bytes_written = 0
            chunk_size = 1024 * 1024  # 1MB chunks

            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                tmp_file.write(chunk)
                bytes_written += len(chunk)

                # Update progress
                if job_id and total_bytes > 0:
                    percent = min(90, int((bytes_written / total_bytes) * 90))
                    _image_pull_jobs[job_id] = ImagePullProgress(
                        job_id=job_id,
                        status="transferring",
                        progress_percent=percent,
                        bytes_transferred=bytes_written,
                        total_bytes=total_bytes,
                    )

            tmp_path = tmp_file.name

        logger.debug(f"Saved {bytes_written} bytes to {tmp_path}")

        # Update status to loading
        if job_id:
            _image_pull_jobs[job_id] = ImagePullProgress(
                job_id=job_id,
                status="loading",
                progress_percent=90,
                bytes_transferred=bytes_written,
                total_bytes=total_bytes,
            )

        # Load into Docker (wrapped in thread to avoid blocking)
        def _sync_docker_load():
            return subprocess.run(
                ["docker", "load", "-i", tmp_path],
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout for large images
            )

        result = await asyncio.to_thread(_sync_docker_load)

        # Clean up temp file
        os.unlink(tmp_path)

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "docker load failed"
            logger.error(f"Docker load failed for {reference}: {error_msg}")
            if job_id:
                _image_pull_jobs[job_id] = ImagePullProgress(
                    job_id=job_id,
                    status="failed",
                    progress_percent=0,
                    error=error_msg,
                )
            return ImageReceiveResponse(success=False, error=error_msg)

        # Parse loaded images from output
        output = (result.stdout or "") + (result.stderr or "")
        loaded_images = []
        for line in output.splitlines():
            if "Loaded image:" in line:
                loaded_images.append(line.split("Loaded image:", 1)[-1].strip())
            elif "Loaded image ID:" in line:
                loaded_images.append(line.split("Loaded image ID:", 1)[-1].strip())

        logger.info(f"Successfully loaded images: {loaded_images}")

        # Update final status
        if job_id:
            _image_pull_jobs[job_id] = ImagePullProgress(
                job_id=job_id,
                status="completed",
                progress_percent=100,
                bytes_transferred=bytes_written,
                total_bytes=total_bytes,
            )

        return ImageReceiveResponse(success=True, loaded_images=loaded_images)

    except subprocess.TimeoutExpired:
        error_msg = "docker load timed out"
        logger.error(f"Docker load timeout for {reference}")
        if job_id:
            _image_pull_jobs[job_id] = ImagePullProgress(
                job_id=job_id,
                status="failed",
                error=error_msg,
            )
        return ImageReceiveResponse(success=False, error=error_msg)

    except Exception as e:
        logger.error(f"Error receiving image {reference}: {e}", exc_info=True)
        error_msg = str(e)
        if job_id:
            _image_pull_jobs[job_id] = ImagePullProgress(
                job_id=job_id,
                status="failed",
                error=error_msg,
            )
        return ImageReceiveResponse(success=False, error=error_msg)


@app.post("/images/pull")
async def pull_image(request: ImagePullRequest) -> ImagePullResponse:
    """Initiate pulling an image from the controller.

    This endpoint starts an async pull operation where the agent
    fetches the image from the controller's stream endpoint.

    Args:
        request: Image ID and reference to pull

    Returns:
        Job ID for tracking progress
    """
    import uuid

    job_id = str(uuid.uuid4())[:8]

    # Initialize job status
    _image_pull_jobs[job_id] = ImagePullProgress(
        job_id=job_id,
        status="pending",
    )

    # Start async pull task
    asyncio.create_task(_execute_pull_from_controller(
        job_id=job_id,
        image_id=request.image_id,
        reference=request.reference,
    ))

    return ImagePullResponse(job_id=job_id, status="pending")


async def _execute_pull_from_controller(job_id: str, image_id: str, reference: str):
    """Execute image pull from controller in background.

    Fetches the image stream from the controller and loads it locally.
    """
    import tempfile
    import subprocess
    import os

    logger.info(f"Starting pull from controller: {reference}")

    try:
        _image_pull_jobs[job_id] = ImagePullProgress(
            job_id=job_id,
            status="transferring",
            progress_percent=5,
        )

        # Build stream URL - encode the image_id for the URL
        from urllib.parse import quote
        encoded_image_id = quote(image_id, safe='')
        stream_url = f"{settings.controller_url}/images/library/{encoded_image_id}/stream"

        logger.debug(f"Fetching from: {stream_url}")

        # Stream the image from controller
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
            async with client.stream("GET", stream_url) as response:
                if response.status_code != 200:
                    error_msg = f"Controller returned {response.status_code}"
                    _image_pull_jobs[job_id] = ImagePullProgress(
                        job_id=job_id,
                        status="failed",
                        error=error_msg,
                    )
                    return

                # Get content length if available
                total_bytes = int(response.headers.get("content-length", 0))

                # Save to temp file
                with tempfile.NamedTemporaryFile(delete=False, suffix=".tar") as tmp_file:
                    bytes_written = 0
                    async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                        tmp_file.write(chunk)
                        bytes_written += len(chunk)

                        # Update progress
                        if total_bytes > 0:
                            percent = min(85, int((bytes_written / total_bytes) * 85))
                        else:
                            percent = min(85, bytes_written // (1024 * 1024))  # 1% per MB
                        _image_pull_jobs[job_id] = ImagePullProgress(
                            job_id=job_id,
                            status="transferring",
                            progress_percent=percent,
                            bytes_transferred=bytes_written,
                            total_bytes=total_bytes,
                        )

                    tmp_path = tmp_file.name

        logger.debug(f"Downloaded {bytes_written} bytes")

        # Update to loading status
        _image_pull_jobs[job_id] = ImagePullProgress(
            job_id=job_id,
            status="loading",
            progress_percent=90,
            bytes_transferred=bytes_written,
            total_bytes=total_bytes,
        )

        # Load into Docker (wrapped in thread to avoid blocking)
        def _sync_docker_load():
            return subprocess.run(
                ["docker", "load", "-i", tmp_path],
                capture_output=True,
                text=True,
                timeout=600,
            )

        result = await asyncio.to_thread(_sync_docker_load)

        os.unlink(tmp_path)

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "docker load failed"
            logger.error(f"Docker load failed for {reference}: {error_msg}")
            _image_pull_jobs[job_id] = ImagePullProgress(
                job_id=job_id,
                status="failed",
                error=error_msg,
            )
            return

        logger.info(f"Successfully loaded image: {reference}")
        _image_pull_jobs[job_id] = ImagePullProgress(
            job_id=job_id,
            status="completed",
            progress_percent=100,
            bytes_transferred=bytes_written,
            total_bytes=total_bytes,
        )

    except Exception as e:
        logger.error(f"Error pulling image {reference}: {e}", exc_info=True)
        _image_pull_jobs[job_id] = ImagePullProgress(
            job_id=job_id,
            status="failed",
            error=str(e),
        )


@app.get("/images/pull/{job_id}/progress")
def get_pull_progress(job_id: str) -> ImagePullProgress:
    """Get progress of an image pull operation.

    Args:
        job_id: The job ID from the pull request

    Returns:
        Current progress of the pull operation. If the job is not found,
        returns a response with status="unknown" instead of 404, as the
        agent may have restarted and lost in-memory job state.
    """
    if job_id not in _image_pull_jobs:
        # Return informative response instead of 404
        # This helps diagnose cases where the agent restarted during a transfer
        return ImagePullProgress(
            job_id=job_id,
            status="unknown",
            progress_percent=0,
            bytes_transferred=0,
            total_bytes=0,
            error="Job not found - agent may have restarted. Check controller for current job status.",
        )
    return _image_pull_jobs[job_id]


# --- Console Endpoint ---

# Import console configuration from central vendor registry
from agent.vendors import get_console_shell, get_console_method, get_console_credentials


async def _get_console_config(container_name: str) -> tuple[str, str, str, str]:
    """Get console configuration based on container's node kind.

    Returns:
        Tuple of (method, shell, username, password)
        method: "docker_exec" or "ssh"
        shell: Shell command for docker_exec
        username/password: Credentials for SSH
    """
    def _sync_get_config() -> tuple[str, str, str, str]:
        try:
            import docker
            client = docker.from_env()
            container = client.containers.get(container_name)
            kind = container.labels.get("archetype.node_kind", "")
            method = get_console_method(kind)
            shell = get_console_shell(kind)
            username, password = get_console_credentials(kind)
            return (method, shell, username, password)
        except Exception:
            return ("docker_exec", "/bin/sh", "admin", "admin")

    return await asyncio.to_thread(_sync_get_config)


async def _get_container_ip(container_name: str) -> str | None:
    """Get the container's IP address for SSH access."""
    def _sync_get_ip() -> str | None:
        try:
            import docker
            client = docker.from_env()
            container = client.containers.get(container_name)
            networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            for net_name, net_config in networks.items():
                ip = net_config.get("IPAddress")
                if ip:
                    return ip
            return None
        except Exception:
            return None

    return await asyncio.to_thread(_sync_get_ip)


async def _get_container_boot_logs(container_name: str, tail_lines: int = 50) -> str | None:
    """Get recent boot logs from a container.

    Args:
        container_name: Name of the container
        tail_lines: Number of log lines to retrieve (default 50)

    Returns:
        Log output as string, or None if unavailable
    """
    def _sync_get_logs() -> str | None:
        try:
            import docker
            client = docker.from_env()
            container = client.containers.get(container_name)
            logs = container.logs(tail=tail_lines, timestamps=False).decode("utf-8", errors="replace")
            return logs if logs.strip() else None
        except Exception:
            return None

    return await asyncio.to_thread(_sync_get_logs)


@app.websocket("/console/{lab_id}/{node_name}")
async def console_websocket(
    websocket: WebSocket,
    lab_id: str,
    node_name: str,
    provider_type: str | None = None,
):
    """WebSocket endpoint for console access to a node.

    Args:
        lab_id: Lab identifier
        node_name: Node name within the lab
        provider_type: Optional provider type ("docker" or "libvirt").
                       If not specified, tries Docker first, then libvirt.
    """
    await websocket.accept()

    # If libvirt explicitly requested, use virsh console
    if provider_type == "libvirt":
        await _console_websocket_libvirt(websocket, lab_id, node_name)
        return

    # Try Docker first
    docker_provider = get_provider("docker")
    if docker_provider is not None:
        container_name = docker_provider.get_container_name(lab_id, node_name)

        # Check if Docker container exists
        container_exists = await _check_container_exists(container_name)
        if container_exists:
            # Get console configuration based on node kind
            method, shell_cmd, username, password = await _get_console_config(container_name)

            if method == "ssh":
                # SSH-based console for vrnetlab/VM containers
                await _console_websocket_ssh(
                    websocket, container_name, node_name, username, password
                )
            else:
                # Docker exec-based console for native containers
                await _console_websocket_docker(websocket, container_name, node_name, shell_cmd)
            return

    # Docker container not found, try libvirt if no specific provider requested
    if provider_type is None:
        libvirt_provider = get_provider("libvirt")
        if libvirt_provider is not None:
            await _console_websocket_libvirt(websocket, lab_id, node_name)
            return

    # No console available
    await websocket.send_text("\r\nError: Node not found (neither Docker nor libvirt)\r\n")
    await websocket.close(code=1011)


async def _check_container_exists(container_name: str) -> bool:
    """Check if a Docker container exists."""
    def _sync_check() -> bool:
        try:
            import docker
            client = docker.from_env()
            client.containers.get(container_name)
            return True
        except Exception:
            return False

    return await asyncio.to_thread(_sync_check)


async def _console_websocket_ssh(
    websocket: WebSocket,
    container_name: str,
    node_name: str,
    username: str,
    password: str,
):
    """Handle console via SSH to container IP (for vrnetlab containers)."""
    from agent.console.ssh_console import SSHConsole

    # Send boot logs before connecting to CLI
    boot_logs = await _get_container_boot_logs(container_name)
    if boot_logs:
        await websocket.send_text("\r\n\x1b[90m--- Boot Log ---\x1b[0m\r\n")
        for line in boot_logs.splitlines():
            await websocket.send_text(f"\x1b[90m{line}\x1b[0m\r\n")
        await websocket.send_text("\x1b[90m--- Connecting to CLI ---\x1b[0m\r\n\r\n")

    # Get container IP
    container_ip = await _get_container_ip(container_name)
    if not container_ip:
        await websocket.send_text(f"\r\nError: Could not get IP for {node_name}\r\n")
        await websocket.send_text(f"Container '{container_name}' may not be running.\r\n")
        await websocket.close(code=1011)
        return

    console = SSHConsole(container_ip, username, password)

    # Try to start SSH console session
    if not await console.start():
        await websocket.send_text(f"\r\nError: Could not SSH to {node_name}\r\n")
        await websocket.send_text(f"Device may still be booting or credentials may be incorrect.\r\n")
        await websocket.close(code=1011)
        return

    # Set initial terminal size
    await console.resize(rows=24, cols=80)

    # Input buffer for data from WebSocket
    input_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def read_websocket():
        """Read from WebSocket and queue input."""
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    await input_queue.put(None)
                    break
                elif message["type"] == "websocket.receive":
                    if "text" in message:
                        text = message["text"]
                        # Check for control messages (JSON)
                        if text.startswith("{"):
                            try:
                                ctrl = json.loads(text)
                                if ctrl.get("type") == "resize":
                                    rows = ctrl.get("rows", 24)
                                    cols = ctrl.get("cols", 80)
                                    await console.resize(rows=rows, cols=cols)
                                    continue  # Don't queue resize messages
                            except json.JSONDecodeError:
                                pass  # Not JSON, treat as terminal input
                        await input_queue.put(text.encode())
                    elif "bytes" in message:
                        await input_queue.put(message["bytes"])
        except WebSocketDisconnect:
            await input_queue.put(None)
        except Exception:
            await input_queue.put(None)

    async def read_ssh():
        """Read from SSH and send to WebSocket."""
        try:
            while console.is_running:
                data = await console.read()
                if data is None:
                    break
                if data:
                    await websocket.send_bytes(data)
        except Exception:
            pass

    async def write_ssh():
        """Read from input queue and write to SSH."""
        try:
            while console.is_running:
                try:
                    data = await asyncio.wait_for(
                        input_queue.get(), timeout=settings.console_input_timeout
                    )
                    if data is None:
                        break
                    if data:
                        await console.write(data)
                except asyncio.TimeoutError:
                    continue
        except Exception:
            pass

    # Run all tasks concurrently
    ws_task = asyncio.create_task(read_websocket())
    read_task = asyncio.create_task(read_ssh())
    write_task = asyncio.create_task(write_ssh())

    try:
        done, pending = await asyncio.wait(
            [ws_task, read_task, write_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    finally:
        await console.close()
        try:
            await websocket.close()
        except Exception:
            pass


async def _console_websocket_docker(
    websocket: WebSocket, container_name: str, node_name: str, shell_cmd: str
):
    """Handle console via docker exec (for native containers)."""
    from agent.console.docker_exec import DockerConsole

    # Send boot logs before connecting to CLI
    boot_logs = await _get_container_boot_logs(container_name)
    if boot_logs:
        await websocket.send_text("\r\n\x1b[90m--- Boot Log ---\x1b[0m\r\n")
        for line in boot_logs.splitlines():
            await websocket.send_text(f"\x1b[90m{line}\x1b[0m\r\n")
        await websocket.send_text("\x1b[90m--- Connecting to CLI ---\x1b[0m\r\n\r\n")

    console = DockerConsole(container_name)

    # Try to start console session with appropriate shell (using async version)
    if not await console.start_async(shell=shell_cmd):
        await websocket.send_text(f"\r\nError: Could not connect to {node_name}\r\n")
        await websocket.send_text(f"Container '{container_name}' may not be running.\r\n")
        await websocket.close(code=1011)
        return

    # Set initial terminal size (resize is fast, no need to wrap)
    console.resize(rows=24, cols=80)

    # Input buffer for data from WebSocket
    input_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def read_websocket():
        """Read from WebSocket and queue input."""
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    await input_queue.put(None)
                    break
                elif message["type"] == "websocket.receive":
                    if "text" in message:
                        text = message["text"]
                        # Check for control messages (JSON)
                        if text.startswith("{"):
                            try:
                                ctrl = json.loads(text)
                                if ctrl.get("type") == "resize":
                                    rows = ctrl.get("rows", 24)
                                    cols = ctrl.get("cols", 80)
                                    console.resize(rows=rows, cols=cols)
                                    continue  # Don't queue resize messages
                            except json.JSONDecodeError:
                                pass  # Not JSON, treat as terminal input
                        await input_queue.put(text.encode())
                    elif "bytes" in message:
                        await input_queue.put(message["bytes"])
        except WebSocketDisconnect:
            await input_queue.put(None)
        except Exception:
            await input_queue.put(None)

    async def read_container():
        """Read from container and send to WebSocket using event-driven I/O."""
        loop = asyncio.get_event_loop()
        data_available = asyncio.Event()

        def on_readable():
            data_available.set()

        fd = console.get_socket_fileno()
        if fd is None:
            return

        try:
            loop.add_reader(fd, on_readable)

            while console.is_running:
                try:
                    await asyncio.wait_for(
                        data_available.wait(), timeout=settings.console_read_timeout
                    )
                except asyncio.TimeoutError:
                    continue

                data_available.clear()

                data = console.read_nonblocking()
                if data is None:
                    break
                if data:
                    await websocket.send_bytes(data)

        except Exception:
            pass
        finally:
            try:
                loop.remove_reader(fd)
            except Exception:
                pass

    async def write_container():
        """Read from input queue and write to container."""
        try:
            while console.is_running:
                try:
                    data = await asyncio.wait_for(
                        input_queue.get(), timeout=settings.console_input_timeout
                    )
                    if data is None:
                        break
                    if data:
                        console.write(data)
                except asyncio.TimeoutError:
                    continue
        except Exception:
            pass

    # Run all tasks concurrently
    ws_task = asyncio.create_task(read_websocket())
    read_task = asyncio.create_task(read_container())
    write_task = asyncio.create_task(write_container())

    try:
        done, pending = await asyncio.wait(
            [ws_task, read_task, write_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    finally:
        console.close()
        try:
            await websocket.close()
        except Exception:
            pass


async def _console_websocket_libvirt(
    websocket: WebSocket,
    lab_id: str,
    node_name: str,
):
    """Handle console via virsh console (for libvirt VMs)."""
    import pty
    import os
    import select
    import termios
    import struct
    import fcntl

    libvirt_provider = get_provider("libvirt")
    if libvirt_provider is None:
        await websocket.send_text("\r\nError: Libvirt provider not available\r\n")
        await websocket.close(code=1011)
        return

    # Get the virsh console command
    console_cmd = await libvirt_provider.get_console_command(
        lab_id, node_name, Path(settings.workspace_path) / lab_id
    )

    if not console_cmd:
        await websocket.send_text(f"\r\nError: VM {node_name} not found or not running\r\n")
        await websocket.close(code=1011)
        return

    await websocket.send_text(f"\r\n\x1b[90m--- Connecting to VM console ---\x1b[0m\r\n")
    await websocket.send_text(f"\x1b[90mPress Ctrl+] to disconnect\x1b[0m\r\n\r\n")

    # Create pseudo-terminal for virsh console
    master_fd, slave_fd = pty.openpty()

    # Set non-blocking on master
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    process = None
    try:
        # Start virsh console process with a controlling TTY
        process = await asyncio.create_subprocess_exec(
            *console_cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,  # Create new session with PTY as controlling terminal
        )

        # Close slave_fd in parent process
        os.close(slave_fd)
        slave_fd = None

        # Brief delay to let virsh connect
        await asyncio.sleep(0.5)

        # Check if process exited immediately (indicates error)
        if process.returncode is not None:
            # Try to read any error output
            try:
                error_data = os.read(master_fd, 4096)
                if error_data:
                    await websocket.send_text(f"\r\n{error_data.decode('utf-8', errors='replace')}\r\n")
            except Exception:
                pass
            await websocket.send_text(f"\r\nError: virsh console exited with code {process.returncode}\r\n")
            await websocket.send_text(f"Command was: {' '.join(console_cmd)}\r\n")
            await websocket.close(code=1011)
            return

        input_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def read_websocket():
            """Read from WebSocket and queue input."""
            try:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        await input_queue.put(None)
                        break
                    elif message["type"] == "websocket.receive":
                        if "text" in message:
                            text = message["text"]
                            # Check for control messages (JSON)
                            if text.startswith("{"):
                                try:
                                    ctrl = json.loads(text)
                                    if ctrl.get("type") == "resize":
                                        rows = ctrl.get("rows", 24)
                                        cols = ctrl.get("cols", 80)
                                        # Resize PTY
                                        winsize = struct.pack("HHHH", rows, cols, 0, 0)
                                        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                                        continue
                                except json.JSONDecodeError:
                                    pass
                            await input_queue.put(text.encode())
                        elif "bytes" in message:
                            await input_queue.put(message["bytes"])
            except WebSocketDisconnect:
                await input_queue.put(None)
            except Exception:
                await input_queue.put(None)

        async def read_pty():
            """Read from PTY and send to WebSocket."""
            loop = asyncio.get_event_loop()
            data_available = asyncio.Event()

            def on_readable():
                data_available.set()

            try:
                loop.add_reader(master_fd, on_readable)

                while process.returncode is None:
                    try:
                        await asyncio.wait_for(data_available.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue

                    data_available.clear()

                    try:
                        data = os.read(master_fd, 4096)
                        if not data:
                            break
                        await websocket.send_bytes(data)
                    except (BlockingIOError, OSError):
                        continue

            except Exception:
                pass
            finally:
                try:
                    loop.remove_reader(master_fd)
                except Exception:
                    pass

        async def write_pty():
            """Read from input queue and write to PTY."""
            try:
                while process.returncode is None:
                    try:
                        data = await asyncio.wait_for(input_queue.get(), timeout=1.0)
                        if data is None:
                            break
                        if data:
                            os.write(master_fd, data)
                    except asyncio.TimeoutError:
                        continue
            except Exception:
                pass

        # Run all tasks concurrently
        ws_task = asyncio.create_task(read_websocket())
        read_task = asyncio.create_task(read_pty())
        write_task = asyncio.create_task(write_pty())

        try:
            done, pending = await asyncio.wait(
                [ws_task, read_task, write_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Send disconnect message with reason
            if process.returncode is not None:
                await websocket.send_text(
                    f"\r\n\x1b[90m[virsh console exited with code {process.returncode}]\x1b[0m\r\n"
                )
            else:
                await websocket.send_text("\r\n\x1b[90m[console disconnected]\x1b[0m\r\n")
        except Exception:
            pass

    finally:
        # Cleanup
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                process.kill()

        try:
            os.close(master_fd)
        except Exception:
            pass

        if slave_fd is not None:
            try:
                os.close(slave_fd)
            except Exception:
                pass

        try:
            await websocket.close()
        except Exception:
            pass


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
