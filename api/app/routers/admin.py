"""Admin and reconciliation endpoints."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import agent_client, db, models
from app.auth import get_current_user
from app.config import settings
from app.utils.lab import get_lab_or_404

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


# --- Log Query Models ---

class LogEntry(BaseModel):
    """A single log entry from Loki."""
    timestamp: str
    level: str
    service: str
    message: str
    correlation_id: str | None = None
    logger: str | None = None
    extra: dict[str, Any] | None = None


class LogQueryResponse(BaseModel):
    """Response from log query endpoint."""
    entries: list[LogEntry]
    total_count: int
    has_more: bool


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
                        lab.state_updated_at = datetime.now(timezone.utc)
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
            lab.state_updated_at = datetime.now(timezone.utc)
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
    """Refresh a single lab's status from all agents that have nodes for it.

    This updates both the lab state and individual NodeState records
    in the database based on actual container status. Queries all agents
    with NodePlacement records for multi-host lab support.
    """
    lab = get_lab_or_404(lab_id, database, current_user)

    # Get ALL agents that have nodes for this lab (multi-host support)
    # Same pattern as reconciliation.py
    placements = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab_id)
        .all()
    )
    agent_ids = {p.host_id for p in placements}
    # Map node names to their expected agent for safer state updates
    node_expected_agent: dict[str, str] = {p.node_name: p.host_id for p in placements}

    # Also include the lab's default agent if set
    if lab.agent_id:
        agent_ids.add(lab.agent_id)

    # If no placements and no default, find any healthy agent
    if not agent_ids:
        fallback_agent = await agent_client.get_healthy_agent(database)
        if fallback_agent:
            agent_ids.add(fallback_agent.id)

    if not agent_ids:
        return {
            "lab_id": lab_id,
            "state": lab.state,
            "nodes": [],
            "error": "No healthy agent available",
        }

    # Query actual container status from ALL agents
    container_status_map: dict[str, str] = {}
    all_nodes: list[dict] = []
    agents_successfully_queried: set[str] = set()
    agents_queried_ids: list[str] = []
    errors: list[str] = []

    for agent_id in agent_ids:
        agent = database.get(models.Host, agent_id)
        if not agent or not agent_client.is_agent_online(agent):
            continue

        try:
            result = await agent_client.get_lab_status_from_agent(agent, lab.id)
            agents_successfully_queried.add(agent_id)
            agents_queried_ids.append(agent_id)
            nodes = result.get("nodes", [])
            all_nodes.extend(nodes)
            # Merge container status from this agent
            for node in nodes:
                node_name = node.get("name", "")
                if node_name:
                    container_status_map[node_name] = node.get("status", "unknown")
        except Exception as e:
            errors.append(f"Agent {agent.name}: {e}")
            logger.warning(f"Failed to query agent {agent.name} for lab {lab_id}: {e}")

    if not agents_successfully_queried:
        return {
            "lab_id": lab_id,
            "state": lab.state,
            "nodes": [],
            "error": "Failed to reach any agent for this lab",
        }

    # Update NodeState records based on actual container status
    node_states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .all()
    )

    updated_nodes = []
    for ns in node_states:
        container_status = container_status_map.get(ns.node_name)
        if container_status:
            # Map container status to our actual_state
            if container_status == "running":
                ns.actual_state = "running"
                ns.error_message = None
                if not ns.boot_started_at:
                    ns.boot_started_at = datetime.now(timezone.utc)
            elif container_status in ("stopped", "exited"):
                ns.actual_state = "stopped"
                ns.error_message = None
                ns.boot_started_at = None
            else:
                # Unknown status, leave as-is but clear error
                ns.error_message = None
            updated_nodes.append({
                "node_id": ns.node_id,
                "node_name": ns.node_name,
                "actual_state": ns.actual_state,
                "container_status": container_status,
            })
        else:
            # Container not found - only update if the relevant agent was queried
            expected_agent = node_expected_agent.get(ns.node_name)
            agent_was_queried = (
                expected_agent in agents_successfully_queried
                if expected_agent
                else len(agents_successfully_queried) > 0
            )
            # If agent was queried but container not found, preserve existing state
            # (don't mark as undeployed - that's reconciliation's job)

    # Determine lab state from node states
    if not all_nodes:
        new_state = "stopped"
    elif all(n.get("status") == "running" for n in all_nodes):
        new_state = "running"
    elif any(n.get("status") == "running" for n in all_nodes):
        new_state = "running"
    else:
        new_state = "stopped"

    # Update lab if state changed
    if lab.state != new_state:
        lab.state = new_state
        lab.state_updated_at = datetime.now(timezone.utc)

    database.commit()

    result = {
        "lab_id": lab_id,
        "state": new_state,
        "nodes": all_nodes,
        "updated_node_states": updated_nodes,
        "agents_queried": agents_queried_ids,
    }
    if errors:
        result["partial_errors"] = errors

    return result


# --- System Logs Endpoint ---

@router.get("/logs")
async def get_system_logs(
    service: str | None = Query(None, description="Filter by service (api, worker, agent)"),
    level: str | None = Query(None, description="Filter by log level (INFO, WARNING, ERROR)"),
    since: str = Query("1h", description="Time range (15m, 1h, 24h)"),
    search: str | None = Query(None, description="Search text in message"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum entries to return"),
    current_user: models.User = Depends(get_current_user),
) -> LogQueryResponse:
    """Query system logs from Loki.

    Requires admin access. Returns recent log entries with optional filtering.

    Args:
        service: Filter to specific service (api, worker, agent)
        level: Filter to specific log level
        since: Time range to query (15m, 1h, 24h)
        search: Search text within log messages
        limit: Maximum number of entries to return

    Returns:
        List of log entries matching the query
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    # Parse time range
    time_ranges = {
        "15m": 15 * 60,
        "1h": 60 * 60,
        "24h": 24 * 60 * 60,
    }
    seconds = time_ranges.get(since, 3600)
    start_ns = (int(datetime.now(timezone.utc).timestamp()) - seconds) * 1_000_000_000

    # Build LogQL query
    # Base selector for archetype services
    label_selectors = []
    if service:
        label_selectors.append(f'service="{service}"')
    else:
        label_selectors.append('service=~"api|worker|agent"')

    selector = "{" + ",".join(label_selectors) + "}"

    # Add pipeline stages for filtering
    pipeline = []

    # JSON parsing (logs are JSON formatted)
    pipeline.append("| json")

    if level:
        pipeline.append(f'| level="{level}"')

    if search:
        # Line filter for search text
        pipeline.append(f'|~ "{search}"')

    query = selector + " " + " ".join(pipeline)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{settings.loki_url}/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": start_ns,
                    "limit": limit,
                    "direction": "backward",  # Most recent first
                },
            )

            if response.status_code != 200:
                logger.warning(f"Loki query failed: {response.status_code} - {response.text}")
                # Return empty result if Loki is not available
                return LogQueryResponse(entries=[], total_count=0, has_more=False)

            data = response.json()

    except httpx.ConnectError:
        logger.warning("Cannot connect to Loki - centralized logging may not be configured")
        return LogQueryResponse(entries=[], total_count=0, has_more=False)
    except Exception as e:
        logger.error(f"Error querying Loki: {e}")
        return LogQueryResponse(entries=[], total_count=0, has_more=False)

    # Parse Loki response
    entries = []
    result_data = data.get("data", {}).get("result", [])

    for stream in result_data:
        labels = stream.get("stream", {})
        service_name = labels.get("service", "unknown")

        for value in stream.get("values", []):
            timestamp_ns, log_line = value

            # Parse the JSON log line
            try:
                import json
                log_data = json.loads(log_line)
                entry = LogEntry(
                    timestamp=log_data.get("timestamp", ""),
                    level=log_data.get("level", "INFO"),
                    service=service_name,
                    message=log_data.get("message", log_line),
                    correlation_id=log_data.get("correlation_id"),
                    logger=log_data.get("logger"),
                    extra=log_data.get("extra"),
                )
            except (json.JSONDecodeError, TypeError):
                # Non-JSON log line
                # Convert nanosecond timestamp
                ts = datetime.fromtimestamp(int(timestamp_ns) / 1_000_000_000, tz=timezone.utc)
                entry = LogEntry(
                    timestamp=ts.isoformat(),
                    level="INFO",
                    service=service_name,
                    message=log_line,
                )

            entries.append(entry)

    # Sort by timestamp (most recent first)
    entries.sort(key=lambda e: e.timestamp, reverse=True)

    return LogQueryResponse(
        entries=entries[:limit],
        total_count=len(entries),
        has_more=len(entries) > limit,
    )


