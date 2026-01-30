"""Agent-Controller protocol schemas.

These Pydantic models define the data structures exchanged between
the agent and the controller via HTTP/WebSocket.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from agent.version import __version__


class AgentStatus(str, Enum):
    """Agent health status."""
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class NodeStatus(str, Enum):
    """Container/VM node status."""
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    UNKNOWN = "unknown"


class JobStatus(str, Enum):
    """Job execution status."""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    # Accepted status for async job execution (callback mode)
    ACCEPTED = "accepted"


class Provider(str, Enum):
    """Supported infrastructure providers."""
    CONTAINERLAB = "containerlab"
    LIBVIRT = "libvirt"


# --- Agent Registration ---

class AgentCapabilities(BaseModel):
    """What the agent can do."""
    providers: list[Provider] = Field(default_factory=list)
    max_concurrent_jobs: int = 4
    features: list[str] = Field(default_factory=list)  # e.g., ["vxlan", "console"]


class AgentInfo(BaseModel):
    """Agent identification and capabilities."""
    agent_id: str
    name: str
    address: str  # host:port for controller to reach agent
    capabilities: AgentCapabilities
    version: str = __version__
    started_at: datetime | None = None  # When the agent process started


class RegistrationRequest(BaseModel):
    """Agent -> Controller: Register this agent."""
    agent: AgentInfo
    token: str | None = None  # Optional auth token


class RegistrationResponse(BaseModel):
    """Controller -> Agent: Registration result."""
    success: bool
    message: str = ""
    assigned_id: str | None = None  # Controller may assign/confirm ID


# --- Heartbeat ---

class HeartbeatRequest(BaseModel):
    """Agent -> Controller: I'm still alive."""
    agent_id: str
    status: AgentStatus = AgentStatus.ONLINE
    active_jobs: int = 0
    resource_usage: dict[str, Any] = Field(default_factory=dict)  # cpu, memory, etc.


class HeartbeatResponse(BaseModel):
    """Controller -> Agent: Acknowledged, here's any pending work."""
    acknowledged: bool
    pending_jobs: list[str] = Field(default_factory=list)  # Job IDs to fetch


# --- Job Execution ---

class DeployRequest(BaseModel):
    """Controller -> Agent: Deploy a lab topology."""
    job_id: str
    lab_id: str
    topology_yaml: str
    provider: Provider = Provider.CONTAINERLAB
    # Optional callback URL for async execution
    # If provided, agent returns 202 Accepted immediately and POSTs result to this URL
    callback_url: str | None = None


class DestroyRequest(BaseModel):
    """Controller -> Agent: Tear down a lab."""
    job_id: str
    lab_id: str
    # Optional callback URL for async execution
    callback_url: str | None = None


class NodeActionRequest(BaseModel):
    """Controller -> Agent: Start/stop a specific node."""
    job_id: str
    lab_id: str
    node_name: str
    action: str  # "start" or "stop"


class JobResult(BaseModel):
    """Agent -> Controller: Job completed."""
    job_id: str
    status: JobStatus
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    error_message: str | None = None
    completed_at: datetime = Field(default_factory=datetime.utcnow)


# --- Status Queries ---

class LabStatusRequest(BaseModel):
    """Controller -> Agent: Get status of a lab."""
    lab_id: str


class NodeInfo(BaseModel):
    """Status of a single node."""
    name: str
    status: NodeStatus
    container_id: str | None = None
    image: str | None = None
    ip_addresses: list[str] = Field(default_factory=list)
    error: str | None = None


class LabStatusResponse(BaseModel):
    """Agent -> Controller: Lab status."""
    lab_id: str
    nodes: list[NodeInfo] = Field(default_factory=list)
    error: str | None = None


# --- Console ---

class ConsoleRequest(BaseModel):
    """Request to open console to a node."""
    lab_id: str
    node_name: str
    shell: str = "/bin/sh"


class ConsoleInfo(BaseModel):
    """Info needed to connect to console WebSocket."""
    websocket_path: str
    session_id: str


# --- Reconciliation ---

class DiscoveredLab(BaseModel):
    """A lab discovered via container inspection."""
    lab_id: str
    nodes: list[NodeInfo] = Field(default_factory=list)


class DiscoverLabsResponse(BaseModel):
    """Response from lab discovery endpoint."""
    labs: list[DiscoveredLab] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class CleanupOrphansRequest(BaseModel):
    """Request to clean up orphan containers."""
    valid_lab_ids: list[str] = Field(default_factory=list)


class CleanupOrphansResponse(BaseModel):
    """Response from orphan cleanup endpoint."""
    removed_containers: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# --- Overlay Networking ---

class CreateTunnelRequest(BaseModel):
    """Controller -> Agent: Create VXLAN tunnel to another host."""
    lab_id: str
    link_id: str  # Unique identifier for this link (e.g., "node1:eth0-node2:eth0")
    local_ip: str  # This agent's IP for VXLAN endpoint
    remote_ip: str  # Remote agent's IP for VXLAN endpoint
    vni: int | None = None  # Optional VNI (auto-allocated if not specified)


