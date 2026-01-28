from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class LabCreate(BaseModel):
    name: str
    provider: str = "containerlab"


class LabUpdate(BaseModel):
    name: str | None = None


class LabOut(BaseModel):
    id: str
    name: str
    owner_id: str | None
    workspace_path: str
    provider: str = "containerlab"
    state: str = "stopped"
    agent_id: str | None = None
    state_updated_at: datetime | None = None
    state_error: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class LabYamlIn(BaseModel):
    content: str


class LabYamlOut(BaseModel):
    content: str


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)


class UserOut(BaseModel):
    id: str
    email: EmailStr
    is_active: bool
    is_admin: bool
    created_at: datetime

    class Config:
        from_attributes = True


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class GraphEndpoint(BaseModel):
    node: str
    ifname: str | None = None
    # External connection type: "node" (default), "bridge", "macvlan", "host"
    # When type is not "node", the node field contains the bridge/interface name
    type: str = "node"
    # IP address for this interface (CIDR format, e.g., "10.0.0.1/24")
    ipv4: str | None = None
    ipv6: str | None = None


class GraphLink(BaseModel):
    endpoints: list[GraphEndpoint]
    type: str | None = None
    name: str | None = None
    pool: str | None = None
    prefix: str | None = None
    bridge: str | None = None
    mtu: int | None = None
    bandwidth: int | None = None


class GraphNode(BaseModel):
    id: str
    name: str  # Display name for UI
    device: str | None = None
    image: str | None = None
    version: str | None = None
    role: str | None = None
    mgmt: dict | None = None
    vars: dict | None = None
    host: str | None = None  # Agent ID for multi-host placement
    network_mode: str | None = None  # Container network mode (e.g., "bridge", "host", "none")
    container_name: str | None = None  # Name used by containerlab (YAML key), may differ from display name


class TopologyGraph(BaseModel):
    nodes: list[GraphNode]
    links: list[GraphLink]
    defaults: dict | None = None


class NodePlacement(BaseModel):
    """Placement of a node on a specific host."""

    node_name: str
    host_id: str  # Agent ID


class CrossHostLink(BaseModel):
    """A link that spans two different hosts."""

    link_id: str  # Unique identifier for the link
    node_a: str  # Node name on host A
    interface_a: str  # Interface name on node A
    host_a: str  # Agent ID for host A
    ip_a: str | None = None  # IP address for node A's interface (CIDR format)
    node_b: str  # Node name on host B
    interface_b: str  # Interface name on node B
    host_b: str  # Agent ID for host B
    ip_b: str | None = None  # IP address for node B's interface (CIDR format)


class TopologyAnalysis(BaseModel):
    """Analysis of a topology for multi-host deployment."""

    placements: dict[str, list[NodePlacement]]  # host_id -> nodes
    cross_host_links: list[CrossHostLink]  # Links spanning hosts
    single_host: bool  # True if all nodes on one host


class JobOut(BaseModel):
    id: str
    lab_id: str | None
    user_id: str | None
    action: str
    status: str
    agent_id: str | None = None
    log_path: str | None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    retry_count: int = 0
    created_at: datetime
    # Derived fields for UI - computed in endpoint
    timeout_at: datetime | None = None  # When job will/did timeout
    is_stuck: bool = False  # True if past expected runtime

    class Config:
        from_attributes = True


class PermissionCreate(BaseModel):
    user_email: EmailStr
    role: str = "viewer"


class PermissionOut(BaseModel):
    id: str
    lab_id: str
    user_id: str
    role: str
    created_at: datetime
    user_email: EmailStr | None = None

    class Config:
        from_attributes = True


# Layout persistence schemas
class NodeLayout(BaseModel):
    """Visual position and styling for a node."""

    x: float
    y: float
    label: str | None = None
    color: str | None = None
    metadata: dict | None = None  # Extensible


class AnnotationLayout(BaseModel):
    """Layout data for an annotation (text, rect, circle, arrow, caption)."""

    id: str
    type: str  # text, rect, circle, arrow, caption
    x: float
    y: float
    width: float | None = None
    height: float | None = None
    text: str | None = None
    color: str | None = None
    fontSize: int | None = None
    targetX: float | None = None  # For arrows
    targetY: float | None = None  # For arrows
    metadata: dict | None = None  # Extensible


class LinkLayout(BaseModel):
    """Visual styling for a link."""

    color: str | None = None
    strokeWidth: int | None = None
    style: str | None = None  # solid, dashed, dotted
    metadata: dict | None = None  # Extensible


class CanvasState(BaseModel):
    """Canvas viewport state."""

    zoom: float | None = None
    offsetX: float | None = None
    offsetY: float | None = None


class LabLayout(BaseModel):
    """Complete visual layout for a lab workspace."""

    version: int = 1  # Schema versioning for migrations
    canvas: CanvasState | None = None
    nodes: dict[str, NodeLayout] = {}  # node_id -> position
    annotations: list[AnnotationLayout] = []
    links: dict[str, LinkLayout] | None = None  # link_id -> styling
    custom: dict | None = None  # Extensible user metadata


