"""Link management, OVS, carrier, interface, external, and port state schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

from agent.schemas.base import BaseResponse
from agent.schemas.enums import LinkState


# --- Hot-Connect Link Management ---


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


class LinkCreateResponse(BaseResponse):
    """Agent -> Controller: Link creation result."""
    link: LinkInfo | None = None


class LinkDeleteResponse(BaseResponse):
    """Agent -> Controller: Link deletion result."""


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


class ExternalConnectResponse(BaseResponse):
    """Response from external connection request."""
    vlan_tag: int | None = None


class ExternalDisconnectRequest(BaseModel):
    """Request to disconnect an external interface."""
    external_interface: str  # Host interface to disconnect


class ExternalDisconnectResponse(BaseResponse):
    """Response from external disconnect request."""


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


class BridgePatchResponse(BaseResponse):
    """Response from bridge patch request."""
    patch_port: str | None = None


class BridgeDeletePatchRequest(BaseModel):
    """Request to delete a patch to another bridge."""
    target_bridge: str


class BridgeDeletePatchResponse(BaseResponse):
    """Response from bridge patch deletion request."""


# --- Carrier State Management ---


class CarrierStateRequest(BaseModel):
    """Request to set interface carrier state."""
    state: str  # "on" or "off"


class CarrierStateResponse(BaseResponse):
    """Response from carrier state operation."""
    container: str
    interface: str
    state: str


class PortIsolateResponse(BaseResponse):
    """Response from port isolation operation."""
    container: str
    interface: str
    vlan_tag: int | None = None


class PortRestoreRequest(BaseModel):
    """Request to restore a port to a specific VLAN."""
    target_vlan: int


class PortRestoreResponse(BaseResponse):
    """Response from port restore operation."""
    container: str
    interface: str
    vlan_tag: int


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


class SetMtuResponse(BaseResponse):
    """Response from setting interface MTU."""
    interface: str
    previous_mtu: int
    new_mtu: int
    persisted: bool = False
    network_manager: str | None = None


# --- Same-host Port Convergence (declare-state) ---


class DeclaredPortPairing(BaseModel):
    """API -> Agent: Desired state for a same-host link."""
    link_name: str
    lab_id: str
    port_a: str           # OVS port name for source
    port_b: str           # OVS port name for target
    vlan_tag: int         # Shared VLAN tag


class DeclarePortStateRequest(BaseModel):
    """API -> Agent: Full desired same-host port state."""
    pairings: list[DeclaredPortPairing]


class DeclaredPortResult(BaseModel):
    """Agent -> API: Result for a single port pairing."""
    link_name: str
    lab_id: str
    status: str           # "converged" | "updated" | "error"
    actual_vlan: int | None = None
    error: str | None = None


class DeclarePortStateResponse(BaseModel):
    """Agent -> API: Results of same-host port convergence."""
    results: list[DeclaredPortResult]


class PortInfo(BaseModel):
    """Agent -> API: OVS port state for a single container interface."""
    node_name: str
    interface_name: str
    ovs_port_name: str
    vlan_tag: int
    carrier: str = "unknown"


class PortStateResponse(BaseModel):
    """Agent -> API: All OVS port state for a lab."""
    ports: list[PortInfo]
