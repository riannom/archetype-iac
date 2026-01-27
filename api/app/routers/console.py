"""WebSocket console proxy endpoint."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import agent_client, models
from app.db import SessionLocal
from app.storage import topology_path
from app.topology import analyze_topology, yaml_to_graph

logger = logging.getLogger(__name__)

router = APIRouter(tags=["console"])


@router.websocket("/labs/{lab_id}/nodes/{node}/console")
async def console_ws(websocket: WebSocket, lab_id: str, node: str) -> None:
    """Proxy console WebSocket to agent."""
    await websocket.accept()

    database = SessionLocal()
    try:
        lab = database.get(models.Lab, lab_id)
        if not lab:
            await websocket.send_text("Lab not found\r\n")
            await websocket.close(code=1008)
            return

        # For multi-host labs, find which agent has the specific node
        agent = None
        node_name = node  # May be GUI ID or actual name
        topo_path = topology_path(lab.id)
        if topo_path.exists():
            try:
                topology_yaml = topo_path.read_text(encoding="utf-8")
                graph = yaml_to_graph(topology_yaml)
                analysis = analyze_topology(graph)

                # Resolve GUI ID to containerlab node name
                # The 'node' parameter may be a GUI ID (e.g., "8e0qdki") or
                # the actual node name (e.g., "EOS_1_96ce")
                # We need the container_name (YAML key) for containerlab, not the display name
                for n in graph.nodes:
                    if n.id == node:
                        # Use container_name (YAML key) if available, otherwise fall back to name
                        node_name = n.container_name or n.name
                        logger.debug(f"Console: resolved GUI ID {node} to container name {node_name}")
                        break

                # Get the provider for this lab
                lab_provider = lab.provider if lab.provider else "containerlab"

                # Check if this is a multi-host lab and find the node's host
                if not analysis.single_host:
                    for host_id, placements in analysis.placements.items():
                        for p in placements:
                            if p.node_name == node_name:
                                # Found the host for this node, get the agent
                                agent = await agent_client.get_agent_by_name(
                                    database, host_id, required_provider=lab_provider
                                )
                                break
                        if agent:
                            break
            except Exception as e:
                logger.warning(f"Console: topology parsing failed for {lab_id}: {e}")
                # Fall back to default behavior

        # Get the provider for this lab (outside try block for fallback)
        lab_provider = lab.provider if lab.provider else "containerlab"

        # If not found via topology (single-host or node not found), use lab's agent
        if not agent:
            agent = await agent_client.get_agent_for_lab(database, lab, required_provider=lab_provider)

        if not agent:
            await websocket.send_text("No healthy agent available\r\n")
            await websocket.close(code=1011)
            return

        # Get agent WebSocket URL (use resolved node_name, not raw GUI ID)
        agent_ws_url = agent_client.get_agent_console_url(agent, lab_id, node_name)

        # Check if node is ready for console access
        # Look up NodeState to check is_ready flag
        node_state = (
            database.query(models.NodeState)
            .filter(
                models.NodeState.lab_id == lab_id,
                models.NodeState.node_name == node_name,
            )
            .first()
        )

        # Also check by node_id in case the raw GUI ID was passed
        if not node_state:
            node_state = (
                database.query(models.NodeState)
                .filter(
                    models.NodeState.lab_id == lab_id,
                    models.NodeState.node_id == node,
                )
                .first()
            )

        boot_warning = None
        if node_state and node_state.actual_state == "running" and not node_state.is_ready:
            # Node is running but not ready - check readiness from agent
            try:
                readiness = await agent_client.check_node_readiness(agent, lab_id, node_name)
                if not readiness.get("is_ready", False):
                    progress = readiness.get("progress_percent")
                    progress_str = f" ({progress}%)" if progress is not None else ""
                    boot_warning = f"\r\n[Boot in progress{progress_str}... Console may be unresponsive]\r\n\r\n"
            except Exception as e:
                logger.debug(f"Readiness check failed for {node_name}: {e}")

    finally:
        database.close()

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
