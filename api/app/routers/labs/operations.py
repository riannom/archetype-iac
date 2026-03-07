"""IaC workflow, logs, cleanup, interface mapping, and infra notification endpoints.

NOTE: Several symbols (agent_client, get_online_agent_for_lab, TopologyService,
interface_mapping_service) are resolved through the parent package
(``app.routers.labs``) so that test monkeypatching on that path continues to work.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app import db, models, schemas
from app.auth import get_current_user
from app.enums import LabRole
from app.state import (
    HostStatus,
    NodeActualState,
    NodeDesiredState,
)
from app.utils.http import raise_not_found
from app.utils.lab import get_lab_or_404, get_lab_provider, get_lab_with_role
from app.utils.nodes import get_node_placement_mapping

from .crud import _ensure_node_states_exist


def _pkg():
    """Resolve the parent package for monkeypatch-safe attribute access."""
    return sys.modules["app.routers.labs"]

logger = logging.getLogger(__name__)

router = APIRouter(tags=["labs"])


# ============================================================================
# Node Readiness Endpoints (IaC Workflow Support)
# ============================================================================


@router.get("/labs/{lab_id}/nodes/ready")
async def check_nodes_ready(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabReadinessResponse:
    """Check readiness status for all nodes in a lab.

    Returns the readiness state of each node, including boot progress
    and management IPs. Useful for CI/CD to poll until lab is ready.

    A node is considered "ready" when:
    - actual_state is "running"
    - is_ready flag is True (boot sequence complete)
    """
    from app.utils.lab import get_node_provider

    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)

    # Get all node states
    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .order_by(models.NodeState.node_name)
        .all()
    )
    # Build node metadata lookup for readiness checks (VMs require kind)
    db_nodes = (
        database.query(models.Node)
        .filter(
            models.Node.lab_id == lab_id,
            models.Node.container_name.in_([s.node_name for s in states]),
        )
        .all()
    )
    nodes_by_name = {n.container_name: n for n in db_nodes}
    node_devices = {n.container_name: n.device for n in db_nodes}
    node_images = {n.container_name: n.image for n in db_nodes}

    # Build per-node placement/agent mapping (multi-host safe).
    placements = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab_id)
        .all()
    )
    placement_by_node = {p.node_name: p.host_id for p in placements}
    host_ids = set(placement_by_node.values())
    if lab.agent_id:
        host_ids.add(lab.agent_id)
    hosts = {}
    if host_ids:
        hosts = {
            h.id: h
            for h in database.query(models.Host).filter(models.Host.id.in_(host_ids)).all()
        }
    lab_provider = get_lab_provider(lab)
    fallback_agent = await _pkg().get_online_agent_for_lab(
        database, lab, required_provider=lab_provider
    )

    nodes_out = []
    ready_count = 0
    running_count = 0

    for state in states:
        # Check readiness from agent if node is running
        progress_percent = None
        message = None

        if state.actual_state == NodeActualState.RUNNING:
            running_count += 1
            db_node = nodes_by_name.get(state.node_name)
            host_id = placement_by_node.get(state.node_name)
            if not host_id and db_node is not None and db_node.host_id:
                host_id = db_node.host_id
            if not host_id:
                host_id = lab.agent_id
            agent = hosts.get(host_id) if host_id else None
            if agent is not None and not _pkg().agent_client.is_agent_online(agent):
                agent = None
            if agent is None and fallback_agent is not None:
                agent = fallback_agent

            if agent:
                try:
                    device_kind = node_devices.get(state.node_name)
                    node_image = node_images.get(state.node_name)
                    provider_type = None
                    if node_image:
                        if db_node is not None:
                            provider_type = get_node_provider(db_node)
                    readiness = await _pkg().agent_client.check_node_readiness(
                        agent,
                        lab.id,
                        state.node_name,
                        kind=device_kind,
                        provider_type=provider_type,
                    )
                    # Update is_ready from agent response
                    if readiness.get("is_ready") and not state.is_ready:
                        state.is_ready = True
                        database.commit()
                    progress_percent = readiness.get("progress_percent")
                    message = readiness.get("message")
                except Exception as e:
                    message = f"Readiness check failed: {e}"
            else:
                message = "No online agent available for node placement"

        if state.is_ready and state.actual_state == NodeActualState.RUNNING:
            ready_count += 1

        nodes_out.append(schemas.NodeReadinessOut(
            node_id=state.node_id,
            node_name=state.node_name,
            is_ready=state.is_ready and state.actual_state == NodeActualState.RUNNING,
            actual_state=state.actual_state,
            progress_percent=progress_percent,
            message=message,
            boot_started_at=state.boot_started_at,
            management_ip=state.management_ip,
        ))

    nodes_should_run = [s for s in states if s.desired_state == NodeDesiredState.RUNNING]
    should_run_count = len(nodes_should_run)
    all_ready = should_run_count == 0 or all(
        s.is_ready and s.actual_state == NodeActualState.RUNNING for s in nodes_should_run
    )

    return schemas.LabReadinessResponse(
        lab_id=lab_id,
        all_ready=all_ready,
        ready_count=ready_count,
        total_count=should_run_count,
        running_count=running_count,
        nodes=nodes_out,
    )


@router.get("/labs/{lab_id}/nodes/ready/poll")
async def poll_nodes_ready(
    lab_id: str,
    timeout: int = 300,
    interval: int = 10,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabReadinessResponse:
    """Long-poll until all running nodes are ready or timeout.

    This endpoint blocks until either:
    - All nodes with desired_state=running are ready
    - The timeout is reached

    Args:
        timeout: Maximum seconds to wait (default: 300, max: 600)
        interval: Seconds between checks (default: 10, min: 5)

    Returns:
        LabReadinessResponse with final readiness state

    Response Headers:
        X-Readiness-Status: "complete" if all ready, "timeout" if timed out
    """
    from fastapi.responses import JSONResponse
    from app.utils.lab import get_node_provider

    # Validate parameters
    timeout = min(max(timeout, 10), 600)  # 10s to 10min
    interval = min(max(interval, 5), 60)  # 5s to 60s

    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)

    db_nodes = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id)
        .all()
    )
    nodes_by_name = {n.container_name: n for n in db_nodes}
    node_devices = {n.container_name: n.device for n in db_nodes}
    node_images = {n.container_name: n.image for n in db_nodes}

    lab_provider = get_lab_provider(lab)
    agent = await _pkg().get_online_agent_for_lab(database, lab, required_provider=lab_provider)

    start_time = asyncio.get_running_loop().time()
    end_time = start_time + timeout

    while asyncio.get_running_loop().time() < end_time:
        # Refresh session to get latest state
        database.expire_all()

        states = (
            database.query(models.NodeState)
            .filter(models.NodeState.lab_id == lab_id)
            .order_by(models.NodeState.node_name)
            .all()
        )

        # Count nodes that should be running
        nodes_should_run = [s for s in states if s.desired_state == NodeDesiredState.RUNNING]

        if not nodes_should_run:
            # No nodes expected to run - return immediately
            return schemas.LabReadinessResponse(
                lab_id=lab_id,
                all_ready=True,
                ready_count=0,
                total_count=len(states),
                running_count=0,
                nodes=[],
            )

        # Check readiness for running nodes
        nodes_out = []
        ready_count = 0
        running_count = 0

        for state in states:
            progress_percent = None
            message = None

            if state.actual_state == NodeActualState.RUNNING:
                running_count += 1
                if agent and not state.is_ready:
                    try:
                        device_kind = node_devices.get(state.node_name)
                        node_image = node_images.get(state.node_name)
                        provider_type = None
                        if node_image:
                            db_node = nodes_by_name.get(state.node_name)
                            if db_node is not None:
                                provider_type = get_node_provider(db_node)
                        readiness = await _pkg().agent_client.check_node_readiness(
                            agent,
                            lab.id,
                            state.node_name,
                            kind=device_kind,
                            provider_type=provider_type,
                        )
                        if readiness.get("is_ready"):
                            state.is_ready = True
                            database.commit()
                        progress_percent = readiness.get("progress_percent")
                        message = readiness.get("message")
                    except Exception as e:
                        message = f"Readiness check failed: {e}"

            if state.is_ready and state.actual_state == NodeActualState.RUNNING:
                ready_count += 1

            if state in nodes_should_run:
                nodes_out.append(schemas.NodeReadinessOut(
                    node_id=state.node_id,
                    node_name=state.node_name,
                    is_ready=state.is_ready and state.actual_state == NodeActualState.RUNNING,
                    actual_state=state.actual_state,
                    progress_percent=progress_percent,
                    message=message,
                    boot_started_at=state.boot_started_at,
                    management_ip=state.management_ip,
                ))

        # Check if all nodes that should run are ready
        all_ready = all(
            s.is_ready and s.actual_state == NodeActualState.RUNNING
            for s in nodes_should_run
        )

        if all_ready:
            response = schemas.LabReadinessResponse(
                lab_id=lab_id,
                all_ready=True,
                ready_count=ready_count,
                total_count=len(states),
                running_count=running_count,
                nodes=nodes_out,
            )
            return JSONResponse(
                content=response.model_dump(mode="json"),
                headers={"X-Readiness-Status": "complete"},
            )

        # Wait before next check
        await asyncio.sleep(interval)

    # Timeout reached - return current state
    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .order_by(models.NodeState.node_name)
        .all()
    )

    nodes_out = []
    ready_count = 0
    running_count = 0

    for state in states:
        if state.actual_state == NodeActualState.RUNNING:
            running_count += 1
        if state.is_ready and state.actual_state == NodeActualState.RUNNING:
            ready_count += 1

        nodes_out.append(schemas.NodeReadinessOut(
            node_id=state.node_id,
            node_name=state.node_name,
            is_ready=state.is_ready and state.actual_state == NodeActualState.RUNNING,
            actual_state=state.actual_state,
            progress_percent=None,
            message="Timeout waiting for readiness",
            boot_started_at=state.boot_started_at,
            management_ip=state.management_ip,
        ))

    response = schemas.LabReadinessResponse(
        lab_id=lab_id,
        all_ready=False,
        ready_count=ready_count,
        total_count=len(states),
        running_count=running_count,
        nodes=nodes_out,
    )
    return JSONResponse(
        content=response.model_dump(mode="json"),
        headers={"X-Readiness-Status": "timeout"},
    )


# ============================================================================
# Inventory Export Endpoint (IaC Workflow Support)
# ============================================================================


@router.get("/labs/{lab_id}/inventory")
def export_inventory(
    lab_id: str,
    format: Literal["json", "ansible", "terraform"] = "json",
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabInventoryResponse:
    """Export lab node inventory for IaC tools.

    Generates an inventory of all nodes with their management IPs
    in a format suitable for automation tools.

    Formats:
    - json: Structured JSON with all node details
    - ansible: Ansible inventory YAML format
    - terraform: Terraform tfvars JSON format

    Example usage:
        curl -H "Authorization: Bearer $TOKEN" \\
            "$API_URL/labs/{id}/inventory?format=ansible" > inventory.yml
    """
    lab = get_lab_or_404(lab_id, database, current_user)
    _ensure_node_states_exist(database, lab.id)

    # Get node states with IPs
    states = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id)
        .order_by(models.NodeState.node_name)
        .all()
    )

    # Get topology for device info
    service = _pkg().TopologyService(database)
    device_info = {}
    if service.has_nodes(lab.id):
        try:
            graph = service.export_to_graph(lab.id)
            for node in graph.nodes:
                node_name = node.container_name or node.name
                device_info[node_name] = {
                    "device": node.device,
                    "kind": node.device,
                }
        except Exception:
            pass

    # Get host placements for multi-host
    placement_by_node, hosts = get_node_placement_mapping(database, lab_id, lab.agent_id)

    # Build inventory entries
    nodes = []
    for state in states:
        all_ips = []
        if state.management_ips_json:
            try:
                all_ips = json.loads(state.management_ips_json)
            except (json.JSONDecodeError, TypeError):
                pass

        info = device_info.get(state.node_name, {})
        host_id = placement_by_node.get(state.node_name) or lab.agent_id

        nodes.append(schemas.NodeInventoryEntry(
            node_name=state.node_name,
            management_ip=state.management_ip,
            all_ips=all_ips,
            device_type=info.get("device"),
            kind=info.get("kind"),
            host_id=host_id,
            host_name=hosts.get(host_id) if host_id else None,
        ))

    # Generate formatted content based on requested format
    content = None

    if format == "ansible":
        # Ansible inventory YAML format
        ansible_hosts = {}
        for node in nodes:
            host_vars = {}
            if node.management_ip:
                host_vars["ansible_host"] = node.management_ip
            if node.device_type:
                # Map common device types to ansible_network_os
                device_os_map = {
                    "ceos": "arista.eos.eos",
                    "vr-veos": "arista.eos.eos",
                    "srl": "nokia.srlinux.srlinux",
                    "vr-sros": "nokia.sros.sros",
                    "crpd": "juniper.device",
                    "vr-vmx": "juniper.device",
                    "vr-xrv": "cisco.iosxr.iosxr",
                    "vr-csr": "cisco.ios.ios",
                    "vr-n9kv": "cisco.nxos.nxos",
                }
                if node.device_type in device_os_map:
                    host_vars["ansible_network_os"] = device_os_map[node.device_type]
                host_vars["device_type"] = node.device_type
            if node.host_name:
                host_vars["lab_host"] = node.host_name
            ansible_hosts[node.node_name] = host_vars

        inventory = {
            "all": {
                "hosts": ansible_hosts,
                "vars": {
                    "ansible_connection": "network_cli",
                    "lab_id": lab_id,
                    "lab_name": lab.name,
                },
            }
        }
        content = yaml.dump(inventory, default_flow_style=False, sort_keys=False)

    elif format == "terraform":
        # Terraform tfvars JSON format
        tf_nodes = {}
        for node in nodes:
            tf_nodes[node.node_name] = {
                "ip": node.management_ip,
                "all_ips": node.all_ips,
                "device_type": node.device_type,
                "kind": node.kind,
            }
            if node.host_name:
                tf_nodes[node.node_name]["host"] = node.host_name

        terraform_vars = {
            "lab_id": lab_id,
            "lab_name": lab.name,
            "lab_nodes": tf_nodes,
        }
        content = json.dumps(terraform_vars, indent=2)

    return schemas.LabInventoryResponse(
        lab_id=lab_id,
        lab_name=lab.name,
        format=format,
        nodes=nodes,
        content=content,
    )


@router.post("/labs/{lab_id}/config-diff")
def generate_config_diff(
    lab_id: str,
    payload: schemas.ConfigDiffRequest,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.ConfigDiffResponse:
    """Generate a unified diff between two config snapshots.

    Uses Python's difflib to compute the diff. Returns structured diff
    lines with line numbers and change types for easy frontend rendering.
    """
    import difflib

    get_lab_or_404(lab_id, database, current_user)

    # Fetch both snapshots
    snapshot_a = (
        database.query(models.ConfigSnapshot)
        .filter(
            models.ConfigSnapshot.id == payload.snapshot_id_a,
            models.ConfigSnapshot.lab_id == lab_id,
        )
        .first()
    )

    snapshot_b = (
        database.query(models.ConfigSnapshot)
        .filter(
            models.ConfigSnapshot.id == payload.snapshot_id_b,
            models.ConfigSnapshot.lab_id == lab_id,
        )
        .first()
    )

    if not snapshot_a:
        raise_not_found(f"Snapshot A not found: {payload.snapshot_id_a}")

    if not snapshot_b:
        raise_not_found(f"Snapshot B not found: {payload.snapshot_id_b}")

    # Generate unified diff
    lines_a = snapshot_a.content.splitlines(keepends=True)
    lines_b = snapshot_b.content.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        lines_a,
        lines_b,
        fromfile=f"{snapshot_a.node_name} ({snapshot_a.created_at.strftime('%Y-%m-%d %H:%M')})",
        tofile=f"{snapshot_b.node_name} ({snapshot_b.created_at.strftime('%Y-%m-%d %H:%M')})",
        lineterm="",
    ))

    # Parse diff into structured lines
    diff_lines: list[schemas.ConfigDiffLine] = []
    additions = 0
    deletions = 0
    line_num_a = 0
    line_num_b = 0

    for line in diff:
        # Strip trailing newline for cleaner display
        line_content = line.rstrip("\n\r")

        if line.startswith("---") or line.startswith("+++"):
            diff_lines.append(schemas.ConfigDiffLine(
                content=line_content,
                type="header",
            ))
        elif line.startswith("@@"):
            # Parse hunk header to get line numbers
            # Format: @@ -start,count +start,count @@
            import re
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if match:
                line_num_a = int(match.group(1)) - 1  # -1 because we increment before use
                line_num_b = int(match.group(2)) - 1
            diff_lines.append(schemas.ConfigDiffLine(
                content=line_content,
                type="header",
            ))
        elif line.startswith("-"):
            line_num_a += 1
            deletions += 1
            diff_lines.append(schemas.ConfigDiffLine(
                line_number_a=line_num_a,
                content=line_content[1:],  # Remove leading -
                type="removed",
            ))
        elif line.startswith("+"):
            line_num_b += 1
            additions += 1
            diff_lines.append(schemas.ConfigDiffLine(
                line_number_b=line_num_b,
                content=line_content[1:],  # Remove leading +
                type="added",
            ))
        elif line.startswith(" "):
            line_num_a += 1
            line_num_b += 1
            diff_lines.append(schemas.ConfigDiffLine(
                line_number_a=line_num_a,
                line_number_b=line_num_b,
                content=line_content[1:],  # Remove leading space
                type="unchanged",
            ))

    return schemas.ConfigDiffResponse(
        snapshot_a=schemas.ConfigSnapshotOut.model_validate(snapshot_a),
        snapshot_b=schemas.ConfigSnapshotOut.model_validate(snapshot_b),
        diff_lines=diff_lines,
        additions=additions,
        deletions=deletions,
    )


# ============================================================================
# Lab Logs Endpoints
# ============================================================================


@router.get("/labs/{lab_id}/logs")
def get_lab_logs(
    lab_id: str,
    job_id: str | None = None,
    host_id: str | None = None,
    level: str | None = None,
    since: str | None = None,
    search: str | None = None,
    limit: int = 500,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.LabLogsResponse:
    """Get aggregated logs for a lab.

    Combines job logs from all jobs in this lab, parsed with host sections.
    Optionally includes system logs from Loki if configured and user is admin.

    Query parameters:
    - job_id: Filter to logs from a specific job
    - host_id: Filter to logs from a specific host
    - level: Filter by minimum log level (info, warning, error)
    - since: Time filter (e.g., "15m", "1h", "24h")
    - search: Text search in log messages
    - limit: Maximum number of entries to return (default 500)

    Returns structured log entries with host associations and summary info.
    """
    from app.services.log_parser import parse_job_log, filter_entries
    from app.utils.logs import get_log_content

    get_lab_or_404(lab_id, database, current_user)

    # Parse 'since' parameter
    from app.utils.time_range import parse_relative_duration
    since_dt = None
    duration = parse_relative_duration(since, allowed={"15m", "1h", "24h"})
    if duration:
        since_dt = datetime.now(timezone.utc) - duration

    # Query jobs for this lab
    jobs_query = (
        database.query(models.Job)
        .filter(models.Job.lab_id == lab_id)
        .order_by(models.Job.created_at.desc())
    )

    if job_id:
        jobs_query = jobs_query.filter(models.Job.id == job_id)

    # Limit to recent jobs for performance
    jobs = jobs_query.limit(50).all()

    # Build agent_id -> host_name lookup from all jobs
    agent_ids = {job.agent_id for job in jobs if job.agent_id}
    agent_name_map: dict[str, str] = {}
    if agent_ids:
        hosts = (
            database.query(models.Host)
            .filter(models.Host.id.in_(agent_ids))
            .all()
        )
        agent_name_map = {h.id: h.name for h in hosts}

    # Parse all job logs
    all_entries = []
    hosts_found = set()

    for job in jobs:
        log_content = get_log_content(job.log_path)
        if not log_content:
            continue

        # Get host info for this job from its agent_id
        job_host_id = job.agent_id
        job_host_name = agent_name_map.get(job.agent_id) if job.agent_id else None

        parsed = parse_job_log(
            log_content=log_content,
            job_id=job.id,
            job_created_at=job.created_at,
        )

        # If job has an agent, set host info on entries that don't have it
        # and add the host to hosts_found
        for entry in parsed.entries:
            if not entry.host_id and job_host_id:
                entry.host_id = job_host_id
                entry.host_name = job_host_name
            if entry.host_name:
                hosts_found.add(entry.host_name)

        all_entries.extend(parsed.entries)
        hosts_found.update(parsed.hosts)

        # Also add job's host to found hosts
        if job_host_name:
            hosts_found.add(job_host_name)

    # Apply filters
    filtered_entries = filter_entries(
        all_entries,
        host_id=host_id,
        level=level,
        search=search,
        since=since_dt,
    )

    # Sort by timestamp (newest first for display, but API returns oldest first for streaming)
    filtered_entries.sort(key=lambda e: e.timestamp)

    # Apply limit
    has_more = len(filtered_entries) > limit
    limited_entries = filtered_entries[:limit]

    # Count errors
    error_count = sum(1 for e in limited_entries if e.level == "error")

    # Build job summaries for filtering UI
    job_summaries = [
        schemas.LabLogJob(
            id=job.id,
            action=job.action,
            status=job.status,
            created_at=job.created_at,
            completed_at=job.completed_at,
        )
        for job in jobs
    ]

    # Convert entries to response schema
    response_entries = [
        schemas.LabLogEntry(
            timestamp=e.timestamp,
            level=e.level,
            message=e.message,
            host_id=e.host_id,
            host_name=e.host_name,
            job_id=e.job_id,
            source=e.source,
        )
        for e in limited_entries
    ]

    return schemas.LabLogsResponse(
        entries=response_entries,
        jobs=job_summaries,
        hosts=list(hosts_found),
        total_count=len(filtered_entries),
        error_count=error_count,
        has_more=has_more,
    )


@router.post("/labs/{lab_id}/cleanup-orphans", response_model=schemas.CleanupOrphansResponse)
async def cleanup_lab_orphans(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.CleanupOrphansResponse:
    """Clean up orphaned containers for a lab across all agents.

    Removes containers for nodes that are no longer assigned to a given agent.
    This happens when nodes are migrated between agents.

    Args:
        lab_id: Lab identifier

    Returns:
        Dict mapping agent names to lists of removed containers
    """
    lab, role = get_lab_with_role(lab_id, database, current_user)
    if role < LabRole.OWNER:
        raise HTTPException(status_code=403, detail="Owner access required")

    # Get all node placements for this lab
    placements = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab_id)
        .all()
    )

    # Build a mapping of host_id -> list of node_names
    nodes_by_host: dict[str, list[str]] = {}
    for placement in placements:
        if placement.host_id not in nodes_by_host:
            nodes_by_host[placement.host_id] = []
        nodes_by_host[placement.host_id].append(placement.node_name)

    # Get all online agents
    agents = (
        database.query(models.Host)
        .filter(models.Host.status == HostStatus.ONLINE)
        .all()
    )

    removed_by_agent: dict[str, list[str]] = {}
    errors: list[str] = []

    # Call each agent to clean up orphans
    for agent in agents:
        # Get the list of nodes that SHOULD be on this agent
        keep_nodes = nodes_by_host.get(agent.id, [])

        try:
            result = await _pkg().agent_client.cleanup_lab_orphans(
                agent, lab_id, keep_nodes
            )
            if result.get("removed_containers"):
                removed_by_agent[agent.name] = result["removed_containers"]
                logger.info(
                    f"Cleaned up {len(result['removed_containers'])} orphan containers on {agent.name}"
                )
            if result.get("errors"):
                errors.extend(result["errors"])
        except Exception as e:
            error_msg = f"Failed to cleanup orphans on {agent.name}: {e}"
            logger.warning(error_msg)
            errors.append(error_msg)

    return schemas.CleanupOrphansResponse(
        removed_by_agent=removed_by_agent,
        errors=errors,
    )


# =============================================================================
# Interface Mapping Endpoints
# =============================================================================


@router.get("/labs/{lab_id}/interface-mappings")
def get_lab_interface_mappings(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.InterfaceMappingsResponse:
    """Get all interface mappings for a lab.

    Returns OVS port, Linux interface, and vendor interface mappings
    for all interfaces in the lab. Includes computed vendor names for
    interfaces discovered from link states (covers VMs and other providers
    that don't go through OVS plugin port sync).
    """
    from app.services.interface_naming import denormalize_interface

    get_lab_or_404(lab_id, database, current_user)

    # Get all nodes for this lab (need device type for vendor name computation)
    all_nodes = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id)
        .all()
    )
    node_id_map = {n.id: n for n in all_nodes}
    node_name_to_node = {n.container_name: n for n in all_nodes}

    # Get existing interface mappings from OVS sync
    mappings = (
        database.query(models.InterfaceMapping)
        .filter(models.InterfaceMapping.lab_id == lab_id)
        .all()
    )

    # Track which (node_name, linux_interface) pairs we already have
    seen: set[tuple[str, str]] = set()
    result: list[schemas.InterfaceMappingOut] = []

    for m in mappings:
        node = node_id_map.get(m.node_id)
        out = schemas.InterfaceMappingOut.model_validate(m)
        out.node_name = node.container_name if node else None
        # Fill in missing vendor_interface from node device type
        if not out.vendor_interface and node and node.device:
            vendor = denormalize_interface(m.linux_interface, node.device)
            if vendor != m.linux_interface:
                out.vendor_interface = vendor
            out.device_type = out.device_type or node.device
        result.append(out)
        if out.node_name:
            seen.add((out.node_name, m.linux_interface))

    # Fill gaps: add entries for interfaces found in link_states but missing
    # from interface_mappings (covers VMs and any other provider types)
    link_states = (
        database.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    )

    for ls in link_states:
        for node_name, iface in [
            (ls.source_node, ls.source_interface),
            (ls.target_node, ls.target_interface),
        ]:
            if not node_name or not iface:
                continue
            if (node_name, iface) in seen:
                continue
            seen.add((node_name, iface))

            node = node_name_to_node.get(node_name)
            vendor = None
            if node and node.device:
                v = denormalize_interface(iface, node.device)
                if v != iface:
                    vendor = v

            result.append(schemas.InterfaceMappingOut(
                id=f"computed-{node_name}-{iface}",
                lab_id=lab_id,
                node_id=node.id if node else "",
                node_name=node_name,
                linux_interface=iface,
                vendor_interface=vendor,
                device_type=node.device if node else None,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ))

    return schemas.InterfaceMappingsResponse(
        mappings=result,
        total=len(result),
    )


@router.get("/labs/{lab_id}/nodes/{node_id}/interfaces")
def get_node_interfaces(
    lab_id: str,
    node_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.InterfaceMappingsResponse:
    """Get interface mappings for a specific node.

    Returns OVS port, Linux interface, and vendor interface mappings
    for all interfaces on the specified node.
    """
    get_lab_or_404(lab_id, database, current_user)

    # Get the Node definition
    node = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id, models.Node.gui_id == node_id)
        .first()
    )
    if not node:
        raise_not_found("Node not found")

    mappings = (
        database.query(models.InterfaceMapping)
        .filter(
            models.InterfaceMapping.lab_id == lab_id,
            models.InterfaceMapping.node_id == node.id,
        )
        .all()
    )

    return schemas.InterfaceMappingsResponse(
        mappings=[schemas.InterfaceMappingOut.model_validate(m) for m in mappings],
        total=len(mappings),
    )


@router.get("/labs/{lab_id}/nodes/{node_id}/interface-diagnostics")
async def get_node_interface_diagnostics(
    lab_id: str,
    node_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.NodeInterfaceDiagnosticResponse:
    """Return controller + agent diagnostics for a node's runtime and interfaces."""
    get_lab_or_404(lab_id, database, current_user)

    node = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id, models.Node.gui_id == node_id)
        .first()
    )
    if not node:
        raise_not_found("Node not found")

    node_state = (
        database.query(models.NodeState)
        .filter(models.NodeState.lab_id == lab_id, models.NodeState.node_definition_id == node.id)
        .first()
    )
    placement = (
        database.query(models.NodePlacement)
        .filter(models.NodePlacement.lab_id == lab_id, models.NodePlacement.node_definition_id == node.id)
        .first()
    )
    host = (
        database.query(models.Host)
        .filter(models.Host.id == placement.host_id)
        .first()
        if placement
        else None
    )

    mappings = (
        database.query(models.InterfaceMapping)
        .filter(
            models.InterfaceMapping.lab_id == lab_id,
            models.InterfaceMapping.node_id == node.id,
        )
        .all()
    )

    links: list[schemas.NodeLinkDiagnosticOut] = []
    link_rows = (
        database.query(models.LinkState)
        .filter(models.LinkState.lab_id == lab_id)
        .all()
    )
    for ls in link_rows:
        if ls.source_node == node.container_name:
            links.append(
                schemas.NodeLinkDiagnosticOut(
                    link_name=ls.link_name,
                    local_interface=ls.source_interface,
                    peer_node=ls.target_node,
                    peer_interface=ls.target_interface,
                    desired_state=ls.desired_state,
                    actual_state=ls.actual_state,
                    error_message=ls.error_message,
                )
            )
        elif ls.target_node == node.container_name:
            links.append(
                schemas.NodeLinkDiagnosticOut(
                    link_name=ls.link_name,
                    local_interface=ls.target_interface,
                    peer_node=ls.source_node,
                    peer_interface=ls.source_interface,
                    desired_state=ls.desired_state,
                    actual_state=ls.actual_state,
                    error_message=ls.error_message,
                )
            )

    agent_status = None
    live_ports: list[schemas.LivePortStateOut] = []
    agent_error = None
    if host and host.status == HostStatus.ONLINE.value:
        try:
            status_result = await _pkg().agent_client.get_lab_status_from_agent(host, lab_id)
            for runtime_node in status_result.get("nodes", []) or []:
                if runtime_node.get("name") == node.container_name:
                    agent_status = schemas.NodeRuntimeIdentityOut(
                        name=runtime_node.get("name"),
                        provider=runtime_node.get("provider"),
                        actual_state=runtime_node.get("state"),
                        node_definition_id=runtime_node.get("node_definition_id"),
                        runtime_id=runtime_node.get("runtime_id"),
                    )
                    break

            port_state = await _pkg().agent_client.get_lab_port_state(host, lab_id)
            for port in port_state or []:
                if port.get("node_name") == node.container_name:
                    live_ports.append(
                        schemas.LivePortStateOut(
                            interface_name=port.get("interface_name"),
                            ovs_port_name=port.get("ovs_port_name"),
                            vlan_tag=port.get("vlan_tag"),
                        )
                    )
        except Exception as exc:
            agent_error = str(exc)

    return schemas.NodeInterfaceDiagnosticResponse(
        lab_id=lab_id,
        node_id=node.gui_id,
        node_name=node.container_name,
        host_id=host.id if host else None,
        host_name=host.name if host else None,
        placement_status=placement.status if placement else None,
        controller_actual_state=node_state.actual_state if node_state else None,
        controller_is_ready=node_state.is_ready if node_state else None,
        controller_runtime_id=placement.runtime_id if placement else None,
        agent_status=agent_status,
        live_ports=live_ports,
        interface_mappings=[schemas.InterfaceMappingOut.model_validate(m) for m in mappings],
        links=links,
        agent_error=agent_error,
    )


