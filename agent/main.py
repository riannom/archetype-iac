"""Archetype Agent - Host-level orchestration agent.

This agent runs on each compute host and handles:
- Container/VM lifecycle via containerlab or libvirt
- Console access to running nodes
- Network overlay management
- Health reporting to controller
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
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
    CleanupOrphansRequest,
    CleanupOrphansResponse,
    CleanupOverlayRequest,
    CleanupOverlayResponse,
    ConsoleRequest,
    CreateTunnelRequest,
    CreateTunnelResponse,
    DeployRequest,
    DestroyRequest,
    DiscoveredLab,
    DiscoverLabsResponse,
    DockerImageInfo,
    ExtractConfigsRequest,
    ExtractConfigsResponse,
    ExtractedConfig,
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
    LabStatusRequest,
    LabStatusResponse,
    NodeActionRequest,
    NodeInfo,
    NodeStatus,
    OverlayStatusResponse,
    Provider,
    RegistrationRequest,
    RegistrationResponse,
    TunnelInfo,
    UpdateRequest,
    UpdateResponse,
    DockerPruneRequest,
    DockerPruneResponse,
)
from agent.version import __version__
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
from datetime import timezone
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

# Deploy locks to prevent concurrent deploys for the same lab
# Maps lab_id -> (lock, result_future)
_deploy_locks: dict[str, asyncio.Lock] = {}
_deploy_results: dict[str, asyncio.Future] = {}

# Track when locks were acquired for stuck detection
_deploy_lock_times: dict[str, datetime] = {}

# Event listener instance (lazy initialized)
_event_listener = None


def get_overlay_manager():
    """Lazy-initialize overlay manager."""
    global _overlay_manager
    if _overlay_manager is None:
        from agent.network.overlay import OverlayManager
        _overlay_manager = OverlayManager()
    return _overlay_manager


def get_event_listener():
    """Lazy-initialize Docker event listener."""
    global _event_listener
    if _event_listener is None:
        from agent.events import DockerEventListener
        _event_listener = DockerEventListener()
    return _event_listener


async def forward_event_to_controller(event):
    """Forward a node event to the controller.

    This function is called by the event listener when a container
    state change is detected. It POSTs the event to the controller's
    /events/node endpoint for real-time state synchronization.
    """
    from agent.events.base import NodeEvent

    if not isinstance(event, NodeEvent):
        return

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
                logger.debug(f"Forwarded event: {event.event_type.value} for {event.node_name}")
            else:
                logger.warning(f"Failed to forward event: HTTP {response.status_code}")
    except Exception as e:
        logger.error(f"Error forwarding event to controller: {e}")


def get_workspace(lab_id: str) -> Path:
    """Get workspace directory for a lab."""
    workspace = Path(settings.workspace_path) / lab_id
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def get_provider_for_request(provider_name: str = "containerlab") -> Provider:
    """Get a provider instance for handling a request.

    Args:
        provider_name: Name of the provider to use (default: containerlab)

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
    if settings.enable_containerlab:
        providers.append(Provider.CONTAINERLAB)
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


def get_resource_usage() -> dict:
    """Gather system resource metrics for heartbeat."""
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
        # Only count Archetype-related containers (containerlab nodes + archetype system)
        containers_running = 0
        containers_total = 0
        container_details = []
        try:
            import docker
            client = docker.from_env()
            all_containers = client.containers.list(all=True)

            # Collect detailed container info with lab associations
            # Only include containerlab nodes and archetype system containers
            for c in all_containers:
                labels = c.labels
                # Containerlab stores lab prefix in 'containerlab' label
                lab_prefix = labels.get("containerlab", "")
                is_clab_node = bool(labels.get("clab-node-name"))
                is_archetype_system = c.name.startswith("archetype-")

                # Only include relevant containers
                if not is_clab_node and not is_archetype_system:
                    continue

                # Count only Archetype-related containers
                containers_total += 1
                if c.status == "running":
                    containers_running += 1

                container_details.append({
                    "name": c.name,
                    "status": c.status,
                    "lab_prefix": lab_prefix,  # Truncated lab ID from containerlab
                    "node_name": labels.get("clab-node-name"),
                    "node_kind": labels.get("clab-node-kind"),
                    "image": c.image.tags[0] if c.image.tags else c.image.short_id,
                    "is_system": is_archetype_system,
                })
        except Exception:
            pass

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


