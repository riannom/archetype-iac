from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


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
    action: Mapped[str] = mapped_column(String(200))
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
