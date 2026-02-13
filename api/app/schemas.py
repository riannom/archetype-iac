from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, model_validator, ConfigDict


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

    model_config = ConfigDict(from_attributes=True)


class LabYamlIn(BaseModel):
    content: str


class LabYamlOut(BaseModel):
    content: str


class UserOut(BaseModel):
    id: str
    username: str
    email: EmailStr
    is_active: bool
    global_role: str = "operator"
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


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


class PermissionCreate(BaseModel):
    user_identifier: str  # username or email
    role: str = "viewer"


class PermissionOut(BaseModel):
    id: str
    lab_id: str
    user_id: str
    role: str
    created_at: datetime
    user_email: EmailStr | None = None
    user_username: str | None = None

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
    device_kind: str | None = None
    mapped_to_node_id: str | None = None
    created_at: datetime
    # Computed fields added by API
    is_active: bool = False
    is_orphaned: bool = False

    model_config = ConfigDict(from_attributes=True)


class ConfigSnapshotsResponse(BaseModel):
    """Response schema for listing config snapshots."""

    snapshots: list[ConfigSnapshotOut]


class ConfigSnapshotCreate(BaseModel):
    """Input schema for creating a config snapshot."""

    node_name: str | None = None  # If None, snapshot all nodes


class ConfigMappingRequest(BaseModel):
    """Input schema for mapping a config snapshot to a target node."""

    target_node_id: str


class SetActiveConfigRequest(BaseModel):
    """Input schema for setting a node's active startup-config."""

    snapshot_id: str


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
# Webhook Schemas (IaC Workflow Support)
# =============================================================================


class WebhookCreate(BaseModel):
    """Input schema for creating a webhook."""

    name: str
    url: str
    events: list[str]  # e.g., ["lab.deploy_complete", "lab.deploy_failed"]
    lab_id: str | None = None  # None = global for all user's labs
    secret: str | None = None  # For HMAC signing
    headers: dict[str, str] | None = None  # Custom headers
    enabled: bool = True


class WebhookUpdate(BaseModel):
    """Input schema for updating a webhook."""

    name: str | None = None
    url: str | None = None
    events: list[str] | None = None
    secret: str | None = None
    headers: dict[str, str] | None = None
    enabled: bool | None = None


class WebhookOut(BaseModel):
    """Output schema for a webhook."""

    id: str
    owner_id: str
    lab_id: str | None
    name: str
    url: str
    events: list[str]
    has_secret: bool = False  # Don't expose actual secret
    headers: dict[str, str] | None = None
    enabled: bool
    last_delivery_at: datetime | None = None
    last_delivery_status: str | None = None
    last_delivery_error: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WebhooksResponse(BaseModel):
    """Response schema for listing webhooks."""

    webhooks: list[WebhookOut]


class WebhookDeliveryOut(BaseModel):
    """Output schema for a webhook delivery."""

    id: str
    webhook_id: str
    event_type: str
    lab_id: str | None
    job_id: str | None
    status_code: int | None
    success: bool
    error: str | None
    duration_ms: int | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WebhookDeliveriesResponse(BaseModel):
    """Response schema for listing webhook deliveries."""

    deliveries: list[WebhookDeliveryOut]


class WebhookTestRequest(BaseModel):
    """Input schema for testing a webhook."""

    event_type: str = "test"


class WebhookTestResponse(BaseModel):
    """Response schema for webhook test."""

    success: bool
    status_code: int | None = None
    response_preview: str | None = None
    error: str | None = None
    duration_ms: int | None = None


# Webhook event types enum for documentation
WEBHOOK_EVENTS = [
    "lab.deploy_started",
    "lab.deploy_complete",
    "lab.deploy_failed",
    "lab.destroy_complete",
    "node.ready",
    "job.completed",
    "job.failed",
]


# =============================================================================
# User Preferences Schemas (Notification System)
# =============================================================================


