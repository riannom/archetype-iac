from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class NodeState(Base):
    """Per-node desired/actual state for lab lifecycle management.

    This model enables per-node control where each node tracks:
    - desired_state: What the user wants (stopped/running)
    - actual_state: What the node actually is (undeployed/pending/starting/running/stopped/stopping/error)
    - is_ready: Whether the node's application has completed boot (for console access)

    Nodes default to 'stopped' when added and only boot when user triggers start.

    Transitional states:
    - pending: Node is being deployed for the first time (creates container)
    - starting: Already-deployed node is being started (starts existing container)
    - stopping: Node is being stopped (desired_state=stopped, tracked by stopping_started_at)
    """
    __tablename__ = "node_states"
    __table_args__ = (UniqueConstraint("lab_id", "node_id", name="uq_node_state_lab_node"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    lab_id: Mapped[str] = mapped_column(String(36), ForeignKey("labs.id", ondelete="CASCADE"))
    node_id: Mapped[str] = mapped_column(String(100))  # Frontend node ID
    node_name: Mapped[str] = mapped_column(String(100))  # Name in topology
    # FK to Node definition (topology source of truth)
    node_definition_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True
    )
    # desired_state: What the user wants - "stopped" or "running"
    desired_state: Mapped[str] = mapped_column(String(50), default="stopped")
    # actual_state: Current reality - "undeployed", "pending", "starting", "running", "stopped", "stopping", "error"
    actual_state: Mapped[str] = mapped_column(String(50), default="undeployed")
    # Error message if actual_state is "error"
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Boot readiness: True when application has completed boot and is ready for console
    is_ready: Mapped[bool] = mapped_column(default=False)
    # Timestamp when container started booting (for tracking boot duration)
    boot_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Timestamp when container started stopping (for tracking stop duration and timeout recovery)
    stopping_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Timestamp when container started starting (for tracking start duration and timeout recovery)
    starting_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Image sync status: null (not syncing), "checking", "syncing", "synced", "failed"
    image_sync_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Image sync progress/error message
    image_sync_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Management IP address(es) captured from container after deploy
    management_ip: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # JSON array of all IP addresses (for nodes with multiple IPs)
    management_ips_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # State enforcement tracking
    # Number of enforcement attempts on this node (reset on success or manual intervention)
    enforcement_attempts: Mapped[int] = mapped_column(default=0)
    # When the last enforcement action was attempted
    last_enforcement_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When enforcement was marked as failed (after max retries exhausted)
    enforcement_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def reset_enforcement(self, clear_error: bool = False) -> None:
        """Reset enforcement tracking fields.

        Called when a node is retried, restarted, or successfully reconciled.
        """
        self.enforcement_attempts = 0
        self.enforcement_failed_at = None
        self.last_enforcement_at = None
        if clear_error:
            self.error_message = None


class LinkState(Base):
    """Per-link desired/actual state for lab lifecycle management.

    This model enables per-link control where each link tracks:
    - desired_state: What the user wants ("up" or "down")
    - actual_state: Current reality (see states below)

    Links are identified by a unique name generated from their endpoints.
    The source/target node and interface fields store the link topology
    for reference and display purposes.

    Link actual_state values:
    - "pending": Link is waiting to be created (initial state)
    - "creating": Link creation in progress (transitional)
    - "up": Link is enabled and active (verified VLAN tags match)
    - "down": Link is administratively disabled
    - "unknown": Link state cannot be determined
    - "error": Link creation or verification failed

    State machine:
        pending -> creating -> up
                            `-> error

    For cross-host links (nodes on different agents), additional fields
    track the VXLAN tunnel used for L2 connectivity.
    """
    __tablename__ = "link_states"
    __table_args__ = (UniqueConstraint("lab_id", "link_name", name="uq_link_state_lab_link"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    lab_id: Mapped[str] = mapped_column(String(36), ForeignKey("labs.id", ondelete="CASCADE"))
    # FK to Link definition (topology source of truth)
    link_definition_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("links.id", ondelete="SET NULL"), nullable=True
    )
    # Unique identifier for this link within the lab (e.g., "node1:eth1-node2:eth1")
    link_name: Mapped[str] = mapped_column(String(255))
    # Source endpoint
    source_node: Mapped[str] = mapped_column(String(100))
    source_interface: Mapped[str] = mapped_column(String(100))
    # Target endpoint
    target_node: Mapped[str] = mapped_column(String(100))
    target_interface: Mapped[str] = mapped_column(String(100))
    # desired_state: What the user wants - "up" or "down"
    desired_state: Mapped[str] = mapped_column(String(50), default="up")
    # actual_state: Current reality - "up", "down", "unknown", "error"
    actual_state: Mapped[str] = mapped_column(String(50), default="unknown")
    # Error message if actual_state is "error"
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Cross-host VXLAN support
    is_cross_host: Mapped[bool] = mapped_column(default=False)
    vni: Mapped[int | None] = mapped_column(nullable=True)  # VXLAN Network Identifier
    vlan_tag: Mapped[int | None] = mapped_column(nullable=True)  # Shared VLAN tag for this link
    # Per-side VLAN tags (local OVS tags on each agent)
    source_vlan_tag: Mapped[int | None] = mapped_column(nullable=True)
    target_vlan_tag: Mapped[int | None] = mapped_column(nullable=True)
    source_host_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("hosts.id"), nullable=True)
    target_host_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("hosts.id"), nullable=True)

    # Per-endpoint carrier state for port-down simulation
    source_carrier_state: Mapped[str] = mapped_column(String(10), default="on")
    target_carrier_state: Mapped[str] = mapped_column(String(10), default="on")

    # Derived operational state per endpoint (admin + local + peer + transport)
    source_oper_state: Mapped[str] = mapped_column(String(20), default="down")
    target_oper_state: Mapped[str] = mapped_column(String(20), default="down")
    source_oper_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_oper_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_last_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    target_last_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    oper_epoch: Mapped[int] = mapped_column(default=0)

    # Partial VXLAN attachment tracking for cross-host links
    # Used to track which side has been successfully attached to the overlay
    # Enables partial recovery when one agent restarts
    source_vxlan_attached: Mapped[bool] = mapped_column(default=False)
    target_vxlan_attached: Mapped[bool] = mapped_column(default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LinkEndpointReservation(Base):
    """Reserves normalized link endpoints for strict 1:1 wire semantics.

    A reservation exists while a link is desired "up", including pending links.
    The unique constraint on (lab_id, node_name, interface_name) prevents an
    endpoint from being claimed by more than one LinkState at a time.
    """
    __tablename__ = "link_endpoint_reservations"
    __table_args__ = (
        UniqueConstraint("lab_id", "node_name", "interface_name", name="uq_link_endpoint_reservation_endpoint"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    lab_id: Mapped[str] = mapped_column(String(36), ForeignKey("labs.id", ondelete="CASCADE"), index=True)
    link_state_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("link_states.id", ondelete="CASCADE"), index=True
    )
    node_name: Mapped[str] = mapped_column(String(100))
    interface_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NodePlacement(Base):
    """Tracks which host is running which node for a lab."""
    __tablename__ = "node_placements"
    __table_args__ = (
        UniqueConstraint("lab_id", "node_name", name="uq_node_placement_lab_node"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    lab_id: Mapped[str] = mapped_column(String(36), ForeignKey("labs.id"))
    node_name: Mapped[str] = mapped_column(String(100))
    # FK to Node definition for reliable lookups (not dependent on name matching)
    node_definition_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True
    )
    host_id: Mapped[str] = mapped_column(String(36), ForeignKey("hosts.id"))
    runtime_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # container/domain ID
    status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NodeMigrationCleanup(Base):
    """Deferred cleanup tasks for nodes moved away from an old host.

    These records are created when migration cannot clean the old host
    immediately (for example the old agent is offline). A background drain
    retries destroy operations when the old host becomes reachable.
    """

    __tablename__ = "node_migration_cleanups"
    __table_args__ = (
        UniqueConstraint(
            "lab_id",
            "node_name",
            "old_host_id",
            name="uq_node_migration_cleanup_target",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    lab_id: Mapped[str] = mapped_column(String(36), ForeignKey("labs.id", ondelete="CASCADE"), index=True)
    node_name: Mapped[str] = mapped_column(String(100), index=True)
    old_host_id: Mapped[str] = mapped_column(String(36), ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(50), default="docker")
    # pending -> running -> pending|failed (rows are deleted on success)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class VxlanTunnel(Base):
    """Tracks VXLAN tunnels for cross-host links.

    In the trunk VTEP model, multiple links between the same host pair share
    a single VTEP with VLAN tags providing per-link isolation. Each link_state
    has at most one tunnel record for tracking purposes.

    Status values:
    - pending: Tunnel setup initiated
    - active: Tunnel established on both agents
    - failed: Tunnel setup failed
    - cleanup: Tunnel being torn down
    """
    __tablename__ = "vxlan_tunnels"
    __table_args__ = (UniqueConstraint("link_state_id", name="uq_vxlan_tunnel_link_state"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    lab_id: Mapped[str] = mapped_column(String(36), ForeignKey("labs.id", ondelete="CASCADE"), index=True)
    link_state_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("link_states.id", ondelete="CASCADE"), nullable=True, unique=True
    )
    # VXLAN Network Identifier (shared per host-pair in trunk VTEP model)
    vni: Mapped[int] = mapped_column(index=True)
    # Shared VLAN tag used on both agent's OVS bridges
    vlan_tag: Mapped[int] = mapped_column()

    # Endpoint A (source side)
    agent_a_id: Mapped[str] = mapped_column(String(36), ForeignKey("hosts.id"), index=True)
    agent_a_ip: Mapped[str] = mapped_column(String(45))  # IPv4 or IPv6

    # Endpoint B (target side)
    agent_b_id: Mapped[str] = mapped_column(String(36), ForeignKey("hosts.id"), index=True)
    agent_b_ip: Mapped[str] = mapped_column(String(45))  # IPv4 or IPv6

    # Tunnel status: pending, active, failed, cleanup
    status: Mapped[str] = mapped_column(String(50), default="pending")
    # Error message if status is 'failed'
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Deterministic OVS port name (vxlan-<hash>)
    port_name: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationship for joinedload in convergence queries
    link_state = relationship("LinkState", foreign_keys=[link_state_id], lazy="select")


class ConfigSnapshot(Base):
    """Configuration snapshot for tracking device configs over time.

    This model stores point-in-time snapshots of device configurations,
    enabling config versioning, comparison, and rollback. Snapshots can
    be created manually or automatically (e.g., on node stop).

    Features:
    - Content hash (SHA256) for deduplication - identical configs share hash
    - Snapshot types: "manual" (user-triggered), "auto_stop" (on lab destroy),
      "autosave" (on node stop, set as active for next start)
    - Per-node snapshots with timestamps for timeline views
    - device_kind for type-matching when mapping orphaned configs to new nodes
    - mapped_to_node_id for reassigning stranded configs to replacement nodes
    """
    __tablename__ = "config_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    lab_id: Mapped[str] = mapped_column(String(36), ForeignKey("labs.id", ondelete="CASCADE"))
    node_name: Mapped[str] = mapped_column(String(100))
    # Full configuration content
    content: Mapped[str] = mapped_column(Text)
    # SHA256 hash of content for deduplication detection
    content_hash: Mapped[str] = mapped_column(String(64))
    # Snapshot type: "manual", "auto_stop", or "autosave"
    snapshot_type: Mapped[str] = mapped_column(String(50))
    # Device type (e.g., "ceos", "srl") for type-matching during config mapping
    device_kind: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # FK to target node when an orphaned config is reassigned
    mapped_to_node_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
