"""Per-node and lab-level operations: deploy, destroy, create, start, stop, readiness."""

from __future__ import annotations

import json
import logging

from app import models
from app.config import settings
from app.agent_client.http import (
    _agent_request,
    _safe_agent_request,
    _timed_node_operation,
    AgentError,
    AgentJobError,
)
from app.agent_client.selection import get_agent_url


logger = logging.getLogger(__name__)


async def deploy_to_agent(
    agent: models.Host,
    job_id: str,
    lab_id: str,
    topology: dict | None = None,
    provider: str = "docker",
) -> dict:
    """Send deploy request to agent with retry logic.

    Args:
        agent: The agent to deploy to
        job_id: Job identifier
        lab_id: Lab identifier
        topology: Structured topology dict
        provider: Provider to use (default: docker)

    Returns:
        Agent response dict
    """
    if topology is None:
        raise ValueError("Deploy requires topology JSON; topology_yaml is no longer supported")

    url = f"{get_agent_url(agent)}/jobs/deploy"
    logger.info(
        "Agent request",
        extra={
            "event": "agent_request",
            "method": "deploy",
            "agent_id": agent.id,
            "agent_name": agent.name,
            "lab_id": lab_id,
            "provider": provider,
        },
    )

    import time as _time
    _t0 = _time.monotonic()
    try:
        # Reduce retries for deploy since it's a long operation and agent has its own deduplication
        payload: dict = {
            "job_id": job_id,
            "lab_id": lab_id,
            "provider": provider,
            "topology": topology,
        }
        result = await _agent_request(
            "POST",
            url,
            json_body=payload,
            timeout=settings.agent_deploy_timeout,
            max_retries=1,
        )
        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        logger.info(
            "Agent response",
            extra={
                "event": "agent_response",
                "method": "deploy",
                "agent_id": agent.id,
                "lab_id": lab_id,
                "status": result.get("status", "unknown"),
                "duration_ms": elapsed_ms,
            },
        )
        return result
    except AgentError as e:
        elapsed_ms = int((_time.monotonic() - _t0) * 1000)
        logger.info(
            "Agent response",
            extra={
                "event": "agent_response",
                "method": "deploy",
                "agent_id": agent.id,
                "lab_id": lab_id,
                "status": "error",
                "duration_ms": elapsed_ms,
                "error": e.message,
            },
        )
        e.agent_id = agent.id
        raise


async def destroy_on_agent(
    agent: models.Host,
    job_id: str,
    lab_id: str,
) -> dict:
    """Send destroy request to agent with retry logic."""
    url = f"{get_agent_url(agent)}/jobs/destroy"
    logger.info(f"Destroying lab {lab_id} via agent {agent.id}")

    try:
        result = await _agent_request(
            "POST",
            url,
            json_body={"job_id": job_id, "lab_id": lab_id},
            timeout=settings.agent_destroy_timeout,
        )
        logger.info(f"Destroy completed for lab {lab_id}: {result.get('status')}")
        return result
    except AgentError as e:
        e.agent_id = agent.id
        raise


async def get_lab_status_from_agent(
    agent: models.Host,
    lab_id: str,
) -> dict:
    """Get lab status from agent with retry logic."""
    url = f"{get_agent_url(agent)}/labs/{lab_id}/status"

    try:
        return await _agent_request(
            "GET",
            url,
            timeout=settings.agent_status_timeout,
            max_retries=1,
            metric_operation="get_lab_status",
            metric_host_id=agent.id,
        )
    except AgentError as e:
        e.agent_id = agent.id
        raise


