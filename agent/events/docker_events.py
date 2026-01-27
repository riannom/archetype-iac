"""Docker Events API listener for real-time container state updates.

This module implements a listener that watches Docker's Events API for
container state changes, filtering for containerlab-managed containers
and forwarding events to the controller.

Docker Events API provides real-time notifications for:
- Container start/stop/die/kill/pause/unpause
- Container health status changes
- Container create/destroy

We filter for containers with the "clab-node-name" label, which identifies
them as managed by containerlab.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import docker
from docker.models.containers import Container

from agent.events.base import EventCallback, NodeEvent, NodeEventListener, NodeEventType

logger = logging.getLogger(__name__)

# Docker event actions mapped to our event types
DOCKER_ACTION_MAP = {
    "start": NodeEventType.STARTED,
    "stop": NodeEventType.STOPPED,
    "die": NodeEventType.DIED,
    "kill": NodeEventType.DIED,
    "oom": NodeEventType.DIED,
    "create": NodeEventType.CREATING,
    "destroy": NodeEventType.DESTROYING,
}


class DockerEventListener(NodeEventListener):
    """Listens to Docker Events API for container state changes.

    This listener:
    1. Connects to the Docker daemon
    2. Subscribes to container events
    3. Filters for containerlab-managed containers (clab-* prefix)
    4. Converts Docker events to NodeEvent objects
    5. Invokes the callback for each relevant event

    The listener handles reconnection automatically if the Docker connection
    is lost, with exponential backoff.
    """

    def __init__(self):
        self._client: docker.DockerClient | None = None
        self._running = False
        self._stop_event = asyncio.Event()
        self._reconnect_delay = 1.0  # Start with 1 second
        self._max_reconnect_delay = 60.0  # Max 60 seconds

    async def start(self, callback: EventCallback) -> None:
        """Start listening for Docker events.

        Args:
            callback: Async function to call with each NodeEvent
        """
        self._running = True
        self._stop_event.clear()

        while self._running and not self._stop_event.is_set():
            try:
                # Connect to Docker
                self._client = docker.from_env()
                logger.info("Docker event listener connected")
                self._reconnect_delay = 1.0  # Reset on successful connection

                # Listen for events in a thread (Docker SDK is sync)
                await self._listen_loop(callback)

            except docker.errors.DockerException as e:
                logger.error(f"Docker connection error: {e}")
                if self._running:
                    logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                    await asyncio.sleep(self._reconnect_delay)
                    self._reconnect_delay = min(
                        self._reconnect_delay * 2, self._max_reconnect_delay
                    )
            except asyncio.CancelledError:
                logger.info("Docker event listener cancelled")
                break
            except Exception as e:
                logger.error(f"Unexpected error in Docker event listener: {e}")
                if self._running:
                    await asyncio.sleep(self._reconnect_delay)

        self._running = False
        logger.info("Docker event listener stopped")

    async def _listen_loop(self, callback: EventCallback) -> None:
        """Main event listening loop.

        Runs Docker events in a thread pool to avoid blocking the event loop.
        """
        loop = asyncio.get_event_loop()

        # Get events generator - filters for container events
        events = self._client.events(
            decode=True,
            filters={
                "type": "container",
                "event": list(DOCKER_ACTION_MAP.keys()),
            },
        )

        try:
            while self._running and not self._stop_event.is_set():
                # Check for event with timeout to allow checking stop flag
                event = await asyncio.wait_for(
                    loop.run_in_executor(None, self._get_next_event, events),
                    timeout=1.0,
                )

                if event is None:
                    continue

                # Process the event
                node_event = self._parse_event(event)
                if node_event:
                    try:
                        await callback(node_event)
                    except Exception as e:
                        logger.error(f"Error in event callback: {e}")

        except asyncio.TimeoutError:
            pass  # Normal timeout, continue loop
        except StopIteration:
            logger.warning("Docker events stream ended")
        finally:
            events.close()

    def _get_next_event(self, events) -> dict | None:
        """Get next event from generator (runs in thread pool)."""
        try:
            return next(events)
        except StopIteration:
            return None

    def _parse_event(self, event: dict) -> NodeEvent | None:
        """Parse a Docker event into a NodeEvent.

        Filters for containerlab-managed containers and extracts relevant info.

        Args:
            event: Raw Docker event dict

        Returns:
            NodeEvent if this is a managed container event, None otherwise
        """
        # Only process container events
        if event.get("Type") != "container":
            return None

        action = event.get("Action", "")
        # Handle compound actions like "exec_start: /bin/sh"
        action = action.split(":")[0]

        if action not in DOCKER_ACTION_MAP:
            return None

        # Get container attributes
        actor = event.get("Actor", {})
        attributes = actor.get("Attributes", {})

        # Filter for containerlab-managed containers
        # They have labels: clab-node-name, containerlab
        if "clab-node-name" not in attributes:
            return None

        node_name = attributes.get("clab-node-name", "")
        lab_prefix = attributes.get("containerlab", "")

        if not node_name or not lab_prefix:
            return None

        container_id = actor.get("ID", "")
        container_name = attributes.get("name", "")

        # Convert timestamp (nanoseconds since epoch)
        timestamp_ns = event.get("timeNano", 0)
        if timestamp_ns:
            timestamp = datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)
        else:
            timestamp = datetime.now(timezone.utc)

        event_type = DOCKER_ACTION_MAP[action]

        # Get current status if available
        status = action
        if action == "die":
            exit_code = attributes.get("exitCode", "unknown")
            status = f"exited (code {exit_code})"

        logger.debug(
            f"Docker event: {action} for {container_name} "
            f"(lab={lab_prefix}, node={node_name})"
        )

        return NodeEvent(
            lab_id=lab_prefix,
            node_name=node_name,
            container_id=container_id,
            event_type=event_type,
            timestamp=timestamp,
            status=status,
            attributes={
                "container_name": container_name,
                "image": attributes.get("image", ""),
                "node_kind": attributes.get("clab-node-kind", ""),
                "exit_code": attributes.get("exitCode"),
            },
        )

    async def stop(self) -> None:
        """Stop listening for events."""
        logger.info("Stopping Docker event listener...")
        self._running = False
        self._stop_event.set()

        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def is_running(self) -> bool:
        """Check if the listener is currently running."""
        return self._running
