"""Event-driven cleanup handler.

Subscribes to the ``cleanup_events`` Redis channel and dispatches targeted
cleanup actions in response to state-change events.  This replaces aggressive
polling with immediate, precise cleanup while keeping periodic monitors as a
safety net.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time

import redis.asyncio as aioredis

from app import agent_client, models
from app.config import settings
from app.metrics import circuit_breaker_state
from app.db import get_session
from app.events.cleanup_events import CLEANUP_CHANNEL, CleanupEvent, CleanupEventType
from app.storage import lab_workspace
from app.tasks.cleanup_base import CleanupResult, CleanupRunner

logger = logging.getLogger(__name__)

_runner = CleanupRunner()

# Dirty flag — set when an event-driven cleanup succeeds so periodic monitors
# can optionally run an extra pass on wakeup.
_cleanup_dirty_event = asyncio.Event()


def is_cleanup_dirty() -> bool:
    return _cleanup_dirty_event.is_set()


def clear_cleanup_dirty() -> None:
    _cleanup_dirty_event.clear()


# ---------------------------------------------------------------------------
# Circuit breaker — prevents cascading failures in cleanup handlers
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """Track consecutive failures per handler type and skip when tripped.

    States:
    - Closed (normal): failures < max_failures, handler runs normally
    - Open (tripped): failures >= max_failures, handler is skipped
    - Half-open: cooldown expired, next call is allowed (resets on success)
    """

    def __init__(self, max_failures: int = 3, cooldown: float = 60.0):
        self.max_failures = max_failures
        self.cooldown = cooldown
        self._failures: dict[str, int] = {}
        self._last_failure_time: dict[str, float] = {}

    def is_open(self, handler_type: str) -> bool:
        """Return True if the circuit is open (handler should be skipped)."""
        failures = self._failures.get(handler_type, 0)
        if failures < self.max_failures:
            circuit_breaker_state.labels(handler_type=handler_type).set(0)
            return False
        # Check if cooldown has elapsed (half-open)
        last_time = self._last_failure_time.get(handler_type, 0.0)
        if time.monotonic() - last_time >= self.cooldown:
            circuit_breaker_state.labels(handler_type=handler_type).set(1)  # half-open
            return False  # Allow one attempt (half-open)
        circuit_breaker_state.labels(handler_type=handler_type).set(2)  # open
        return True

    def record_failure(self, handler_type: str) -> None:
        self._failures[handler_type] = self._failures.get(handler_type, 0) + 1
        self._last_failure_time[handler_type] = time.monotonic()
        if self._failures[handler_type] >= self.max_failures:
            circuit_breaker_state.labels(handler_type=handler_type).set(2)

    def record_success(self, handler_type: str) -> None:
        self._failures.pop(handler_type, None)
        self._last_failure_time.pop(handler_type, None)
        circuit_breaker_state.labels(handler_type=handler_type).set(0)


# ---------------------------------------------------------------------------
# Targeted cleanup functions
# ---------------------------------------------------------------------------

async def _cleanup_lab_workspace(lab_id: str) -> CleanupResult:
    """Remove the on-disk workspace directory for a deleted lab."""
    result = CleanupResult(task_name="cleanup_lab_workspace")
    ws = lab_workspace(lab_id)
    if ws.exists():
        try:
            shutil.rmtree(ws)
            result.deleted = 1
            result.details["path"] = str(ws)
        except Exception as e:
            result.errors.append(f"Failed to remove workspace {ws}: {e}")
    return result


async def _cleanup_lab_config_snapshots(lab_id: str) -> CleanupResult:
    """Bulk-delete ConfigSnapshot rows for a deleted lab."""
    result = CleanupResult(task_name="cleanup_lab_config_snapshots")
    with get_session() as session:
        count = (
            session.query(models.ConfigSnapshot)
            .filter(models.ConfigSnapshot.lab_id == lab_id)
            .delete()
        )
        session.commit()
        result.deleted = count
    return result


async def _cleanup_lab_placements(lab_id: str) -> CleanupResult:
    """Delete orphaned NodePlacement rows whose node no longer exists."""
    result = CleanupResult(task_name="cleanup_lab_placements")
    with get_session() as session:
        # Find placements whose node_name is no longer in the nodes table
        existing_names = {
            name for (name,) in
            session.query(models.Node.name).filter(models.Node.lab_id == lab_id).all()
        }
        placements = (
            session.query(models.NodePlacement)
            .filter(models.NodePlacement.lab_id == lab_id)
            .all()
        )
        deleted = 0
        for p in placements:
            if p.node_name not in existing_names:
                session.delete(p)
                deleted += 1
        if deleted:
            session.commit()
        result.deleted = deleted
    return result


async def _cleanup_recovered_vxlan_ports(lab_id: str) -> CleanupResult:
    """Remove recovered VXLAN ports that no longer match active tunnels."""
    result = CleanupResult(task_name="cleanup_recovered_vxlan_ports")
    with get_session() as session:
        try:
            active_tunnels = (
                session.query(models.VxlanTunnel)
                .filter(models.VxlanTunnel.status == "active")
                .all()
            )

            agent_valid_ports: dict[str, set[str]] = {}
            for tunnel in active_tunnels:
                link_state = session.get(models.LinkState, tunnel.link_state_id)
                if not link_state:
                    continue
                port_name = agent_client.compute_vxlan_port_name(
                    str(tunnel.lab_id), link_state.link_name
                )
                for aid in [tunnel.agent_a_id, tunnel.agent_b_id]:
                    if aid:
                        agent_valid_ports.setdefault(str(aid), set()).add(port_name)

            all_agents = session.query(models.Host).all()
            for agent in all_agents:
                if not agent_client.is_agent_online(agent):
                    continue
                valid = list(agent_valid_ports.get(str(agent.id), set()))
                try:
                    reconcile = await agent_client.reconcile_vxlan_ports_on_agent(
                        agent,
                        valid,
                        force=True,
                        confirm=True,
                        allow_empty=True,
                    )
                    removed = reconcile.get("removed_ports", [])
                    result.deleted += len(removed)
                except Exception as e:
                    result.errors.append(f"{agent.name}: {e}")
        except Exception as e:
            result.errors.append(str(e))
    return result


async def _cleanup_node_placement(lab_id: str, node_name: str) -> CleanupResult:
    """Delete the NodePlacement for a specific removed node."""
    result = CleanupResult(task_name="cleanup_node_placement")
    with get_session() as session:
        count = (
            session.query(models.NodePlacement)
            .filter(
                models.NodePlacement.lab_id == lab_id,
                models.NodePlacement.node_name == node_name,
            )
            .delete()
        )
        session.commit()
        result.deleted = count
    return result


async def _cleanup_agent_image_hosts(agent_id: str) -> CleanupResult:
    """Delete ImageHost records for an agent that went offline."""
    result = CleanupResult(task_name="cleanup_agent_image_hosts")
    with get_session() as session:
        count = (
            session.query(models.ImageHost)
            .filter(models.ImageHost.host_id == agent_id)
            .delete()
        )
        session.commit()
        result.deleted = count
    return result


async def _cleanup_agent_workspaces(lab_id: str) -> None:
    """Tell all online agents to remove workspace for a deleted lab."""
    with get_session() as session:
        agents = (
            session.query(models.Host)
            .filter(models.Host.status == "online")
            .all()
        )
        for agent in agents:
            try:
                await agent_client.cleanup_agent_workspace(agent, lab_id)
            except Exception as e:
                logger.warning(
                    f"Failed to clean agent workspace for lab {lab_id} "
                    f"on {agent.name}: {e}"
                )


# ---------------------------------------------------------------------------
# Event handler
# ---------------------------------------------------------------------------

class CleanupEventHandler:
    """Subscribe to cleanup events and dispatch targeted cleanup.

    Uses a bounded asyncio.Queue between the Redis subscriber and event
    processing to provide backpressure.  When the queue is full, new events
    are dropped with a warning — periodic cleanup monitors act as the safety
    net for any lost events.
    """

    _QUEUE_MAXSIZE = 100
    _QUEUE_WARN_DEPTH = 50

    _dispatch: dict[CleanupEventType, str] = {
        CleanupEventType.LAB_DELETED: "_handle_lab_deleted",
        CleanupEventType.NODE_REMOVED: "_handle_node_removed",
        CleanupEventType.NODE_PLACEMENT_CHANGED: "_handle_node_placement_changed",
        CleanupEventType.LINK_REMOVED: "_handle_link_removed",
        CleanupEventType.AGENT_OFFLINE: "_handle_agent_offline",
        CleanupEventType.DEPLOY_FINISHED: "_handle_deploy_finished",
        CleanupEventType.DESTROY_FINISHED: "_handle_destroy_finished",
        CleanupEventType.JOB_COMPLETED: "_handle_job_completed",
        CleanupEventType.JOB_FAILED: "_handle_job_failed",
        CleanupEventType.STATE_CHECK_REQUESTED: "_handle_state_check_requested",
    }

    def __init__(self) -> None:
        self.circuit_breaker = CircuitBreaker()
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self._QUEUE_MAXSIZE)

    async def run(self) -> None:
        """Connect to Redis and process events forever.

        Runs two concurrent tasks:
        - _subscriber_loop: reads from Redis pub/sub and enqueues raw messages
        - _processor_loop: dequeues and processes events sequentially
        """
        redis = await aioredis.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True,
        )
        pubsub = redis.pubsub()
        await pubsub.subscribe(CLEANUP_CHANNEL)
        logger.info(f"Cleanup event handler subscribed to {CLEANUP_CHANNEL}")

        try:
            await asyncio.gather(
                self._subscriber_loop(pubsub),
                self._processor_loop(),
            )
        finally:
            await pubsub.unsubscribe(CLEANUP_CHANNEL)
            await pubsub.close()
            await redis.close()

    async def _subscriber_loop(self, pubsub) -> None:
        """Read from Redis pub/sub and enqueue raw messages."""
        while True:
            try:
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0,
                )
                if msg is not None and msg["type"] == "message":
                    try:
                        self._queue.put_nowait(msg["data"])
                    except asyncio.QueueFull:
                        logger.warning(
                            "Cleanup event queue full (%d), dropping event "
                            "(periodic cleanup is safety net)",
                            self._QUEUE_MAXSIZE,
                        )
                    else:
                        depth = self._queue.qsize()
                        if depth >= self._QUEUE_WARN_DEPTH:
                            logger.warning(
                                "Cleanup event queue depth high: %d/%d",
                                depth, self._QUEUE_MAXSIZE,
                            )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Error receiving cleanup event: {e}")
                await asyncio.sleep(0.5)

    async def _processor_loop(self) -> None:
        """Dequeue and process events sequentially."""
        while True:
            try:
                raw = await self._queue.get()
                await self._process_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Error in cleanup event processor: {e}")

    async def _process_message(self, raw: str) -> None:
        try:
            event = CleanupEvent.from_json(raw)
        except Exception as e:
            logger.warning(f"Invalid cleanup event payload: {e}")
            return

        method_name = self._dispatch.get(event.event_type)
        if not method_name:
            logger.warning(f"No handler for cleanup event type: {event.event_type}")
            return

        handler_type = event.event_type.value

        # Circuit breaker: skip if handler has too many consecutive failures
        if self.circuit_breaker.is_open(handler_type):
            logger.warning(
                f"Circuit breaker open for {handler_type}, skipping event"
            )
            return

        handler = getattr(self, method_name)
        try:
            await handler(event)
            self.circuit_breaker.record_success(handler_type)
            _cleanup_dirty_event.set()
        except Exception:
            # Backoff before retry
            await asyncio.sleep(1.0)
            # Single retry on transient failure
            try:
                await handler(event)
                self.circuit_breaker.record_success(handler_type)
                _cleanup_dirty_event.set()
            except Exception as e2:
                self.circuit_breaker.record_failure(handler_type)
                logger.error(
                    f"Cleanup handler {method_name} failed after retry for "
                    f"{event.event_type.value}: {e2}"
                )

    # ---- Handler methods ----

    async def _handle_lab_deleted(self, event: CleanupEvent) -> None:
        lab_id = event.lab_id
        if not lab_id:
            return
        logger.info(f"Handling LAB_DELETED cleanup for lab {lab_id}")
        await _runner.run_task(_cleanup_lab_workspace, lab_id)
        await _runner.run_task(_cleanup_lab_config_snapshots, lab_id)
        await _runner.run_task(_cleanup_lab_placements, lab_id)
        await _runner.run_task(_cleanup_recovered_vxlan_ports, lab_id)
        await _cleanup_agent_workspaces(lab_id)

    async def _handle_node_removed(self, event: CleanupEvent) -> None:
        if not event.lab_id or not event.node_name:
            return
        logger.info(f"Handling NODE_REMOVED cleanup: {event.node_name} in lab {event.lab_id}")
        await _runner.run_task(_cleanup_node_placement, event.lab_id, event.node_name)

    async def _handle_node_placement_changed(self, event: CleanupEvent) -> None:
        logger.info(
            f"Node placement changed: {event.node_name} in lab {event.lab_id} "
            f"(old_agent={event.old_agent_id} -> new_agent={event.agent_id})"
        )

    async def _handle_link_removed(self, event: CleanupEvent) -> None:
        logger.debug(f"Link removed in lab {event.lab_id} (teardown handled by live_links)")

    async def _handle_agent_offline(self, event: CleanupEvent) -> None:
        if not event.agent_id:
            return
        logger.info(f"Handling AGENT_OFFLINE cleanup for agent {event.agent_id}")
        await _runner.run_task(_cleanup_agent_image_hosts, event.agent_id)

    async def _handle_deploy_finished(self, event: CleanupEvent) -> None:
        logger.debug(f"Deploy finished for lab {event.lab_id}")
        if event.lab_id:
            await self._trigger_lab_state_check(event.lab_id)

    async def _handle_destroy_finished(self, event: CleanupEvent) -> None:
        if not event.lab_id:
            return
        logger.info(f"Handling DESTROY_FINISHED cleanup for lab {event.lab_id}")
        await _runner.run_task(_cleanup_lab_placements, event.lab_id)
        await _runner.run_task(_cleanup_recovered_vxlan_ports, event.lab_id)
        await self._trigger_lab_state_check(event.lab_id)

    async def _handle_job_completed(self, event: CleanupEvent) -> None:
        logger.debug(f"Job completed: {event.job_id} for lab {event.lab_id}")

    async def _handle_job_failed(self, event: CleanupEvent) -> None:
        logger.debug(f"Job failed: {event.job_id} ({event.job_action}) for lab {event.lab_id}")
        if event.lab_id:
            await self._trigger_lab_state_check(event.lab_id)

    async def _handle_state_check_requested(self, event: CleanupEvent) -> None:
        """Run reconciliation + enforcement for a lab after a lifecycle event."""
        if not event.lab_id:
            return
        logger.info(f"Handling STATE_CHECK_REQUESTED for lab {event.lab_id}")
        try:
            from app.tasks.reconciliation import refresh_states_from_agents
            from app.tasks.state_enforcement import enforce_lab_states

            await refresh_states_from_agents()
            await enforce_lab_states()
        except Exception as e:
            logger.warning(f"State check failed for lab {event.lab_id}: {e}")

    async def _trigger_lab_state_check(self, lab_id: str) -> None:
        """Emit a STATE_CHECK_REQUESTED event for a lab."""
        try:
            from app.events.publisher import emit_state_check_requested
            await emit_state_check_requested(lab_id)
        except Exception as e:
            logger.debug(f"Failed to emit state check for lab {lab_id}: {e}")


# ---------------------------------------------------------------------------
# Monitor entry point
# ---------------------------------------------------------------------------

async def cleanup_event_monitor() -> None:
    """Background task — runs the cleanup event handler loop."""
    handler = CleanupEventHandler()
    logger.info("Cleanup event monitor starting")
    try:
        await handler.run()
    except asyncio.CancelledError:
        logger.info("Cleanup event monitor stopped")
    except Exception as e:
        logger.error(f"Cleanup event monitor crashed: {e}")