async def reconcile_nodes_on_agent(
    agent: models.Host,
    lab_id: str,
    nodes: list[dict],
) -> dict:
    """Reconcile nodes to their desired states on an agent.

    Args:
        agent: The agent managing the nodes
        lab_id: Lab identifier
        nodes: List of dicts with 'container_name' and 'desired_state' keys

    Returns:
        Dict with 'lab_id', 'results' list, and optionally 'error' key
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/reconcile"
    try:
        return await _agent_request(
            "POST",
            url,
            json_body={"nodes": nodes},
            timeout=settings.agent_deploy_timeout,
            max_retries=0,
        )
    except AgentError as e:
        raise AgentError(
            f"Reconcile request failed: {e}",
            agent_id=agent.id,
        ) from e


async def check_node_readiness(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    kind: str | None = None,
    provider_type: str | None = None,
) -> dict:
    """Check if a node has completed its boot sequence.

    Args:
        agent: The agent managing the node
        lab_id: Lab identifier
        node_name: Name of the node to check
        kind: Device kind (e.g., "cisco_iosv") - required for VM readiness
        provider_type: Provider type ("docker" or "libvirt") - auto-detected if None

    Returns:
        Dict with 'is_ready', 'message', and optionally 'progress_percent' keys
    """
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/{node_name}/ready"

    # Add query parameters if provided
    params = {}
    if kind:
        params["kind"] = kind
    if provider_type:
        params["provider_type"] = provider_type

    try:
        return await _agent_request(
            "GET",
            url,
            params=params or None,
            timeout=10.0,
            max_retries=0,
            metric_operation="check_node_readiness",
            metric_host_id=agent.id,
        )
    except Exception as e:
        logger.error(f"Failed to check readiness for {node_name} on agent {agent.id}: {e}")
        return {
            "is_ready": False,
            "message": f"Readiness check failed: {str(e)}",
            "progress_percent": None,
        }


async def get_node_runtime_profile(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    provider_type: str | None = None,
) -> dict:
    """Get runtime profile for a node from an agent."""
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/{node_name}/runtime"
    params = {"provider_type": provider_type} if provider_type else None
    return await _agent_request(
        "GET",
        url,
        params=params,
        timeout=10.0,
        max_retries=0,
        metric_operation="get_node_runtime",
        metric_host_id=agent.id,
    )


async def container_action(
    agent: models.Host,
    container_name: str,
    action: str,  # "start" or "stop"
    lab_id: str | None = None,
) -> dict:
    """Execute start/stop action on a specific container or VM.

    Args:
        agent: The agent where the container/VM is running
        container_name: Full container name (e.g., "arch-labid-nodename")
        action: "start" or "stop"
        lab_id: Optional lab ID. When provided, uses the reconcile endpoint
                which supports both Docker containers and libvirt VMs.

    Returns:
        Dict with 'success' key and optional 'error' message
    """
    logger.info(f"Container {action} for {container_name} via agent {agent.id}")

    # If lab_id is provided, use the reconcile endpoint which handles both
    # Docker containers and libvirt VMs
    if lab_id:
        desired_state = "running" if action == "start" else "stopped"
        try:
            result = await reconcile_nodes_on_agent(
                agent,
                lab_id,
                nodes=[{"container_name": container_name, "desired_state": desired_state}],
            )
            # Extract result for this specific node
            results = result.get("results", [])
            if results:
                node_result = results[0]
                if node_result.get("success"):
                    logger.info(f"Container {action} completed for {container_name}")
                    return {"success": True, "message": f"Container {node_result.get('action', action)}"}
                else:
                    error_msg = node_result.get("error", f"{action} failed")
                    logger.warning(f"Container {action} failed for {container_name}: {error_msg}")
                    return {"success": False, "error": error_msg}
            else:
                return {"success": False, "error": "No result from reconcile"}
        except AgentError as e:
            logger.error(f"Container {action} failed for {container_name}: {e.message}")
            return {"success": False, "error": e.message}
        except Exception as e:
            logger.error(f"Container {action} failed for {container_name}: {e}")
            return {"success": False, "error": str(e)}

    # Legacy path: use the Docker-only endpoint when lab_id is not provided
    url = f"{get_agent_url(agent)}/containers/{container_name}/{action}"

    try:
        result = await _agent_request(
            "POST",
            url,
            timeout=60.0,
            max_retries=0,
        )
        if result.get("success"):
            logger.info(f"Container {action} completed for {container_name}")
        else:
            logger.warning(f"Container {action} failed for {container_name}: {result.get('error')}")
        return result
    except AgentJobError as e:
        error_msg = e.message
        try:
            if e.stderr and "Response:" in e.stderr:
                error_body = e.stderr.split("Response:", 1)[1].strip()
                data = json.loads(error_body)
                if isinstance(data, dict):
                    error_msg = data.get("detail", error_msg)
        except Exception:
            pass
        logger.error(f"Container {action} failed for {container_name}: {error_msg}")
        return {"success": False, "error": error_msg}
    except Exception as e:
        logger.error(f"Container {action} failed for {container_name}: {e}")
        return {"success": False, "error": str(e)}


async def create_node_on_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    kind: str,
    *,
    node_definition_id: str | None = None,
    image: str | None = None,
    display_name: str | None = None,
    interface_count: int | None = None,
    binds: list[str] | None = None,
    env: dict[str, str] | None = None,
    startup_config: str | None = None,
    provider: str = "docker",
    memory: int | None = None,
    cpu: int | None = None,
    cpu_limit: int | None = None,
    disk_driver: str | None = None,
    nic_driver: str | None = None,
    machine_type: str | None = None,
    libvirt_driver: str | None = None,
    readiness_probe: str | None = None,
    readiness_pattern: str | None = None,
    readiness_timeout: int | None = None,
    efi_boot: bool | None = None,
    efi_vars: str | None = None,
    data_volume_gb: int | None = None,
    image_sha256: str | None = None,
) -> dict:
    """Create a single node container on an agent without starting it."""
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/{node_name}/create?provider={provider}"

    payload: dict = {"node_name": node_name, "kind": kind}
    if node_definition_id:
        payload["node_definition_id"] = node_definition_id
    if image:
        payload["image"] = image
    if display_name:
        payload["display_name"] = display_name
    if interface_count is not None:
        payload["interface_count"] = interface_count
    if binds:
        payload["binds"] = binds
    if env:
        payload["env"] = env
    if startup_config:
        payload["startup_config"] = startup_config
    if memory:
        payload["memory"] = memory
    if cpu:
        payload["cpu"] = cpu
    if cpu_limit is not None:
        payload["cpu_limit"] = cpu_limit
    if disk_driver:
        payload["disk_driver"] = disk_driver
    if nic_driver:
        payload["nic_driver"] = nic_driver
    if machine_type:
        payload["machine_type"] = machine_type
    if libvirt_driver:
        payload["libvirt_driver"] = libvirt_driver
    if readiness_probe:
        payload["readiness_probe"] = readiness_probe
    if readiness_pattern:
        payload["readiness_pattern"] = readiness_pattern
    if readiness_timeout:
        payload["readiness_timeout"] = readiness_timeout
    if efi_boot is not None:
        payload["efi_boot"] = efi_boot
    if efi_vars:
        payload["efi_vars"] = efi_vars
    if data_volume_gb is not None:
        payload["data_volume_gb"] = data_volume_gb
    if image_sha256:
        payload["image_sha256"] = image_sha256

    return await _timed_node_operation(
        agent, "POST", url, "create_node", lab_id, node_name,
        json_body=payload, timeout=120.0,
    )


async def probe_runtime_conflict_on_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    *,
    node_definition_id: str | None = None,
    provider: str = "docker",
) -> dict:
    """Probe whether the target runtime namespace is safe for create."""
    url = (
        f"{get_agent_url(agent)}/labs/{lab_id}/nodes/"
        f"{node_name}/runtime-conflict?provider={provider}"
    )
    payload: dict[str, str] = {"node_name": node_name}
    if node_definition_id:
        payload["node_definition_id"] = node_definition_id
    return await _timed_node_operation(
        agent,
        "POST",
        url,
        "probe_runtime_conflict",
        lab_id,
        node_name,
        json_body=payload,
        timeout=30.0,
    )


async def start_node_on_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    *,
    repair_endpoints: bool = True,
    fix_interfaces: bool = True,
    provider: str = "docker",
) -> dict:
    """Start a node on an agent with optional veth repair."""
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/{node_name}/start?provider={provider}"
    return await _timed_node_operation(
        agent, "POST", url, "start_node", lab_id, node_name,
        json_body={"repair_endpoints": repair_endpoints, "fix_interfaces": fix_interfaces},
        timeout=120.0,
    )


async def stop_node_on_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    *,
    provider: str = "docker",
) -> dict:
    """Stop a node on an agent."""
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/{node_name}/stop?provider={provider}"
    return await _timed_node_operation(
        agent, "POST", url, "stop_node", lab_id, node_name,
        timeout=60.0,
    )


async def destroy_node_on_agent(
    agent: models.Host,
    lab_id: str,
    node_name: str,
    *,
    provider: str = "docker",
) -> dict:
    """Destroy a node container on an agent."""
    url = f"{get_agent_url(agent)}/labs/{lab_id}/nodes/{node_name}?provider={provider}"
    return await _timed_node_operation(
        agent, "DELETE", url, "destroy_node", lab_id, node_name,
        timeout=60.0,
    )


# --- Orphan Cleanup Functions ---

async def destroy_lab_on_agent(
    agent: models.Host,
    lab_id: str,
) -> dict:
    """Destroy a lab's containers on a specific agent (for orphan cleanup).

    This is used when a lab has moved to a new agent and we need to
    clean up orphaned containers on the old agent.

    Args:
        agent: The agent to clean up
        lab_id: Lab identifier

    Returns:
        Agent response dict with status and details
    """
    from uuid import uuid4

    url = f"{get_agent_url(agent)}/jobs/destroy"
    logger.info(f"Cleaning up orphan containers for lab {lab_id} on agent {agent.id}")

    try:
        result = await _agent_request(
            "POST",
            url,
            json_body={
                "job_id": f"orphan-cleanup-{uuid4()}",
                "lab_id": lab_id,
            },
            timeout=120.0,
            max_retries=0,
        )
        logger.info(f"Orphan cleanup completed for lab {lab_id} on agent {agent.id}")
        return result
    except Exception as e:
        logger.error(f"Failed to cleanup orphans for lab {lab_id} on agent {agent.id}: {e}")
        return {"status": "failed", "error": str(e)}


async def destroy_container_on_agent(
    agent: models.Host,
    lab_id: str,
    container_name: str,
) -> dict:
    """Destroy a single container on a specific agent.

    This is used for live node removal when a user deletes a node from
    the canvas. It only removes the specified container, not the whole lab.

    Args:
        agent: The agent hosting the container
        lab_id: Lab identifier
        container_name: Name of the container to destroy

    Returns:
        Dict with 'success' bool and 'error' message if failed
    """
    url = f"{get_agent_url(agent)}/containers/{lab_id}/{container_name}"
    logger.info(f"Destroying container {container_name} for lab {lab_id} on agent {agent.id}")

    try:
        result = await _agent_request(
            "DELETE",
            url,
            timeout=60.0,
            max_retries=0,
        )
        logger.info(f"Container {container_name} destroyed on agent {agent.id}")
        return {"success": True, **result}
    except Exception as e:
        logger.error(f"Failed to destroy container {container_name} on agent {agent.id}: {e}")
        return {"success": False, "error": str(e)}


# --- Reconciliation Functions ---

async def discover_labs_on_agent(agent: models.Host) -> dict:
    """Discover all running labs on an agent."""
    return await _safe_agent_request(
        agent, "GET", "/discover-labs",
        fallback={"labs": []}, timeout=30.0,
        description="Discover labs", log_level="error",
    )


async def get_runtime_identity_audit(agent: models.Host) -> dict:
    """Query runtime identity audit coverage from an agent."""
    return await _safe_agent_request(
        agent, "GET", "/runtime-identity-audit",
        fallback={"providers": [], "errors": []},
        timeout=30.0,
        description="Runtime identity audit",
        log_level="error",
    )


async def backfill_runtime_identity(
    agent: models.Host,
    entries: list[dict[str, str]],
    *,
    dry_run: bool = True,
) -> dict:
    """Request runtime identity backfill on an agent."""
    return await _safe_agent_request(
        agent,
        "POST",
        "/runtime-identity/backfill",
        json_body={"entries": entries, "dry_run": dry_run},
        fallback={"providers": [], "errors": []},
        timeout=60.0,
        description="Runtime identity backfill",
        log_level="warning",
    )


async def cleanup_orphans_on_agent(agent: models.Host, valid_lab_ids: list[str]) -> dict:
    """Tell agent to clean up orphan containers."""
    return await _safe_agent_request(
        agent, "POST", "/cleanup-orphans",
        json_body={"valid_lab_ids": valid_lab_ids},
        fallback={"removed_containers": [], "errors": []},
        timeout=120.0, description="Cleanup orphans", log_level="error",
    )


async def cleanup_lab_orphans(
    agent: models.Host,
    lab_id: str,
    keep_node_names: list[str],
) -> dict:
    """Tell agent to clean up orphan containers for a specific lab.

    Used when nodes are migrated between agents. Removes containers for
    nodes that are no longer assigned to this agent.

    Args:
        agent: The agent to clean up
        lab_id: Lab identifier
        keep_node_names: List of node names that should be kept on this agent

    Returns dict with 'removed_containers' and 'kept_containers' keys.
    """
    url = f"{get_agent_url(agent)}/cleanup-lab-orphans"

    try:
        return await _agent_request(
            "POST",
            url,
            json_body={
                "lab_id": lab_id,
                "keep_node_names": keep_node_names,
            },
            timeout=120.0,
            max_retries=0,
        )
    except Exception as e:
        logger.error(f"Failed to cleanup lab orphans on agent {agent.id}: {e}")
        return {"removed_containers": [], "kept_containers": [], "errors": [str(e)]}


# --- Lock Management Functions ---

async def get_agent_lock_status(agent: models.Host) -> dict:
    """Get lock status from an agent."""
    return await _safe_agent_request(
        agent, "GET", "/locks/status",
        fallback={"locks": []}, timeout=10.0,
        description="Get lock status", log_level="error",
    )


async def release_agent_lock(agent: models.Host, lab_id: str) -> dict:
    """Release a stuck lock on an agent."""
    result = await _safe_agent_request(
        agent, "POST", f"/locks/{lab_id}/release",
        fallback={"status": "error"}, timeout=10.0,
        description=f"Release lock for lab {lab_id}", log_level="error",
    )
    if result.get("status") == "cleared":
        logger.info(f"Released stuck lock for lab {lab_id} on agent {agent.id}")
    return result


# Alias for clarity - force_release emphasizes this is for stuck recovery
force_release_lock = release_agent_lock


async def get_agent_images(agent: models.Host) -> dict:
    """Get list of Docker images on an agent."""
    return await _safe_agent_request(
        agent, "GET", "/images",
        fallback={"images": []}, timeout=30.0,
        description="Get images", log_level="error",
    )


async def backfill_image_metadata(agent: models.Host, entries: dict[str, str]) -> dict:
    """Push {reference: device_id} mappings to an agent's metadata store."""
    return await _safe_agent_request(
        agent, "POST", "/images/backfill-metadata",
        json_body=entries, fallback={"updated": 0}, timeout=30.0,
        description="Backfill image metadata", log_level="warning",
    )
