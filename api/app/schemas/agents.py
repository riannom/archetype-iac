from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict


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
    node_name: str | None = None  # Container name (from Node table)
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
