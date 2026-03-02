from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Host(Base):
    """Compute host running an agent that can execute labs."""
    __tablename__ = "hosts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(255))  # host:port for reaching agent
    status: Mapped[str] = mapped_column(String(50), default="offline")  # online/offline/degraded
    capabilities: Mapped[str] = mapped_column(Text, default="{}")  # JSON: providers, features
    version: Mapped[str] = mapped_column(String(50), default="")
    git_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resource_usage: Mapped[str] = mapped_column(Text, default="{}")  # JSON: cpu_percent, memory_percent, etc.
    # Image sync strategy: push, pull, on_demand, disabled
    # - push: Receive images immediately when uploaded to controller
    # - pull: Pull missing images when agent comes online
    # - on_demand: Sync only when deployment requires an image
    # - disabled: No automatic sync, manual only
    image_sync_strategy: Mapped[str] = mapped_column(String(50), default="on_demand")
    # Deployment mode: how the agent was installed (systemd, docker, unknown)
    deployment_mode: Mapped[str] = mapped_column(String(50), default="unknown")
    # Whether this agent is co-located with the controller (for rebuild support)
    is_local: Mapped[bool] = mapped_column(default=False)
    # When the agent process started (for uptime tracking)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Error tracking: persists agent-level errors across operations
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Separate data plane address for VXLAN tunnels (vs management address)
    data_plane_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def _parse_json_field(self, value: str | None) -> dict:
        try:
            return json.loads(value) if value else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def get_resource_usage(self) -> dict:
        """Parse resource_usage JSON string into a dict, with safe fallback."""
        return self._parse_json_field(self.resource_usage)

    def get_capabilities(self) -> dict:
        """Parse capabilities JSON string into a dict, with safe fallback."""
        return self._parse_json_field(self.capabilities)


class InfraSettings(Base):
    """Global infrastructure settings (singleton row).

    This model stores cluster-wide settings that apply to all agents,
    such as the default overlay MTU for VXLAN tunnels.

    The table always contains exactly one row with id="global".
    """
    __tablename__ = "infra_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="global")
    # Default MTU for VXLAN overlay (accounts for ~50 byte encapsulation overhead)
    overlay_mtu: Mapped[int] = mapped_column(default=1450)
    # Whether to enable automatic MTU verification between agents
    mtu_verification_enabled: Mapped[bool] = mapped_column(default=True)
    # Preserve container MTU for overlay links (avoid clamping inside containers)
    overlay_preserve_container_mtu: Mapped[bool] = mapped_column(default=False)
    # Clamp host-side veth MTU for overlay links
    overlay_clamp_host_mtu: Mapped[bool] = mapped_column(default=True)
    # Login screen defaults for dark mode
    login_dark_theme_id: Mapped[str] = mapped_column(String(64), default="midnight")
    login_dark_background_id: Mapped[str] = mapped_column(String(64), default="floating-lanterns")
    login_dark_background_opacity: Mapped[int] = mapped_column(Integer, default=50)
    # Login screen defaults for light mode
    login_light_theme_id: Mapped[str] = mapped_column(String(64), default="sakura-sumie")
    login_light_background_id: Mapped[str] = mapped_column(String(64), default="sakura-redux")
    login_light_background_opacity: Mapped[int] = mapped_column(Integer, default=100)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)


