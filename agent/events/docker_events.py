"""Docker Events API listener for real-time container state updates.

This module implements a listener that watches Docker's Events API for
container state changes, filtering for Archetype-managed containers
and forwarding events to the controller.

Docker Events API provides real-time notifications for:
- Container start/stop/die/kill/pause/unpause
- Container health status changes
- Container create/destroy

We filter for containers with the "archetype.node_name" label to identify
managed containers.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone

import docker

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
    3. Filters for Archetype-managed containers (archetype.* labels)
    4. Converts Docker events to NodeEvent objects
    5. Invokes the callback for each relevant event

    The listener handles reconnection automatically if the Docker connection
    is lost, with exponential backoff.
    """

    def __init__(self):
        self._client: docker.DockerClient | None = None
        self._running = False
        # Lazily initialized in start() to avoid binding asyncio primitives to a
        # non-running (or already closed) loop during import/unit tests.
        self._stop_event: asyncio.Event | None = None
        self._thread_stop = threading.Event()
        self._async_queue: asyncio.Queue | None = None
        self._reader_thread: threading.Thread | None = None
        self._reconnect_delay = 1.0  # Start with 1 second
        self._max_reconnect_delay = 60.0  # Max 60 seconds

    async def start(self, callback: EventCallback) -> None:
        """Start listening for Docker events.

        Args:
            callback: Async function to call with each NodeEvent
        """
        self._running = True
        # Create a fresh stop event bound to the currently running loop.
        # This avoids "Event loop is closed" issues when the listener is
        # instantiated in synchronous code (e.g. tests) and later started.
        self._stop_event = asyncio.Event()
        self._stop_event.clear()
        self._thread_stop.clear()

        while self._running and self._stop_event is not None and not self._stop_event.is_set():
            try:
                # Connect to Docker
                self._client = docker.from_env()
                logger.info("Docker event listener connected")
                self._reconnect_delay = 1.0  # Reset on successful connection

                # Listen for events using a background thread
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

    def _event_reader_thread(self, events, loop: asyncio.AbstractEventLoop) -> None:
        """Background thread that reads Docker events and pushes to async queue.

        Uses call_soon_threadsafe to put events on the asyncio.Queue without
        blocking any thread pool threads.
        """
        try:
            for event in events:
                if self._thread_stop.is_set():
                    break
                loop.call_soon_threadsafe(self._async_queue.put_nowait, event)
        except Exception as e:
            if not self._thread_stop.is_set():
                loop.call_soon_threadsafe(self._async_queue.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(self._async_queue.put_nowait, None)

    async def _listen_loop(self, callback: EventCallback) -> None:
        """Main event listening loop.

        Uses a background thread to read Docker events (blocking) and
        an asyncio.Queue for zero-thread-pool event delivery to the
        async consumer.
        """
        # Get events generator - filters for container events
        events = self._client.events(
            decode=True,
            filters={
                "type": "container",
                "event": list(DOCKER_ACTION_MAP.keys()),
            },
        )

        # Start background thread to read events
        self._thread_stop.clear()
        self._async_queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        self._reader_thread = threading.Thread(
            target=self._event_reader_thread,
            args=(events, loop),
            daemon=True,
        )
        self._reader_thread.start()

        try:
            while self._running and not self._stop_event.is_set():
                # Wait for event from async queue (no thread pool thread consumed)
                try:
                    event = await asyncio.wait_for(
                        self._async_queue.get(), timeout=2.0
                    )
                except asyncio.TimeoutError:
                    # Normal timeout, continue loop to check stop flag
                    continue

                # Check for end of stream or error
                if event is None:
                    logger.warning("Docker events stream ended")
                    break
                if isinstance(event, Exception):
                    raise event

                # Process the event
                node_event = self._parse_event(event)
                if node_event:
                    try:
                        await callback(node_event)
                    except Exception as e:
                        logger.error(f"Error in event callback: {e}")

        finally:
            # Stop the reader thread
            self._thread_stop.set()
            events.close()
            if self._reader_thread and self._reader_thread.is_alive():
                self._reader_thread.join(timeout=2.0)

    def _parse_event(self, event: dict) -> NodeEvent | None:
        """Parse a Docker event into a NodeEvent.

        Filters for Archetype-managed containers and extracts relevant info.

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

        # Filter for Archetype-managed containers
        if "archetype.node_name" not in attributes:
            return None

        node_name = attributes.get("archetype.node_name", "")
        lab_prefix = attributes.get("archetype.lab_id", "")
        node_kind = attributes.get("archetype.node_kind", "")
        display_name = attributes.get("archetype.node_display_name")

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

        # Format log name with display name if available
        log_name = f"{display_name}({node_name})" if display_name and display_name != node_name else node_name
        logger.debug(
            f"Docker event: {action} for {log_name} "
            f"(lab={lab_prefix})"
        )

        return NodeEvent(
            lab_id=lab_prefix,
            node_name=node_name,
            container_id=container_id,
            event_type=event_type,
            timestamp=timestamp,
            status=status,
            display_name=display_name,
            attributes={
                "container_name": container_name,
                "image": attributes.get("image", ""),
                "node_kind": node_kind,
                "exit_code": attributes.get("exitCode"),
            },
        )

    async def stop(self) -> None:
        """Stop listening for events."""
        logger.info("Stopping Docker event listener...")
        self._running = False
        if self._stop_event is not None:
            self._stop_event.set()
        self._thread_stop.set()

        # Wait for reader thread to finish
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)

        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def is_running(self) -> bool:
        """Check if the listener is currently running."""
        return self._running