class NotificationToastSettings(BaseModel):
    """Settings for toast notifications."""
    enabled: bool = True
    position: str = "bottom-right"  # bottom-right, bottom-left, top-right, top-left
    duration: int = 5000  # milliseconds
    showJobStart: bool = True
    showJobComplete: bool = True
    showJobFailed: bool = True
    showJobRetry: bool = True
    showImageSync: bool = True


class NotificationBellSettings(BaseModel):
    """Settings for notification center (bell icon)."""
    enabled: bool = True
    maxHistory: int = 50
    soundEnabled: bool = False


class NotificationSettings(BaseModel):
    """Combined notification settings."""
    toasts: NotificationToastSettings = NotificationToastSettings()
    bell: NotificationBellSettings = NotificationBellSettings()


class CanvasErrorIndicatorSettings(BaseModel):
    """Settings for canvas error indicators on nodes."""
    showIcon: bool = True
    showBorder: bool = True
    pulseAnimation: bool = True


class SidebarFilterSettings(BaseModel):
    """Sidebar device library filter preferences."""
    searchQuery: str = ""
    selectedVendors: list[str] = []
    selectedTypes: list[str] = []
    imageStatus: str = "all"  # 'all' | 'has_image' | 'has_default' | 'no_image'


class CanvasSettings(BaseModel):
    """Canvas display preferences."""
    errorIndicator: CanvasErrorIndicatorSettings = CanvasErrorIndicatorSettings()
    showAgentIndicators: bool = True
    sidebarFilters: SidebarFilterSettings = SidebarFilterSettings()
    consoleInBottomPanel: bool = False
    metricsBarExpanded: bool = False


class ThemeSettings(BaseModel):
    """Theme customization preferences."""
    themeId: str = "sage-stone"
    mode: str = "system"  # 'light' | 'dark' | 'system'
    backgroundId: str = "breath"
    backgroundOpacity: int = 50
    taskLogOpacity: int = 92
    favoriteBackgrounds: list[str] = Field(default_factory=list)
    favoriteThemeIds: list[str] = Field(default_factory=list)
    customThemes: list[dict[str, Any]] = Field(default_factory=list)


class UserPreferencesOut(BaseModel):
    """Output schema for user preferences."""
    notification_settings: NotificationSettings = NotificationSettings()
    canvas_settings: CanvasSettings = CanvasSettings()
    theme_settings: ThemeSettings = ThemeSettings()


class UserPreferencesUpdate(BaseModel):
    """Input schema for updating user preferences."""
    notification_settings: NotificationSettings | None = None
    canvas_settings: CanvasSettings | None = None
    theme_settings: ThemeSettings | None = None


# =============================================================================
# Version and Update Schemas
# =============================================================================


class VersionInfo(BaseModel):
    """Current version information."""
    version: str
    build_time: str | None = None
    commit: str | None = None


class UpdateInfo(BaseModel):
    """Update check results."""
    current_version: str
    latest_version: str | None = None
    update_available: bool = False
    release_url: str | None = None
    release_notes: str | None = None
    published_at: str | None = None
    error: str | None = None


# =============================================================================
# Lab Logs Schemas
# =============================================================================


class LabLogEntry(BaseModel):
    """A single log entry for a lab."""

    timestamp: datetime
    level: str  # "info", "success", "warning", "error"
    message: str
    host_id: str | None = None
    host_name: str | None = None
    job_id: str | None = None
    source: str = "job"  # "job", "system", "realtime"


class LabLogJob(BaseModel):
    """Summary of a job for log filtering."""

    id: str
    action: str
    status: str
    created_at: datetime
    completed_at: datetime | None = None


class LabLogsResponse(BaseModel):
    """Response schema for lab logs endpoint."""

    entries: list[LabLogEntry]
    jobs: list[LabLogJob]  # Jobs available for filtering
    hosts: list[str]  # Hosts found in logs
    total_count: int
    error_count: int
    has_more: bool = False


