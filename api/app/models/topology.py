from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


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

    # External network fields (new: managed_interface_id references infrastructure)
    managed_interface_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("agent_managed_interfaces.id", ondelete="SET NULL"), nullable=True
    )
    # Legacy external network fields (kept for backward compat)
    connection_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    parent_interface: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vlan_id: Mapped[int | None] = mapped_column(nullable=True)
    bridge_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Extra config as JSON (vars, binds, env, role, mgmt, etc.)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Active startup-config snapshot (used for deploy priority)
    active_config_snapshot_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("config_snapshots.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __init__(self, **kwargs):
        # Backward-compatibility aliases used by legacy tests and fixtures.
        if "name" in kwargs and "display_name" not in kwargs:
            kwargs["display_name"] = kwargs.pop("name")
        if "kind" in kwargs and "node_type" not in kwargs:
            kwargs["node_type"] = kwargs.pop("kind")
        super().__init__(**kwargs)

    @property
    def name(self) -> str:
        return self.display_name

    @name.setter
    def name(self, value: str) -> None:
        self.display_name = value

    @property
    def kind(self) -> str:
        return self.node_type

    @kind.setter
    def kind(self, value: str) -> None:
        self.node_type = value


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
