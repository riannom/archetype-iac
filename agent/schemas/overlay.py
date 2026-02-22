"""Overlay networking, VXLAN tunnel, and VTEP schemas."""

from typing import Any

from pydantic import BaseModel, Field


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


# --- Trunk VTEP Model ---


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


# --- Overlay Convergence (declare-state) ---


class DeclaredTunnel(BaseModel):
    """API -> Agent: Desired state for a single VXLAN tunnel port."""
    link_id: str          # link_state.link_name
    lab_id: str
    vni: int
    local_ip: str
    remote_ip: str
    expected_vlan: int    # Per-side VLAN tag from LinkState
    port_name: str        # Deterministic OVS port name (vxlan-<hash>)
    mtu: int = 0


class DeclareOverlayStateRequest(BaseModel):
    """API -> Agent: Full desired overlay state."""
    tunnels: list[DeclaredTunnel]


class DeclaredTunnelResult(BaseModel):
    """Agent -> API: Result for a single declared tunnel."""
    link_id: str
    lab_id: str
    status: str           # "converged" | "created" | "updated" | "error"
    actual_vlan: int | None = None
    error: str | None = None


class DeclareOverlayStateResponse(BaseModel):
    """Agent -> API: Results of overlay convergence."""
    results: list[DeclaredTunnelResult]
    orphans_removed: list[str] = []
