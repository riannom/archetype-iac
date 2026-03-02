from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


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