# =============================================================================
# System Logs Schemas
# =============================================================================


class SystemLogEntry(BaseModel):
    """A single log entry from centralized logging."""

    timestamp: str
    level: str
    service: str
    message: str
    correlation_id: str | None = None
    logger: str | None = None
    extra: dict[str, Any] | None = None


class SystemLogQueryResponse(BaseModel):
    """Response from system log query endpoint."""

    entries: list[SystemLogEntry]
    total_count: int
    has_more: bool


# =============================================================================
# Infrastructure Settings Schemas
# =============================================================================


class InfraSettingsOut(BaseModel):
    """Output schema for infrastructure settings."""

    overlay_mtu: int = 1450
    mtu_verification_enabled: bool = True
    overlay_preserve_container_mtu: bool = False
    overlay_clamp_host_mtu: bool = True
    login_dark_theme_id: str = "midnight"
    login_dark_background_id: str = "floating-lanterns"
    login_dark_background_opacity: int = 50
    login_light_theme_id: str = "sakura-sumie"
    login_light_background_id: str = "sakura-redux"
    login_light_background_opacity: int = 100
    updated_at: datetime | None = None
    updated_by_id: str | None = None

    model_config = ConfigDict(from_attributes=True)


class InfraSettingsUpdate(BaseModel):
    """Input schema for updating infrastructure settings."""

    overlay_mtu: int | None = Field(None, ge=68, le=9000, description="Overlay MTU (68-9000)")
    mtu_verification_enabled: bool | None = None
    overlay_preserve_container_mtu: bool | None = None
    overlay_clamp_host_mtu: bool | None = None
    login_dark_theme_id: str | None = None
    login_dark_background_id: str | None = None
    login_dark_background_opacity: int | None = Field(None, ge=0, le=100)
    login_light_theme_id: str | None = None
    login_light_background_id: str | None = None
    login_light_background_opacity: int | None = Field(None, ge=0, le=100)


class LoginDefaultsOut(BaseModel):
    """Public login screen defaults for unauthenticated clients."""

    dark_theme_id: str = "midnight"
    dark_background_id: str = "floating-lanterns"
    dark_background_opacity: int = 50
    light_theme_id: str = "sakura-sumie"
    light_background_id: str = "sakura-redux"
    light_background_opacity: int = 100


# =============================================================================
# Agent Link / Mesh Schemas
# =============================================================================


class AgentLinkOut(BaseModel):
    """Output schema for an agent-to-agent link."""

    id: str
    source_agent_id: str
    source_agent_name: str | None = None
    target_agent_id: str
    target_agent_name: str | None = None
    link_type: str  # "direct", "routed", "unknown"
    configured_mtu: int
    tested_mtu: int | None = None
    last_test_at: datetime | None = None
    test_status: str  # "pending", "success", "failed", "untested"
    test_error: str | None = None
    latency_ms: float | None = None
    test_path: str = "management"  # "data_plane" or "management"

    model_config = ConfigDict(from_attributes=True)


class AgentMeshNode(BaseModel):
    """Agent node in mesh visualization."""

    id: str
    name: str
    address: str
    status: str  # "online", "offline", "degraded"


class AgentMeshResponse(BaseModel):
    """Response for agent mesh visualization."""

    agents: list[AgentMeshNode]
    links: list[AgentLinkOut]
    settings: InfraSettingsOut


class MtuTestRequest(BaseModel):
    """Request to test MTU between two agents."""

    source_agent_id: str
    target_agent_id: str
    test_path: str | None = None  # None = auto-detect, "data_plane" or "management"


class MtuTestResponse(BaseModel):
    """Response from MTU test."""

    success: bool
    source_agent_id: str
    target_agent_id: str
    configured_mtu: int
    tested_mtu: int | None = None
    link_type: str | None = None  # "direct", "routed"
    latency_ms: float | None = None
    test_path: str | None = None  # "data_plane" or "management"
    error: str | None = None


