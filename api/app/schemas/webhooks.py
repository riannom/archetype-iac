from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


# =============================================================================
# Webhook Schemas (IaC Workflow Support)
# =============================================================================


class WebhookCreate(BaseModel):
    """Input schema for creating a webhook."""

    name: str
    url: str
    events: list[str]  # e.g., ["lab.deploy_complete", "lab.deploy_failed"]
    lab_id: str | None = None  # None = global for all user's labs
    secret: str | None = None  # For HMAC signing
    headers: dict[str, str] | None = None  # Custom headers
    enabled: bool = True


class WebhookUpdate(BaseModel):
    """Input schema for updating a webhook."""

    name: str | None = None
    url: str | None = None
    events: list[str] | None = None
    secret: str | None = None
    headers: dict[str, str] | None = None
    enabled: bool | None = None


class WebhookOut(BaseModel):
    """Output schema for a webhook."""

    id: str
    owner_id: str
    lab_id: str | None
    name: str
    url: str
    events: list[str]
    has_secret: bool = False  # Don't expose actual secret
    headers: dict[str, str] | None = None
    enabled: bool
    last_delivery_at: datetime | None = None
    last_delivery_status: str | None = None
    last_delivery_error: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WebhooksResponse(BaseModel):
    """Response schema for listing webhooks."""

    webhooks: list[WebhookOut]


class WebhookDeliveryOut(BaseModel):
    """Output schema for a webhook delivery."""

    id: str
    webhook_id: str
    event_type: str
    lab_id: str | None
    job_id: str | None
    status_code: int | None
    success: bool
    error: str | None
    duration_ms: int | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WebhookDeliveriesResponse(BaseModel):
    """Response schema for listing webhook deliveries."""

    deliveries: list[WebhookDeliveryOut]


class WebhookTestRequest(BaseModel):
    """Input schema for testing a webhook."""

    event_type: str = "test"


class WebhookTestResponse(BaseModel):
    """Response schema for webhook test."""

    success: bool
    status_code: int | None = None
    response_preview: str | None = None
    error: str | None = None
    duration_ms: int | None = None


# Webhook event types enum for documentation
WEBHOOK_EVENTS = [
    "lab.deploy_started",
    "lab.deploy_complete",
    "lab.deploy_failed",
    "lab.destroy_complete",
    "node.ready",
    "job.completed",
    "job.failed",
]
