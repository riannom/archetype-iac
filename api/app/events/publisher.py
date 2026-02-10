"""Fire-and-forget publisher for cleanup events.

Follows the singleton Redis pattern used by broadcaster.py.
All publish calls log warnings on failure but never raise.
"""
from __future__ import annotations

import logging

import redis.asyncio as aioredis

from app.config import settings
from app.events.cleanup_events import CLEANUP_CHANNEL, CleanupEvent, CleanupEventType

logger = logging.getLogger(__name__)

_publisher_redis: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _publisher_redis
    if _publisher_redis is None:
        _publisher_redis = await aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _publisher_redis


async def publish_cleanup_event(event: CleanupEvent) -> None:
    """Publish a cleanup event to Redis. Fire-and-forget."""
    try:
        redis = await _get_redis()
        await redis.publish(CLEANUP_CHANNEL, event.to_json())
        logger.debug(f"Published cleanup event: {event.event_type.value} lab={event.lab_id}")
    except Exception as e:
        logger.warning(f"Failed to publish cleanup event {event.event_type.value}: {e}")


# ---- Convenience wrappers ----

async def emit_lab_deleted(lab_id: str) -> None:
    await publish_cleanup_event(CleanupEvent(
        event_type=CleanupEventType.LAB_DELETED, lab_id=lab_id,
    ))


async def emit_node_removed(lab_id: str, node_name: str, agent_id: str | None = None) -> None:
    await publish_cleanup_event(CleanupEvent(
        event_type=CleanupEventType.NODE_REMOVED,
        lab_id=lab_id, node_name=node_name, agent_id=agent_id,
    ))


async def emit_node_placement_changed(
    lab_id: str, node_name: str, agent_id: str | None = None, old_agent_id: str | None = None,
) -> None:
    await publish_cleanup_event(CleanupEvent(
        event_type=CleanupEventType.NODE_PLACEMENT_CHANGED,
        lab_id=lab_id, node_name=node_name, agent_id=agent_id, old_agent_id=old_agent_id,
    ))


async def emit_link_removed(lab_id: str) -> None:
    await publish_cleanup_event(CleanupEvent(
        event_type=CleanupEventType.LINK_REMOVED, lab_id=lab_id,
    ))


async def emit_deploy_finished(lab_id: str, agent_id: str | None = None, job_id: str | None = None) -> None:
    await publish_cleanup_event(CleanupEvent(
        event_type=CleanupEventType.DEPLOY_FINISHED,
        lab_id=lab_id, agent_id=agent_id, job_id=job_id,
    ))


async def emit_destroy_finished(lab_id: str, agent_id: str | None = None, job_id: str | None = None) -> None:
    await publish_cleanup_event(CleanupEvent(
        event_type=CleanupEventType.DESTROY_FINISHED,
        lab_id=lab_id, agent_id=agent_id, job_id=job_id,
    ))


async def emit_job_failed(lab_id: str, job_id: str | None = None, job_action: str | None = None) -> None:
    await publish_cleanup_event(CleanupEvent(
        event_type=CleanupEventType.JOB_FAILED,
        lab_id=lab_id, job_id=job_id, job_action=job_action,
    ))


async def emit_agent_offline(agent_id: str) -> None:
    await publish_cleanup_event(CleanupEvent(
        event_type=CleanupEventType.AGENT_OFFLINE, agent_id=agent_id,
    ))


async def emit_state_check_requested(lab_id: str) -> None:
    await publish_cleanup_event(CleanupEvent(
        event_type=CleanupEventType.STATE_CHECK_REQUESTED, lab_id=lab_id,
    ))


async def close_publisher() -> None:
    """Close the publisher Redis connection."""
    global _publisher_redis
    if _publisher_redis is not None:
        await _publisher_redis.close()
        _publisher_redis = None
