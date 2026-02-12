"""WebSocket endpoint for real-time lab state updates.

This module provides a WebSocket endpoint that clients can connect to
for receiving real-time state updates for a specific lab. It replaces
polling with push-based updates for better user experience.

The endpoint subscribes to Redis pub/sub channels and forwards state
change messages to connected clients. Multiple clients can connect
to the same lab and all will receive updates.

Usage:
    WebSocket connect to: /ws/labs/{lab_id}/state
    Messages are JSON objects with:
    - type: "node_state" | "link_state" | "lab_state" | "job_progress"
    - timestamp: ISO timestamp
    - data: type-specific payload
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import db, models
from app.config import settings
from app.services.broadcaster import get_broadcaster
from app.services.state_machine import NodeStateMachine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["state_ws"])


class ConnectionManager:
    """Manages WebSocket connections per lab.

    Tracks active connections and provides methods for broadcasting
    to all clients connected to a specific lab.
    """

    def __init__(self):
        # lab_id -> set of WebSocket connections
        self.active_connections: dict[str, set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, lab_id: str) -> None:
        """Accept a new WebSocket connection for a lab."""
        await websocket.accept()
        if lab_id not in self.active_connections:
            self.active_connections[lab_id] = set()
        self.active_connections[lab_id].add(websocket)
        logger.info(f"WebSocket connected for lab {lab_id} (total: {len(self.active_connections[lab_id])})")

    def disconnect(self, websocket: WebSocket, lab_id: str) -> None:
        """Remove a WebSocket connection."""
        if lab_id in self.active_connections:
            self.active_connections[lab_id].discard(websocket)
            if not self.active_connections[lab_id]:
                del self.active_connections[lab_id]
            logger.info(f"WebSocket disconnected for lab {lab_id}")

    async def send_personal_message(self, message: dict, websocket: WebSocket) -> None:
        """Send a message to a specific client."""
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.warning(f"Failed to send message to client: {e}")

    async def broadcast_to_lab(self, lab_id: str, message: dict) -> None:
        """Broadcast a message to all clients connected to a lab."""
        if lab_id not in self.active_connections:
            return

        disconnected = []
        for connection in self.active_connections[lab_id]:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)

        # Clean up disconnected clients
        for conn in disconnected:
            self.active_connections[lab_id].discard(conn)


# Global connection manager
manager = ConnectionManager()


async def _subscribe_and_forward(websocket: WebSocket, lab_id: str) -> None:
    """Subscribe to Redis pub/sub and forward messages to WebSocket client.

    This runs in a background task and forwards all state change messages
    from Redis to the connected WebSocket client.
    """
    broadcaster = get_broadcaster()

    try:
        async for message in broadcaster.subscribe(lab_id):
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.debug(f"Failed to send to WebSocket, likely disconnected: {e}")
                break
    except asyncio.CancelledError:
        logger.debug(f"Subscription task cancelled for lab {lab_id}")
    except Exception as e:
        logger.warning(f"Error in subscription loop for lab {lab_id}: {e}")


async def _send_initial_state(websocket: WebSocket, lab_id: str) -> None:
    """Send initial state snapshot when client connects.

    Queries all data from the database, releases the session, then sends
    messages over WebSocket. This prevents holding a DB connection during I/O.
    """
    try:
        # Phase 1: Query all data and build messages (session held briefly)
        messages = []
        with db.get_session() as database:
            lab = database.get(models.Lab, lab_id)
            if not lab:
                messages.append({
                    "type": "error",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": {"message": f"Lab {lab_id} not found"},
                })
                # Release session, then send error
            else:
                messages.append({
                    "type": "lab_state",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": {
                        "lab_id": lab_id,
                        "state": lab.state,
                        "error": lab.state_error,
                    },
                })

                # Get node states
                node_states = (
                    database.query(models.NodeState)
                    .filter(models.NodeState.lab_id == lab_id)
                    .all()
                )

                # Get host placements for host info
                placements = (
                    database.query(models.NodePlacement)
                    .filter(models.NodePlacement.lab_id == lab_id)
                    .all()
                )
                placement_by_node = {p.node_name: p.host_id for p in placements}

                # Get host names
                host_ids = set(placement_by_node.values())
                if lab.agent_id:
                    host_ids.add(lab.agent_id)
                hosts = {}
                if host_ids:
                    host_records = (
                        database.query(models.Host)
                        .filter(models.Host.id.in_(host_ids))
                        .all()
                    )
                    hosts = {h.id: h.name for h in host_records}

                # Build node data
                nodes_data = []
                for ns in node_states:
                    host_id = placement_by_node.get(ns.node_name) or lab.agent_id
                    will_retry = (
                        ns.actual_state == "error"
                        and ns.enforcement_attempts < settings.state_enforcement_max_retries
                        and ns.enforcement_failed_at is None
                    )
                    nodes_data.append({
                        "node_id": ns.node_id,
                        "node_name": ns.node_name,
                        "desired_state": ns.desired_state,
                        "actual_state": ns.actual_state,
                        "is_ready": ns.is_ready,
                        "error_message": ns.error_message,
                        "host_id": host_id,
                        "host_name": hosts.get(host_id) if host_id else None,
                        "image_sync_status": ns.image_sync_status,
                        "image_sync_message": ns.image_sync_message,
                        "will_retry": will_retry,
                        "display_state": NodeStateMachine.compute_display_state(
                            ns.actual_state, ns.desired_state
                        ),
                        "enforcement_attempts": ns.enforcement_attempts,
                        "max_enforcement_attempts": settings.state_enforcement_max_retries,
                        "starting_started_at": ns.starting_started_at.isoformat() if ns.starting_started_at else None,
                    })

                messages.append({
                    "type": "initial_state",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "data": {"nodes": nodes_data},
                })

                # Get link states
                link_states = (
                    database.query(models.LinkState)
                    .filter(models.LinkState.lab_id == lab_id)
                    .all()
                )

                links_data = []
                for ls in link_states:
                    links_data.append({
                        "link_name": ls.link_name,
                        "desired_state": ls.desired_state,
                        "actual_state": ls.actual_state,
                        "source_node": ls.source_node,
                        "target_node": ls.target_node,
                        "error_message": ls.error_message,
                        "source_oper_state": ls.source_oper_state,
                        "target_oper_state": ls.target_oper_state,
                        "source_oper_reason": ls.source_oper_reason,
                        "target_oper_reason": ls.target_oper_reason,
                        "oper_epoch": ls.oper_epoch,
                    })

                if links_data:
                    messages.append({
                        "type": "initial_links",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "data": {"links": links_data},
                    })

        # Phase 2: Send all messages (session already released)
        for message in messages:
            await websocket.send_json(message)

        if len(messages) > 1:
            # Count nodes/links from built data
            n_nodes = len(messages[1].get("data", {}).get("nodes", []))
            n_links = len(messages[2]["data"]["links"]) if len(messages) > 2 else 0
            logger.debug(f"Sent initial state for lab {lab_id}: {n_nodes} nodes, {n_links} links")

    except Exception as e:
        logger.error(f"Failed to send initial state for lab {lab_id}: {e}")


@router.websocket("/ws/labs/{lab_id}/state")
async def lab_state_websocket(
    websocket: WebSocket,
    lab_id: str,
) -> None:
    """WebSocket endpoint for real-time lab state updates.

    Clients connect to receive push notifications when:
    - Node state changes (actual_state, is_ready)
    - Link state changes
    - Lab state changes
    - Job progress updates

    The connection receives an initial state snapshot on connect,
    then incremental updates as changes occur.

    Protocol:
    - Server sends JSON messages with type, timestamp, data
    - Client can send JSON messages for future commands (currently unused)
    - Connection closes on client disconnect or error

    Note: We use get_session() context manager for the initial state query
    rather than Depends(get_db) because WebSocket handlers run indefinitely.
    Using a dependency would keep the session open for the entire connection
    lifetime, leading to 'idle in transaction' connection leaks.
    """
    await manager.connect(websocket, lab_id)

    # Start subscription task
    subscription_task = asyncio.create_task(_subscribe_and_forward(websocket, lab_id))

    try:
        # Send initial state (opens and releases its own session)
        await _send_initial_state(websocket, lab_id)

        # Keep connection alive and handle client messages
        while True:
            try:
                # Wait for client messages (keep-alive pings, future commands)
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=30.0,
                )
                # Handle client messages if needed
                try:
                    message = json.loads(data)
                    msg_type = message.get("type")

                    if msg_type == "ping":
                        # Respond to ping with pong
                        await websocket.send_json({
                            "type": "pong",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                    elif msg_type == "refresh":
                        # Client requests state refresh (opens own session)
                        await _send_initial_state(websocket, lab_id)
                except json.JSONDecodeError:
                    pass  # Ignore non-JSON messages

            except asyncio.TimeoutError:
                # Send heartbeat to keep connection alive
                try:
                    await websocket.send_json({
                        "type": "heartbeat",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception:
                    break  # Connection lost

    except WebSocketDisconnect:
        logger.debug(f"Client disconnected from lab {lab_id}")
    except Exception as e:
        logger.warning(f"WebSocket error for lab {lab_id}: {e}")
    finally:
        # Clean up
        subscription_task.cancel()
        try:
            await subscription_task
        except asyncio.CancelledError:
            pass
        manager.disconnect(websocket, lab_id)
