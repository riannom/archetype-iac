"""Endpoint repair, interface provisioning, and transport config schemas."""

from typing import Literal

from pydantic import BaseModel, Field


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