class InterfaceMapping(Base):
    """Maps OVS ports to Linux interfaces to vendor interface names.

    This model provides a translation layer between different naming conventions:
    - OVS layer: ovs_port ("vh614ed63ed40"), ovs_bridge ("arch-ovs")
    - Linux layer: linux_interface ("eth1")
    - Vendor layer: vendor_interface ("Ethernet1", "ge-0/0/0")

    This enables:
    - Translating link_states interfaces (eth1) to vendor names (Ethernet1) for display
    - Looking up OVS port names for VLAN tag verification
    - Validating link connectivity by querying OVS state
    """
    __tablename__ = "interface_mappings"
    __table_args__ = (
        UniqueConstraint("lab_id", "node_id", "linux_interface", name="uq_interface_mapping_lab_node_interface"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    lab_id: Mapped[str] = mapped_column(String(36), ForeignKey("labs.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(36), ForeignKey("nodes.id", ondelete="CASCADE"), index=True)

    # OVS layer
    ovs_port: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)  # "vh614ed63ed40"
    ovs_bridge: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "arch-ovs"
    vlan_tag: Mapped[int | None] = mapped_column(nullable=True)

    # Linux layer
    linux_interface: Mapped[str] = mapped_column(String(20))  # "eth1"

    # Vendor layer
    vendor_interface: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "Ethernet1"
    device_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "arista_ceos"

    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentLink(Base):
    """Tracks connectivity between agent pairs for mesh visualization.

    Each record represents a directional connection from one agent to another.
    For bidirectional testing, there will be two records (A->B and B->A) to
    capture asymmetric path characteristics.

    Link types:
    - direct: L2 adjacent (TTL unchanged, typically switched)
    - routed: L3 routed path (TTL decremented)
    - unknown: Not yet tested or unable to determine
    """
    __tablename__ = "agent_links"
    __table_args__ = (UniqueConstraint("source_agent_id", "target_agent_id", "test_path", name="uq_agent_link_pair_path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source_agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    target_agent_id: Mapped[str] = mapped_column(String(36), ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    # Link type: "direct" (L2), "routed" (L3), "unknown"
    link_type: Mapped[str] = mapped_column(String(20), default="unknown")
    # Configured MTU from InfraSettings at test time
    configured_mtu: Mapped[int] = mapped_column(default=1450)
    # Actually tested/verified MTU (null if untested)
    tested_mtu: Mapped[int | None] = mapped_column(nullable=True)
    # When the last MTU test was performed
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Test status: "pending", "success", "failed", "untested"
    test_status: Mapped[str] = mapped_column(String(20), default="untested")
    # Error message if test failed
    test_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Measured latency in milliseconds
    latency_ms: Mapped[float | None] = mapped_column(nullable=True)
    # Which network path was tested: "data_plane" or "management"
    test_path: Mapped[str] = mapped_column(String(20), default="management", server_default="management")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AgentNetworkConfig(Base):
    """Per-agent network interface configuration for MTU management.

    Stores the configured data plane interface and desired MTU for each agent,
    along with sync status to track whether the agent's actual configuration
    matches the desired state.

    Sync statuses:
    - unconfigured: No interface configured yet
    - synced: Agent's MTU matches desired MTU
    - mismatch: Agent's MTU differs from desired (needs sync)
    - error: Last sync attempt failed
    - unknown: Unable to determine current state
    """
    __tablename__ = "agent_network_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    host_id: Mapped[str] = mapped_column(String(36), ForeignKey("hosts.id", ondelete="CASCADE"), unique=True, index=True)
    # The physical interface to configure (e.g., "eth0", "ens192")
    data_plane_interface: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # User-desired MTU (what we want the interface set to)
    desired_mtu: Mapped[int] = mapped_column(default=9000)
    # Last known actual MTU on the interface
    current_mtu: Mapped[int | None] = mapped_column(nullable=True)
    # When we last synced/checked the MTU
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Sync status: synced, mismatch, error, unknown, unconfigured
    sync_status: Mapped[str] = mapped_column(String(20), default="unconfigured")
    # Error message if sync failed
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Transport configuration: how VXLAN traffic reaches the data plane
    # "management" = use management interface (default), "subinterface" = VLAN subinterface, "dedicated" = separate NIC
    transport_mode: Mapped[str] = mapped_column(String(20), default="management")
    # Parent interface for subinterface mode (e.g., "ens192", "eth0")
    parent_interface: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # VLAN ID for subinterface mode
    vlan_id: Mapped[int | None] = mapped_column(nullable=True)
    # IP/CIDR for the transport interface (e.g., "10.100.0.1/24")
    transport_ip: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Subnet for auto-assign pool (e.g., "10.100.0.0/24")
    transport_subnet: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AgentManagedInterface(Base):
    """Tracks provisioned interfaces on agent hosts.

    Each record represents a managed interface (subinterface, dedicated NIC, etc.)
    that the controller has provisioned or is tracking on an agent host.

    Interface types:
    - transport: Data plane interface for VXLAN tunnels (one per host max)
    - external: Interface for external network connectivity
    - custom: User-created interface for other purposes

    Sync statuses:
    - unconfigured: Interface not yet provisioned on host
    - synced: Interface exists on host and matches desired config
    - mismatch: Interface exists but config differs (needs sync)
    - error: Last provisioning/sync attempt failed
    - unknown: Unable to determine current state
    """
    __tablename__ = "agent_managed_interfaces"
    __table_args__ = (UniqueConstraint("host_id", "name", name="uq_managed_interface_host_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    host_id: Mapped[str] = mapped_column(String(36), ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    # Interface name on the host (e.g., "ens192.100", "eth1")
    name: Mapped[str] = mapped_column(String(64))
    # Type: "transport", "external", "custom"
    interface_type: Mapped[str] = mapped_column(String(20))
    # Parent interface (for VLAN subinterfaces)
    parent_interface: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # VLAN ID (for VLAN subinterfaces)
    vlan_id: Mapped[int | None] = mapped_column(nullable=True)
    # IP address in CIDR format (e.g., "10.100.0.1/24")
    ip_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Desired MTU for this interface
    desired_mtu: Mapped[int] = mapped_column(default=9000)
    # Last known actual MTU on the interface
    current_mtu: Mapped[int | None] = mapped_column(nullable=True)
    # Whether the interface is operationally up
    is_up: Mapped[bool] = mapped_column(default=False)
    # Sync status: unconfigured, synced, mismatch, error, unknown
    sync_status: Mapped[str] = mapped_column(String(20), default="unconfigured")
    # Error message if sync failed
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When the interface was last synced/checked
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class HostNicGroup(Base):
    """Grouping of managed interfaces on a host (future NIC/VLAN affinity)."""
    __tablename__ = "host_nic_groups"
    __table_args__ = (UniqueConstraint("host_id", "name", name="uq_host_nic_group_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    host_id: Mapped[str] = mapped_column(String(36), ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class HostNicGroupMember(Base):
    """Member interface inside a NIC group."""
    __tablename__ = "host_nic_group_members"
    __table_args__ = (UniqueConstraint("nic_group_id", "managed_interface_id", name="uq_nic_group_member"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    nic_group_id: Mapped[str] = mapped_column(String(36), ForeignKey("host_nic_groups.id", ondelete="CASCADE"), index=True)
    managed_interface_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_managed_interfaces.id", ondelete="CASCADE"), index=True)
    role: Mapped[str | None] = mapped_column(String(50), nullable=True)  # transport/external/custom
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExternalNetworkAffinity(Base):
    """Preference mapping from external network nodes to NIC groups/interfaces."""
    __tablename__ = "external_network_affinities"
    __table_args__ = (UniqueConstraint("lab_id", "external_node_id", name="uq_external_affinity"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    lab_id: Mapped[str] = mapped_column(String(36), ForeignKey("labs.id", ondelete="CASCADE"), index=True)
    external_node_id: Mapped[str] = mapped_column(String(36), ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    preferred_nic_group_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("host_nic_groups.id", ondelete="SET NULL"), nullable=True
    )
    preferred_managed_interface_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_managed_interfaces.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