class TunnelInfo(BaseModel):
    """Information about a VXLAN tunnel."""
    vni: int
    interface_name: str
    local_ip: str
    remote_ip: str
    lab_id: str
    link_id: str


class CreateTunnelResponse(BaseModel):
    """Agent -> Controller: Tunnel creation result."""
    success: bool
    tunnel: TunnelInfo | None = None
    error: str | None = None


class AttachContainerRequest(BaseModel):
    """Controller -> Agent: Attach container to overlay bridge."""
    lab_id: str
    link_id: str  # Which tunnel/bridge to attach to
    container_name: str  # Docker container name
    interface_name: str  # Interface name inside container (e.g., eth1)
    ip_address: str | None = None  # Optional IP address (CIDR format, e.g., "10.0.0.1/24")


class AttachContainerResponse(BaseModel):
    """Agent -> Controller: Attachment result."""
    success: bool
    error: str | None = None


class CleanupOverlayRequest(BaseModel):
    """Controller -> Agent: Clean up all overlay networking for a lab."""
    lab_id: str


class CleanupOverlayResponse(BaseModel):
    """Agent -> Controller: Cleanup result."""
    tunnels_deleted: int = 0
    bridges_deleted: int = 0
    errors: list[str] = Field(default_factory=list)


class OverlayStatusResponse(BaseModel):
    """Agent -> Controller: Status of all overlay networks."""
    tunnels: list[TunnelInfo] = Field(default_factory=list)
    bridges: list[dict[str, Any]] = Field(default_factory=list)


# --- Config Extraction ---

class ExtractConfigsRequest(BaseModel):
    """Controller -> Agent: Extract configs from running cEOS nodes."""
    lab_id: str


class ExtractedConfig(BaseModel):
    """A single extracted node configuration."""
    node_name: str
    content: str


class ExtractConfigsResponse(BaseModel):
    """Agent -> Controller: Config extraction result."""
    success: bool
    extracted_count: int = 0
    configs: list[ExtractedConfig] = Field(default_factory=list)
    error: str | None = None


# --- Image Synchronization ---

class DockerImageInfo(BaseModel):
    """Information about a Docker image on an agent."""
    id: str  # Docker image ID (sha256:...)
    tags: list[str] = Field(default_factory=list)  # Image tags (e.g., ["ceos:4.28.0F"])
    size_bytes: int = 0
    created: str | None = None  # ISO timestamp


class ImageInventoryResponse(BaseModel):
    """Agent -> Controller: List of Docker images on agent."""
    images: list[DockerImageInfo] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ImageExistsResponse(BaseModel):
    """Agent -> Controller: Whether an image exists."""
    exists: bool
    image: DockerImageInfo | None = None


class ImageReceiveRequest(BaseModel):
    """Controller -> Agent: Metadata for incoming image stream."""
    image_id: str  # Library image ID (e.g., "docker:ceos:4.28.0F")
    reference: str  # Docker reference (e.g., "ceos:4.28.0F")
    total_bytes: int  # Expected size for progress tracking
    job_id: str | None = None  # Sync job ID for progress reporting


class ImageReceiveResponse(BaseModel):
    """Agent -> Controller: Result of receiving an image."""
    success: bool
    loaded_images: list[str] = Field(default_factory=list)  # Tags of loaded images
    error: str | None = None


class ImagePullRequest(BaseModel):
    """Agent -> Controller: Request to pull an image from controller."""
    image_id: str  # Library image ID
    reference: str  # Docker reference


class ImagePullResponse(BaseModel):
    """Controller -> Agent: Pull job created."""
    job_id: str
    status: str = "pending"


class ImagePullProgress(BaseModel):
    """Progress of an image pull operation."""
    job_id: str
    status: str  # pending, transferring, loading, completed, failed
    progress_percent: int = 0
    bytes_transferred: int = 0
    total_bytes: int = 0
    error: str | None = None


# --- Agent Updates ---

class UpdateRequest(BaseModel):
    """Controller -> Agent: Update to a new version."""
    job_id: str
    target_version: str
    callback_url: str


class UpdateProgressCallback(BaseModel):
    """Agent -> Controller: Update progress report."""
    job_id: str
    agent_id: str
    status: str  # downloading, installing, restarting, completed, failed
    progress_percent: int = 0
    error_message: str | None = None


class UpdateResponse(BaseModel):
    """Agent -> Controller: Immediate response to update request."""
    accepted: bool
    message: str = ""
    deployment_mode: str = "unknown"  # systemd, docker, unknown


# --- Docker Pruning ---

class DockerPruneRequest(BaseModel):
    """Controller -> Agent: Request to prune Docker resources."""
    valid_lab_ids: list[str] = Field(default_factory=list)
    prune_dangling_images: bool = True
    prune_build_cache: bool = True
    prune_unused_volumes: bool = False


class DockerPruneResponse(BaseModel):
    """Agent -> Controller: Result of Docker prune operation."""
    success: bool = True
    images_removed: int = 0
    build_cache_removed: int = 0
    volumes_removed: int = 0
    space_reclaimed: int = 0
    errors: list[str] = Field(default_factory=list)