@router.post("/reconcile-images")
async def reconcile_images(
    verify_agents: bool = False,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Reconcile image manifest with ImageHost database table.

    This endpoint ensures consistency between manifest.json (source of truth
    for image metadata) and the ImageHost table (tracks which images exist
    on which agents).

    Args:
        verify_agents: If True, also query agents to verify actual image status

    Returns:
        Summary of reconciliation actions taken
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    from app.tasks.image_reconciliation import (
        reconcile_image_hosts,
        full_image_reconciliation,
    )

    logger.info("Starting image reconciliation")

    if verify_agents:
        result = await full_image_reconciliation()
    else:
        result = await reconcile_image_hosts()

    return result.to_dict()


@router.post("/cleanup-stuck-jobs")
async def cleanup_stuck_jobs(
    max_age_minutes: int = Query(5, ge=1, le=60, description="Mark jobs stuck longer than this as failed"),
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Manually clean up stuck jobs.

    This endpoint finds jobs that have been in 'running' or 'queued' state
    for longer than the specified duration and marks them as failed.

    Args:
        max_age_minutes: Jobs older than this (in minutes) will be marked failed

    Returns:
        Summary of cleanup actions taken
    """
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    from datetime import timedelta

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=max_age_minutes)

    # Find stuck jobs
    stuck_jobs = (
        database.query(models.Job)
        .filter(
            models.Job.status.in_(["running", "queued"]),
            models.Job.created_at < cutoff,
        )
        .all()
    )

    cleaned = []
    for job in stuck_jobs:
        logger.info(f"Cleaning up stuck job {job.id}: action={job.action}, status={job.status}")
        job.status = "failed"
        job.completed_at = now
        if not job.log_path:
            job.log_path = f"Manually marked as failed (stuck for >{max_age_minutes} minutes)"
        cleaned.append({
            "id": job.id,
            "action": job.action,
            "lab_id": job.lab_id,
            "previous_status": job.status,
            "age_minutes": int((now - job.created_at.replace(tzinfo=timezone.utc)).total_seconds() / 60),
        })

    database.commit()

    logger.info(f"Cleaned up {len(cleaned)} stuck jobs")

    return {
        "cleaned_count": len(cleaned),
        "cleaned_jobs": cleaned,
        "cutoff_minutes": max_age_minutes,
    }
