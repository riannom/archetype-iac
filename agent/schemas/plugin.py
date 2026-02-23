"""Docker OVS plugin schemas."""

from typing import Any

from pydantic import BaseModel, Field

from agent.schemas.base import BaseResponse


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


class PluginVxlanResponse(BaseResponse):
    """Response from VXLAN tunnel creation."""
    port_name: str | None = None


class PluginExternalAttachRequest(BaseModel):
    """Request to attach external interface to lab bridge."""
    external_interface: str
    vlan_tag: int | None = None


class PluginExternalAttachResponse(BaseResponse):
    """Response from external interface attachment."""
    vlan_tag: int = 0


class PluginExternalInfo(BaseModel):
    """Information about an external interface attachment."""
    interface: str
    vlan_tag: int = 0


class PluginExternalListResponse(BaseModel):
    """Response listing external interfaces for a lab."""
    lab_id: str
    interfaces: list[PluginExternalInfo] = Field(default_factory=list)


