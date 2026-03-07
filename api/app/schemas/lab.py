from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict, model_validator

from app.schemas.agents import InterfaceMappingOut


class LabCreate(BaseModel):
    name: str
    provider: str = "docker"


class LabUpdate(BaseModel):
    name: str | None = None


class LabOut(BaseModel):
    id: str
    name: str
    owner_id: str | None
    workspace_path: str
    provider: str = "docker"
    state: str = "stopped"
    agent_id: str | None = None
    state_updated_at: datetime | None = None
    state_error: str | None = None
    created_at: datetime
    user_role: str | None = None  # Effective lab role for the requesting user
    node_count: int = 0  # Total nodes in topology (from DB)
    running_count: int = 0  # Nodes with actual_state='running' (from DB)
    container_count: int = 0  # Docker container nodes
    vm_count: int = 0  # Libvirt/QEMU VM nodes

    model_config = ConfigDict(from_attributes=True)


class LabYamlIn(BaseModel):
    content: str


class LabYamlOut(BaseModel):
    content: str


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
    node_definition_id: str | None = None
    # Node type: "device" for lab devices, "external" for external network connections
    node_type: str = "device"
    device: str | None = None
    image: str | None = None
    version: str | None = None
    role: str | None = None
    mgmt: dict | None = None
    vars: dict | None = None
    host: str | None = None  # Agent ID for multi-host placement
    network_mode: str | None = None  # Container network mode (e.g., "bridge", "host", "none")
    container_name: str | None = None  # Container name (YAML key), may differ from display name
    # Hardware spec overrides (per-node, takes priority over device defaults)
    memory: int | None = None      # RAM in MB
    cpu: int | None = None         # vCPU count
    cpu_limit: int | None = None   # CPU limit percentage (1-100)
    disk_driver: str | None = None  # Disk bus: virtio, ide, sata
    nic_driver: str | None = None   # NIC model: virtio, e1000, rtl8139
    machine_type: str | None = None  # QEMU machine type
    libvirt_driver: str | None = None  # Libvirt domain driver (kvm or qemu)
    efi_boot: bool | None = None  # Enable EFI firmware boot
    efi_vars: str | None = None  # EFI vars mode (e.g., stateless)
    # External network fields (when node_type="external")
    managed_interface_id: str | None = None  # FK to AgentManagedInterface
    # Derived fields (populated server-side for frontend display)
    managed_interface_name: str | None = None
    managed_interface_host_id: str | None = None
    managed_interface_host_name: str | None = None
    # Legacy external network fields (kept for backward compat)
    connection_type: str | None = None  # "vlan" or "bridge"
    parent_interface: str | None = None  # e.g., "ens192", "eth0"
    vlan_id: int | None = None  # VLAN ID (1-4094)
    bridge_name: str | None = None  # e.g., "br-prod"


class TopologyGraph(BaseModel):
    nodes: list[GraphNode]
    links: list[GraphLink]
    defaults: dict | None = None
    tests: list[dict] | None = None


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
    error_summary: str | None = None  # One-liner error message for failed jobs
    # Image sync events that occurred before job started (for task log display)
    image_sync_events: list[str] | None = None

    model_config = ConfigDict(from_attributes=True)


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
    actual_state: str  # "undeployed", "pending", "starting", "running", "stopped", "stopping", "error"
    error_message: str | None = None
    # Boot readiness: True when application has completed boot
    is_ready: bool = False
    # Boot timestamp for tracking how long boot is taking
    boot_started_at: datetime | None = None
    # Timestamp when node entered starting/deploying state (for elapsed timer)
    starting_started_at: datetime | None = None
    # Image sync status: null (not syncing), "checking", "syncing", "synced", "failed"
    image_sync_status: str | None = None
    # Image sync progress/error message
    image_sync_message: str | None = None
    # Management IP address(es) for IaC integration
    management_ip: str | None = None
    all_ips: list[str] = []
    # Host/agent info for multi-host visibility
    host_id: str | None = None
    host_name: str | None = None
    # Whether enforcement will automatically retry after an error
    will_retry: bool = False
    # Number of enforcement attempts so far
    enforcement_attempts: int = 0
    # Server-computed display state: running, starting, stopping, stopped, error
    display_state: str = "stopped"
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def _compute_display_state(self) -> "NodeStateOut":
        from app.services.state_machine import NodeStateMachine
        self.display_state = NodeStateMachine.compute_display_state(
            self.actual_state, self.desired_state
        )
        return self