class MtuTestAllResponse(BaseModel):
    """Response from testing all agent pairs."""

    total_pairs: int
    successful: int
    failed: int
    results: list[MtuTestResponse]


# =============================================================================
# Host Interface Configuration Schemas
# =============================================================================


class InterfaceDetailOut(BaseModel):
    """Detailed information about a host network interface."""

    name: str
    mtu: int
    is_physical: bool
    is_default_route: bool
    mac: str | None = None
    ipv4_addresses: list[str] = Field(default_factory=list)
    state: str


class InterfaceDetailsResponseOut(BaseModel):
    """Response from agent interface details endpoint."""

    interfaces: list[InterfaceDetailOut] = Field(default_factory=list)
    default_route_interface: str | None = None
    network_manager: str | None = None


class SetMtuRequestIn(BaseModel):
    """Request to set MTU on an interface."""

    mtu: int = Field(ge=68, le=9216)
    persist: bool = True


class SetMtuResponseOut(BaseModel):
    """Response from setting interface MTU."""

    success: bool
    interface: str
    previous_mtu: int
    new_mtu: int
    persisted: bool = False
    network_manager: str | None = None
    error: str | None = None


class AgentNetworkConfigOut(BaseModel):
    """Output schema for agent network configuration."""

    id: str
    host_id: str
    host_name: str | None = None
    data_plane_interface: str | None = None
    desired_mtu: int = 9000
    current_mtu: int | None = None
    last_sync_at: datetime | None = None
    sync_status: str = "unknown"
    sync_error: str | None = None
    # Transport configuration
    transport_mode: str = "management"  # management, subinterface, dedicated
    parent_interface: str | None = None
    vlan_id: int | None = None
    transport_ip: str | None = None
    transport_subnet: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AgentNetworkConfigUpdate(BaseModel):
    """Request to update agent network configuration."""

    data_plane_interface: str | None = None
    desired_mtu: int | None = Field(default=None, ge=68, le=9216)
    # Transport configuration
    transport_mode: str | None = Field(default=None, pattern="^(management|subinterface|dedicated)$")
    parent_interface: str | None = None
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    transport_ip: str | None = None
    transport_subnet: str | None = None


# =============================================================================
# Interface Mapping Schemas
# =============================================================================


class InterfaceMappingOut(BaseModel):
    """Output schema for interface mapping.

    Maps between OVS ports, Linux interfaces, and vendor interface names.
    """

    id: str
    lab_id: str
    node_id: str
    # OVS layer
    ovs_port: str | None = None
    ovs_bridge: str | None = None
    vlan_tag: int | None = None
    # Linux layer
    linux_interface: str
    # Vendor layer
    vendor_interface: str | None = None
    device_type: str | None = None
    # Metadata
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InterfaceMappingsResponse(BaseModel):
    """Response for listing interface mappings."""

    mappings: list[InterfaceMappingOut]
    total: int


# =============================================================================
# Agent Managed Interface Schemas
# =============================================================================


class AgentManagedInterfaceOut(BaseModel):
    """Output schema for a managed interface on an agent host."""

    id: str
    host_id: str
    host_name: str | None = None
    name: str
    interface_type: str  # transport, external, custom
    parent_interface: str | None = None
    vlan_id: int | None = None
    ip_address: str | None = None
    desired_mtu: int = 9000
    current_mtu: int | None = None
    is_up: bool = False
    sync_status: str = "unconfigured"
    sync_error: str | None = None
    last_sync_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AgentManagedInterfaceCreate(BaseModel):
    """Request to create a managed interface on an agent host."""

    name: str | None = None  # Auto-generated for subinterfaces if not provided
    interface_type: str = Field(pattern="^(transport|external|custom)$")
    parent_interface: str | None = None  # Required for subinterface creation
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    ip_address: str | None = None  # IP/CIDR format
    desired_mtu: int = Field(default=9000, ge=68, le=9216)
    attach_to_ovs: bool = False
    ovs_vlan_tag: int | None = None


