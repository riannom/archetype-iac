"""Lab status, config extraction, container control, and cleanup endpoints."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import docker
from fastapi import APIRouter, HTTPException

from agent.config import settings
from agent.docker_client import get_docker_client
from agent.helpers import (
    get_workspace, get_provider_for_request, provider_status_to_schema,
    _validate_container_name, _sync_prune_docker,
)
from agent.providers import get_provider, list_providers
from agent.schemas import (
    CleanupLabOrphansRequest, CleanupLabOrphansResponse,
    CleanupOrphansRequest, CleanupOrphansResponse,
    CleanupWorkspacesRequest,
    DiscoveredLab, DiscoverLabsResponse,
    DockerPruneRequest, DockerPruneResponse,
    ExtractConfigsResponse, ExtractNodeConfigResponse, ExtractedConfig,
    LabStatusResponse,
    NodeInfo, NodeReconcileRequest, NodeReconcileResponse, NodeReconcileResult, NodeStatus,
    UpdateConfigRequest, UpdateConfigResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["labs"])


@router.get("/labs/{lab_id}/status")
async def lab_status(lab_id: str) -> LabStatusResponse:
    """Get status of all nodes in a lab.

    Queries both Docker and libvirt providers and merges the results.
    This supports mixed labs with both containers and VMs.
    """
    logger.debug(f"Status request: lab={lab_id}")

    workspace = get_workspace(lab_id)
    all_nodes: list[NodeInfo] = []
    errors: list[str] = []

    # Query Docker provider
    docker_provider = get_provider("docker")
    if docker_provider:
        try:
            docker_result = await docker_provider.status(
                lab_id=lab_id,
                workspace=workspace,
            )
            for node in docker_result.nodes:
                all_nodes.append(NodeInfo(
                    name=node.name,
                    status=provider_status_to_schema(node.status),
                    container_id=node.container_id,
                    image=node.image,
                    ip_addresses=node.ip_addresses,
                ))
            if docker_result.error:
                errors.append(f"Docker: {docker_result.error}")
        except Exception as e:
            errors.append(f"Docker query failed: {e}")

    # Query libvirt provider for VMs
    libvirt_provider = get_provider("libvirt")
    if libvirt_provider:
        try:
            libvirt_result = await libvirt_provider.status(
                lab_id=lab_id,
                workspace=workspace,
            )
            for node in libvirt_result.nodes:
                all_nodes.append(NodeInfo(
                    name=node.name,
                    status=provider_status_to_schema(node.status),
                    container_id=node.container_id,
                    image=node.image,
                    ip_addresses=node.ip_addresses,
                ))
            if libvirt_result.error:
                errors.append(f"Libvirt: {libvirt_result.error}")
        except Exception as e:
            logger.debug(f"Libvirt query failed (may not have VMs): {e}")

    return LabStatusResponse(
        lab_id=lab_id,
        nodes=all_nodes,
        error="; ".join(errors) if errors else None,
    )


async def _reconcile_single_node(
    lab_id: str,
    target,
    workspace: Path,
) -> NodeReconcileResult:
    """Process one node reconciliation - returns result, never raises."""
    container_name = target.container_name
    desired = target.desired_state

    # Try Docker first
    try:
        def _sync_get_container(cn):
            client = get_docker_client()
            return client.containers.get(cn)

        container = await asyncio.to_thread(_sync_get_container, container_name)
        current_status = container.status

        if desired == "running":
            if current_status == "running":
                return NodeReconcileResult(
                    container_name=container_name,
                    action="already_running",
                    success=True,
                )
            else:
                await asyncio.to_thread(container.start)
                logger.info(f"Started container {container_name}")
                return NodeReconcileResult(
                    container_name=container_name,
                    action="started",
                    success=True,
                )

        elif desired == "stopped":
            from agent.readiness import clear_post_boot_state
            # Unified lifecycle: stop = remove container entirely
            if current_status == "running":
                await asyncio.to_thread(container.stop, timeout=settings.container_stop_timeout)
            await asyncio.to_thread(container.remove, force=True, v=True)
            clear_post_boot_state(container_name)
            logger.info(f"Stopped and removed container {container_name}")

            # Clean up lab-level resources if this was the last container.
            docker_provider = get_provider("docker")
            cleanup_if_empty = getattr(docker_provider, "cleanup_lab_resources_if_empty", None)
            if docker_provider and callable(cleanup_if_empty):
                try:
                    cleanup_result = await cleanup_if_empty(lab_id, workspace)
                    if cleanup_result.get("cleaned"):
                        logger.info(
                            f"Reconcile: cleaned lab resources for {lab_id} "
                            f"(networks_deleted={cleanup_result.get('networks_deleted', 0)})"
                        )
                    elif cleanup_result.get("error"):
                        logger.warning(
                            f"Reconcile: skipped lab cleanup for {lab_id}: "
                            f"{cleanup_result['error']}"
                        )
                except Exception as net_err:
                    logger.warning(f"Reconcile: lab cleanup failed for lab {lab_id}: {net_err}")

            return NodeReconcileResult(
                container_name=container_name,
                action="removed",
                success=True,
            )

    except docker.errors.NotFound:
        # Docker container not found, try libvirt below
        pass
    except Exception as e:
        logger.error(f"Error reconciling Docker container {container_name}: {e}")
        return NodeReconcileResult(
            container_name=container_name,
            action="error",
            success=False,
            error=str(e),
        )

    # Try libvirt for VMs
    libvirt_provider = get_provider("libvirt")
    if libvirt_provider:
        try:
            # Extract node name from container_name
            # NLM truncates lab_id to 20 chars: archetype-{lab_id[:20]}-{node}
            # Libvirt domains also truncate: arch-{lab_id[:20]}-{node}
            # Must use same sanitization as NLM's _get_container_name()
            import re as _re
            safe_lab = _re.sub(r"[^a-zA-Z0-9_-]", "", lab_id)[:20]
            archetype_prefix = f"archetype-{safe_lab}-"
            arch_prefix = f"arch-{safe_lab}-"
            if container_name.startswith(archetype_prefix):
                node_name = container_name[len(archetype_prefix):]
            elif container_name.startswith(arch_prefix):
                node_name = container_name[len(arch_prefix):]
            else:
                node_name = container_name  # fallback

            if desired == "running":
                result = await libvirt_provider.start_node(lab_id, node_name, workspace)
                if result.success:
                    action = "started" if result.new_status == NodeStatus.RUNNING else "already_running"
                    return NodeReconcileResult(
                        container_name=container_name,
                        action=action,
                        success=True,
                    )
                else:
                    return NodeReconcileResult(
                        container_name=container_name,
                        action="error",
                        success=False,
                        error=result.error or "Failed to start VM",
                    )

            elif desired == "stopped":
                result = await libvirt_provider.stop_node(lab_id, node_name, workspace)
                if result.success:
                    action = "stopped" if result.new_status == NodeStatus.STOPPED else "already_stopped"
                    return NodeReconcileResult(
                        container_name=container_name,
                        action=action,
                        success=True,
                    )
                else:
                    return NodeReconcileResult(
                        container_name=container_name,
                        action="error",
                        success=False,
                        error=result.error or "Failed to stop VM",
                    )

        except Exception as e:
            logger.error(f"Error reconciling libvirt VM {container_name}: {e}")

    # Neither Docker nor libvirt found the node
    if desired == "stopped":
        # Unified lifecycle: if node is already gone and we want it stopped, that's fine
        logger.info(f"Node {container_name} already absent (desired=stopped)")
        return NodeReconcileResult(
            container_name=container_name,
            action="already_stopped",
            success=True,
        )
    logger.warning(f"Node {container_name} not found in Docker or libvirt")
    return NodeReconcileResult(
        container_name=container_name,
        action="error",
        success=False,
        error=f"Node not found: {container_name}",
    )


@router.post("/labs/{lab_id}/nodes/reconcile")
async def reconcile_nodes(
    lab_id: str,
    request: NodeReconcileRequest,
) -> NodeReconcileResponse:
    """Reconcile nodes to their desired states.

    For each node in the request, this endpoint will:
    - Start the container/VM if desired_state is "running" and it's stopped
    - Stop the container/VM if desired_state is "stopped" and it's running
    - Skip if already in the desired state

    Supports both Docker containers and libvirt VMs.
    Processes all nodes in parallel using asyncio.gather.
    """
    logger.info(f"Reconcile request: lab={lab_id}, nodes={len(request.nodes)}")

    workspace = get_workspace(lab_id)

    results = await asyncio.gather(*[
        _reconcile_single_node(lab_id, target, workspace)
        for target in request.nodes
    ])

    return NodeReconcileResponse(
        lab_id=lab_id,
        results=list(results),
    )


@router.post("/labs/{lab_id}/extract-configs")
async def extract_configs(lab_id: str) -> ExtractConfigsResponse:
    """Extract running configs from all nodes in a lab.

    This extracts the running-config from:
    - Docker containers (cEOS and other containerized devices)
    - Libvirt VMs (Cisco IOSv, CSR1000v, ASAv, etc.)

    Configs are saved to the workspace as startup-config files for persistence.
    Returns both the count and the actual config content for each node.
    """
    logger.info(f"Extract configs request: lab={lab_id}")

    try:
        workspace = get_workspace(lab_id)
        all_configs: list[tuple[str, str]] = []

        # Extract from Docker containers if enabled
        if settings.enable_docker:
            try:
                docker_provider = get_provider_for_request("docker")
                docker_configs = await docker_provider._extract_all_ceos_configs(lab_id, workspace)
                all_configs.extend(docker_configs)
                logger.info(f"Extracted {len(docker_configs)} Docker configs")
            except Exception as e:
                logger.warning(f"Docker config extraction failed: {e}")

        # Extract from libvirt VMs if enabled
        if settings.enable_libvirt:
            try:
                from agent.providers.libvirt import LibvirtProvider, LIBVIRT_AVAILABLE
                if LIBVIRT_AVAILABLE:
                    libvirt_provider = LibvirtProvider()
                    vm_configs = await libvirt_provider._extract_all_vm_configs(lab_id, workspace)
                    all_configs.extend(vm_configs)
                    logger.info(f"Extracted {len(vm_configs)} VM configs")
                else:
                    logger.debug("Libvirt not available, skipping VM config extraction")
            except ImportError:
                logger.debug("Libvirt provider not available, skipping VM config extraction")
            except Exception as e:
                logger.warning(f"VM config extraction failed: {e}")

        # Convert to response format
        configs = [
            ExtractedConfig(node_name=node_name, content=content)
            for node_name, content in all_configs
        ]

        return ExtractConfigsResponse(
            success=True,
            extracted_count=len(configs),
            configs=configs,
        )

    except Exception as e:
        logger.error(f"Extract configs error for lab {lab_id}: {e}", exc_info=True)
        return ExtractConfigsResponse(
            success=False,
            extracted_count=0,
            error=str(e),
        )


@router.post("/labs/{lab_id}/nodes/{node_name}/extract-config")
async def extract_node_config(
    lab_id: str,
    node_name: str,
) -> ExtractNodeConfigResponse:
    """Extract running config from a specific node in a lab."""
    logger.info(f"Extract node config request: lab={lab_id} node={node_name}")

    try:
        workspace = get_workspace(lab_id)

        # Try Docker node first.
        if settings.enable_docker:
            try:
                docker_provider = get_provider_for_request("docker")
                container_name = docker_provider.get_container_name(lab_id, node_name)
                container = await asyncio.to_thread(
                    docker_provider.docker.containers.get,
                    container_name,
                )
                if container.status == "running":
                    labels = container.labels or {}
                    kind = labels.get("archetype.node_kind", "")
                    from agent.vendors import get_config_extraction_settings
                    extraction_settings = get_config_extraction_settings(kind)
                    cmd = extraction_settings.command
                    config_content = None
                    if extraction_settings.method == "nvram":
                        config_content = await docker_provider._extract_config_via_nvram(
                            container_name, workspace
                        )
                    elif cmd:
                        if extraction_settings.method == "ssh":
                            config_content = await docker_provider._extract_config_via_ssh(
                                container, kind, cmd, node_name
                            )
                        elif extraction_settings.method == "docker":
                            config_content = await docker_provider._extract_config_via_docker(
                                container, cmd, node_name
                            )
                    if config_content and config_content.strip():
                        config_dir = workspace / "configs" / node_name
                        config_dir.mkdir(parents=True, exist_ok=True)
                        config_path = config_dir / "startup-config"
                        config_path.write_text(config_content)
                        logger.info(
                            f"Extracted config from Docker node {node_name} "
                            f"({len(config_content)} bytes)"
                        )
                        return ExtractNodeConfigResponse(
                            success=True,
                            node_name=node_name,
                            content=config_content,
                        )
            except Exception as e:
                logger.debug(f"Docker node extraction skipped/failed for {node_name}: {e}")

        # Then try libvirt VM node.
        if settings.enable_libvirt:
            try:
                libvirt_provider = get_provider("libvirt")
                if libvirt_provider:
                    domain_name = libvirt_provider._domain_name(lab_id, node_name)

                    def _sync_lookup_kind():
                        domain = libvirt_provider.conn.lookupByName(domain_name)
                        return libvirt_provider._get_domain_kind(domain)

                    kind = await libvirt_provider._run_libvirt(_sync_lookup_kind)
                    if kind:
                        result = await libvirt_provider._extract_config(lab_id, node_name, kind)
                        if result:
                            _, config_content = result
                            config_dir = workspace / "configs" / node_name
                            config_dir.mkdir(parents=True, exist_ok=True)
                            config_path = config_dir / "startup-config"
                            config_path.write_text(config_content)
                            logger.info(
                                f"Extracted config from VM node {node_name} "
                                f"({len(config_content)} bytes)"
                            )
                            return ExtractNodeConfigResponse(
                                success=True,
                                node_name=node_name,
                                content=config_content,
                            )
            except Exception as e:
                logger.debug(f"Libvirt node extraction skipped/failed for {node_name}: {e}")

        return ExtractNodeConfigResponse(
            success=False,
            node_name=node_name,
            error="Node not running, not found, or config extraction is not supported",
        )

    except Exception as e:
        logger.error(f"Extract node config error for {node_name}: {e}", exc_info=True)
        return ExtractNodeConfigResponse(
            success=False,
            node_name=node_name,
            error=str(e),
        )


@router.put("/labs/{lab_id}/nodes/{node_name}/config")
async def update_node_config(
    lab_id: str,
    node_name: str,
    request: UpdateConfigRequest,
) -> UpdateConfigResponse:
    """Update the startup config for a node.

    This saves the config to the agent's workspace so it will be used
    on next container restart/redeploy. Called by the API after extracting
    configs to sync the agent's workspace with the API's workspace.
    """
    logger.info(f"Update config request: lab={lab_id} node={node_name}")

    try:
        workspace = get_workspace(lab_id)
        config_dir = workspace / "configs" / node_name
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "startup-config"
        config_file.write_text(request.content)
        logger.info(f"Saved startup config for {node_name} ({len(request.content)} bytes)")
        return UpdateConfigResponse(success=True)
    except Exception as e:
        logger.error(f"Update config error for {node_name}: {e}", exc_info=True)
        return UpdateConfigResponse(success=False, error=str(e))


# --- Container Control Endpoints ---

@router.post("/containers/{container_name}/start")
async def start_container(container_name: str) -> dict:
    """Start a stopped container.

    Used by the sync system to start individual nodes without redeploying.
    Uses asyncio.to_thread() to avoid blocking the event loop.
    """
    if not _validate_container_name(container_name):
        raise HTTPException(status_code=400, detail="Invalid container name: must start with 'archetype-' or 'arch-'")
    logger.info(f"Starting container: {container_name}")

    try:
        def _sync_start():
            client = get_docker_client()
            container = client.containers.get(container_name)
            if container.status == "running":
                return {"success": True, "message": "Container already running"}
            container.start()
            return {"success": True, "message": "Container started"}

        return await asyncio.to_thread(_sync_start)

    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Container '{container_name}' not found")
    except docker.errors.APIError as e:
        logger.error(f"Docker API error starting {container_name}: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Error starting container {container_name}: {e}")
        return {"success": False, "error": str(e)}


@router.post("/containers/{container_name}/stop")
async def stop_container(container_name: str) -> dict:
    """Stop a running container.

    Used by the sync system to stop individual nodes without destroying the lab.
    Uses asyncio.to_thread() to avoid blocking the event loop.
    """
    if not _validate_container_name(container_name):
        raise HTTPException(status_code=400, detail="Invalid container name: must start with 'archetype-' or 'arch-'")
    logger.info(f"Stopping container: {container_name}")

    try:
        stop_timeout = settings.container_stop_timeout

        def _sync_stop():
            client = get_docker_client()
            container = client.containers.get(container_name)
            if container.status != "running":
                return {"success": True, "message": "Container already stopped"}
            container.stop(timeout=stop_timeout)
            return {"success": True, "message": "Container stopped"}

        return await asyncio.to_thread(_sync_stop)

    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Container '{container_name}' not found")
    except docker.errors.APIError as e:
        logger.error(f"Docker API error stopping {container_name}: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Error stopping container {container_name}: {e}")
        return {"success": False, "error": str(e)}


@router.delete("/containers/{container_name}")
async def remove_container(container_name: str, force: bool = False) -> dict:
    """Remove a container.

    Used to clean up orphan containers or containers that need to be recreated.
    Uses asyncio.to_thread() to avoid blocking the event loop.
    """
    if not _validate_container_name(container_name):
        raise HTTPException(status_code=400, detail="Invalid container name: must start with 'archetype-' or 'arch-'")
    logger.info(f"Removing container: {container_name} (force={force})")

    try:
        def _sync_remove():
            client = get_docker_client()
            container = client.containers.get(container_name)
            container.remove(force=force)
            return {"success": True, "message": "Container removed"}

        return await asyncio.to_thread(_sync_remove)

    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Container '{container_name}' not found")
    except docker.errors.APIError as e:
        logger.error(f"Docker API error removing {container_name}: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Error removing container {container_name}: {e}")
        return {"success": False, "error": str(e)}


@router.delete("/containers/{lab_id}/{container_name}")
async def remove_container_for_lab(
    lab_id: str, container_name: str, force: bool = False
) -> dict:
    """Remove a specific container for a lab.

    This endpoint is used for live node removal when a user deletes a node
    from the canvas. The lab_id is used for logging and validation.

    Args:
        lab_id: Lab identifier (for logging/validation)
        container_name: Name of the container to remove
        force: Whether to force removal of running container

    Returns:
        Dict with success status and message/error
    """
    if not _validate_container_name(container_name):
        raise HTTPException(status_code=400, detail="Invalid container name: must start with 'archetype-' or 'arch-'")
    logger.info(f"Removing container {container_name} for lab {lab_id} (force={force})")

    try:
        def _sync_remove_for_lab():
            client = get_docker_client()
            container = client.containers.get(container_name)

            # Validate container belongs to the lab (optional safety check)
            labels = container.labels or {}
            container_lab_id = labels.get("archetype.lab_id")
            if container_lab_id and container_lab_id != lab_id:
                logger.warning(
                    f"Container {container_name} belongs to lab {container_lab_id}, "
                    f"not {lab_id} - proceeding anyway"
                )

            # Stop first if running, then remove
            if container.status == "running":
                logger.info(f"Stopping running container {container_name} before removal")
                container.stop(timeout=10)

            container.remove(force=force)

        await asyncio.to_thread(_sync_remove_for_lab)
        logger.info(f"Successfully removed container {container_name} for lab {lab_id}")
        return {"success": True, "message": "Container removed"}

    except docker.errors.NotFound:
        logger.info(f"Container {container_name} not found (already removed)")
        return {"success": True, "message": "Container not found (already removed)"}
    except docker.errors.APIError as e:
        logger.error(f"Docker API error removing {container_name}: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Error removing container {container_name}: {e}")
        return {"success": False, "error": str(e)}


# --- Reconciliation Endpoints ---

@router.get("/discover-labs")
async def discover_labs() -> DiscoverLabsResponse:
    """Discover all running labs by inspecting all available providers.

    Used by controller to reconcile state after restart.
    Queries both Docker and libvirt providers (if enabled) and merges results.
    """
    logger.info("Discovering running labs...")

    # Query all available providers and merge results by lab_id
    merged: dict[str, list] = {}
    for provider_name in list_providers():
        provider = get_provider(provider_name)
        if provider is None:
            continue
        try:
            discovered = await provider.discover_labs()
            for lab_id, nodes in discovered.items():
                if lab_id not in merged:
                    merged[lab_id] = []
                merged[lab_id].extend(nodes)
        except Exception as e:
            logger.warning(f"discover_labs failed for provider {provider_name}: {e}")

    labs = [
        DiscoveredLab(
            lab_id=lab_id,
            nodes=[
                NodeInfo(
                    name=node.name,
                    status=provider_status_to_schema(node.status),
                    container_id=node.container_id,
                    image=node.image,
                    ip_addresses=node.ip_addresses,
                )
                for node in nodes
            ],
        )
        for lab_id, nodes in merged.items()
    ]

    return DiscoverLabsResponse(labs=labs)


@router.post("/cleanup-orphans")
async def cleanup_orphans(request: CleanupOrphansRequest) -> CleanupOrphansResponse:
    """Remove orphan resources across all available providers.

    Args:
        request: Contains list of valid lab IDs to keep

    Returns:
        List of removed container/VM names
    """
    logger.info(f"Cleaning up orphan resources, keeping {len(request.valid_lab_ids)} valid labs")

    valid_ids = set(request.valid_lab_ids)
    all_removed: list[str] = []
    errors: list[str] = []

    for provider_name in list_providers():
        provider = get_provider(provider_name)
        if provider is None:
            continue
        try:
            removed = await provider.cleanup_orphan_containers(valid_ids)
            all_removed.extend(removed)
        except Exception as e:
            msg = f"cleanup_orphans failed for provider {provider_name}: {e}"
            logger.warning(msg)
            errors.append(msg)

    return CleanupOrphansResponse(
        removed_containers=all_removed,
        errors=errors,
    )


@router.post("/cleanup-lab-orphans")
async def cleanup_lab_orphans(request: CleanupLabOrphansRequest) -> CleanupLabOrphansResponse:
    """Remove orphaned containers/VMs for a specific lab.

    Used when nodes are deleted from topology or migrated between agents.
    Removes containers and VMs for nodes that are no longer in the topology.

    Args:
        request: Contains lab_id and list of node_names to keep

    Returns:
        Lists of removed and kept containers/VMs
    """
    logger.info(f"Cleaning up orphan containers/VMs for lab {request.lab_id}, keeping {len(request.keep_node_names)} nodes")

    removed = []
    kept = []
    errors = []
    keep_set = set(request.keep_node_names)

    # Clean up Docker containers
    try:
        def _sync_cleanup_docker_orphans(lab_id, keep):
            _removed, _kept, _errors = [], [], []
            client = get_docker_client()
            containers = client.containers.list(all=True, filters={
                "label": f"archetype.lab_id={lab_id}"
            })
            for container in containers:
                node_name = container.labels.get("archetype.node_name")
                if not node_name:
                    continue
                if node_name in keep:
                    _kept.append(container.name)
                    logger.debug(f"Keeping container {container.name} (node {node_name} in topology)")
                else:
                    try:
                        logger.info(f"Removing orphan container {container.name} (node {node_name} deleted from topology)")
                        container.remove(force=True)
                        _removed.append(container.name)
                    except docker.errors.APIError as e:
                        error_msg = f"Failed to remove {container.name}: {e}"
                        logger.warning(error_msg)
                        _errors.append(error_msg)
            return _removed, _kept, _errors

        d_removed, d_kept, d_errors = await asyncio.to_thread(_sync_cleanup_docker_orphans, request.lab_id, keep_set)
        removed.extend(d_removed)
        kept.extend(d_kept)
        errors.extend(d_errors)

    except Exception as e:
        error_msg = f"Error during Docker orphan cleanup: {e}"
        logger.error(error_msg)
        errors.append(error_msg)

    # Clean up libvirt VMs if enabled
    if settings.enable_libvirt:
        try:
            from agent.providers.libvirt import LibvirtProvider, LIBVIRT_AVAILABLE
            if LIBVIRT_AVAILABLE:
                libvirt_provider = LibvirtProvider()
                workspace_base = Path(settings.workspace_path)
                vm_result = await libvirt_provider.cleanup_lab_orphan_domains(
                    lab_id=request.lab_id,
                    keep_node_names=keep_set,
                    workspace_base=workspace_base,
                )
                removed.extend(vm_result.get("domains", []))
                logger.info(f"Cleaned up {len(vm_result.get('domains', []))} orphan VMs")
            else:
                logger.debug("Libvirt not available, skipping VM orphan cleanup")
        except ImportError:
            logger.debug("Libvirt provider not available, skipping VM orphan cleanup")
        except Exception as e:
            error_msg = f"Error during VM orphan cleanup: {e}"
            logger.error(error_msg)
            errors.append(error_msg)

    return CleanupLabOrphansResponse(
        removed_containers=removed,
        kept_containers=kept,
        errors=errors,
    )


@router.post("/prune-docker")
async def prune_docker(request: DockerPruneRequest) -> DockerPruneResponse:
    """Prune Docker resources to reclaim disk space without blocking the event loop."""
    logger.info(
        f"Docker prune request: dangling_images={request.prune_dangling_images}, "
        f"build_cache={request.prune_build_cache}, unused_volumes={request.prune_unused_volumes}, "
        f"stopped_containers={request.prune_stopped_containers}, unused_networks={request.prune_unused_networks}"
    )
    return await asyncio.to_thread(_sync_prune_docker, request)


# --- Workspace Cleanup Endpoints ---


@router.delete("/labs/{lab_id}/workspace")
async def delete_lab_workspace(lab_id: str):
    """Remove a specific lab's workspace directory."""
    import shutil
    workspace = Path(settings.workspace_path) / lab_id
    if not workspace.exists():
        return {"success": True, "message": "workspace does not exist"}
    try:
        shutil.rmtree(workspace)
        logger.info(f"Deleted workspace for lab {lab_id}")
        return {"success": True, "deleted": str(workspace)}
    except Exception as e:
        logger.error(f"Failed to delete workspace for lab {lab_id}: {e}")
        return {"success": False, "error": str(e)}


@router.post("/cleanup-workspaces")
async def cleanup_workspaces(request: CleanupWorkspacesRequest):
    """Remove workspace directories for labs not in the valid list."""
    import shutil
    workspace_root = Path(settings.workspace_path)
    if not workspace_root.exists():
        return {"success": True, "removed": [], "errors": []}

    valid_set = set(request.valid_lab_ids)
    removed = []
    errors = []

    for entry in workspace_root.iterdir():
        if not entry.is_dir():
            continue
        # Skip known non-lab directories
        if entry.name in ("images", "uploads", ".tmp", "configs", ".poap-tftp"):
            continue
        if entry.name not in valid_set:
            try:
                shutil.rmtree(entry)
                removed.append(entry.name)
                logger.info(f"Cleaned orphaned agent workspace: {entry.name}")
            except Exception as e:
                errors.append(f"{entry.name}: {e}")
                logger.warning(f"Failed to clean workspace {entry.name}: {e}")

    return {"success": True, "removed": removed, "errors": errors}
