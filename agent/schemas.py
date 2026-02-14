"""Agent-Controller protocol schemas.

These Pydantic models define the data structures exchanged between
the agent and the controller via HTTP/WebSocket.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent.version import __version__, get_commit


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
    DOCKER = "docker"  # Native Docker management for containers
    LIBVIRT = "libvirt"  # Libvirt for qcow2 VMs


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
    commit: str = Field(default_factory=get_commit)
    started_at: datetime | None = None  # When the agent process started
    is_local: bool = False  # True if co-located with controller (enables rebuild)
    deployment_mode: str = "unknown"  # systemd, docker, unknown - for update strategy
    # Separate data plane IP for VXLAN tunnels (when transport config is active)
    data_plane_ip: str | None = None


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

class DeployNode(BaseModel):
    """Node definition for JSON deploy request."""
    name: str                         # Container name (internal ID)
    display_name: str | None = None   # Human-readable name for logs
    kind: str = "linux"               # Device kind (ceos, srl, linux, etc.)
    image: str | None = None          # Docker image (uses vendor default if not specified)
    # Max interface index needed for this node (e.g., eth3 => 3).
    # Sourced from UI maxPorts (vendor defaults/overrides) and raised if any
    # link references a higher interface. Used to pre-provision interfaces
    # before boot (critical for devices like cEOS).
    interface_count: int | None = None
    binds: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    ports: list[str] = Field(default_factory=list)
    startup_config: str | None = None
    exec_cmds: list[str] = Field(default_factory=list)
    # Hardware spec overrides (controller-resolved, highest priority at runtime)
    memory: int | None = Field(None, gt=0, description="RAM in MB")
    cpu: int | None = Field(None, gt=0, description="vCPU count")
    cpu_limit: int | None = Field(None, ge=1, le=100, description="CPU limit percentage")
    disk_driver: str | None = Field(None, description="Disk bus: virtio, ide, sata")
    nic_driver: str | None = Field(None, description="NIC model: virtio, e1000, rtl8139")
    machine_type: str | None = Field(None, description="QEMU machine type")
    libvirt_driver: str | None = Field(None, description="Libvirt domain driver: kvm or qemu")
    readiness_probe: str | None = Field(None, description="Readiness probe type override")
    readiness_pattern: str | None = Field(None, description="Readiness regex override")
    readiness_timeout: int | None = Field(None, gt=0, description="Readiness timeout override")
    efi_boot: bool | None = Field(None, description="Enable EFI firmware boot")
    efi_vars: str | None = Field(None, description="EFI vars mode (e.g., stateless)")
    data_volume_gb: int | None = Field(None, ge=0, description="Data volume size in GB (0 = none)")
    # Readiness overrides (controller-resolved, used for custom/imported kinds)
    readiness_probe: str | None = None
    readiness_pattern: str | None = None
    readiness_timeout: int | None = Field(None, gt=0, description="Boot readiness timeout in seconds")


class DeployLink(BaseModel):
    """Link definition for JSON deploy request."""
    source_node: str
    source_interface: str
    target_node: str
    target_interface: str


class DeployTopology(BaseModel):
    """Topology for JSON deploy request.

    This is the structured JSON format that replaces YAML for multi-host deployments.
    Each agent receives only the nodes assigned to it, with node host assignments
    determined by the controller using database `nodes.host_id`.
    """
    nodes: list[DeployNode]
    links: list[DeployLink] = Field(default_factory=list)


class DeployRequest(BaseModel):
    """Controller -> Agent: Deploy a lab topology.

    Uses structured JSON format only.
    """
    job_id: str
    lab_id: str
    topology: DeployTopology | None = None  # New JSON format (preferred)
    provider: Provider = Provider.DOCKER
    # Optional callback URL for async execution
    # If provided, agent returns 202 Accepted immediately and POSTs result to this URL
    callback_url: str | None = None


class DestroyRequest(BaseModel):
    """Controller -> Agent: Tear down a lab."""
    job_id: str
    lab_id: str
    provider: Provider = Provider.DOCKER
    # Optional callback URL for async execution
    callback_url: str | None = None


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


# --- Node Reconciliation ---

class NodeReconcileTarget(BaseModel):
    """A node to reconcile with its desired state."""
    container_name: str
    desired_state: Literal["running", "stopped"]


class NodeReconcileRequest(BaseModel):
    """Controller -> Agent: Reconcile nodes to desired states."""
    nodes: list[NodeReconcileTarget]


class NodeReconcileResult(BaseModel):
    """Result of reconciling a single node."""
    container_name: str
    action: Literal["started", "stopped", "removed", "already_running", "already_stopped", "error"]
    success: bool
    error: str | None = None


class NodeReconcileResponse(BaseModel):
    """Agent -> Controller: Reconcile results."""
    lab_id: str
    results: list[NodeReconcileResult] = Field(default_factory=list)
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


class CleanupLabOrphansRequest(BaseModel):
    """Request to clean up orphan containers for a specific lab.

    Used when nodes are migrated between agents - removes containers
    for nodes that are no longer assigned to this agent.
    """
    lab_id: str
    keep_node_names: list[str] = Field(default_factory=list)
    """Node names that should be kept on this agent. All other containers for this lab will be removed."""


class CleanupLabOrphansResponse(BaseModel):
    """Response from lab orphan cleanup endpoint."""
    removed_containers: list[str] = Field(default_factory=list)
    kept_containers: list[str] = Field(default_factory=list)
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
    vlan_tag: int | None = None


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


class CleanupAuditRequest(BaseModel):
    """Controller -> Agent: Dry-run cleanup audit request."""
    include_ovs: bool = False


class CleanupAuditResponse(BaseModel):
    """Agent -> Controller: Dry-run cleanup audit response."""
    network: dict[str, Any] = Field(default_factory=dict)
    ovs: dict[str, Any] | None = None
    errors: list[str] = Field(default_factory=list)


class OverlayStatusResponse(BaseModel):
    """Agent -> Controller: Status of all overlay networks."""
    vteps: list[dict[str, Any]] = Field(default_factory=list)  # New trunk VTEPs
    tunnels: list[TunnelInfo] = Field(default_factory=list)  # Legacy per-link tunnels
    bridges: list[dict[str, Any]] = Field(default_factory=list)
    link_tunnels: list[dict[str, Any]] = Field(default_factory=list)  # Per-link VNI tunnels


# --- New Trunk VTEP Model ---


class VtepInfo(BaseModel):
    """Information about a VXLAN Tunnel Endpoint (trunk model)."""
    interface_name: str
    vni: int
    local_ip: str
    remote_ip: str
    remote_host_id: str | None = None
    tenant_mtu: int = 0


class EnsureVtepRequest(BaseModel):
    """Controller -> Agent: Ensure VTEP exists to remote host.

    In the trunk VTEP model, there is one VTEP per remote host (not per link).
    This creates the VTEP if it doesn't exist, or returns the existing one.
    """
    local_ip: str  # This agent's IP for VXLAN endpoint
    remote_ip: str  # Remote agent's IP for VXLAN endpoint
    remote_host_id: str | None = None  # Optional remote host identifier


class EnsureVtepResponse(BaseModel):
    """Agent -> Controller: VTEP creation/lookup result."""
    success: bool
    vtep: VtepInfo | None = None
    created: bool = False  # True if newly created, False if already existed
    error: str | None = None


class AttachOverlayInterfaceRequest(BaseModel):
    """Controller -> Agent: Create per-link VXLAN tunnel and attach container.

    Per-link VNI model: each cross-host link gets its own VXLAN port in
    access mode. The agent discovers the container's local VLAN and creates
    an access-mode VXLAN port with tag=<local_vlan> and options:key=<vni>.
    """
    lab_id: str
    container_name: str
    interface_name: str  # Interface inside container (e.g., eth1)
    vni: int  # VXLAN Network Identifier (shared between both sides)
    local_ip: str  # This agent's IP for VXLAN endpoint
    remote_ip: str  # Remote agent's IP for VXLAN endpoint
    link_id: str  # Link identifier for tracking
    tenant_mtu: int = 0  # Optional MTU (0 = auto-discover)


class AttachOverlayInterfaceResponse(BaseModel):
    """Agent -> Controller: Attachment result."""
    success: bool
    local_vlan: int | None = None  # The container's local VLAN used for access mode
    vni: int | None = None  # The VNI used for the tunnel
    error: str | None = None


class DetachOverlayInterfaceRequest(BaseModel):
    """Controller -> Agent: Detach a link from the overlay.

    This performs a complete detach operation:
    1. Restores the container interface to an isolated VLAN (unique tag)
    2. Deletes the per-link VXLAN tunnel port
    """
    lab_id: str  # Lab identifier
    container_name: str  # Container name (short form, e.g., "eos_1")
    interface_name: str  # Interface inside container (e.g., eth1)
    link_id: str  # Link identifier for tunnel lookup


class DetachOverlayInterfaceResponse(BaseModel):
    """Agent -> Controller: Detach result."""
    success: bool
    interface_isolated: bool = False  # True if interface was restored to isolated VLAN
    new_vlan: int | None = None  # The new isolated VLAN assigned
    tunnel_deleted: bool = False  # True if per-link VXLAN port was deleted
    error: str | None = None


class AttachOverlayExternalRequest(BaseModel):
    """Controller -> Agent: Create VXLAN tunnel for an external interface.

    Unlike container-based overlay links, this endpoint takes a direct
    VLAN tag (from the external interface's OVS port) instead of
    discovering it from a container.
    """
    lab_id: str
    external_interface: str  # Host interface name (e.g., "eth0.200")
    vlan_tag: int  # OVS VLAN tag of the external interface
    vni: int  # VXLAN Network Identifier
    local_ip: str  # This agent's IP for VXLAN endpoint
    remote_ip: str  # Remote agent's IP for VXLAN endpoint
    link_id: str  # Link identifier for tracking


class AttachOverlayExternalResponse(BaseModel):
    """Agent -> Controller: External overlay attachment result."""
    success: bool
    vni: int | None = None
    error: str | None = None


# --- MTU Testing ---


class MtuTestRequest(BaseModel):
    """Controller -> Agent: Test MTU to a target IP."""
    target_ip: str
    mtu: int = 1450  # MTU to test
    source_ip: str | None = None  # Optional source IP for bind address (data plane)


class MtuTestResponse(BaseModel):
    """Agent -> Controller: MTU test result."""
    success: bool
    tested_mtu: int | None = None  # Verified working MTU
    link_type: str | None = None  # "direct", "routed", "unknown"
    latency_ms: float | None = None  # Round-trip time
    ttl: int | None = None  # TTL from ping response (for link type detection)
    error: str | None = None


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


class ExtractNodeConfigResponse(BaseModel):
    """Agent -> Controller: Single-node config extraction result."""
    success: bool
    node_name: str
    content: str | None = None
    error: str | None = None


class UpdateConfigRequest(BaseModel):
    """Controller -> Agent: Push a startup config for a node."""
    content: str


class UpdateConfigResponse(BaseModel):
    """Agent -> Controller: Config update result."""
    success: bool
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
    prune_stopped_containers: bool = False
    prune_unused_networks: bool = False


class DockerPruneResponse(BaseModel):
    """Agent -> Controller: Result of Docker prune operation."""
    success: bool = True
    images_removed: int = 0
    build_cache_removed: int = 0
    volumes_removed: int = 0
    containers_removed: int = 0
    networks_removed: int = 0
    space_reclaimed: int = 0
    errors: list[str] = Field(default_factory=list)


# --- Workspace Cleanup ---

class CleanupWorkspacesRequest(BaseModel):
    """Controller -> Agent: Request to remove orphaned workspace directories."""
    valid_lab_ids: list[str] = Field(default_factory=list)


# --- Hot-Connect Link Management ---

class LinkState(str, Enum):
    """State of a network link."""
    CONNECTED = "connected"  # Link is active, traffic can flow
    DISCONNECTED = "disconnected"  # Link is down, ports isolated
    PENDING = "pending"  # Link is being created/modified
    ERROR = "error"  # Link creation failed


class LinkCreate(BaseModel):
    """Controller -> Agent: Create a hot-connect link between two interfaces."""
    source_node: str  # Source container name or node identifier
    source_interface: str  # Source interface name (e.g., "eth1", "Ethernet1")
    target_node: str  # Target container name or node identifier
    target_interface: str  # Target interface name


class LinkInfo(BaseModel):
    """Information about a network link."""
    link_id: str  # Unique link identifier (e.g., "r1:eth1-r2:eth1")
    lab_id: str
    source_node: str
    source_interface: str
    target_node: str
    target_interface: str
    state: LinkState = LinkState.DISCONNECTED
    vlan_tag: int | None = None  # OVS VLAN tag for this link
    error: str | None = None


class LinkCreateResponse(BaseModel):
    """Agent -> Controller: Link creation result."""
    success: bool
    link: LinkInfo | None = None
    error: str | None = None


class LinkDeleteResponse(BaseModel):
    """Agent -> Controller: Link deletion result."""
    success: bool
    error: str | None = None


class LinkListResponse(BaseModel):
    """Agent -> Controller: List of links for a lab."""
    links: list[LinkInfo] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class FixInterfacesResponse(BaseModel):
    """Agent -> Controller: Interface fixing result."""
    success: bool
    node: str
    fixed: int = 0
    already_correct: int = 0
    errors: list[str] = Field(default_factory=list)


# --- OVS Status ---

class OVSPortInfo(BaseModel):
    """Information about an OVS port."""
    port_name: str  # OVS port name
    container_name: str
    interface_name: str
    vlan_tag: int
    lab_id: str


class OVSStatusResponse(BaseModel):
    """Agent -> Controller: Status of OVS networking."""
    bridge_name: str
    initialized: bool = False
    ports: list[OVSPortInfo] = Field(default_factory=list)
    links: list[LinkInfo] = Field(default_factory=list)
    vlan_allocations: int = 0


# --- External Network Connectivity ---

class ExternalConnectRequest(BaseModel):
    """Request to connect a container interface to an external network."""
    container_name: str | None = None  # Container name (overrides node_name)
    node_name: str | None = None  # Node name (requires lab_id)
    interface_name: str
    external_interface: str  # Host interface to connect to
    vlan_tag: int | None = None  # Optional VLAN for isolation


class ExternalConnectResponse(BaseModel):
    """Response from external connection request."""
    success: bool
    vlan_tag: int | None = None
    error: str | None = None


class ExternalDisconnectRequest(BaseModel):
    """Request to disconnect an external interface."""
    external_interface: str  # Host interface to disconnect


class ExternalDisconnectResponse(BaseModel):
    """Response from external disconnect request."""
    success: bool
    error: str | None = None


class ExternalConnectionInfo(BaseModel):
    """Information about an external network connection."""
    external_interface: str
    vlan_tag: int | None = None
    connected_ports: list[str] = Field(default_factory=list)  # container:interface


class ExternalListResponse(BaseModel):
    """Response listing external network connections."""
    connections: list[ExternalConnectionInfo] = Field(default_factory=list)


class BridgePatchRequest(BaseModel):
    """Request to create a patch to another bridge."""
    target_bridge: str
    vlan_tag: int | None = None


class BridgePatchResponse(BaseModel):
    """Response from bridge patch request."""
    success: bool
    patch_port: str | None = None
    error: str | None = None


class BridgeDeletePatchRequest(BaseModel):
    """Request to delete a patch to another bridge."""
    target_bridge: str


class BridgeDeletePatchResponse(BaseModel):
    """Response from bridge patch deletion request."""
    success: bool
    error: str | None = None


# --- Docker OVS Plugin Status ---

class PluginHealthResponse(BaseModel):
    """Response from plugin health check."""
    healthy: bool
    checks: dict[str, Any] = Field(default_factory=dict)
    uptime_seconds: float = 0
    started_at: str | None = None


class PluginBridgeInfo(BaseModel):
    """Information about a lab's OVS bridge."""
    lab_id: str
    bridge_name: str
    port_count: int = 0
    vlan_range_used: tuple[int, int] = (100, 100)
    vxlan_tunnels: int = 0
    external_interfaces: list[str] = Field(default_factory=list)
    last_activity: str | None = None


class PluginStatusResponse(BaseModel):
    """Response from plugin status endpoint."""
    healthy: bool
    labs_count: int = 0
    endpoints_count: int = 0
    networks_count: int = 0
    management_networks_count: int = 0
    bridges: list[PluginBridgeInfo] = Field(default_factory=list)
    uptime_seconds: float = 0


class PluginPortInfo(BaseModel):
    """Information about an OVS port in the plugin."""
    port_name: str
    bridge_name: str | None = None  # OVS bridge name
    container: str | None = None
    interface: str
    vlan_tag: int = 0
    rx_bytes: int = 0
    tx_bytes: int = 0


class PluginLabPortsResponse(BaseModel):
    """Response listing ports for a lab."""
    lab_id: str
    ports: list[PluginPortInfo] = Field(default_factory=list)


class PluginFlowsResponse(BaseModel):
    """Response with OVS flows for a lab."""
    bridge: str | None = None
    flow_count: int = 0
    flows: list[str] = Field(default_factory=list)
    error: str | None = None


class PluginVxlanRequest(BaseModel):
    """Request to create a VXLAN tunnel on the plugin bridge."""
    link_id: str
    local_ip: str
    remote_ip: str
    vni: int
    vlan_tag: int


class PluginVxlanResponse(BaseModel):
    """Response from VXLAN tunnel creation."""
    success: bool
    port_name: str | None = None
    error: str | None = None


class PluginExternalAttachRequest(BaseModel):
    """Request to attach external interface to lab bridge."""
    external_interface: str
    vlan_tag: int | None = None


class PluginExternalAttachResponse(BaseModel):
    """Response from external interface attachment."""
    success: bool
    vlan_tag: int = 0
    error: str | None = None


class PluginExternalInfo(BaseModel):
    """Information about an external interface attachment."""
    interface: str
    vlan_tag: int = 0


class PluginExternalListResponse(BaseModel):
    """Response listing external interfaces for a lab."""
    lab_id: str
    interfaces: list[PluginExternalInfo] = Field(default_factory=list)


class PluginMgmtNetworkInfo(BaseModel):
    """Information about a management network."""
    lab_id: str
    network_id: str
    network_name: str
    subnet: str
    gateway: str


class PluginMgmtNetworkResponse(BaseModel):
    """Response from management network operations."""
    success: bool
    network: PluginMgmtNetworkInfo | None = None
    error: str | None = None


class PluginMgmtAttachRequest(BaseModel):
    """Request to attach container to management network."""
    container_id: str


class PluginMgmtAttachResponse(BaseModel):
    """Response from management network attachment."""
    success: bool
    ip_address: str | None = None
    error: str | None = None


# --- Carrier State Management ---


class CarrierStateRequest(BaseModel):
    """Request to set interface carrier state."""
    state: str  # "on" or "off"


class CarrierStateResponse(BaseModel):
    """Response from carrier state operation."""
    success: bool
    container: str
    interface: str
    state: str
    error: str | None = None


class PortIsolateResponse(BaseModel):
    """Response from port isolation operation."""
    success: bool
    container: str
    interface: str
    vlan_tag: int | None = None
    error: str | None = None


class PortRestoreRequest(BaseModel):
    """Request to restore a port to a specific VLAN."""
    target_vlan: int


class PortRestoreResponse(BaseModel):
    """Response from port restore operation."""
    success: bool
    container: str
    interface: str
    vlan_tag: int
    error: str | None = None


class PortVlanResponse(BaseModel):
    """Response containing a port's VLAN tag."""
    container: str
    interface: str
    vlan_tag: int | None = None
    error: str | None = None


# --- Host Interface Configuration ---


class InterfaceDetail(BaseModel):
    """Detailed information about a host network interface."""
    name: str
    mtu: int
    is_physical: bool
    is_default_route: bool  # True if this is the default route interface
    mac: str | None = None
    ipv4_addresses: list[str] = Field(default_factory=list)
    state: str  # up/down/unknown


class InterfaceDetailsResponse(BaseModel):
    """Response from interface details endpoint."""
    interfaces: list[InterfaceDetail] = Field(default_factory=list)
    default_route_interface: str | None = None
    network_manager: str | None = None  # networkmanager, netplan, systemd-networkd, unknown


class SetMtuRequest(BaseModel):
    """Request to set MTU on an interface."""
    mtu: int = Field(ge=68, le=9216)
    persist: bool = True  # Whether to make persistent across reboots


class SetMtuResponse(BaseModel):
    """Response from setting interface MTU."""
    success: bool
    interface: str
    previous_mtu: int
    new_mtu: int
    persisted: bool = False
    network_manager: str | None = None
    error: str | None = None


# --- Per-Node Lifecycle ---

class CreateNodeRequest(BaseModel):
    """Controller -> Agent: Create a single node container."""
    node_name: str
    display_name: str | None = None
    kind: str = "linux"
    image: str | None = None
    interface_count: int | None = None
    binds: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    startup_config: str | None = None
    # Hardware spec overrides (API-resolved, take priority over VENDOR_CONFIGS)
    memory: int | None = Field(None, gt=0, description="RAM in MB")
    cpu: int | None = Field(None, gt=0, description="vCPU count")
    cpu_limit: int | None = Field(None, ge=1, le=100, description="CPU limit percentage")
    disk_driver: str | None = Field(None, description="Disk bus: virtio, ide, sata")
    nic_driver: str | None = Field(None, description="NIC model: virtio, e1000, rtl8139")
    machine_type: str | None = Field(None, description="QEMU machine type")
    libvirt_driver: str | None = Field(None, description="Libvirt domain driver: kvm or qemu")
    readiness_probe: str | None = Field(None, description="Readiness probe type override")
    readiness_pattern: str | None = Field(None, description="Readiness regex override")
    readiness_timeout: int | None = Field(None, gt=0, description="Boot readiness timeout in seconds")
    efi_boot: bool | None = Field(None, description="Enable EFI firmware boot")
    efi_vars: str | None = Field(None, description="EFI vars mode (e.g., stateless)")
    data_volume_gb: int | None = Field(None, ge=0, description="Data volume size in GB (0 = none)")


class CreateNodeResponse(BaseModel):
    """Agent -> Controller: Node creation result."""
    success: bool
    container_name: str | None = None
    container_id: str | None = None
    status: str = "unknown"
    error: str | None = None
    duration_ms: int | None = None


class StartNodeRequest(BaseModel):
    """Controller -> Agent: Start a node with optional veth repair."""
    repair_endpoints: bool = True
    fix_interfaces: bool = True


class StartNodeResponse(BaseModel):
    """Agent -> Controller: Node start result."""
    success: bool
    status: str = "unknown"
    endpoints_repaired: int = 0
    interfaces_fixed: int = 0
    error: str | None = None
    duration_ms: int | None = None


class StopNodeResponse(BaseModel):
    """Agent -> Controller: Node stop result."""
    success: bool
    status: str = "unknown"
    error: str | None = None
    duration_ms: int | None = None


class DestroyNodeResponse(BaseModel):
    """Agent -> Controller: Node destroy result."""
    success: bool
    container_removed: bool = False
    error: str | None = None
    duration_ms: int | None = None


# --- Endpoint Repair ---

class RepairEndpointsRequest(BaseModel):
    """Controller -> Agent: Repair missing veth pairs and OVS ports."""
    nodes: list[str] = Field(default_factory=list)
    """Node names to repair. If empty, repairs all nodes in the lab."""


class EndpointRepairResult(BaseModel):
    """Result of repairing a single endpoint."""
    interface: str
    status: str  # "ok", "repaired", "error"
    host_veth: str | None = None
    vlan_tag: int | None = None
    message: str | None = None


class RepairEndpointsResponse(BaseModel):
    """Agent -> Controller: Endpoint repair results."""
    success: bool
    nodes_repaired: int = 0
    total_endpoints_repaired: int = 0
    results: dict[str, list[EndpointRepairResult]] = Field(default_factory=dict)
    """Map of node_name -> list of repair results per endpoint."""
    error: str | None = None


# =============================================================================
# Interface Provisioning Schemas
# =============================================================================


class InterfaceProvisionRequest(BaseModel):
    """Request to provision/configure/delete a network interface on the agent host."""
    action: Literal["create_subinterface", "configure", "delete"]
    # Parent interface for subinterface creation (e.g., "ens192", "eth0")
    parent_interface: str | None = None
    # Explicit interface name (auto-generated for subinterfaces if not provided)
    name: str | None = None
    # VLAN ID for subinterface creation
    vlan_id: int | None = None
    # IP address in CIDR format (e.g., "10.100.0.1/24")
    ip_cidr: str | None = None
    # Desired MTU for the interface
    mtu: int | None = None
    # Whether to also add the interface to the OVS bridge
    attach_to_ovs: bool = False
    # VLAN tag for OVS attachment (access mode)
    ovs_vlan_tag: int | None = None


class InterfaceProvisionResponse(BaseModel):
    """Response from interface provisioning operation."""
    success: bool
    interface_name: str | None = None
    mtu: int | None = None
    ip_address: str | None = None
    error: str | None = None


class TransportConfigResponse(BaseModel):
    """Controller -> Agent: Transport configuration for bootstrap."""
    transport_mode: str = "management"  # management, subinterface, dedicated
    parent_interface: str | None = None
    vlan_id: int | None = None
    transport_ip: str | None = None  # IP/CIDR
    desired_mtu: int = 9000
    data_plane_interface: str | None = None  # For dedicated mode