class NodeStateUpdate(BaseModel):
    """Input schema for updating a node's desired state."""

    state: str = Field(..., pattern="^(stopped|running)$")


class NodeStatesResponse(BaseModel):
    """Response schema for listing all node states in a lab."""

    nodes: list[NodeStateOut]
    affected: int | None = None
    skipped_transitional: int | None = None
    already_in_state: int | None = None


class ReconcileResponse(BaseModel):
    """Response schema for reconcile operations."""

    job_id: str
    message: str
    nodes_to_reconcile: list[str] = []  # List of node IDs that will be reconciled


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
    lab_id: str  # Lab identifier
    node_name: str  # Node name (from archetype.node_name label)
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


class CarrierStateChangeRequest(BaseModel):
    """Payload for carrier state change callbacks from agents."""

    lab_id: str
    node: str
    interface: str
    carrier_state: Literal["on", "off"]


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
    source_vlan_tag: int | None = None
    target_vlan_tag: int | None = None
    source_oper_state: str = "down"
    target_oper_state: str = "down"
    source_oper_reason: str | None = None
    target_oper_reason: str | None = None
    source_last_change_at: datetime | None = None
    target_last_change_at: datetime | None = None
    oper_epoch: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LinkStateUpdate(BaseModel):
    """Input schema for updating a link's desired state."""

    state: str = Field(..., pattern="^(up|down)$")


class LinkStatesResponse(BaseModel):
    """Response schema for listing all link states in a lab."""

    links: list[LinkStateOut]


class LinkStateRefreshResponse(BaseModel):
    """Response schema for link state refresh operations."""

    message: str
    links_updated: int = 0
    links_created: int = 0


# =============================================================================
# Node Readiness Schemas (IaC Workflow Support)
# =============================================================================


class NodeReadinessOut(BaseModel):
    """Readiness status for a single node."""

    node_id: str
    node_name: str
    is_ready: bool
    actual_state: str  # "running", "stopped", etc.
    progress_percent: int | None = None
    message: str | None = None
    boot_started_at: datetime | None = None
    management_ip: str | None = None


class LabReadinessResponse(BaseModel):
    """Response schema for lab-wide readiness check."""

    lab_id: str
    all_ready: bool
    ready_count: int
    total_count: int
    running_count: int
    nodes: list[NodeReadinessOut]


class NodeRuntimeIdentityOut(BaseModel):
    """Agent-reported runtime identity for a node."""

    name: str
    provider: str | None = None
    actual_state: str | None = None
    node_definition_id: str | None = None
    runtime_id: str | None = None


class LivePortStateOut(BaseModel):
    """Live port-state view from the agent."""

    interface_name: str
    ovs_port_name: str | None = None
    vlan_tag: int | None = None


class NodeLinkDiagnosticOut(BaseModel):
    """Link-state detail scoped to a specific node."""

    link_name: str
    local_interface: str
    peer_node: str
    peer_interface: str
    desired_state: str
    actual_state: str
    error_message: str | None = None


class NodeInterfaceDiagnosticResponse(BaseModel):
    """Controller + agent diagnostic bundle for one node's interfaces/runtime."""

    lab_id: str
    node_id: str
    node_name: str
    host_id: str | None = None
    host_name: str | None = None
    placement_status: str | None = None
    controller_actual_state: str | None = None
    controller_is_ready: bool | None = None
    controller_runtime_id: str | None = None
    agent_status: NodeRuntimeIdentityOut | None = None
    live_ports: list[LivePortStateOut] = []
    interface_mappings: list[InterfaceMappingOut] = []
    links: list[NodeLinkDiagnosticOut] = []
    agent_error: str | None = None


# =============================================================================
# Inventory Export Schemas (IaC Workflow Support)
# =============================================================================


