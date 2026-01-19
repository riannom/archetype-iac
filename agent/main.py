"""Aura Agent - Host-level orchestration agent.

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
from agent.providers import ContainerlabProvider, NodeStatus as ProviderNodeStatus
from agent.schemas import (
    AgentCapabilities,
    AgentInfo,
    AgentStatus,
    CleanupOrphansRequest,
    CleanupOrphansResponse,
    ConsoleRequest,
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
    Provider,
    RegistrationRequest,
    RegistrationResponse,
)


# Generate agent ID if not configured
AGENT_ID = settings.agent_id or str(uuid.uuid4())[:8]

# Track registration state
_registered = False
_heartbeat_task: asyncio.Task | None = None

# Provider instance
_containerlab = ContainerlabProvider()


def get_workspace(lab_id: str) -> Path:
    """Get workspace directory for a lab."""
    workspace = Path(settings.workspace_path) / lab_id
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


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

    return AgentCapabilities(
        providers=providers,
        max_concurrent_jobs=4,
        features=["console", "status"],
    )


def get_agent_info() -> AgentInfo:
    """Build agent info for registration."""
    address = f"{settings.agent_host}:{settings.agent_port}"
    # If host is 0.0.0.0, controller can't reach us - use name instead
    if settings.agent_host == "0.0.0.0":
        address = f"{settings.agent_name}:{settings.agent_port}"

    return AgentInfo(
        agent_id=AGENT_ID,
        name=settings.agent_name,
        address=address,
        capabilities=get_capabilities(),
    )


async def register_with_controller() -> bool:
    """Register this agent with the controller."""
    global _registered

    request = RegistrationRequest(
        agent=get_agent_info(),
        token=settings.registration_token or None,
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.controller_url}/agents/register",
                json=request.model_dump(),
                timeout=10.0,
            )
            if response.status_code == 200:
                result = RegistrationResponse(**response.json())
                if result.success:
                    _registered = True
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
        resource_usage={},  # TODO: gather resource metrics
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.controller_url}/agents/{AGENT_ID}/heartbeat",
                json=request.model_dump(),
                timeout=5.0,
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
    global _heartbeat_task

    print(f"Agent {AGENT_ID} starting...")
    print(f"Controller URL: {settings.controller_url}")
    print(f"Capabilities: {get_capabilities()}")

    # Try initial registration
    await register_with_controller()

    # Start heartbeat background task
    _heartbeat_task = asyncio.create_task(heartbeat_loop())

    yield

    # Cleanup
    if _heartbeat_task:
        _heartbeat_task.cancel()
        try:
            await _heartbeat_task
        except asyncio.CancelledError:
            pass

    print(f"Agent {AGENT_ID} shutting down")


# Create FastAPI app
app = FastAPI(
    title="Aura Agent",
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


# --- Job Execution Endpoints (called by controller) ---

@app.post("/jobs/deploy")
async def deploy_lab(request: DeployRequest) -> JobResult:
    """Deploy a lab topology."""
    print(f"Deploy request: lab={request.lab_id}, job={request.job_id}")

    workspace = get_workspace(request.lab_id)
    result = await _containerlab.deploy(
        lab_id=request.lab_id,
        topology_yaml=request.topology_yaml,
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


@app.post("/jobs/destroy")
async def destroy_lab(request: DestroyRequest) -> JobResult:
    """Tear down a lab."""
    print(f"Destroy request: lab={request.lab_id}, job={request.job_id}")

    workspace = get_workspace(request.lab_id)
    result = await _containerlab.destroy(
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


@app.post("/jobs/node-action")
async def node_action(request: NodeActionRequest) -> JobResult:
    """Start or stop a specific node."""
    print(f"Node action: lab={request.lab_id}, node={request.node_name}, action={request.action}")

    workspace = get_workspace(request.lab_id)

    if request.action == "start":
        result = await _containerlab.start_node(
            lab_id=request.lab_id,
            node_name=request.node_name,
            workspace=workspace,
        )
    elif request.action == "stop":
        result = await _containerlab.stop_node(
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

    workspace = get_workspace(request.lab_id)
    result = await _containerlab.status(
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


# --- Reconciliation Endpoints ---

@app.get("/discover-labs")
async def discover_labs() -> DiscoverLabsResponse:
    """Discover all running labs by inspecting containers.

    Used by controller to reconcile state after restart.
    """
    print("Discovering running labs...")

    discovered = await _containerlab.discover_labs()

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

    valid_ids = set(request.valid_lab_ids)
    removed = await _containerlab.cleanup_orphan_containers(valid_ids)

    return CleanupOrphansResponse(
        removed_containers=removed,
        errors=[],
    )


# --- Console Endpoint ---

@app.websocket("/console/{lab_id}/{node_name}")
async def console_websocket(websocket: WebSocket, lab_id: str, node_name: str):
    """WebSocket endpoint for console access to a node."""
    await websocket.accept()

    # Get container name from provider
    container_name = _containerlab.get_container_name(lab_id, node_name)

    # Import console module
    from agent.console.docker_exec import DockerConsole

    console = DockerConsole(container_name)

    # Try to start console session
    if not console.start():
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
        """Read from container and send to WebSocket."""
        try:
            while console.is_running:
                data = console.read_blocking(timeout=0.05)
                if data is None:
                    break
                if data:
                    await websocket.send_bytes(data)
                await asyncio.sleep(0)
        except Exception:
            pass

    async def write_container():
        """Read from input queue and write to container."""
        try:
            while console.is_running:
                try:
                    data = await asyncio.wait_for(input_queue.get(), timeout=0.1)
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
        reload=True,
    )
