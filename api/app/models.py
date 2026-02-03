from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserPreferences(Base):
    """User-specific preferences stored in database."""
    __tablename__ = "user_preferences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    # Notification preferences as JSON
    notification_settings: Mapped[str] = mapped_column(Text, default="{}")
    # Canvas display preferences as JSON
    canvas_settings: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Lab(Base):
    __tablename__ = "labs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    workspace_path: Mapped[str] = mapped_column(String(500), default="")
    # Infrastructure provider for this lab (docker, libvirt, etc.)
    provider: Mapped[str] = mapped_column(String(50), default="docker")
    # Lab state: stopped, starting, running, stopping, error, unknown
    state: Mapped[str] = mapped_column(String(50), default="stopped")
    # Agent currently managing this lab (for multi-host support)
    agent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("hosts.id"), nullable=True)
    # Last state update timestamp
    state_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Error message if state is 'error'
    state_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LabFile(Base):
    __tablename__ = "lab_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    lab_id: Mapped[str] = mapped_column(String(36), ForeignKey("labs.id"))
    kind: Mapped[str] = mapped_column(String(50))
    path: Mapped[str] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    lab_id: Mapped[str] = mapped_column(String(36), ForeignKey("labs.id"))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(50), default="viewer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    """Background job tracking for lab operations.

    Status values:
    - queued: Job created, waiting for agent to pick up
    - running: Agent is executing the job
    - completed: Job finished successfully
    - failed: Job failed (error or timeout after max retries)
    - cancelled: Job cancelled by user
    """
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    lab_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("labs.id"), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    # Status: queued, running, completed, failed, cancelled
    status: Mapped[str] = mapped_column(String(50), default="queued")
    # Agent executing this job
    agent_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("hosts.id"), nullable=True)
    # Log content (stored directly instead of file path for simplicity)
    log_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Timestamps for tracking job lifecycle
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Last heartbeat from agent (proves job is still making progress)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Number of retry attempts
    retry_count: Mapped[int] = mapped_column(default=0)
    # Parent job ID for child jobs (e.g., sync:agent jobs spawned by sync:lab)
    parent_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # ID of job that superseded this one (when cancelled and replaced by retry)
    superseded_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Host(Base):
    """Compute host running an agent that can execute labs."""
    __tablename__ = "hosts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(255))  # host:port for reaching agent
    status: Mapped[str] = mapped_column(String(50), default="offline")  # online/offline/degraded
    capabilities: Mapped[str] = mapped_column(Text, default="{}")  # JSON: providers, features
    version: Mapped[str] = mapped_column(String(50), default="")
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NodePlacement(Base):
    """Tracks which host is running which node for a lab."""
    __tablename__ = "node_placements"

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
    source_host_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("hosts.id"), nullable=True)
    target_host_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("hosts.id"), nullable=True)

    # Per-endpoint carrier state for port-down simulation
    source_carrier_state: Mapped[str] = mapped_column(String(10), default="on")
    target_carrier_state: Mapped[str] = mapped_column(String(10), default="on")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class VxlanTunnel(Base):
    """Tracks VXLAN tunnels for cross-host links.

    Each VXLAN tunnel connects two agents for a specific link in a lab.
    The tunnel uses a unique VNI (VXLAN Network Identifier) and is
    associated with a VLAN tag on the OVS bridge.

    Status values:
    - pending: Tunnel setup initiated
    - active: Tunnel established on both agents
    - failed: Tunnel setup failed
    - cleanup: Tunnel being torn down
    """
    __tablename__ = "vxlan_tunnels"
    __table_args__ = (UniqueConstraint("lab_id", "vni", name="uq_vxlan_tunnel_lab_vni"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    lab_id: Mapped[str] = mapped_column(String(36), ForeignKey("labs.id", ondelete="CASCADE"), index=True)
    link_state_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("link_states.id", ondelete="SET NULL"), nullable=True
    )
    # VXLAN Network Identifier (unique per tunnel in lab)
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

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ConfigSnapshot(Base):
    """Configuration snapshot for tracking device configs over time.

    This model stores point-in-time snapshots of device configurations,
    enabling config versioning, comparison, and rollback. Snapshots can
    be created manually or automatically (e.g., on node stop).

    Features:
    - Content hash (SHA256) for deduplication - identical configs share hash
    - Snapshot types: "manual" (user-triggered), "auto_stop" (on node stop)
    - Per-node snapshots with timestamps for timeline views
    """
    __tablename__ = "config_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    lab_id: Mapped[str] = mapped_column(String(36), ForeignKey("labs.id", ondelete="CASCADE"))
    node_name: Mapped[str] = mapped_column(String(100))
    # Full configuration content
    content: Mapped[str] = mapped_column(Text)
    # SHA256 hash of content for deduplication detection
    content_hash: Mapped[str] = mapped_column(String(64))
    # Snapshot type: "manual" or "auto_stop"
    snapshot_type: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImageHost(Base):
    """Tracks which images exist on which agents.

    This model enables image synchronization across a multi-agent deployment.
    Each record represents an image's presence (or absence) on a specific agent.

    Status values:
    - synced: Image exists on agent and matches controller
    - syncing: Transfer in progress
    - failed: Last sync attempt failed
    - missing: Image should exist but doesn't (needs sync)
    - unknown: Status not yet determined
    """
    __tablename__ = "image_hosts"
    __table_args__ = (UniqueConstraint("image_id", "host_id", name="uq_image_host"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    # Image ID from the image library (e.g., "docker:ceos:4.28.0F")
    image_id: Mapped[str] = mapped_column(String(255), index=True)
    # Foreign key to hosts table
    host_id: Mapped[str] = mapped_column(String(36), ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    # Docker image reference (e.g., "ceos:4.28.0F")
    reference: Mapped[str] = mapped_column(String(255))
    # Sync status: synced, syncing, failed, missing, unknown
    status: Mapped[str] = mapped_column(String(50), default="unknown")
    # Image size in bytes (if known) - using BigInteger for large images
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # When the image was last synced to this host
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Error message if status is 'failed'
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ImageSyncJob(Base):
    """Tracks image transfer operations with progress.

    Each sync job represents a single image transfer from controller to agent.
    Progress is tracked as bytes transferred and percentage complete.

    Status values:
    - pending: Job created, waiting to start
    - transferring: Streaming image data to agent
    - loading: Agent is loading image into Docker
    - completed: Sync finished successfully
    - failed: Sync failed
    - cancelled: User cancelled the sync
    """
    __tablename__ = "image_sync_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    # Image ID from the image library
    image_id: Mapped[str] = mapped_column(String(255), index=True)
    # Target agent
    host_id: Mapped[str] = mapped_column(String(36), ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    # Job status: pending, transferring, loading, completed, failed, cancelled
    status: Mapped[str] = mapped_column(String(50), default="pending")
    # Progress tracking - using BigInteger for large file transfers
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    bytes_transferred: Mapped[int] = mapped_column(BigInteger, default=0)
    total_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    # Error message if failed
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Timestamps
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentUpdateJob(Base):
    """Tracks agent software update operations.

    Each update job represents a software update for a specific agent.
    Progress is tracked through status transitions as the update proceeds.

    Status values:
    - pending: Job created, waiting to send to agent
    - downloading: Agent is downloading new version
    - installing: Agent is installing dependencies
    - restarting: Agent is restarting with new version
    - completed: Update finished successfully
    - failed: Update failed
    """
    __tablename__ = "agent_update_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    # Target agent
    host_id: Mapped[str] = mapped_column(String(36), ForeignKey("hosts.id", ondelete="CASCADE"), index=True)
    # Version transition
    from_version: Mapped[str] = mapped_column(String(50))
    to_version: Mapped[str] = mapped_column(String(50))
    # Job status: pending, downloading, installing, restarting, completed, failed
    status: Mapped[str] = mapped_column(String(50), default="pending")
    # Progress percentage (0-100)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    # Error message if failed
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Timestamps
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ISOImportJob(Base):
    """Tracks ISO image import operations.

    Each import job represents an operation to scan and import images
    from a vendor ISO file (like Cisco RefPlat).

    Status values:
    - pending: Job created, not started
    - scanning: Parsing ISO contents
    - importing: Extracting and importing selected images
    - completed: Import finished successfully
    - failed: Import failed
    - cancelled: User cancelled the import
    """
    __tablename__ = "iso_import_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    # User who initiated the import
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    # Path to the ISO file
    iso_path: Mapped[str] = mapped_column(String(500))
    # Detected ISO format (virl2, eve-ng, etc.)
    format: Mapped[str] = mapped_column(String(50), default="unknown")
    # Parsed manifest as JSON
    manifest_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Selected image IDs for import (JSON array)
    selected_images: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-image progress as JSON
    image_progress: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Job status: pending, scanning, importing, completed, failed, cancelled
    status: Mapped[str] = mapped_column(String(50), default="pending")
    # Overall progress percentage (0-100)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    # Error message if failed
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Count of images imported
    images_imported: Mapped[int] = mapped_column(Integer, default=0)
    # Count of images failed
    images_failed: Mapped[int] = mapped_column(Integer, default=0)
    # Timestamps
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Webhook(Base):
    """User-configurable webhook for event notifications.

    Webhooks allow users to receive HTTP callbacks when lab events occur,
    enabling integration with CI/CD pipelines and external systems.

    Events:
    - lab.deploy_started: Lab deployment has begun
    - lab.deploy_complete: Lab deployment finished successfully
    - lab.deploy_failed: Lab deployment failed
    - lab.destroy_complete: Lab infrastructure destroyed
    - node.ready: A node has completed boot and is ready
    - job.completed: Any job completed successfully
    - job.failed: Any job failed
    """
    __tablename__ = "webhooks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    # Owner of this webhook
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    # Optional: scope to specific lab (null = all user's labs)
    lab_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("labs.id", ondelete="CASCADE"), nullable=True)
    # Webhook configuration
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(500))
    # Event types to trigger on (JSON array)
    events: Mapped[str] = mapped_column(Text)  # e.g., ["lab.deploy_started", "lab.deploy_complete"]
    # Optional secret for HMAC-SHA256 signing
    secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Optional custom headers (JSON object)
    headers: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Status
    enabled: Mapped[bool] = mapped_column(default=True)
    # Last delivery tracking
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_delivery_status: Mapped[str | None] = mapped_column(String(50), nullable=True)  # success, failed
    last_delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookDelivery(Base):
    """Log of webhook delivery attempts.

    Each delivery attempt is logged for debugging and monitoring.
    Entries are retained for a limited time (e.g., 7 days).
    """
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    webhook_id: Mapped[str] = mapped_column(String(36), ForeignKey("webhooks.id", ondelete="CASCADE"), index=True)
    # Event details
    event_type: Mapped[str] = mapped_column(String(50))
    lab_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Request/response
    payload: Mapped[str] = mapped_column(Text)  # JSON payload sent
    status_code: Mapped[int | None] = mapped_column(nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    # Result
    success: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Node(Base):
    """Topology node definition - replaces YAML nodes section.

    This model stores the authoritative definition of nodes in a lab topology.
    All runtime queries about topology structure read from this table.
    YAML is generated on-demand for exports and agent communication.

    Node identity:
    - gui_id: Frontend-assigned ID (preserved through YAML round-trips)
    - display_name: User-visible name (can be changed without breaking operations)
    - container_name: Containerlab/YAML key (immutable after first deploy)

    Node types:
    - device: Regular lab device (ceos, srl, linux, etc.)
    - external: External network connection (bridge, VLAN, etc.)
    """
    __tablename__ = "nodes"
    __table_args__ = (UniqueConstraint("lab_id", "container_name", name="uq_node_lab_container"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    lab_id: Mapped[str] = mapped_column(String(36), ForeignKey("labs.id", ondelete="CASCADE"), index=True)

    # Identity
    gui_id: Mapped[str] = mapped_column(String(100))  # Frontend ID
    display_name: Mapped[str] = mapped_column(String(200))  # User-visible name
    container_name: Mapped[str] = mapped_column(String(100))  # YAML key (immutable)

    # Device config
    node_type: Mapped[str] = mapped_column(String(50), default="device")  # device, external
    device: Mapped[str | None] = mapped_column(String(100), nullable=True)  # ceos, srl, etc.
    image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    network_mode: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Placement (replaces YAML host: field)
    host_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("hosts.id"), nullable=True)

    # External network fields
    connection_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    parent_interface: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vlan_id: Mapped[int | None] = mapped_column(nullable=True)
    bridge_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Extra config as JSON (vars, binds, env, role, mgmt, etc.)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Link(Base):
    """Topology link definition - replaces YAML links section.

    This model stores the authoritative definition of links in a lab topology.
    All runtime queries about topology structure read from this table.
    YAML is generated on-demand for exports and agent communication.

    Links connect two endpoints (nodes or external connections).
    Each endpoint has a node reference and interface name.
    """
    __tablename__ = "links"
    __table_args__ = (UniqueConstraint("lab_id", "link_name", name="uq_link_lab_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    lab_id: Mapped[str] = mapped_column(String(36), ForeignKey("labs.id", ondelete="CASCADE"), index=True)
    link_name: Mapped[str] = mapped_column(String(255))  # e.g., "nodeA:eth1-nodeB:eth1"

    # Source endpoint
    source_node_id: Mapped[str] = mapped_column(String(36), ForeignKey("nodes.id", ondelete="CASCADE"))
    source_interface: Mapped[str] = mapped_column(String(100))

    # Target endpoint
    target_node_id: Mapped[str] = mapped_column(String(36), ForeignKey("nodes.id", ondelete="CASCADE"))
    target_interface: Mapped[str] = mapped_column(String(100))

    # Link properties
    mtu: Mapped[int | None] = mapped_column(nullable=True)
    bandwidth: Mapped[int | None] = mapped_column(nullable=True)

    # Extra link attributes as JSON (type, name, pool, prefix, bridge, etc.)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    ovs_bridge: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "arch-ovs" or "ovs-{lab_id}"
    vlan_tag: Mapped[int | None] = mapped_column(nullable=True)

    # Linux layer
    linux_interface: Mapped[str] = mapped_column(String(20))  # "eth1"

    # Vendor layer
    vendor_interface: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "Ethernet1"
    device_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "arista_ceos"

    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AgentLink(Base):
    """Tracks connectivity between agent pairs for mesh visualization.

    Each record represents a directional connection from one agent to another.
    For bidirectional testing, there will be two records (A→B and B→A) to
    capture asymmetric path characteristics.

    Link types:
    - direct: L2 adjacent (TTL unchanged, typically switched)
    - routed: L3 routed path (TTL decremented)
    - unknown: Not yet tested or unable to determine
    """
    __tablename__ = "agent_links"
    __table_args__ = (UniqueConstraint("source_agent_id", "target_agent_id", name="uq_agent_link_pair"),)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
