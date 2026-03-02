from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


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
