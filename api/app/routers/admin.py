"""Admin and reconciliation endpoints."""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import agent_client, db, models
from app.auth import get_current_user
from app.utils.lab import get_lab_or_404

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


@router.post("/reconcile")
async def reconcile_state(
    cleanup_orphans: bool = False,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Reconcile lab states with actual container status on agents.

    This endpoint queries all healthy agents to discover running containers
    and updates the database to match reality.

    Args:
        cleanup_orphans: If True, also remove containers for labs not in DB

    Returns:
        Summary of reconciliation actions taken
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    logger.info("Starting reconciliation")

    result = {
        "agents_queried": 0,
        "labs_updated": 0,
        "labs_discovered": [],
        "orphans_cleaned": [],
        "errors": [],
    }

    # Get all healthy agents
    agents = database.query(models.Host).filter(models.Host.status == "online").all()

    if not agents:
        result["errors"].append("No healthy agents available")
        return result

    # Get all labs from database
    all_labs = database.query(models.Lab).all()
    lab_ids = {lab.id for lab in all_labs}
    lab_by_id = {lab.id: lab for lab in all_labs}

    # Query each agent for discovered labs
    for agent in agents:
        try:
            discovered = await agent_client.discover_labs_on_agent(agent)
            result["agents_queried"] += 1

            for lab_info in discovered.get("labs", []):
                lab_id = lab_info.get("lab_id")
                nodes = lab_info.get("nodes", [])

                if lab_id in lab_by_id:
                    # Lab exists in DB, update its state based on containers
                    lab = lab_by_id[lab_id]

                    # Determine lab state from node states
                    if not nodes:
                        new_state = "stopped"
                    elif all(n.get("status") == "running" for n in nodes):
                        new_state = "running"
                    elif any(n.get("status") == "running" for n in nodes):
                        new_state = "running"  # Partially running = running
                    else:
                        new_state = "stopped"

                    if lab.state != new_state:
                        logger.info(f"Updating lab {lab_id} state: {lab.state} -> {new_state}")
                        lab.state = new_state
                        lab.state_updated_at = datetime.utcnow()
                        lab.agent_id = agent.id
                        result["labs_updated"] += 1

                    result["labs_discovered"].append({
                        "lab_id": lab_id,
                        "state": new_state,
                        "node_count": len(nodes),
                        "agent_id": agent.id,
                    })
                else:
                    # Lab has containers but not in DB - orphan
                    result["labs_discovered"].append({
                        "lab_id": lab_id,
                        "state": "orphan",
                        "node_count": len(nodes),
                        "agent_id": agent.id,
                    })

            # Clean up orphans if requested
            if cleanup_orphans:
                cleanup_result = await agent_client.cleanup_orphans_on_agent(agent, list(lab_ids))
                if cleanup_result.get("removed_containers"):
                    result["orphans_cleaned"].extend(cleanup_result["removed_containers"])
                    logger.info(f"Cleaned up {len(cleanup_result['removed_containers'])} orphan containers on agent {agent.id}")

        except Exception as e:
            error_msg = f"Error querying agent {agent.id}: {str(e)}"
            result["errors"].append(error_msg)
            logger.error(error_msg)

    # Update labs that have no containers running (if they were marked running)
    discovered_lab_ids = {d["lab_id"] for d in result["labs_discovered"] if d["state"] != "orphan"}
    for lab in all_labs:
        if lab.id not in discovered_lab_ids and lab.state == "running":
            logger.info(f"Lab {lab.id} has no containers, marking as stopped")
            lab.state = "stopped"
            lab.state_updated_at = datetime.utcnow()
            result["labs_updated"] += 1

    database.commit()
    logger.info(f"Reconciliation complete: {result['labs_updated']} labs updated")

    return result


@router.get("/labs/{lab_id}/refresh-status")
async def refresh_lab_status(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Refresh a single lab's status from the agent.

    This updates both the lab state and individual NodeState records
    in the database based on actual container status.
    """
    lab = get_lab_or_404(lab_id, database, current_user)

    # Try to get status from agent
    agent = await agent_client.get_healthy_agent(database)
    if not agent:
        return {
            "lab_id": lab_id,
            "state": lab.state,
            "nodes": [],
            "error": "No healthy agent available",
        }

    try:
        result = await agent_client.get_lab_status_from_agent(agent, lab.id)
        nodes = result.get("nodes", [])

        # Build a map of container status by node name
        # Node names from agent include the lab prefix, extract just the node part
        container_status_map = {}
        for node in nodes:
            # Container names are like "clab-{lab_id_prefix}-{node_name}"
            # The node name in the response should match our node_name
            node_name = node.get("name", "")
            container_status_map[node_name] = node.get("status", "unknown")

        # Update NodeState records based on actual container status
        node_states = (
            database.query(models.NodeState)
            .filter(models.NodeState.lab_id == lab_id)
            .all()
        )

        updated_nodes = []
        for ns in node_states:
            # Try to find matching container status
            container_status = container_status_map.get(ns.node_name)
            if container_status:
                # Map container status to our actual_state
                if container_status == "running":
                    ns.actual_state = "running"
                    ns.error_message = None
                elif container_status in ("stopped", "exited"):
                    ns.actual_state = "stopped"
                    ns.error_message = None
                else:
                    # Unknown status, leave as-is but clear error
                    ns.error_message = None
                updated_nodes.append({
                    "node_id": ns.node_id,
                    "node_name": ns.node_name,
                    "actual_state": ns.actual_state,
                    "container_status": container_status,
                })

        # Determine lab state from node states
        if not nodes:
            new_state = "stopped"
        elif all(n.get("status") == "running" for n in nodes):
            new_state = "running"
        elif any(n.get("status") == "running" for n in nodes):
            new_state = "running"
        else:
            new_state = "stopped"

        # Update lab if state changed
        if lab.state != new_state:
            lab.state = new_state
            lab.state_updated_at = datetime.utcnow()
            lab.agent_id = agent.id

        database.commit()

        return {
            "lab_id": lab_id,
            "state": new_state,
            "nodes": nodes,
            "updated_node_states": updated_nodes,
            "agent_id": agent.id,
        }

    except Exception as e:
        return {
            "lab_id": lab_id,
            "state": lab.state,
            "nodes": [],
            "error": str(e),
        }