class AgentManagedInterfaceUpdate(BaseModel):
    """Request to update a managed interface."""

    desired_mtu: int | None = Field(default=None, ge=68, le=9216)
    ip_address: str | None = None


class AgentManagedInterfacesResponse(BaseModel):
    """Response for listing managed interfaces."""

    interfaces: list[AgentManagedInterfaceOut]
    total: int


# =============================================================================
# NIC Group Schemas (future interface affinity)
# =============================================================================


class HostNicGroupOut(BaseModel):
    """Output schema for a NIC group on a host."""

    id: str
    host_id: str
    host_name: str | None = None
    name: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime
    members: list["HostNicGroupMemberOut"] = []

    model_config = ConfigDict(from_attributes=True)


class HostNicGroupCreate(BaseModel):
    """Request to create a NIC group on a host."""

    name: str
    description: str | None = None


class HostNicGroupMemberOut(BaseModel):
    """Output schema for a NIC group member."""

    id: str
    nic_group_id: str
    managed_interface_id: str
    interface_name: str | None = None
    interface_type: str | None = None
    role: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class HostNicGroupMemberCreate(BaseModel):
    """Request to add a managed interface to a NIC group."""

    managed_interface_id: str
    role: str | None = None


class HostNicGroupsResponse(BaseModel):
    """Response for listing NIC groups."""

    groups: list[HostNicGroupOut]
    total: int


# =============================================================================
# User Management Schemas (RBAC)
# =============================================================================


class UserCreateAdmin(BaseModel):
    """Admin-created user."""

    username: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9._-]{2,31}$")
    password: str = Field(min_length=8, max_length=72)
    email: EmailStr | None = None
    global_role: str = "operator"


class UserUpdateAdmin(BaseModel):
    """Admin update of user profile."""

    email: EmailStr | None = None
    global_role: str | None = None


class PasswordChange(BaseModel):
    """Password change request."""

    current_password: str | None = None  # Required for self-change, optional for admin
    new_password: str = Field(min_length=8, max_length=72)


class UserListResponse(BaseModel):
    """Response for listing users."""

    users: list[UserOut]
    total: int


# =============================================================================
# Audit Log Schemas
# =============================================================================


class AuditLogOut(BaseModel):
    """Output schema for an audit log entry."""

    id: str
    event_type: str
    user_id: str | None = None
    target_user_id: str | None = None
    ip_address: str | None = None
    details: dict | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogsResponse(BaseModel):
    """Response for listing audit logs."""

    entries: list[AuditLogOut]
    total: int
    has_more: bool = False


# =============================================================================
# Support Bundle Schemas
# =============================================================================


class SupportBundleCreate(BaseModel):
    """Create request for an offline support bundle."""

    summary: str = Field(min_length=5, max_length=4000)
    repro_steps: str = Field(min_length=5, max_length=10000)
    expected_behavior: str = Field(min_length=2, max_length=4000)
    actual_behavior: str = Field(min_length=2, max_length=4000)
    incident_started_at: datetime | None = None
    incident_ended_at: datetime | None = None
    time_window_hours: int = Field(default=24, ge=1, le=168)
    impacted_lab_ids: list[str] = Field(default_factory=list)
    impacted_agent_ids: list[str] = Field(default_factory=list)
    include_configs: bool = False
    pii_safe: bool = True

    @model_validator(mode="after")
    def validate_incident_window(self):
        if self.incident_started_at and self.incident_ended_at:
            if self.incident_ended_at < self.incident_started_at:
                raise ValueError("incident_ended_at must be >= incident_started_at")
        return self


class SupportBundleOut(BaseModel):
    """Metadata for a generated support bundle."""

    id: str
    user_id: str
    status: str
    include_configs: bool
    pii_safe: bool
    time_window_hours: int
    size_bytes: int | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
