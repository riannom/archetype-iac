"""WebSocket console proxy endpoint."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import agent_client, models
from app.db import get_session
from app.services.topology import TopologyService
from app.utils.lab import get_node_provider
from app.utils.agents import get_online_agent_for_lab

logger = logging.getLogger(__name__)

router = APIRouter(tags=["console"])


@dataclass
class _ConsoleDBResult:
    """Result from sync DB lookup for console connection."""
    error: str | None = None
    agent: Any = None
    agent_ws_url: str | None = None
    node_name: str = ""
    lab_provider: str = "docker"
    lab_agent_id: str | None = None
    # For readiness check
    node_actual_state: str | None = None
    node_is_ready: bool = True
    node_def: Any = None


@router.websocket("/labs/{lab_id}/nodes/{node}/console")
async def console_ws(websocket: WebSocket, lab_id: str, node: str, token: str | None = None) -> None:
    """Proxy console WebSocket to agent."""
    # Validate JWT token before accepting connection
    from app.auth import validate_ws_token
    user = validate_ws_token(token)
    if not user:
        await websocket.close(code=4401, reason="Authentication required")
        return

    await websocket.accept()

    def _sync_db_lookup() -> _ConsoleDBResult:
        """Run all DB queries in a worker thread."""
        result = _ConsoleDBResult()
        with get_session() as database:
            lab = database.get(models.Lab, lab_id)
            if not lab:
                result.error = "lab_not_found"
                return result

            result.node_name = node  # May be GUI ID or actual name
            result.lab_provider = lab.provider if lab.provider else "docker"
            result.lab_agent_id = lab.agent_id

            # Use TopologyService to look up node and its host from database
            topology_service = TopologyService(database)

            node_def = topology_service.get_node_by_any_id(lab.id, node)
            if node_def:
                result.node_name = node_def.container_name
                result.node_def = node_def
                logger.debug(f"Console: resolved {node} to container name {result.node_name} from DB")

            agent = topology_service.get_node_host(lab.id, result.node_name)
            if agent and not agent_client.is_agent_online(agent):
                agent = None

            if agent:
                result.agent = agent
                result.agent_ws_url = agent_client.get_agent_console_url(agent, lab_id, result.node_name)

            # Check node readiness
            node_state = (
                database.query(models.NodeState)
                .filter(
                    models.NodeState.lab_id == lab_id,
                    models.NodeState.node_name == result.node_name,
                )
                .first()
            )

            if not node_state:
                node_state = (
                    database.query(models.NodeState)
                    .filter(
                        models.NodeState.lab_id == lab_id,
                        models.NodeState.node_id == node,
                    )
                    .first()
                )

            if node_state:
                result.node_actual_state = node_state.actual_state
                result.node_is_ready = node_state.is_ready

        return result

    db_result = await asyncio.to_thread(_sync_db_lookup)

    if db_result.error == "lab_not_found":
        await websocket.send_text("Lab not found\r\n")
        await websocket.close(code=1008)
        return

    # Handle async agent lookup if topology-based lookup didn't find one
    if not db_result.agent:
        # get_online_agent_for_lab is async and needs a DB session; open a
        # fresh session for the brief placement/host queries it runs.
        with get_session() as database:
            lab = database.get(models.Lab, lab_id)
            if lab:
                agent = await get_online_agent_for_lab(database, lab, required_provider=db_result.lab_provider)
                if agent:
                    db_result.agent = agent
                    db_result.agent_ws_url = agent_client.get_agent_console_url(agent, lab_id, db_result.node_name)

    if not db_result.agent:
        await websocket.send_text("No healthy agent available\r\n")
        await websocket.close(code=1011)
        return

    agent = db_result.agent
    agent_ws_url = db_result.agent_ws_url
    node_name = db_result.node_name

    boot_warning = None
    if db_result.node_actual_state == "running" and not db_result.node_is_ready:
        # Node is running but not ready - check readiness from agent
        try:
            provider_type = get_node_provider(db_result.node_def) if db_result.node_def is not None else None
            device_kind = db_result.node_def.device if db_result.node_def is not None else None
            readiness = await agent_client.check_node_readiness(
                agent,
                lab_id,
                node_name,
                kind=device_kind,
                provider_type=provider_type,
            )
            if not readiness.get("is_ready", False):
                progress = readiness.get("progress_percent")
                progress_str = f" ({progress}%)" if progress is not None else ""
                detail = readiness.get("message") or "Console may be unresponsive"
                boot_warning = (
                    f"\r\n[Boot in progress{progress_str}: {detail}]\r\n"
                    "[For SSH-console VMs, no CLI appears until management IP/SSH is available.]\r\n\r\n"
                )
        except Exception as e:
            logger.debug(f"Readiness check failed for {node_name}: {e}")

    # Connect to agent WebSocket and proxy
    import websockets

    logger.info(f"Console: connecting to agent at {agent_ws_url}")

    # Send boot warning if node is not yet ready
    if boot_warning:
        try:
            await websocket.send_text(boot_warning)
        except Exception:
            pass

    try:
        async with websockets.connect(agent_ws_url) as agent_ws:
            async def forward_to_client():
                """Forward data from agent to client."""
                try:
                    async for message in agent_ws:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)
                except Exception:
                    pass

            async def forward_to_agent():
                """Forward data from client to agent."""
                try:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            break
                        elif message["type"] == "websocket.receive":
                            if "text" in message:
                                await agent_ws.send(message["text"])
                            elif "bytes" in message:
                                await agent_ws.send(message["bytes"])
                except WebSocketDisconnect:
                    pass
                except Exception:
                    pass

            # Run both directions concurrently
            to_client_task = asyncio.create_task(forward_to_client())
            to_agent_task = asyncio.create_task(forward_to_agent())

            try:
                done, pending = await asyncio.wait(
                    [to_client_task, to_agent_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            finally:
                pass

    except Exception as e:
        logger.error(f"Console connection failed to {agent_ws_url}: {e}")
        try:
            await websocket.send_text(f"Console connection failed: {e}\r\n")
        except Exception:
            pass

    try:
        await websocket.close()
    except Exception:
        pass
