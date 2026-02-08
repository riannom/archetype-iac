"""Real-time state broadcasting via Redis pub/sub.

This module provides a centralized mechanism for broadcasting state changes
to all connected WebSocket clients. It uses Redis pub/sub for distribution
across multiple API instances in a scaled deployment.

Usage:
    from app.services.broadcaster import get_broadcaster

    broadcaster = get_broadcaster()
    await broadcaster.publish_node_state(lab_id, node_data)
    await broadcaster.publish_link_state(lab_id, link_data)

The WebSocket endpoint subscribes to the lab's channel and forwards
messages to connected clients.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator, Literal

import redis.asyncio as aioredis

from app.config import settings
from app.utils.timeouts import REDIS_OPERATION_TIMEOUT

logger = logging.getLogger(__name__)

# Singleton broadcaster instance
_broadcaster: "StateBroadcaster | None" = None


class StateBroadcaster:
    """Broadcasts state changes via Redis pub/sub.

    This class manages publishing state updates to Redis channels and
    provides an async generator for subscribing to updates for a specific lab.
    """

    def __init__(self, redis_url: str):
        """Initialize the broadcaster with a Redis connection.

        Args:
            redis_url: Redis connection URL
        """
        self._redis_url = redis_url
        self._redis: aioredis.Redis | None = None
        self._pubsub_connections: dict[str, aioredis.client.PubSub] = {}

    async def _get_redis(self) -> aioredis.Redis:
        """Get or create Redis connection."""
        if self._redis is None:
            self._redis = await aioredis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    def _channel_name(self, lab_id: str) -> str:
        """Get the Redis channel name for a lab."""
        return f"lab_state:{lab_id}"

    async def _publish(self, channel: str, data: str) -> int:
        """Publish to Redis with timeout protection."""
        r = await self._get_redis()
        return await asyncio.wait_for(
            r.publish(channel, data),
            timeout=REDIS_OPERATION_TIMEOUT,
        )

    async def publish_node_state(
        self,
        lab_id: str,
        node_id: str,
        node_name: str,
        desired_state: str,
        actual_state: str,
        is_ready: bool = False,
        error_message: str | None = None,
        host_id: str | None = None,
        host_name: str | None = None,
        image_sync_status: str | None = None,
        image_sync_message: str | None = None,
        will_retry: bool = False,
        display_state: str | None = None,
        enforcement_attempts: int = 0,
        max_enforcement_attempts: int = 0,
    ) -> int:
        """Publish a node state change event.

        Args:
            lab_id: Lab identifier
            node_id: Node GUI ID
            node_name: Node container name
            desired_state: Desired state (running/stopped)
            actual_state: Actual state (running/stopped/pending/etc.)
            is_ready: Whether node has completed boot
            error_message: Error message if any
            host_id: Agent host ID
            host_name: Agent host name
            image_sync_status: Image sync status (checking/syncing/synced/failed)
            image_sync_message: Image sync progress or error message
            will_retry: Whether enforcement will automatically retry after error
            display_state: Server-computed display state (running/starting/stopping/stopped/error)
            enforcement_attempts: Number of enforcement attempts so far
            max_enforcement_attempts: Maximum enforcement attempts before giving up

        Returns:
            Number of subscribers that received the message
        """
        try:
            # Auto-compute display_state if not provided
            if display_state is None:
                from app.services.state_machine import NodeStateMachine
                display_state = NodeStateMachine.compute_display_state(actual_state, desired_state)

            message = {
                "type": "node_state",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": {
                    "node_id": node_id,
                    "node_name": node_name,
                    "desired_state": desired_state,
                    "actual_state": actual_state,
                    "is_ready": is_ready,
                    "error_message": error_message,
                    "host_id": host_id,
                    "host_name": host_name,
                    "image_sync_status": image_sync_status,
                    "image_sync_message": image_sync_message,
                    "will_retry": will_retry,
                    "display_state": display_state,
                    "enforcement_attempts": enforcement_attempts,
                    "max_enforcement_attempts": max_enforcement_attempts,
                },
            }
            channel = self._channel_name(lab_id)
            count = await self._publish(channel, json.dumps(message))
            logger.debug(f"Published node state to {channel}: {node_name} -> {actual_state} ({count} subscribers)")
            return count
        except Exception as e:
            logger.warning(f"Failed to publish node state for {node_name}: {e}")
            return 0

    async def publish_link_state(
        self,
        lab_id: str,
        link_name: str,
        desired_state: str,
        actual_state: str,
        source_node: str,
        target_node: str,
        error_message: str | None = None,
    ) -> int:
        """Publish a link state change event.

        Args:
            lab_id: Lab identifier
            link_name: Link name
            desired_state: Desired state (up/down)
            actual_state: Actual state (up/down/pending/error)
            source_node: Source node name
            target_node: Target node name
            error_message: Error message if any

        Returns:
            Number of subscribers that received the message
        """
        try:
            message = {
                "type": "link_state",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": {
                    "link_name": link_name,
                    "desired_state": desired_state,
                    "actual_state": actual_state,
                    "source_node": source_node,
                    "target_node": target_node,
                    "error_message": error_message,
                },
            }
            channel = self._channel_name(lab_id)
            count = await self._publish(channel, json.dumps(message))
            logger.debug(f"Published link state to {channel}: {link_name} -> {actual_state}")
            return count
        except Exception as e:
            logger.warning(f"Failed to publish link state for {link_name}: {e}")
            return 0

    async def publish_lab_state(
        self,
        lab_id: str,
        state: str,
        error: str | None = None,
    ) -> int:
        """Publish a lab-level state change event.

        Args:
            lab_id: Lab identifier
            state: Lab state (running/stopped/starting/stopping/error)
            error: Error message if any

        Returns:
            Number of subscribers that received the message
        """
        try:
            message = {
                "type": "lab_state",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": {
                    "lab_id": lab_id,
                    "state": state,
                    "error": error,
                },
            }
            channel = self._channel_name(lab_id)
            count = await self._publish(channel, json.dumps(message))
            logger.debug(f"Published lab state to {channel}: {state}")
            return count
        except Exception as e:
            logger.warning(f"Failed to publish lab state for {lab_id}: {e}")
            return 0

    async def publish_job_progress(
        self,
        lab_id: str,
        job_id: str,
        action: str,
        status: str,
        progress_message: str | None = None,
        error_message: str | None = None,
    ) -> int:
        """Publish a job progress update.

        Args:
            lab_id: Lab identifier
            job_id: Job identifier
            action: Job action (up/down/sync/node:start:name)
            status: Job status (queued/running/completed/failed)
            progress_message: Progress message if any
            error_message: Error message if any

        Returns:
            Number of subscribers that received the message
        """
        try:
            message = {
                "type": "job_progress",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": {
                    "job_id": job_id,
                    "action": action,
                    "status": status,
                    "progress_message": progress_message,
                    "error_message": error_message,
                },
            }
            channel = self._channel_name(lab_id)
            count = await self._publish(channel, json.dumps(message))
            logger.debug(f"Published job progress to {channel}: {job_id} -> {status}")
            return count
        except Exception as e:
            logger.warning(f"Failed to publish job progress for {job_id}: {e}")
            return 0

    async def subscribe(self, lab_id: str) -> AsyncGenerator[dict, None]:
        """Subscribe to state updates for a lab.

        This is an async generator that yields state change messages
        as they are published to the lab's channel.

        Args:
            lab_id: Lab identifier to subscribe to

        Yields:
            Parsed message dicts with type, timestamp, and data fields
        """
        redis = await self._get_redis()
        pubsub = redis.pubsub()
        channel = self._channel_name(lab_id)

        try:
            await pubsub.subscribe(channel)
            logger.info(f"Subscribed to channel {channel}")

            while True:
                try:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=1.0,
                    )
                    if message is not None and message["type"] == "message":
                        try:
                            data = json.loads(message["data"])
                            yield data
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON in message: {message['data']}")
                except asyncio.CancelledError:
                    logger.info(f"Subscription cancelled for {channel}")
                    break
                except Exception as e:
                    logger.warning(f"Error receiving message from {channel}: {e}")
                    await asyncio.sleep(0.1)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
            logger.info(f"Unsubscribed from channel {channel}")

    async def close(self) -> None:
        """Close the Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None


