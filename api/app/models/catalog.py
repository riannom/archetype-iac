from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


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


class CatalogVendor(Base):
    """Canonical vendor entries for the device/image catalog."""
    __tablename__ = "catalog_vendors"
    __table_args__ = (UniqueConstraint("vendor_key", name="uq_catalog_vendor_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    vendor_key: Mapped[str] = mapped_column(String(120), index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    lifecycle_status: Mapped[str] = mapped_column(String(20), default="active")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CatalogDeviceType(Base):
    """Canonical device type identity records."""
    __tablename__ = "catalog_device_types"
    __table_args__ = (UniqueConstraint("canonical_device_id", name="uq_catalog_device_canonical_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    canonical_device_id: Mapped[str] = mapped_column(String(120), index=True)
    vendor_id: Mapped[str] = mapped_column(String(36), ForeignKey("catalog_vendors.id", ondelete="RESTRICT"), index=True)
    runtime_kind: Mapped[str | None] = mapped_column(String(120), nullable=True)
    display_name: Mapped[str] = mapped_column(String(200))
    device_class: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="builtin")
    lifecycle_status: Mapped[str] = mapped_column(String(20), default="active")
    eol_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CatalogDeviceRevision(Base):
    """Versioned runtime/spec snapshots for a canonical device type."""
    __tablename__ = "catalog_device_revisions"
    __table_args__ = (
        UniqueConstraint("device_type_id", "version_tag", name="uq_catalog_device_revision_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    device_type_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("catalog_device_types.id", ondelete="CASCADE"), index=True
    )
    version_tag: Mapped[str] = mapped_column(String(120), default="current")
    runtime_kind: Mapped[str | None] = mapped_column(String(120), nullable=True)
    memory_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_ports: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supported_image_kinds_json: Mapped[str] = mapped_column(Text, default="[]")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_current: Mapped[bool] = mapped_column(default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CatalogDeviceAlias(Base):
    """Alias tokens that resolve to canonical device types."""
    __tablename__ = "catalog_device_aliases"
    __table_args__ = (
        UniqueConstraint("alias", "device_type_id", name="uq_catalog_device_alias_pair"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    device_type_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("catalog_device_types.id", ondelete="CASCADE"), index=True
    )
    alias: Mapped[str] = mapped_column(String(120), index=True)
    alias_type: Mapped[str] = mapped_column(String(40), default="explicit")
    source: Mapped[str] = mapped_column(String(40), default="seed")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CatalogImage(Base):
    """Canonical image artifacts imported into the controller."""
    __tablename__ = "catalog_images"
    __table_args__ = (UniqueConstraint("external_id", name="uq_catalog_image_external_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    digest_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    archive_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    archive_status: Mapped[str] = mapped_column(String(20), default="none")
    archive_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    archive_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    archive_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archive_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archive_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    vendor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="manifest")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CatalogImageCompatibility(Base):
    """Many-to-many compatibility between images and canonical device types."""
    __tablename__ = "catalog_image_device_compat"
    __table_args__ = (
        UniqueConstraint("image_id", "device_type_id", name="uq_catalog_image_device_compat"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    image_id: Mapped[str] = mapped_column(String(36), ForeignKey("catalog_images.id", ondelete="CASCADE"), index=True)
    device_type_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("catalog_device_types.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(40), default="manifest")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CatalogImageDefault(Base):
    """Current default image per canonical device type."""
    __tablename__ = "catalog_image_defaults"
    __table_args__ = (UniqueConstraint("device_type_id", name="uq_catalog_image_default_device"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    device_type_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("catalog_device_types.id", ondelete="CASCADE"), index=True
    )
    image_id: Mapped[str] = mapped_column(String(36), ForeignKey("catalog_images.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(40), default="manifest")
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CatalogIngestEvent(Base):
    """Append-only ingest/change events for catalog traceability."""
    __tablename__ = "catalog_ingest_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source: Mapped[str] = mapped_column(String(80), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
