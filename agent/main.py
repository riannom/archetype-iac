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
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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
    HeartbeatRequest,
    HeartbeatResponse,
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
)


# Generate agent ID if not configured
AGENT_ID = settings.agent_id or str(uuid.uuid4())[:8]

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
                print(f"Forwarded event: {event.event_type.value} for {event.node_name}")
            else:
                print(f"Failed to forward event: HTTP {response.status_code}")
    except Exception as e:
        print(f"Error forwarding event to controller: {e}")


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
            "disk_percent": disk_percent,
            "disk_used_gb": disk_used_gb,
            "disk_total_gb": disk_total_gb,
            "containers_running": containers_running,
            "containers_total": containers_total,
            "container_details": container_details,
        }
    except Exception as e:
        print(f"Failed to gather resource usage: {e}")
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
                json=request.model_dump(),
                timeout=settings.registration_timeout,
            )
            if response.status_code == 200:
                result = RegistrationResponse(**response.json())
                if result.success:
                    _registered = True
                    # Use the assigned ID from controller (may differ if we're
                    # re-registering an existing agent with a new generated ID)
                    if result.assigned_id and result.assigned_id != AGENT_ID:
                        print(f"Controller assigned existing ID: {result.assigned_id}")
                        AGENT_ID = result.assigned_id
                    print(f"Registered with controller as {AGENT_ID}")
                    return True
                else:
                    print(f"Registration rejected: {result.message}")
                    return False
            else:
                print(f"Registration failed: HTTP {response.status_code}")
                return False
    except httpx.ConnectError:
        print(f"Cannot connect to controller at {settings.controller_url}")
        return False
    except Exception as e:
        print(f"Registration error: {e}")
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
        print(f"Heartbeat failed: {e}")
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
            print("Lost connection to controller, will retry registration")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - register on startup, cleanup on shutdown."""
    global _heartbeat_task, _event_listener_task

    print(f"Agent {AGENT_ID} starting...")
    print(f"Controller URL: {settings.controller_url}")
    print(f"Capabilities: {get_capabilities()}")

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
            print("Docker event listener started")
        except Exception as e:
            print(f"Failed to start Docker event listener: {e}")

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

    print(f"Agent {AGENT_ID} shutting down")


# Create FastAPI app
app = FastAPI(
    title="Archetype Agent",
    version="0.1.0",
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
    print(f"Deploy request: lab={lab_id}, job={request.job_id}, provider={request.provider.value}")
    if request.callback_url:
        print(f"  Async mode with callback: {request.callback_url}")

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
        print(f"Deploy already in progress for lab {lab_id}, waiting...")
        # Wait for the lock and return the cached result
        async with lock:
            # Deploy finished while we were waiting, check for cached result
            if lab_id in _deploy_results:
                cached = _deploy_results.get(lab_id)
                if cached:
                    print(f"Returning cached deploy result for lab {lab_id}")
                    # Return the same result but with this job's ID
                    return JobResult(
                        job_id=request.job_id,
                        status=cached.status,
                        stdout=cached.stdout,
                        stderr=cached.stderr,
                        error_message=cached.error_message,
                    )
        # No cached result, continue with deploy

    async with lock:
        try:
            provider = get_provider_for_request(request.provider.value)
            workspace = get_workspace(lab_id)
            print(f"Deploy starting: workspace={workspace}")

            result = await provider.deploy(
                lab_id=lab_id,
                topology_yaml=request.topology_yaml,
                workspace=workspace,
            )

            print(f"Deploy finished: success={result.success}")

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
            print(f"Deploy error: {e}")
            import traceback
            traceback.print_exc()
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

    async with lock:
        try:
            provider = get_provider_for_request(provider_name)
            workspace = get_workspace(lab_id)
            print(f"Async deploy starting: workspace={workspace}")

            result = await provider.deploy(
                lab_id=lab_id,
                topology_yaml=topology_yaml,
                workspace=workspace,
            )

            print(f"Async deploy finished: success={result.success}")

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
            print(f"Async deploy error: {e}")
            import traceback
            traceback.print_exc()

            payload = CallbackPayload(
                job_id=job_id,
                agent_id=AGENT_ID,
                status="failed",
                error_message=str(e),
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
    print(f"Destroy request: lab={request.lab_id}, job={request.job_id}")
    if request.callback_url:
        print(f"  Async mode with callback: {request.callback_url}")

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
        print(f"Async destroy starting: workspace={workspace}")

        result = await provider.destroy(
            lab_id=lab_id,
            workspace=workspace,
        )

        print(f"Async destroy finished: success={result.success}")

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
        print(f"Async destroy error: {e}")
        import traceback
        traceback.print_exc()

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
    print(f"Node action: lab={request.lab_id}, node={request.node_name}, action={request.action}")

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
    print(f"Status request: lab={request.lab_id}")

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


# --- Container Control Endpoints ---

@app.post("/containers/{container_name}/start")
async def start_container(container_name: str) -> dict:
    """Start a stopped container.

    Used by the sync system to start individual nodes without redeploying.
    """
    print(f"Starting container: {container_name}")

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
        print(f"Docker API error starting {container_name}: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"Error starting container {container_name}: {e}")
        return {"success": False, "error": str(e)}


@app.post("/containers/{container_name}/stop")
async def stop_container(container_name: str) -> dict:
    """Stop a running container.

    Used by the sync system to stop individual nodes without destroying the lab.
    """
    print(f"Stopping container: {container_name}")

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
        print(f"Docker API error stopping {container_name}: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"Error stopping container {container_name}: {e}")
        return {"success": False, "error": str(e)}


# --- Reconciliation Endpoints ---

@app.get("/discover-labs")
async def discover_labs() -> DiscoverLabsResponse:
    """Discover all running labs by inspecting containers.

    Used by controller to reconcile state after restart.
    """
    print("Discovering running labs...")

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
    print(f"Cleaning up orphan containers, keeping {len(request.valid_lab_ids)} valid labs")

    # Use default provider for cleanup
    provider = get_provider_for_request("containerlab")
    valid_ids = set(request.valid_lab_ids)
    removed = await provider.cleanup_orphan_containers(valid_ids)

    return CleanupOrphansResponse(
        removed_containers=removed,
        errors=[],
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

    print(f"Creating tunnel: lab={request.lab_id}, link={request.link_id}, remote={request.remote_ip}")

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
        print(f"Tunnel creation failed: {e}")
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

    print(f"Attaching container: {request.container_name} to bridge for {request.link_id}")

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
        print(f"Container attachment failed: {e}")
        return AttachContainerResponse(
            success=False,
            error=str(e),
        )


@app.post("/overlay/cleanup")
async def cleanup_overlay(request: CleanupOverlayRequest) -> CleanupOverlayResponse:
    """Clean up all overlay networking for a lab."""
    if not settings.enable_vxlan:
        return CleanupOverlayResponse()

    print(f"Cleaning up overlay for lab: {request.lab_id}")

    try:
        overlay = get_overlay_manager()
        result = await overlay.cleanup_lab(request.lab_id)

        return CleanupOverlayResponse(
            tunnels_deleted=result["tunnels_deleted"],
            bridges_deleted=result["bridges_deleted"],
            errors=result["errors"],
        )

    except Exception as e:
        print(f"Overlay cleanup failed: {e}")
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
        print(f"Overlay status failed: {e}")
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


# --- Console Endpoint ---

# Import console shell configuration from central vendor registry
from agent.vendors import get_console_shell


def _get_shell_for_container(container_name: str) -> str:
    """Get the appropriate shell command based on container's node kind.

    Uses the centralized vendor registry (agent/vendors.py) to determine
    the correct shell command for each containerlab node kind.
    """
    try:
        import docker
        client = docker.from_env()
        container = client.containers.get(container_name)
        kind = container.labels.get("clab-node-kind", "")
        return get_console_shell(kind)
    except Exception:
        return "/bin/sh"


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

    # Determine shell based on node kind
    shell_cmd = _get_shell_for_container(container_name)

    # Import console module
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
        """Read from container and send to WebSocket using event-driven I/O.

        Uses asyncio event loop's add_reader for immediate notification when
        data is available, eliminating polling latency.
        """
        loop = asyncio.get_event_loop()
        data_available = asyncio.Event()

        def on_readable():
            """Callback when socket has data available."""
            data_available.set()

        fd = console.get_socket_fileno()
        if fd is None:
            return

        try:
            # Register for read events on the socket
            loop.add_reader(fd, on_readable)

            while console.is_running:
                # Wait for data to become available (event-driven, no polling)
                try:
                    await asyncio.wait_for(
                        data_available.wait(),
                        timeout=settings.console_read_timeout
                    )
                except asyncio.TimeoutError:
                    # Fallback timeout - check if still running
                    continue

                data_available.clear()

                # Read all available data without blocking
                data = console.read_nonblocking()
                if data is None:
                    break  # Connection closed
                if data:
                    await websocket.send_bytes(data)

        except Exception:
            pass
        finally:
            # Always remove the reader when done
            try:
                loop.remove_reader(fd)
            except Exception:
                pass

    async def write_container():
        """Read from input queue and write to container."""
        try:
            while console.is_running:
                try:
                    data = await asyncio.wait_for(input_queue.get(), timeout=settings.console_input_timeout)
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
        # Wait for any task to complete (usually disconnect)
        done, pending = await asyncio.wait(
            [ws_task, read_task, write_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel remaining tasks
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
