"""Image synchronization tasks for multi-agent deployments.

This module provides functions for synchronizing Docker images between
the controller and agents. It supports multiple sync strategies:
- push: Automatically push images to agents when uploaded
- pull: Agents pull missing images when they come online
- on_demand: Sync images only when needed for deployment
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.db import SessionLocal, get_session
from app.image_store import find_image_by_id, load_manifest
from app.services.topology import TopologyService


async def sync_image_to_agent(
    image_id: str,
    host_id: str,
    database: Session | None = None,
) -> tuple[bool, str | None]:
    """Sync a single image to a specific agent.

    Args:
        image_id: Image ID from the library (e.g., "docker:ceos:4.28.0F")
        host_id: Target agent's host ID
        database: Optional database session (creates one if not provided)

    Returns:
        Tuple of (success, error_message)
    """
    if database is not None:
        return await _sync_image_to_agent_impl(image_id, host_id, database)
    else:
        with get_session() as session:
            return await _sync_image_to_agent_impl(image_id, host_id, session)


async def _sync_image_to_agent_impl(
    image_id: str,
    host_id: str,
    database: Session,
) -> tuple[bool, str | None]:
    """Implementation of sync_image_to_agent."""
    try:
        # Get image from manifest
        manifest = load_manifest()
        image = find_image_by_id(manifest, image_id)
        if not image:
            return False, "Image not found in library"

        if image.get("kind") != "docker":
            return False, "Only Docker images can be synced"

        # Get target host
        host = database.get(models.Host, host_id)
        if not host:
            return False, "Host not found"

        if host.status != "online":
            return False, "Host is not online"

        # Check if already synced
        image_host = database.query(models.ImageHost).filter(
            models.ImageHost.image_id == image_id,
            models.ImageHost.host_id == host_id
        ).first()

        if image_host and image_host.status == "synced":
            return True, None

        # Create or update ImageHost record
        if not image_host:
            image_host = models.ImageHost(
                id=str(uuid4()),
                image_id=image_id,
                host_id=host_id,
                reference=image.get("reference", ""),
                status="syncing",
            )
            database.add(image_host)
        else:
            image_host.status = "syncing"
            image_host.error_message = None

        # Create sync job
        job = models.ImageSyncJob(
            id=str(uuid4()),
            image_id=image_id,
            host_id=host_id,
            status="pending",
        )
        database.add(job)
        database.commit()

        # Import the sync execution function
        from app.routers.images import _execute_sync_job

        # Execute sync
        await _execute_sync_job(job.id, image_id, image, host)

        # Check result
        job = database.get(models.ImageSyncJob, job.id)
        database.refresh(job)

        if job and job.status == "completed":
            return True, None
        else:
            return False, job.error_message if job else "Sync job disappeared"

    except Exception as e:
        return False, str(e)


async def check_agent_has_image(host: models.Host, reference: str) -> bool:
    """Check if an agent has a specific Docker image.

    Args:
        host: The host/agent to check
        reference: Docker image reference (e.g., "ceos:4.28.0F")

    Returns:
        True if the image exists on the agent
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # URL-encode the reference for the path
            from urllib.parse import quote
            encoded_ref = quote(reference, safe='')
            response = await client.get(
                f"http://{host.address}/images/{encoded_ref}"
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("exists", False)
            return False
    except Exception as e:
        print(f"Error checking image on {host.name}: {e}")
        return False


async def get_agent_image_inventory(host: models.Host) -> list[dict]:
    """Get list of Docker images on an agent.

    Args:
        host: The host/agent to query

    Returns:
        List of image info dicts with id, tags, size_bytes
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"http://{host.address}/images")
            if response.status_code == 200:
                result = response.json()
                return result.get("images", [])
            return []
    except Exception as e:
        print(f"Error getting image inventory from {host.name}: {e}")
        return []


async def reconcile_agent_images(host_id: str, database: Session | None = None):
    """Reconcile ImageHost records with actual agent inventory.

    Queries the agent for its Docker images and updates the ImageHost
    records in the database to reflect reality.

    Args:
        host_id: Host ID to reconcile
        database: Optional database session
    """
    if database is not None:
        # Use provided session
        await _reconcile_agent_images_impl(host_id, database)
    else:
        # Create our own session with proper cleanup
        with get_session() as session:
            await _reconcile_agent_images_impl(host_id, session)


async def _reconcile_agent_images_impl(host_id: str, database: Session):
    """Implementation of reconcile_agent_images."""
    try:
        host = database.get(models.Host, host_id)
        if not host or host.status != "online":
            return

        # Get agent's image inventory
        inventory = await get_agent_image_inventory(host)

        # Build set of image tags/IDs on agent
        agent_images = set()
        for img in inventory:
            agent_images.add(img.get("id", ""))
            for tag in img.get("tags", []):
                agent_images.add(tag)

        # Get all library images
        manifest = load_manifest()
        library_images = manifest.get("images", [])

        # Update ImageHost records
        for lib_image in library_images:
            if lib_image.get("kind") != "docker":
                continue

            image_id = lib_image.get("id")
            reference = lib_image.get("reference", "")

            # Check if image is on agent
            is_present = reference in agent_images

            # Get or create ImageHost record
            image_host = database.query(models.ImageHost).filter(
                models.ImageHost.image_id == image_id,
                models.ImageHost.host_id == host_id
            ).first()

            if is_present:
                if image_host:
                    if image_host.status != "synced":
                        image_host.status = "synced"
                        image_host.synced_at = datetime.now(timezone.utc)
                        image_host.error_message = None
                else:
                    image_host = models.ImageHost(
                        id=str(uuid4()),
                        image_id=image_id,
                        host_id=host_id,
                        reference=reference,
                        status="synced",
                        synced_at=datetime.now(timezone.utc),
                    )
                    database.add(image_host)
            else:
                if image_host:
                    if image_host.status == "synced":
                        # Image was there but now missing
                        image_host.status = "missing"
                else:
                    # No record yet, image not present
                    image_host = models.ImageHost(
                        id=str(uuid4()),
                        image_id=image_id,
                        host_id=host_id,
                        reference=reference,
                        status="missing",
                    )
                    database.add(image_host)

        database.commit()
        print(f"Reconciled images for agent {host.name}")

    except Exception as e:
        print(f"Error reconciling images for host {host_id}: {e}")
        import traceback
        traceback.print_exc()


async def push_image_on_upload(image_id: str, database: Session | None = None):
    """Push a newly uploaded image to all agents with 'push' strategy.

    Called after an image is uploaded to the controller.

    Args:
        image_id: The newly uploaded image ID
        database: Optional database session
    """
    if not settings.image_sync_enabled:
        return

    if database is not None:
        await _push_image_on_upload_impl(image_id, database)
    else:
        with get_session() as session:
            await _push_image_on_upload_impl(image_id, session)


async def _push_image_on_upload_impl(image_id: str, database: Session):
    """Implementation of push_image_on_upload."""
    # Get all online hosts with push strategy
    hosts = database.query(models.Host).filter(
        models.Host.status == "online",
        models.Host.image_sync_strategy == "push"
    ).all()

    if not hosts:
        return

    print(f"Pushing image {image_id} to {len(hosts)} agents")

    # Start sync tasks for each host
    for host in hosts:
        asyncio.create_task(sync_image_to_agent(image_id, host.id))


async def pull_images_on_registration(host_id: str, database: Session | None = None):
    """Pull all library images to a newly registered agent with 'pull' strategy.

    Called when an agent registers with the controller.

    Args:
        host_id: The newly registered agent's host ID
        database: Optional database session
    """
    if not settings.image_sync_enabled:
        return

    if database is not None:
        await _pull_images_on_registration_impl(host_id, database)
    else:
        with get_session() as session:
            await _pull_images_on_registration_impl(host_id, session)


async def _pull_images_on_registration_impl(host_id: str, database: Session):
    """Implementation of pull_images_on_registration."""
    host = database.get(models.Host, host_id)
    if not host:
        return

    # Check if host has pull strategy
    strategy = host.image_sync_strategy
    if not strategy:
        strategy = settings.image_sync_fallback_strategy

    if strategy != "pull":
        return

    print(f"Agent {host.name} has 'pull' strategy, syncing all images")

    # First reconcile to see what's already there
    await reconcile_agent_images(host_id, database)

    # Get all Docker images from library
    manifest = load_manifest()
    library_images = manifest.get("images", [])

    # Find images that need syncing
    for lib_image in library_images:
        if lib_image.get("kind") != "docker":
            continue

        image_id = lib_image.get("id")

        # Check current status
        image_host = database.query(models.ImageHost).filter(
            models.ImageHost.image_id == image_id,
            models.ImageHost.host_id == host_id
        ).first()

        if image_host and image_host.status == "synced":
            continue

        # Need to sync
        print(f"Syncing {image_id} to {host.name}")
        asyncio.create_task(sync_image_to_agent(image_id, host_id))


async def ensure_images_for_deployment(
    host_id: str,
    image_references: list[str],
    timeout: int | None = None,
    database: Session | None = None,
    lab_id: str | None = None,
    image_to_nodes: dict[str, list[str]] | None = None,
) -> tuple[bool, list[str], list[str]]:
    """Ensure all required images exist on agent before deployment.

    This is the pre-deploy check that ensures images are available
    on the target agent. If images are missing and on_demand sync
    is enabled, it will sync them.

    Args:
        host_id: Target agent's host ID
        image_references: List of Docker image references needed
        timeout: Max seconds to wait for sync (default from settings)
        database: Optional database session
        lab_id: Optional lab ID for updating NodeState records
        image_to_nodes: Optional mapping from image refs to node names

    Returns:
        Tuple of (all_ready, missing_images, log_entries)
        - all_ready: True if all images are available
        - missing_images: List of image references that are still missing
        - log_entries: List of log messages about what happened
    """
    log_entries: list[str] = []

    if not settings.image_sync_pre_deploy_check:
        return True, [], log_entries

    if timeout is None:
        timeout = settings.image_sync_timeout

    if database is not None:
        return await _ensure_images_for_deployment_impl(
            host_id, image_references, timeout, database, lab_id, image_to_nodes, log_entries
        )
    else:
        with get_session() as session:
            return await _ensure_images_for_deployment_impl(
                host_id, image_references, timeout, session, lab_id, image_to_nodes, log_entries
            )


async def _ensure_images_for_deployment_impl(
    host_id: str,
    image_references: list[str],
    timeout: int,
    database: Session,
    lab_id: str | None,
    image_to_nodes: dict[str, list[str]] | None,
    log_entries: list[str],
) -> tuple[bool, list[str], list[str]]:
    """Implementation of ensure_images_for_deployment."""
    # Use provided mapping or empty dict
    if image_to_nodes is None:
        image_to_nodes = {}

    def update_nodes_for_images(images: list[str], status: str | None, message: str | None = None):
        """Helper to update NodeState for nodes using given images."""
        if not lab_id or not image_to_nodes:
            return
        affected_nodes: set[str] = set()
        for img in images:
            if img in image_to_nodes:
                affected_nodes.update(image_to_nodes[img])
        if affected_nodes:
            update_node_image_sync_status(database, lab_id, list(affected_nodes), status, message)

    try:
        host = database.get(models.Host, host_id)
        if not host or host.status != "online":
            return False, image_references, log_entries

        # Check which images are missing
        log_entries.append(f"Checking {len(image_references)} image(s) on agent {host.name}...")
        update_nodes_for_images(image_references, "checking", "Checking image availability...")

        missing = []
        for reference in image_references:
            exists = await check_agent_has_image(host, reference)
            if not exists:
                missing.append(reference)

        if not missing:
            log_entries.append("All images already present on agent")
            update_nodes_for_images(image_references, None, None)  # Clear status
            return True, [], log_entries

        log_entries.append(f"Agent {host.name} missing {len(missing)} image(s): {', '.join(missing[:3])}" +
                          (f" (+{len(missing) - 3} more)" if len(missing) > 3 else ""))

        # Check if on_demand sync is enabled
        strategy = host.image_sync_strategy or settings.image_sync_fallback_strategy
        if strategy == "disabled":
            log_entries.append("Image sync is disabled for this agent")
            update_nodes_for_images(missing, "failed", "Image sync disabled")
            return False, missing, log_entries

        # Find image IDs in library for missing references
        manifest = load_manifest()
        image_map = {}  # reference -> image_id
        for lib_image in manifest.get("images", []):
            ref = lib_image.get("reference", "")
            if ref in missing:
                image_map[ref] = lib_image.get("id")

        # Start sync tasks for missing images
        sync_tasks = []
        for reference in missing:
            image_id = image_map.get(reference)
            if image_id:
                task = asyncio.create_task(sync_image_to_agent(image_id, host_id))
                sync_tasks.append((reference, task))

        if not sync_tasks:
            # No images found in library
            log_entries.append("Missing images not found in library - cannot sync")
            update_nodes_for_images(missing, "failed", "Image not in library")
            return False, missing, log_entries

        # Update nodes to syncing status
        syncing_refs = [ref for ref, _ in sync_tasks]
        log_entries.append(f"Pushing {len(sync_tasks)} image(s) to agent {host.name}...")
        update_nodes_for_images(syncing_refs, "syncing", f"Pushing image to {host.name}...")

        # Wait for syncs to complete with timeout
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*[t[1] for t in sync_tasks], return_exceptions=True),
                timeout=timeout
            )

            # Check results
            still_missing = []
            synced_images = []
            for (reference, _), result in zip(sync_tasks, results):
                if isinstance(result, Exception):
                    still_missing.append(reference)
                    log_entries.append(f"  {reference}: FAILED - {str(result)[:100]}")
                elif isinstance(result, tuple):
                    success, error = result
                    if not success:
                        still_missing.append(reference)
                        log_entries.append(f"  {reference}: FAILED - {error or 'Unknown error'}")
                    else:
                        synced_images.append(reference)
                        log_entries.append(f"  {reference}: synced successfully")

            # Update node states based on results
            if synced_images:
                update_nodes_for_images(synced_images, None, None)  # Clear status on success
            if still_missing:
                update_nodes_for_images(still_missing, "failed", "Image sync failed")

            if not still_missing:
                log_entries.append(f"All {len(synced_images)} image(s) synced successfully")
            else:
                log_entries.append(f"Image sync completed with {len(still_missing)} failure(s)")

            return len(still_missing) == 0, still_missing, log_entries

        except asyncio.TimeoutError:
            log_entries.append(f"Image sync timed out after {timeout}s")
            update_nodes_for_images(syncing_refs, "failed", "Sync timed out")
            return False, missing, log_entries

    except Exception as e:
        log_entries.append(f"Error during image sync: {e}")
        return False, image_references, log_entries


def get_images_from_topology(topology_yaml: str) -> list[str]:
    """Extract Docker image references from a topology YAML.

    Args:
        topology_yaml: The topology.yml content

    Returns:
        List of unique image references used in the topology
    """
    import yaml

    try:
        topology = yaml.safe_load(topology_yaml)
        if not topology:
            return []

        images = set()
        nodes = topology.get("topology", {}).get("nodes", {})

        for node_name, node_config in nodes.items():
            if isinstance(node_config, dict):
                image = node_config.get("image")
                if image:
                    images.add(image)

        return list(images)

    except Exception as e:
        print(f"Error parsing topology: {e}")
        return []


def get_images_from_db(lab_id: str, database: Session) -> list[str]:
    """Extract Docker image references from database topology.

    Args:
        lab_id: Lab ID to get images for
        database: Database session

    Returns:
        List of unique image references used in the topology
    """
    images = (
        database.query(models.Node.image)
        .filter(
            models.Node.lab_id == lab_id,
            models.Node.image.isnot(None),
        )
        .distinct()
        .all()
    )
    return [img[0] for img in images if img[0]]


def get_image_to_nodes_map(topology_yaml: str) -> dict[str, list[str]]:
    """Extract a mapping from image references to node names.

    Args:
        topology_yaml: The topology.yml content

    Returns:
        Dict mapping image references to list of node names using that image
    """
    import yaml

    try:
        topology = yaml.safe_load(topology_yaml)
        if not topology:
            return {}

        image_to_nodes: dict[str, list[str]] = {}
        nodes = topology.get("topology", {}).get("nodes", {})

        for node_name, node_config in nodes.items():
            if isinstance(node_config, dict):
                image = node_config.get("image")
                if image:
                    if image not in image_to_nodes:
                        image_to_nodes[image] = []
                    image_to_nodes[image].append(node_name)

        return image_to_nodes

    except Exception as e:
        print(f"Error parsing topology: {e}")
        return {}


def update_node_image_sync_status(
    database: Session,
    lab_id: str,
    node_names: list[str],
    status: str | None,
    message: str | None = None,
) -> None:
    """Update image sync status for nodes.

    Args:
        database: Database session
        lab_id: Lab ID
        node_names: List of node names to update
        status: New status (None, "checking", "syncing", "synced", "failed")
        message: Optional message (progress or error)
    """
    if not node_names:
        return

    # Update NodeState records
    database.query(models.NodeState).filter(
        models.NodeState.lab_id == lab_id,
        models.NodeState.node_name.in_(node_names),
    ).update(
        {
            models.NodeState.image_sync_status: status,
            models.NodeState.image_sync_message: message,
        },
        synchronize_session=False,
    )
    database.commit()