class NodeInventoryEntry(BaseModel):
    """A single node entry for inventory export."""

    node_name: str
    management_ip: str | None = None
    all_ips: list[str] = []
    device_type: str | None = None  # e.g., "ceos", "vr-veos"
    kind: str | None = None  # Device kind for YAML export
    host_id: str | None = None  # For multi-host deployments
    host_name: str | None = None


class LabInventoryResponse(BaseModel):
    """Response schema for inventory export."""

    lab_id: str
    lab_name: str
    format: str  # "json", "ansible", "terraform"
    nodes: list[NodeInventoryEntry]
    content: str | None = None  # Formatted content for non-JSON formats


# =============================================================================
# Lab Operation Response Schemas
# =============================================================================


class CheckResourcesRequest(BaseModel):
    """Request body for resource capacity check."""
    node_ids: list[str] | None = None  # null = check all nodes


class PerHostCapacity(BaseModel):
    agent_name: str = ""
    fits: bool = True
    has_warnings: bool = False
    projected_memory_pct: float = 0
    projected_cpu_pct: float = 0
    projected_disk_pct: float = 0
    node_count: int = 0
    required_memory_mb: int = 0
    required_cpu_cores: int = 0
    available_memory_mb: float = 0
    available_cpu_cores: float = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CheckResourcesResponse(BaseModel):
    sufficient: bool = True
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    per_host: dict[str, PerHostCapacity] = Field(default_factory=dict)


class HotConnectRequest(BaseModel):
    """Request to hot-connect two interfaces."""
    source_node: str
    source_interface: str
    target_node: str
    target_interface: str


class HotConnectResponse(BaseModel):
    """Response from hot-connect request."""
    success: bool
    link_id: str | None = None
    vlan_tag: int | None = None
    error: str | None = None


class ExternalConnectRequest(BaseModel):
    """Request to connect a node to an external network."""
    node_name: str
    interface_name: str
    external_interface: str
    vlan_tag: int | None = None


class ExternalConnectResponse(BaseModel):
    """Response from external connect request."""
    success: bool
    vlan_tag: int | None = None
    error: str | None = None


class CleanupOrphansResponse(BaseModel):
    """Response from orphan cleanup."""
    removed_by_agent: dict[str, list[str]] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class InterfaceMappingSyncResponse(BaseModel):
    """Response from syncing interface mappings."""
    created: int
    updated: int
    errors: int
    agents_queried: int


class LinkReconciliationResponse(BaseModel):
    """Response from link reconciliation."""
    checked: int
    valid: int
    repaired: int
    errors: int
    skipped: int


class InfraNotification(BaseModel):
    """A single infrastructure notification for the Infra view."""
    id: str
    severity: Literal["error", "warning", "info"]
    category: str  # e.g. "tunnel_cleanup", "link_error", "node_error"
    title: str
    detail: str | None = None
    entity_type: str | None = None  # "tunnel", "link", "node"
    entity_name: str | None = None
    timestamp: datetime | None = None


class InfraNotificationsResponse(BaseModel):
    """Response from infra notifications endpoint."""
    notifications: list[InfraNotification] = Field(default_factory=list)


# ── Lab verification framework ──


class LinkEndpointDetail(BaseModel):
    """Detail for one endpoint of a link path."""
    node_name: str
    interface: str
    vendor_interface: str | None = None
    ovs_port: str | None = None
    ovs_bridge: str | None = None
    vlan_tag: int | None = None
    host_id: str | None = None
    host_name: str | None = None
    oper_state: str | None = None
    oper_reason: str | None = None
    carrier_state: str | None = None
    vxlan_attached: bool | None = None


class VxlanTunnelDetail(BaseModel):
    """VXLAN tunnel detail for a cross-host link."""
    vni: int
    vlan_tag: int
    agent_a_ip: str
    agent_b_ip: str
    port_name: str | None = None
    status: str
    error_message: str | None = None


class LinkPathDetail(BaseModel):
    """Full logical path detail for a link, combining link state + tunnel + interface mappings."""
    link_name: str
    actual_state: str
    desired_state: str
    error_message: str | None = None
    is_cross_host: bool = False
    source: LinkEndpointDetail
    target: LinkEndpointDetail
    tunnel: VxlanTunnelDetail | None = None
