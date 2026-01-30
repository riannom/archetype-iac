"""Webhook management endpoints."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import db, models, schemas, webhooks
from app.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _webhook_to_out(webhook: models.Webhook) -> schemas.WebhookOut:
    """Convert Webhook model to output schema."""
    events = []
    try:
        events = json.loads(webhook.events)
    except json.JSONDecodeError:
        pass

    headers = None
    try:
        if webhook.headers:
            headers = json.loads(webhook.headers)
    except json.JSONDecodeError:
        pass

    return schemas.WebhookOut(
        id=webhook.id,
        owner_id=webhook.owner_id,
        lab_id=webhook.lab_id,
        name=webhook.name,
        url=webhook.url,
        events=events,
        has_secret=bool(webhook.secret),
        headers=headers,
        enabled=webhook.enabled,
        last_delivery_at=webhook.last_delivery_at,
        last_delivery_status=webhook.last_delivery_status,
        last_delivery_error=webhook.last_delivery_error,
        created_at=webhook.created_at,
    )


@router.get("")
def list_webhooks(
    lab_id: str | None = None,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.WebhooksResponse:
    """List all webhooks for the current user.

    Optionally filter by lab_id to see webhooks scoped to a specific lab.
    """
    query = database.query(models.Webhook).filter(
        models.Webhook.owner_id == current_user.id
    )

    if lab_id:
        # Include both lab-specific and global webhooks
        query = query.filter(
            (models.Webhook.lab_id == lab_id) | (models.Webhook.lab_id.is_(None))
        )

    webhooks_list = query.order_by(models.Webhook.created_at.desc()).all()

    return schemas.WebhooksResponse(
        webhooks=[_webhook_to_out(wh) for wh in webhooks_list]
    )


@router.post("")
def create_webhook(
    payload: schemas.WebhookCreate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.WebhookOut:
    """Create a new webhook.

    Events that can be subscribed to:
    - lab.deploy_started: Lab deployment has begun
    - lab.deploy_complete: Lab deployment finished successfully
    - lab.deploy_failed: Lab deployment failed
    - lab.destroy_complete: Lab infrastructure destroyed
    - node.ready: A node has completed boot and is ready
    - job.completed: Any job completed successfully
    - job.failed: Any job failed
    """
    # Validate events
    valid_events = set(schemas.WEBHOOK_EVENTS)
    invalid_events = [e for e in payload.events if e not in valid_events]
    if invalid_events:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid event types: {invalid_events}. Valid events: {list(valid_events)}",
        )

    # Validate lab_id if provided
    if payload.lab_id:
        lab = database.get(models.Lab, payload.lab_id)
        if not lab or lab.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail="Lab not found")

    # Validate URL
    if not payload.url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="Webhook URL must start with http:// or https://",
        )

    webhook = models.Webhook(
        owner_id=current_user.id,
        lab_id=payload.lab_id,
        name=payload.name,
        url=payload.url,
        events=json.dumps(payload.events),
        secret=payload.secret,
        headers=json.dumps(payload.headers) if payload.headers else None,
        enabled=payload.enabled,
    )
    database.add(webhook)
    database.commit()
    database.refresh(webhook)

    logger.info(f"Created webhook {webhook.id} for user {current_user.id}")
    return _webhook_to_out(webhook)


@router.get("/{webhook_id}")
def get_webhook(
    webhook_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.WebhookOut:
    """Get details of a specific webhook."""
    webhook = database.get(models.Webhook, webhook_id)
    if not webhook or webhook.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Webhook not found")

    return _webhook_to_out(webhook)


@router.put("/{webhook_id}")
def update_webhook(
    webhook_id: str,
    payload: schemas.WebhookUpdate,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.WebhookOut:
    """Update a webhook."""
    webhook = database.get(models.Webhook, webhook_id)
    if not webhook or webhook.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Webhook not found")

    if payload.name is not None:
        webhook.name = payload.name

    if payload.url is not None:
        if not payload.url.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=400,
                detail="Webhook URL must start with http:// or https://",
            )
        webhook.url = payload.url

    if payload.events is not None:
        valid_events = set(schemas.WEBHOOK_EVENTS)
        invalid_events = [e for e in payload.events if e not in valid_events]
        if invalid_events:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid event types: {invalid_events}",
            )
        webhook.events = json.dumps(payload.events)

    if payload.secret is not None:
        webhook.secret = payload.secret if payload.secret else None

    if payload.headers is not None:
        webhook.headers = json.dumps(payload.headers) if payload.headers else None

    if payload.enabled is not None:
        webhook.enabled = payload.enabled

    database.commit()
    database.refresh(webhook)

    logger.info(f"Updated webhook {webhook_id}")
    return _webhook_to_out(webhook)


@router.delete("/{webhook_id}")
def delete_webhook(
    webhook_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    """Delete a webhook."""
    webhook = database.get(models.Webhook, webhook_id)
    if not webhook or webhook.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Webhook not found")

    database.delete(webhook)
    database.commit()

    logger.info(f"Deleted webhook {webhook_id}")
    return {"status": "deleted", "webhook_id": webhook_id}


@router.post("/{webhook_id}/test")
async def test_webhook_endpoint(
    webhook_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.WebhookTestResponse:
    """Send a test event to a webhook.

    This sends a test payload to verify the webhook URL is reachable
    and properly configured.
    """
    webhook = database.get(models.Webhook, webhook_id)
    if not webhook or webhook.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Webhook not found")

    success, status_code, error, duration_ms = await webhooks.test_webhook(webhook)

    # Log the test delivery
    payload = webhooks.build_webhook_payload(
        event_type="test",
        extra={
            "message": "Test webhook delivery",
            "webhook_id": webhook.id,
        },
    )
    webhooks.log_delivery(
        database,
        webhook,
        "test",
        payload,
        success,
        status_code,
        error,
        duration_ms,
    )

    return schemas.WebhookTestResponse(
        success=success,
        status_code=status_code,
        error=error,
        duration_ms=duration_ms,
    )


@router.get("/{webhook_id}/deliveries")
def list_webhook_deliveries(
    webhook_id: str,
    limit: int = 50,
    offset: int = 0,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.WebhookDeliveriesResponse:
    """List recent delivery attempts for a webhook.

    Returns deliveries ordered by most recent first.
    """
    webhook = database.get(models.Webhook, webhook_id)
    if not webhook or webhook.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Webhook not found")

    deliveries = (
        database.query(models.WebhookDelivery)
        .filter(models.WebhookDelivery.webhook_id == webhook_id)
        .order_by(models.WebhookDelivery.created_at.desc())
        .offset(offset)
        .limit(min(limit, 100))
        .all()
    )

    return schemas.WebhookDeliveriesResponse(
        deliveries=[
            schemas.WebhookDeliveryOut(
                id=d.id,
                webhook_id=d.webhook_id,
                event_type=d.event_type,
                lab_id=d.lab_id,
                job_id=d.job_id,
                status_code=d.status_code,
                success=d.success,
                error=d.error,
                duration_ms=d.duration_ms,
                created_at=d.created_at,
            )
            for d in deliveries
        ]
    )