def get_broadcaster() -> StateBroadcaster:
    """Get the singleton broadcaster instance.

    Returns:
        The global StateBroadcaster instance
    """
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = StateBroadcaster(settings.redis_url)
    return _broadcaster


async def broadcast_node_state_change(
    lab_id: str,
    node_id: str,
    node_name: str,
    desired_state: str,
    actual_state: str,
    is_ready: bool = False,
    error_message: str | None = None,
    host_id: str | None = None,
    host_name: str | None = None,
    image_sync_status: str | None = None,
    image_sync_message: str | None = None,
    will_retry: bool = False,
    display_state: str | None = None,
    enforcement_attempts: int = 0,
    max_enforcement_attempts: int = 0,
) -> None:
    """Convenience function to broadcast a node state change.

    This is a fire-and-forget wrapper around the broadcaster.
    """
    broadcaster = get_broadcaster()
    await broadcaster.publish_node_state(
        lab_id=lab_id,
        node_id=node_id,
        node_name=node_name,
        desired_state=desired_state,
        actual_state=actual_state,
        is_ready=is_ready,
        error_message=error_message,
        host_id=host_id,
        host_name=host_name,
        image_sync_status=image_sync_status,
        image_sync_message=image_sync_message,
        will_retry=will_retry,
        display_state=display_state,
        enforcement_attempts=enforcement_attempts,
        max_enforcement_attempts=max_enforcement_attempts,
    )


async def broadcast_link_state_change(
    lab_id: str,
    link_name: str,
    desired_state: str,
    actual_state: str,
    source_node: str,
    target_node: str,
    error_message: str | None = None,
) -> None:
    """Convenience function to broadcast a link state change.

    This is a fire-and-forget wrapper around the broadcaster.
    """
    broadcaster = get_broadcaster()
    await broadcaster.publish_link_state(
        lab_id=lab_id,
        link_name=link_name,
        desired_state=desired_state,
        actual_state=actual_state,
        source_node=source_node,
        target_node=target_node,
        error_message=error_message,
    )