@router.post("/labs/{lab_id}/interface-mappings/sync")
async def sync_interface_mappings(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.InterfaceMappingSyncResponse:
    """Sync interface mappings from all agents for a lab.

    Fetches OVS port information from agents and updates the
    interface_mappings table with current state.
    """
    get_lab_or_404(lab_id, database, current_user)

    result = await _pkg().interface_mapping_service.populate_all_agents(database, lab_id)

    return schemas.InterfaceMappingSyncResponse(
        created=result["created"],
        updated=result["updated"],
        errors=result["errors"],
        agents_queried=result["agents_queried"],
    )


@router.post("/labs/{lab_id}/nodes/{node_id}/interface-mappings/sync")
async def sync_node_interface_mappings(
    lab_id: str,
    node_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.InterfaceMappingSyncResponse:
    """Refresh interface mappings for one node from its current host's live data."""
    get_lab_or_404(lab_id, database, current_user)

    node = (
        database.query(models.Node)
        .filter(models.Node.lab_id == lab_id, models.Node.gui_id == node_id)
        .first()
    )
    if not node:
        raise_not_found("Node not found")

    placement = (
        database.query(models.NodePlacement)
        .filter(
            models.NodePlacement.lab_id == lab_id,
            models.NodePlacement.node_definition_id == node.id,
        )
        .first()
    )
    if not placement:
        raise HTTPException(status_code=409, detail="Node has no placement to refresh")

    agent = (
        database.query(models.Host)
        .filter(models.Host.id == placement.host_id)
        .first()
    )
    if not agent or agent.status != HostStatus.ONLINE.value:
        raise HTTPException(status_code=409, detail="Node host is offline or unavailable")

    result = await _pkg().interface_mapping_service.populate_node_from_agent(
        database,
        lab_id,
        node,
        agent,
    )

    return schemas.InterfaceMappingSyncResponse(
        created=result["created"],
        updated=result["updated"],
        errors=result["errors"],
        agents_queried=1,
    )


@router.get("/labs/{lab_id}/infra/notifications")
def get_infra_notifications(
    lab_id: str,
    database: Session = Depends(db.get_db),
    current_user: models.User = Depends(get_current_user),
) -> schemas.InfraNotificationsResponse:
    """Return infrastructure notifications for a lab.

    Surfaces tunnel cleanup deferrals, link errors, and node errors
    relevant to the lab's multi-host infrastructure.
    """
    get_lab_or_404(lab_id, database, current_user)
    notifications: list[schemas.InfraNotification] = []

    # 1. VxlanTunnel records with issues (cleanup/failed status OR non-null error_message)
    tunnels = (
        database.query(models.VxlanTunnel)
        .filter(
            models.VxlanTunnel.lab_id == lab_id,
            or_(
                models.VxlanTunnel.status.in_(["cleanup", "failed"]),
                and_(
                    models.VxlanTunnel.error_message.isnot(None),
                    models.VxlanTunnel.error_message != "",
                ),
            ),
        )
        .all()
    )
    for t in tunnels:
        has_error_msg = bool(t.error_message)
        if t.status in ("cleanup", "failed"):
            severity = "warning" if t.status == "cleanup" else "error"
            category = "tunnel_cleanup" if t.status == "cleanup" else "tunnel_failed"
            title = (
                f"Tunnel cleanup deferred (VNI {t.vni})"
                if t.status == "cleanup"
                else f"Tunnel failed (VNI {t.vni})"
            )
        elif has_error_msg:
            severity = "warning"
            category = "tunnel_cleanup"
            title = f"Tunnel issue (VNI {t.vni})"
        else:
            continue
        notifications.append(schemas.InfraNotification(
            id=f"tunnel:{t.id}",
            severity=severity,
            category=category,
            title=title,
            detail=t.error_message,
            entity_type="tunnel",
            entity_name=f"VNI {t.vni}",
            timestamp=t.updated_at if hasattr(t, "updated_at") else None,
        ))

    # 2. LinkState records with errors
    error_links = (
        database.query(models.LinkState)
        .filter(
            models.LinkState.lab_id == lab_id,
            models.LinkState.actual_state == "error",
        )
        .all()
    )
    for ls in error_links:
        notifications.append(schemas.InfraNotification(
            id=f"link:{ls.id}",
            severity="error",
            category="link_error",
            title=f"Link error: {ls.link_name}",
            detail=ls.error_message,
            entity_type="link",
            entity_name=ls.link_name,
            timestamp=ls.updated_at if hasattr(ls, "updated_at") else None,
        ))

    # 3. NodeState records with errors (infra-relevant)
    error_nodes = (
        database.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == lab_id,
            models.NodeState.actual_state == "error",
        )
        .all()
    )
    for ns in error_nodes:
        notifications.append(schemas.InfraNotification(
            id=f"node:{ns.id}",
            severity="error",
            category="node_error",
            title=f"Node error: {ns.node_name}",
            detail=ns.error_message,
            entity_type="node",
            entity_name=ns.node_name,
            timestamp=ns.updated_at if hasattr(ns, "updated_at") else None,
        ))

    return schemas.InfraNotificationsResponse(notifications=notifications)