def get_agent_info() -> AgentInfo:
    """Build agent info for registration."""
    address = f"{settings.agent_host}:{settings.agent_port}"
    # If host is 0.0.0.0, controller can't reach us - use local_ip or name
    if settings.agent_host == "0.0.0.0":
        if settings.local_ip:
            address = f"{settings.local_ip}:{settings.agent_port}"
        else:
            address = f"{settings.agent_name}:{settings.agent_port}"

    return AgentInfo(
        agent_id=AGENT_ID,
        name=settings.agent_name,
        address=address,
        capabilities=get_capabilities(),
        started_at=AGENT_STARTED_AT,
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
        active_jobs=0,  # TODO: track active jobs
        resource_usage=get_resource_usage(),
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
    global _heartbeat_task, _event_listener_task

    logger.info(f"Agent {AGENT_ID} starting...")
    logger.info(f"Controller URL: {settings.controller_url}")
    logger.info(f"Capabilities: {get_capabilities()}")

    # Try initial registration
    await register_with_controller()

    # Start heartbeat background task
    _heartbeat_task = asyncio.create_task(heartbeat_loop())

    # Start Docker event listener if containerlab is enabled
    if settings.enable_containerlab:
        try:
            listener = get_event_listener()
            _event_listener_task = asyncio.create_task(
                listener.start(forward_event_to_controller)
            )
            logger.info("Docker event listener started")
        except Exception as e:
            logger.error(f"Failed to start Docker event listener: {e}")

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
        "registered": _registered,
        "timestamp": datetime.utcnow().isoformat(),
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
def get_lock_status():
    """Get status of all deploy locks on this agent.

    Returns information about currently held locks including:
    - lab_id: The lab holding the lock
    - acquired_at: When the lock was acquired
    - age_seconds: How long the lock has been held
    - is_stuck: Whether the lock exceeds the stuck threshold

    Used by controller to detect and clean up stuck locks.
    """
    now = datetime.now(timezone.utc)
    locks = []
    for lab_id, acquired_at in _deploy_lock_times.items():
        age_seconds = (now - acquired_at).total_seconds()
        locks.append({
            "lab_id": lab_id,
            "acquired_at": acquired_at.isoformat(),
            "age_seconds": age_seconds,
            "is_stuck": age_seconds > settings.lock_stuck_threshold,
        })
    return {"locks": locks, "timestamp": now.isoformat()}


@app.post("/locks/{lab_id}/release")
def release_lock(lab_id: str):
    """Release a stuck deploy lock for a lab.

    This clears the lock tracking state to allow new deploys.
    Note: This cannot forcibly release an asyncio.Lock, but it:
    1. Removes the lock from time tracking
    2. Clears any cached deploy result

    After calling this, a new deploy request should be able to proceed
    once the current (stuck) operation times out or completes.
    """
    if lab_id in _deploy_lock_times:
        _deploy_lock_times.pop(lab_id, None)
        _deploy_results.pop(lab_id, None)
        logger.info(f"Released stuck lock for lab {lab_id}")
        return {"status": "cleared", "lab_id": lab_id}
    return {"status": "not_found", "lab_id": lab_id}


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

    Uses per-lab locking to prevent concurrent deploys for the same lab.
    If a deploy is already in progress, subsequent requests wait for it to complete.

    If callback_url is provided, returns 202 Accepted immediately and executes
    the deploy in the background, POSTing the result to the callback URL when done.
    """
    lab_id = request.lab_id
    logger.info(f"Deploy request: lab={lab_id}, job={request.job_id}, provider={request.provider.value}")
    if request.callback_url:
        logger.debug(f"  Async mode with callback: {request.callback_url}")

    # Get or create lock for this lab
    if lab_id not in _deploy_locks:
        _deploy_locks[lab_id] = asyncio.Lock()

    lock = _deploy_locks[lab_id]

    # Async callback mode - return immediately and execute in background
    if request.callback_url:
        # Start async execution
        asyncio.create_task(
            _execute_deploy_with_callback(
                request.job_id,
                lab_id,
                request.topology_yaml,
                request.provider.value,
                request.callback_url,
                lock,
            )
        )
        return JobResult(
            job_id=request.job_id,
            status=JobStatus.ACCEPTED,
            stdout="Deploy accepted for async execution",
        )

    # Synchronous mode (existing behavior)
    # Check if deploy is already in progress
    if lock.locked():
        logger.info(f"Deploy already in progress for lab {lab_id}, waiting...")
        # Try to acquire lock with timeout
        try:
            async with asyncio.timeout(settings.lock_acquire_timeout):
                async with lock:
                    # Deploy finished while we were waiting, check for cached result
                    if lab_id in _deploy_results:
                        cached = _deploy_results.get(lab_id)
                        if cached:
                            logger.debug(f"Returning cached deploy result for lab {lab_id}")
                            # Return the same result but with this job's ID
                            return JobResult(
                                job_id=request.job_id,
                                status=cached.status,
                                stdout=cached.stdout,
                                stderr=cached.stderr,
                                error_message=cached.error_message,
                            )
                    # No cached result, continue with deploy below
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for deploy lock on lab {lab_id}")
            raise HTTPException(
                status_code=503,
                detail=f"Deploy already in progress for lab {lab_id}, try again later"
            )

    # Try to acquire lock with timeout
    try:
        async with asyncio.timeout(settings.lock_acquire_timeout):
            async with lock:
                # Track lock acquisition time for stuck detection
                _deploy_lock_times[lab_id] = datetime.now(timezone.utc)
                try:
                    provider = get_provider_for_request(request.provider.value)
                    workspace = get_workspace(lab_id)
                    logger.info(f"Deploy starting: lab={lab_id}, workspace={workspace}")

                    result = await provider.deploy(
                        lab_id=lab_id,
                        topology_yaml=request.topology_yaml,
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
                    # Clean up cache after a short delay
                    asyncio.create_task(_cleanup_deploy_cache(lab_id, delay=5.0))

                    return job_result

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
                finally:
                    # Clear lock time tracking when lock is released
                    _deploy_lock_times.pop(lab_id, None)
    except asyncio.TimeoutError:
        logger.warning(f"Timeout waiting for deploy lock on lab {lab_id}")
        raise HTTPException(
            status_code=503,
            detail=f"Deploy already in progress for lab {lab_id}, try again later"
        )


async def _execute_deploy_with_callback(
    job_id: str,
    lab_id: str,
    topology_yaml: str,
    provider_name: str,
    callback_url: str,
    lock: asyncio.Lock,
) -> None:
    """Execute deploy in background and send result via callback.

    This function handles the async deploy execution pattern:
    1. Acquire the lab lock (prevents concurrent deploys)
    2. Execute the deploy operation
    3. POST the result to the callback URL
    4. Handle callback delivery failures with retry
    """
    from agent.callbacks import CallbackPayload, deliver_callback
    from datetime import datetime, timezone

    started_at = datetime.now(timezone.utc)

    try:
        async with asyncio.timeout(settings.lock_acquire_timeout):
            async with lock:
                # Track lock acquisition time for stuck detection
                _deploy_lock_times[lab_id] = datetime.now(timezone.utc)
                try:
                    provider = get_provider_for_request(provider_name)
                    workspace = get_workspace(lab_id)
                    logger.info(f"Async deploy starting: lab={lab_id}, workspace={workspace}")

                    result = await provider.deploy(
                        lab_id=lab_id,
                        topology_yaml=topology_yaml,
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
                finally:
                    # Clear lock time tracking when lock is released
                    _deploy_lock_times.pop(lab_id, None)
    except asyncio.TimeoutError:
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
    logger.info(f"Destroy request: lab={request.lab_id}, job={request.job_id}")
    if request.callback_url:
        logger.debug(f"  Async mode with callback: {request.callback_url}")

    # Async callback mode - return immediately and execute in background
    if request.callback_url:
        asyncio.create_task(
            _execute_destroy_with_callback(
                request.job_id,
                request.lab_id,
                request.callback_url,
            )
        )
        return JobResult(
            job_id=request.job_id,
            status=JobStatus.ACCEPTED,
            stdout="Destroy accepted for async execution",
        )

    # Synchronous mode (existing behavior)
    # Use default provider for destroy (containerlab)
    # In future, could get provider from request or lab metadata
    provider = get_provider_for_request("containerlab")
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


async def _execute_destroy_with_callback(
    job_id: str,
    lab_id: str,
    callback_url: str,
) -> None:
    """Execute destroy in background and send result via callback."""
    from agent.callbacks import CallbackPayload, deliver_callback
    from datetime import datetime, timezone

    started_at = datetime.now(timezone.utc)

    try:
        provider = get_provider_for_request("containerlab")
        workspace = get_workspace(lab_id)
        logger.info(f"Async destroy starting: lab={lab_id}, workspace={workspace}")

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


@app.post("/jobs/node-action")
async def node_action(request: NodeActionRequest) -> JobResult:
    """Start or stop a specific node."""
    logger.info(f"Node action: lab={request.lab_id}, node={request.node_name}, action={request.action}")

    # Use default provider for node actions
    provider = get_provider_for_request("containerlab")
    workspace = get_workspace(request.lab_id)

    if request.action == "start":
        result = await provider.start_node(
            lab_id=request.lab_id,
            node_name=request.node_name,
            workspace=workspace,
        )
    elif request.action == "stop":
        result = await provider.stop_node(
            lab_id=request.lab_id,
            node_name=request.node_name,
            workspace=workspace,
        )
    else:
        return JobResult(
            job_id=request.job_id,
            status=JobStatus.FAILED,
            error_message=f"Unknown action: {request.action}",
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


# --- Status Endpoints ---

@app.post("/labs/status")
async def lab_status(request: LabStatusRequest) -> LabStatusResponse:
    """Get status of all nodes in a lab."""
    logger.debug(f"Status request: lab={request.lab_id}")

    # Use default provider for status queries
    provider = get_provider_for_request("containerlab")
    workspace = get_workspace(request.lab_id)
    result = await provider.status(
        lab_id=request.lab_id,
        workspace=workspace,
    )

    # Convert provider NodeInfo to schema NodeInfo
    nodes = [
        NodeInfo(
            name=node.name,
            status=provider_status_to_schema(node.status),
            container_id=node.container_id,
            image=node.image,
            ip_addresses=node.ip_addresses,
        )
        for node in result.nodes
    ]

    return LabStatusResponse(
        lab_id=request.lab_id,
        nodes=nodes,
        error=result.error,
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
        provider = get_provider_for_request("containerlab")
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


# --- Container Control Endpoints ---

@app.post("/containers/{container_name}/start")
async def start_container(container_name: str) -> dict:
    """Start a stopped container.

    Used by the sync system to start individual nodes without redeploying.
    """
    logger.info(f"Starting container: {container_name}")

    try:
        import docker
        client = docker.from_env()
        container = client.containers.get(container_name)

        if container.status == "running":
            return {"success": True, "message": "Container already running"}

        container.start()
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
    """
    logger.info(f"Stopping container: {container_name}")

    try:
        import docker
        client = docker.from_env()
        container = client.containers.get(container_name)

        if container.status != "running":
            return {"success": True, "message": "Container already stopped"}

        container.stop(timeout=10)
        return {"success": True, "message": "Container stopped"}

    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Container '{container_name}' not found")
    except docker.errors.APIError as e:
        logger.error(f"Docker API error stopping {container_name}: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Error stopping container {container_name}: {e}")
        return {"success": False, "error": str(e)}


# --- Reconciliation Endpoints ---

@app.get("/discover-labs")
async def discover_labs() -> DiscoverLabsResponse:
    """Discover all running labs by inspecting containers.

    Used by controller to reconcile state after restart.
    """
    logger.info("Discovering running labs...")

    # Use default provider for discovery
    provider = get_provider_for_request("containerlab")
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
    provider = get_provider_for_request("containerlab")
    valid_ids = set(request.valid_lab_ids)
    removed = await provider.cleanup_orphan_containers(valid_ids)

    return CleanupOrphansResponse(
        removed_containers=removed,
        errors=[],
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
                lab_prefix = labels.get("containerlab", "")

                # Protect images from valid labs
                is_valid_lab = any(
                    lab_id.startswith(lab_prefix) or lab_prefix.startswith(lab_id[:20])
                    for lab_id in request.valid_lab_ids
                ) if lab_prefix else False

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

    logger.info(f"Attaching container: {request.container_name} to bridge for {request.link_id}")

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
            container_name=request.container_name,
            interface_name=request.interface_name,
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
            tunnels=tunnels,
            bridges=status["bridges"],
        )

    except Exception as e:
        logger.error(f"Overlay status failed: {e}")
        return OverlayStatusResponse()


# --- Node Readiness Endpoint ---

@app.get("/labs/{lab_id}/nodes/{node_name}/ready")
async def check_node_ready(lab_id: str, node_name: str) -> dict:
    """Check if a node has completed its boot sequence.

    Returns readiness status based on vendor-specific probes that check
    container logs or CLI output for boot completion patterns.
    """
    from agent.readiness import get_probe_for_vendor, get_readiness_timeout

    # Get container name from provider
    provider = get_provider("containerlab")
    if provider is None:
        return {
            "is_ready": False,
            "message": "No provider available",
            "progress_percent": None,
        }

    container_name = provider.get_container_name(lab_id, node_name)

    # Get the node kind to determine appropriate probe
    try:
        import docker
        client = docker.from_env()
        container = client.containers.get(container_name)
        kind = container.labels.get("clab-node-kind", "")
    except Exception as e:
        return {
            "is_ready": False,
            "message": f"Container not found: {str(e)}",
            "progress_percent": 0,
        }

    # Get and run the appropriate probe
    probe = get_probe_for_vendor(kind)
    result = await probe.check(container_name)

    return {
        "is_ready": result.is_ready,
        "message": result.message,
        "progress_percent": result.progress_percent,
        "timeout": get_readiness_timeout(kind),
    }


# --- Network Interface Discovery Endpoints ---

@app.get("/interfaces")
async def list_interfaces() -> dict:
    """List available network interfaces on this host.

    Returns physical interfaces that can be used for VLAN sub-interfaces
    or external network connections.
    """
    import subprocess

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


@app.get("/bridges")
async def list_bridges() -> dict:
    """List available Linux bridges on this host.

    Returns bridges that can be used for external network connections.
    """
    import subprocess

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
                # Skip containerlab and docker bridges
                if bridge_name.startswith(("clab", "docker", "br-")):
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
                    # Skip containerlab and docker bridges
                    if name.startswith(("clab", "docker", "br-")):
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

        # Load into Docker
        result = subprocess.run(
            ["docker", "load", "-i", tmp_path],
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout for large images
        )

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

        # Load into Docker
        result = subprocess.run(
            ["docker", "load", "-i", tmp_path],
            capture_output=True,
            text=True,
            timeout=600,
        )

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


def _get_console_config(container_name: str) -> tuple[str, str, str, str]:
    """Get console configuration based on container's node kind.

    Returns:
        Tuple of (method, shell, username, password)
        method: "docker_exec" or "ssh"
        shell: Shell command for docker_exec
        username/password: Credentials for SSH
    """
    try:
        import docker
        client = docker.from_env()
        container = client.containers.get(container_name)
        kind = container.labels.get("clab-node-kind", "")
        method = get_console_method(kind)
        shell = get_console_shell(kind)
        username, password = get_console_credentials(kind)
        return (method, shell, username, password)
    except Exception:
        return ("docker_exec", "/bin/sh", "admin", "admin")


def _get_container_ip(container_name: str) -> str | None:
    """Get the container's IP address for SSH access."""
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


@app.websocket("/console/{lab_id}/{node_name}")
async def console_websocket(websocket: WebSocket, lab_id: str, node_name: str):
    """WebSocket endpoint for console access to a node."""
    await websocket.accept()

    # Get container name from provider (use default containerlab for now)
    provider = get_provider("containerlab")
    if provider is None:
        await websocket.send_text("\r\nError: No provider available\r\n")
        await websocket.close(code=1011)
        return

    container_name = provider.get_container_name(lab_id, node_name)

    # Get console configuration based on node kind
    method, shell_cmd, username, password = _get_console_config(container_name)

    if method == "ssh":
        # SSH-based console for vrnetlab/VM containers
        await _console_websocket_ssh(
            websocket, container_name, node_name, username, password
        )
    else:
        # Docker exec-based console for native containers
        await _console_websocket_docker(websocket, container_name, node_name, shell_cmd)


async def _console_websocket_ssh(
    websocket: WebSocket,
    container_name: str,
    node_name: str,
    username: str,
    password: str,
):
    """Handle console via SSH to container IP (for vrnetlab containers)."""
    from agent.console.ssh_console import SSHConsole

    # Get container IP
    container_ip = _get_container_ip(container_name)
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

    console = DockerConsole(container_name)

    # Try to start console session with appropriate shell
    if not console.start(shell=shell_cmd):
        await websocket.send_text(f"\r\nError: Could not connect to {node_name}\r\n")
        await websocket.send_text(f"Container '{container_name}' may not be running.\r\n")
        await websocket.close(code=1011)
        return

    # Set initial terminal size
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