# Node state management schemas
class NodeStateOut(BaseModel):
    """Output schema for a single node's state."""

    id: str
    lab_id: str
    node_id: str
    node_name: str
    desired_state: str  # "stopped" or "running"
    actual_state: str  # "undeployed", "pending", "running", "stopped", "error"
    error_message: str | None = None
    # Boot readiness: True when application has completed boot
    is_ready: bool = False
    # Boot timestamp for tracking how long boot is taking
    boot_started_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class NodeStateUpdate(BaseModel):
    """Input schema for updating a node's desired state."""

    state: str = Field(..., pattern="^(stopped|running)$")


class NodeStatesResponse(BaseModel):
    """Response schema for listing all node states in a lab."""

    nodes: list[NodeStateOut]


class SyncResponse(BaseModel):
    """Response schema for sync operations."""

    job_id: str
    message: str
    nodes_to_sync: list[str] = []  # List of node IDs that will be synced


# =============================================================================
# Event Schemas (Phase 2: Real-time state updates)
# =============================================================================


class NodeEventPayload(BaseModel):
    """Payload for node state change events from agents.

    Agents forward container/VM state changes to the controller
    for real-time state synchronization.
    """

    # Agent sending the event
    agent_id: str

    # Lab and node identification
    lab_id: str  # Containerlab lab prefix
    node_name: str  # Node name (clab-node-name label)
    container_id: str | None = None  # Container/VM ID

    # Event details
    event_type: str  # started, stopped, died, etc.
    timestamp: datetime
    status: str  # Current status string

    # Additional attributes
    attributes: dict | None = None  # Provider-specific details


class NodeEventResponse(BaseModel):
    """Response to node event submission."""

    success: bool
    message: str | None = None


# =============================================================================
# Callback Schemas (Phase 3: Async job completion)
# =============================================================================


class JobCallbackPayload(BaseModel):
    """Payload for job completion callbacks from agents.

    When using async job execution, agents POST results to this
    callback endpoint when operations complete.
    """

    # Job identification
    job_id: str
    agent_id: str

    # Job result
    status: str  # completed, failed
    stdout: str | None = None
    stderr: str | None = None
    error_message: str | None = None

    # Node state updates (optional)
    # Maps node_name -> actual_state for batch updates
    node_states: dict[str, str] | None = None

    # Timestamps
    started_at: datetime | None = None
    completed_at: datetime | None = None


class JobCallbackResponse(BaseModel):
    """Response to job callback submission."""

    success: bool
    message: str | None = None


# =============================================================================
# Link State Management Schemas
# =============================================================================


class LinkStateOut(BaseModel):
    """Output schema for a single link's state."""

    id: str
    lab_id: str
    link_name: str
    source_node: str
    source_interface: str
    target_node: str
    target_interface: str
    desired_state: str  # "up" or "down"
    actual_state: str  # "up", "down", "unknown", "error"
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LinkStateUpdate(BaseModel):
    """Input schema for updating a link's desired state."""

    state: str = Field(..., pattern="^(up|down)$")


class LinkStatesResponse(BaseModel):
    """Response schema for listing all link states in a lab."""

    links: list[LinkStateOut]


class LinkStateSyncResponse(BaseModel):
    """Response schema for link sync operations."""

    message: str
    links_updated: int = 0
    links_created: int = 0


# =============================================================================
# Config Snapshot Schemas
# =============================================================================


class ConfigSnapshotOut(BaseModel):
    """Output schema for a single config snapshot."""

    id: str
    lab_id: str
    node_name: str
    content: str
    content_hash: str
    snapshot_type: str  # "manual" or "auto_stop"
    created_at: datetime

    class Config:
        from_attributes = True


class ConfigSnapshotsResponse(BaseModel):
    """Response schema for listing config snapshots."""

    snapshots: list[ConfigSnapshotOut]


class ConfigSnapshotCreate(BaseModel):
    """Input schema for creating a config snapshot."""

    node_name: str | None = None  # If None, snapshot all nodes


class ConfigDiffRequest(BaseModel):
    """Input schema for generating a diff between two snapshots."""

    snapshot_id_a: str
    snapshot_id_b: str


class ConfigDiffLine(BaseModel):
    """A single line in a unified diff."""

    line_number_a: int | None = None  # Line number in version A (None for additions)
    line_number_b: int | None = None  # Line number in version B (None for deletions)
    content: str
    type: str  # "unchanged", "added", "removed", "header"


class ConfigDiffResponse(BaseModel):
    """Response schema for a config diff."""

    snapshot_a: ConfigSnapshotOut
    snapshot_b: ConfigSnapshotOut
    diff_lines: list[ConfigDiffLine]
    additions: int = 0
    deletions: int = 0
