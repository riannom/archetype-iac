"""Webhook dispatch service for event notifications.

This module handles:
- Finding webhooks that match events
- Signing payloads with HMAC-SHA256
- Delivering webhooks with timeout and retry
- Logging delivery attempts
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

from app import models
from app.db import get_session

logger = logging.getLogger(__name__)


# Standard webhook payload structure
def build_webhook_payload(
    event_type: str,
    lab: models.Lab | None = None,
    job: models.Job | None = None,
    nodes: list[dict] | None = None,
    extra: dict | None = None,
) -> dict[str, Any]:
    """Build a standardized webhook payload.

    Example payload:
    {
        "id": "evt_abc123",
        "event": "lab.deploy_complete",
        "timestamp": "2024-01-15T10:30:00Z",
        "lab": {
            "id": "lab_xyz",
            "name": "DC Fabric",
            "state": "running"
        },
        "nodes": [...],
        "job": {...}
    }
    """
    payload: dict[str, Any] = {
        "id": f"evt_{uuid4().hex[:12]}",
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if lab:
        payload["lab"] = {
            "id": lab.id,
            "name": lab.name,
            "state": lab.state,
        }

    if job:
        duration = None
        if job.started_at and job.completed_at:
            duration = (job.completed_at - job.started_at).total_seconds()

        payload["job"] = {
            "id": job.id,
            "action": job.action,
            "status": job.status,
            "duration_seconds": duration,
        }

    if nodes:
        payload["nodes"] = nodes

    if extra:
        payload.update(extra)

    return payload


def sign_payload(payload: str, secret: str) -> str:
    """Sign payload with HMAC-SHA256.

    Returns hex-encoded signature for X-Webhook-Signature header.
    """
    signature = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={signature}"


async def deliver_webhook(
    webhook: models.Webhook,
    payload: dict[str, Any],
    timeout: float = 30.0,
) -> tuple[bool, int | None, str | None, int]:
    """Deliver a webhook payload.

    Args:
        webhook: The webhook configuration
        payload: The event payload to send
        timeout: Request timeout in seconds

    Returns:
        Tuple of (success, status_code, error_message, duration_ms)
    """
    payload_json = json.dumps(payload)
    start_time = time.monotonic()

    # Build headers
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Archetype-Webhook/1.0",
        "X-Webhook-Event": payload.get("event", "unknown"),
        "X-Webhook-Delivery": payload.get("id", "unknown"),
    }

    # Add custom headers
    if webhook.headers:
        try:
            custom_headers = json.loads(webhook.headers)
            headers.update(custom_headers)
        except json.JSONDecodeError:
            pass

    # Add signature if secret is configured
    if webhook.secret:
        headers["X-Webhook-Signature"] = sign_payload(payload_json, webhook.secret)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                webhook.url,
                content=payload_json,
                headers=headers,
                timeout=timeout,
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)

            success = 200 <= response.status_code < 300
            return success, response.status_code, None, duration_ms

    except httpx.TimeoutException:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return False, None, "Request timed out", duration_ms

    except httpx.ConnectError as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return False, None, f"Connection error: {e}", duration_ms

    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return False, None, f"Unexpected error: {e}", duration_ms


def log_delivery(
    session,
    webhook: models.Webhook,
    event_type: str,
    payload: dict[str, Any],
    success: bool,
    status_code: int | None,
    error: str | None,
    duration_ms: int,
    response_body: str | None = None,
) -> models.WebhookDelivery:
    """Log a webhook delivery attempt."""
    delivery = models.WebhookDelivery(
        webhook_id=webhook.id,
        event_type=event_type,
        lab_id=payload.get("lab", {}).get("id"),
        job_id=payload.get("job", {}).get("id"),
        payload=json.dumps(payload),
        status_code=status_code,
        response_body=response_body[:1000] if response_body else None,
        error=error,
        duration_ms=duration_ms,
        success=success,
    )
    session.add(delivery)

    # Update webhook's last delivery info
    webhook.last_delivery_at = datetime.now(timezone.utc)
    webhook.last_delivery_status = "success" if success else "failed"
    webhook.last_delivery_error = error

    session.commit()
    return delivery


async def dispatch_webhook_event(
    event_type: str,
    lab_id: str | None = None,
    user_id: str | None = None,
    lab: models.Lab | None = None,
    job: models.Job | None = None,
    nodes: list[dict] | None = None,
    extra: dict | None = None,
) -> list[str]:
    """Dispatch a webhook event to all matching webhooks.

    Args:
        event_type: The event type (e.g., "lab.deploy_complete")
        lab_id: Lab ID to filter webhooks (optional)
        user_id: User ID whose webhooks to trigger (if lab not provided)
        lab: Lab model for payload (fetched if not provided)
        job: Job model for payload (optional)
        nodes: Node info for payload (optional)
        extra: Extra fields to include in payload

    Returns:
        List of webhook IDs that were triggered
    """
    triggered_webhooks = []

    with get_session() as session:
        try:
            # Get lab if not provided but lab_id is
            if lab_id and not lab:
                lab = session.get(models.Lab, lab_id)

            # Determine user_id from lab if not provided
            if not user_id and lab:
                user_id = lab.owner_id

            if not user_id:
                logger.warning(f"Cannot dispatch webhook {event_type}: no user_id")
                return []

            # Find matching webhooks
            # Match webhooks where:
            # 1. Owner matches user_id
            # 2. Webhook is enabled
            # 3. Event type is in webhook's events list
            # 4. Either webhook.lab_id is NULL (global) or matches the lab_id
            webhooks = (
                session.query(models.Webhook)
                .filter(
                    models.Webhook.owner_id == user_id,
                    models.Webhook.enabled == True,
                )
                .all()
            )

            # Filter by event type and lab scope
            matching_webhooks = []
            for webhook in webhooks:
                # Check event type
                try:
                    events = json.loads(webhook.events)
                    if event_type not in events:
                        continue
                except json.JSONDecodeError:
                    continue

                # Check lab scope
                if webhook.lab_id and lab_id and webhook.lab_id != lab_id:
                    continue

                matching_webhooks.append(webhook)

            if not matching_webhooks:
                logger.debug(f"No webhooks matched event {event_type} for user {user_id}")
                return []

            # Build payload
            payload = build_webhook_payload(
                event_type=event_type,
                lab=lab,
                job=job,
                nodes=nodes,
                extra=extra,
            )

            # Deliver to all matching webhooks concurrently
            async def deliver_and_log(webhook: models.Webhook):
                success, status_code, error, duration_ms = await deliver_webhook(
                    webhook, payload
                )
                log_delivery(
                    session,
                    webhook,
                    event_type,
                    payload,
                    success,
                    status_code,
                    error,
                    duration_ms,
                )
                return webhook.id

            tasks = [deliver_and_log(wh) for wh in matching_webhooks]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, str):
                    triggered_webhooks.append(result)
                elif isinstance(result, Exception):
                    logger.error(f"Webhook delivery error: {result}")

            logger.info(
                f"Dispatched {event_type} to {len(triggered_webhooks)} webhook(s) "
                f"for lab {lab_id}"
            )

        except Exception as e:
            logger.exception(f"Error dispatching webhook event {event_type}: {e}")

    return triggered_webhooks


async def test_webhook(webhook: models.Webhook) -> tuple[bool, int | None, str | None, int]:
    """Send a test event to a webhook.

    Returns:
        Tuple of (success, status_code, error_or_response, duration_ms)
    """
    payload = build_webhook_payload(
        event_type="test",
        extra={
            "message": "This is a test webhook delivery from Archetype",
            "webhook_id": webhook.id,
            "webhook_name": webhook.name,
        },
    )

    success, status_code, error, duration_ms = await deliver_webhook(webhook, payload)

    return success, status_code, error, duration_ms
