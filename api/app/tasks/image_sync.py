"""Image synchronization tasks for multi-agent deployments.

This module provides functions for synchronizing Docker images between
the controller and agents. It supports multiple sync strategies:
- push: Automatically push images to agents when uploaded
- pull: Agents pull missing images when they come online
- on_demand: Sync images only when needed for deployment
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.db import get_session
from app.image_store import find_image_by_id, load_manifest

logger = logging.getLogger(__name__)


def _is_file_reference(reference: str) -> bool:
    """Return True when reference points to a host filesystem image."""
    return reference.startswith("/") or reference.endswith((".qcow2", ".img", ".iol"))


def _required_provider_for_reference(reference: str) -> str | None:
    """Return required provider for a file-based image reference."""
    if reference.endswith((".qcow2", ".img")):
        return "libvirt"
    if reference.endswith(".iol"):
        return "docker"
    return None


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

        reference = image.get("reference", "")
        if image.get("kind") != "docker" and not _is_file_reference(reference):
            return False, f"Image kind '{image.get('kind')}' is not syncable"

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


async def check_agent_has_image(
    host: models.Host, reference: str, expected_sha256: str | None = None,
) -> bool:
    """Check if an agent has a specific image available.

    For Docker images: queries the agent's /images endpoint
    For qcow2/file images: checks if file exists and agent has libvirt capability

    Args:
        host: The host/agent to check
        reference: Image reference (Docker tag or file path for qcow2)
        expected_sha256: Optional expected SHA256 hash for integrity verification

    Returns:
        True if the image is available (and checksum matches, if both provided)
    """
    from urllib.parse import quote

    # Check if this is a file-based image (qcow2, iol)
    if _is_file_reference(reference):
        # Parse host capabilities
        caps = host.get_capabilities()
        host_providers = caps.get("providers", [])

        # qcow2/img files need libvirt, iol files need docker
        required_provider = _required_provider_for_reference(reference)
        if required_provider and required_provider not in host_providers:
            return False

        # Query the target agent directly for file existence.
        try:
            from app.agent_client import _get_agent_auth_headers
            async with httpx.AsyncClient(timeout=10.0) as client:
                encoded_ref = quote(reference, safe="")
                response = await client.get(
                    f"http://{host.address}/images/{encoded_ref}",
                    headers=_get_agent_auth_headers(),
                )
                if response.status_code == 200:
                    result = response.json()
                    if not result.get("exists", False):
                        return False
                    # Compare checksums if both sides have them
                    if expected_sha256 and result.get("sha256"):
                        if result["sha256"] != expected_sha256:
                            logger.warning(
                                  f"Checksum mismatch on {host.name} for {reference}: "
                                  f"expected {expected_sha256[:16]}..., "
                                  f"agent has {result['sha256'][:16]}..."
                            )
                            return False
                    return True
                return False
        except Exception as e:
            logger.error(f"Error checking image on {host.name}: {e}")
            return False

    # Docker image - query agent
    try:
        from app.agent_client import _get_agent_auth_headers
        async with httpx.AsyncClient(timeout=10.0) as client:
            # URL-encode the reference for the path
            encoded_ref = quote(reference, safe='')
            response = await client.get(
                f"http://{host.address}/images/{encoded_ref}",
                headers=_get_agent_auth_headers(),
            )
            if response.status_code == 200:
                result = response.json()
                return result.get("exists", False)
            return False
    except Exception as e:
        logger.error(f"Error checking image on {host.name}: {e}")
        return False


async def get_agent_image_inventory(host: models.Host) -> list[dict]:
    """Get list of Docker images on an agent.

    Args:
        host: The host/agent to query

    Returns:
        List of image info dicts with id, tags, size_bytes
    """
    try:
        from app.agent_client import _get_agent_auth_headers
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"http://{host.address}/images", headers=_get_agent_auth_headers())
            if response.status_code == 200:
                result = response.json()
                return result.get("images", [])
            return []
    except Exception as e:
        logger.error(f"Error getting image inventory from {host.name}: {e}")
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
        logger.info(f"Reconciled images for agent {host.name}")

    except Exception as e:
        logger.error(f"Error reconciling images for host {host_id}: {e}", exc_info=True)


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

    logger.info(f"Pushing image {image_id} to {len(hosts)} agents")

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

    logger.info(f"Agent {host.name} has 'pull' strategy, syncing all images")

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
        logger.info(f"Syncing {image_id} to {host.name}")
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

        # Build reference -> sha256 map from manifest for integrity checks
        manifest = load_manifest()
        ref_sha256_map: dict[str, str | None] = {}
        for lib_image in manifest.get("images", []):
            ref = lib_image.get("reference", "")
            if ref in image_references:
                ref_sha256_map[ref] = lib_image.get("sha256")

        missing = []
        for reference in image_references:
            exists = await check_agent_has_image(
                host, reference, expected_sha256=ref_sha256_map.get(reference),
            )
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


async def check_and_start_image_sync(
    host_id: str,
    image_references: list[str],
    database: Session,
    lab_id: str,
    job_id: str,
    node_ids: list[str],
    image_to_nodes: dict[str, list[str]],
    provider: str = "docker",
) -> tuple[set[str], set[str], list[str]]:
    """Non-blocking image sync check. Returns immediately after firing off sync tasks.

    1. Checks which images are missing on the agent
    2. For missing images: sets node state to "syncing", fires async sync with callback
    3. Returns immediately -- does NOT wait for sync to complete

    Args:
        host_id: Target agent's host ID
        image_references: List of Docker image references to check
        database: Database session
        lab_id: Lab ID for node state updates
        job_id: Current job ID (for logging)
        node_ids: Node IDs being reconciled (passed to callback for re-reconcile)
        image_to_nodes: Mapping from image refs to node names
        provider: Provider type (passed to callback for re-reconcile)

    Returns:
        Tuple of (syncing_node_names, failed_node_names, log_entries)
    """
    from app.services.broadcaster import broadcast_node_state_change
    from app.utils.async_tasks import safe_create_task

    log_entries: list[str] = []
    syncing_nodes: set[str] = set()
    failed_nodes: set[str] = set()

    try:
        host = database.get(models.Host, host_id)
        if not host or host.status != "online":
            log_entries.append("Agent not available for image sync")
            # All nodes fail
            for ref in image_references:
                for node_name in image_to_nodes.get(ref, []):
                    failed_nodes.add(node_name)
            return syncing_nodes, failed_nodes, log_entries

        # Check which images are missing
        log_entries.append(f"Checking {len(image_references)} image(s) on agent {host.name}...")
        all_node_names = []
        for ref in image_references:
            all_node_names.extend(image_to_nodes.get(ref, []))
        if all_node_names:
            update_node_image_sync_status(database, lab_id, all_node_names, "checking", "Checking image availability...")

        missing = []
        for reference in image_references:
            exists = await check_agent_has_image(host, reference)
            if not exists:
                missing.append(reference)

        if not missing:
            log_entries.append("All images already present on agent")
            if all_node_names:
                update_node_image_sync_status(database, lab_id, all_node_names, None, None)
            return syncing_nodes, failed_nodes, log_entries

        log_entries.append(
            f"Agent {host.name} missing {len(missing)} image(s): "
            + ", ".join(missing[:3])
            + (f" (+{len(missing) - 3} more)" if len(missing) > 3 else "")
        )

        # Check if sync is enabled
        strategy = host.image_sync_strategy or settings.image_sync_fallback_strategy
        if strategy == "disabled":
            log_entries.append("Image sync is disabled for this agent")
            for ref in missing:
                for node_name in image_to_nodes.get(ref, []):
                    failed_nodes.add(node_name)
            update_node_image_sync_status(
                database, lab_id, list(failed_nodes), "failed", "Image sync disabled"
            )
            return syncing_nodes, failed_nodes, log_entries

        # Find image entries in library for missing references.
        manifest = load_manifest()
        lib_images_by_ref: dict[str, dict] = {}
        for lib_image in manifest.get("images", []):
            ref = lib_image.get("reference", "")
            if ref in missing:
                lib_images_by_ref[ref] = lib_image

        caps = host.get_capabilities()
        host_providers = caps.get("providers", [])

        # Fire off non-blocking sync for each missing image
        for reference in missing:
            lib_image = lib_images_by_ref.get(reference)
            affected_nodes = image_to_nodes.get(reference, [])

            if not lib_image:
                log_entries.append(f"  {reference}: not found in library - cannot sync")
                for node_name in affected_nodes:
                    failed_nodes.add(node_name)
                if affected_nodes:
                    update_node_image_sync_status(
                        database, lab_id, affected_nodes, "failed", "Image not found in library"
                    )
                continue

            image_id = lib_image.get("id")
            if not image_id:
                log_entries.append(
                    f"  {reference}: missing image id in library entry - cannot sync"
                )
                for node_name in affected_nodes:
                    failed_nodes.add(node_name)
                if affected_nodes:
                    update_node_image_sync_status(
                        database, lab_id, affected_nodes, "failed", "Invalid image entry (missing id)"
                    )
                continue

            required_provider = _required_provider_for_reference(reference)
            if required_provider and required_provider not in host_providers:
                log_entries.append(
                    f"  {reference}: target host does not support required provider '{required_provider}'"
                )
                for node_name in affected_nodes:
                    failed_nodes.add(node_name)
                if affected_nodes:
                    update_node_image_sync_status(
                        database,
                        lab_id,
                        affected_nodes,
                        "failed",
                        f"Target host missing {required_provider} capability",
                    )
                continue

            # Dedup: check for existing active sync job
            existing_job = database.query(models.ImageSyncJob).filter(
                models.ImageSyncJob.image_id == image_id,
                models.ImageSyncJob.host_id == host_id,
                models.ImageSyncJob.status.in_(["pending", "transferring", "loading"]),
            ).first()

            if existing_job:
                log_entries.append(f"  {reference}: sync already in progress (job {existing_job.id[:8]})")
                sync_job_id = existing_job.id
            else:
                # Create ImageHost record
                image_host = database.query(models.ImageHost).filter(
                    models.ImageHost.image_id == image_id,
                    models.ImageHost.host_id == host_id,
                ).first()
                if not image_host:
                    image_host = models.ImageHost(
                        id=str(uuid4()),
                        image_id=image_id,
                        host_id=host_id,
                        reference=reference,
                        status="syncing",
                    )
                    database.add(image_host)
                else:
                    image_host.status = "syncing"
                    image_host.error_message = None

                # Create sync job
                sync_job = models.ImageSyncJob(
                    id=str(uuid4()),
                    image_id=image_id,
                    host_id=host_id,
                    status="pending",
                )
                database.add(sync_job)
                database.commit()
                sync_job_id = sync_job.id
                log_entries.append(f"  {reference}: started sync (job {sync_job_id[:8]})")

            # Mark affected nodes as syncing
            for node_name in affected_nodes:
                syncing_nodes.add(node_name)

            # Update node states to syncing
            if affected_nodes:
                update_node_image_sync_status(
                    database, lab_id, affected_nodes, "syncing", f"Syncing image to {host.name}..."
                )

            # Broadcast syncing state for each node
            for node_name in affected_nodes:
                ns = database.query(models.NodeState).filter(
                    models.NodeState.lab_id == lab_id,
                    models.NodeState.node_name == node_name,
                ).first()
                if ns:
                    safe_create_task(
                        broadcast_node_state_change(
                            lab_id=lab_id,
                            node_id=ns.node_id,
                            node_name=ns.node_name,
                            desired_state=ns.desired_state,
                            actual_state=ns.actual_state,
                            is_ready=ns.is_ready,
                            image_sync_status="syncing",
                            image_sync_message=f"Syncing image to {host.name}...",
                        ),
                        name=f"broadcast:imgsync:{lab_id}:{ns.node_id}"
                    )

            # Fire-and-forget: run sync with completion callback
            if not existing_job:
                safe_create_task(
                    _run_sync_and_callback(
                        sync_job_id=sync_job_id,
                        image_id=image_id,
                        image=lib_image,
                        host_id=host.id,
                        lab_id=lab_id,
                        node_ids=node_ids,
                        image_to_nodes=image_to_nodes,
                        provider=provider,
                    ),
                    name=f"imgsync:{sync_job_id[:8]}"
                )
            else:
                # Sync already in progress from another reconcile pass.
                # Wait for it to finish, then re-reconcile OUR node_ids.
                safe_create_task(
                    _wait_for_sync_and_callback(
                        sync_job_id=sync_job_id,
                        image=lib_image,
                        host_id=host.id,
                        lab_id=lab_id,
                        node_ids=node_ids,
                        image_to_nodes=image_to_nodes,
                        provider=provider,
                    ),
                    name=f"imgsync:wait:{sync_job_id[:8]}"
                )

        return syncing_nodes, failed_nodes, log_entries

    except Exception as e:
        log_entries.append(f"Error during image sync check: {e}")
        return syncing_nodes, failed_nodes, log_entries


async def _run_sync_and_callback(
    sync_job_id: str,
    image_id: str,
    image: dict,
    host_id: str,
    lab_id: str,
    node_ids: list[str],
    image_to_nodes: dict[str, list[str]],
    provider: str,
) -> None:
    """Execute sync job, then re-trigger reconciliation on completion."""
    import logging

    logger = logging.getLogger(__name__)

    from app.routers.images import _execute_sync_job

    # Re-query host in a fresh session — the original may be detached
    with get_session() as session:
        host = session.get(models.Host, host_id)
        if not host:
            logger.error(f"Host {host_id} not found for sync job {sync_job_id}")
            return
        # Expunge so we can use it outside this session (read-only attrs)
        session.expunge(host)

    try:
        await _execute_sync_job(sync_job_id, image_id, image, host)
    except Exception as e:
        logger.error(f"Image sync job {sync_job_id} raised exception: {e}")

    # Check result and trigger callback
    with get_session() as session:
        sync_job = session.get(models.ImageSyncJob, sync_job_id)
        reference = image.get("reference", "")
        affected_nodes = image_to_nodes.get(reference, [])

        if sync_job and sync_job.status == "completed":
            logger.info(f"Image sync completed for {reference} on {host.name}, re-triggering reconcile")
            _broadcast_nodes_sync_cleared(session, lab_id, affected_nodes)
            _trigger_re_reconcile(session, lab_id, node_ids, provider)
        else:
            error_msg = sync_job.error_message if sync_job else "Sync job not found"
            logger.warning(f"Image sync failed for {reference} on {host.name}: {error_msg}")
            _mark_nodes_sync_failed(session, lab_id, affected_nodes, error_msg)


async def _wait_for_sync_and_callback(
    sync_job_id: str,
    image: dict,
    host_id: str,
    lab_id: str,
    node_ids: list[str],
    image_to_nodes: dict[str, list[str]],
    provider: str,
    poll_interval: float = 5.0,
    max_wait: float = 600.0,
) -> None:
    """Wait for an existing sync job to finish, then re-trigger reconciliation.

    Used when a second reconcile pass detects a sync already in progress
    from a different node's reconcile. Polls the sync job status and
    triggers re-reconcile for our node_ids when it completes.
    """
    import logging

    logger = logging.getLogger(__name__)
    reference = image.get("reference", "")
    affected_nodes = image_to_nodes.get(reference, [])
    elapsed = 0.0

    while elapsed < max_wait:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        with get_session() as session:
            sync_job = session.get(models.ImageSyncJob, sync_job_id)
            if not sync_job:
                logger.warning(f"Sync job {sync_job_id} disappeared while waiting")
                _mark_nodes_sync_failed(session, lab_id, affected_nodes, "Sync job not found")
                return

            if sync_job.status in ("completed", "failed", "error"):
                if sync_job.status == "completed":
                    logger.info(f"Waited sync completed for {reference}, re-triggering reconcile for {node_ids}")
                    _broadcast_nodes_sync_cleared(session, lab_id, affected_nodes)
                    _trigger_re_reconcile(session, lab_id, node_ids, provider)
                else:
                    error_msg = sync_job.error_message or "Sync failed"
                    logger.warning(f"Waited sync failed for {reference}: {error_msg}")
                    _mark_nodes_sync_failed(session, lab_id, affected_nodes, error_msg)
                return

    # Timed out waiting
    logger.error(f"Timed out waiting for sync job {sync_job_id} after {max_wait}s")
    with get_session() as session:
        _mark_nodes_sync_failed(session, lab_id, affected_nodes, f"Sync timed out after {int(max_wait)}s")


def _trigger_re_reconcile(
    session: Session,
    lab_id: str,
    node_ids: list[str],
    provider: str,
) -> None:
    """Re-trigger node reconciliation after image sync completes.

    Only nodes still requesting running state are re-queued. This prevents
    image-sync callbacks from overriding a user-issued stop request.
    """
    import logging

    logger = logging.getLogger(__name__)

    from app.tasks.jobs import run_node_reconcile
    from app.utils.async_tasks import safe_create_task

    lab = session.get(models.Lab, lab_id)
    if not lab:
        logger.warning(f"Cannot re-reconcile: lab {lab_id} not found")
        return

    runnable_node_ids = [
        ns.node_id
        for ns in session.query(models.NodeState)
        .filter(
            models.NodeState.lab_id == lab_id,
            models.NodeState.node_id.in_(node_ids),
            models.NodeState.desired_state == "running",
        )
        .all()
    ]
    if not runnable_node_ids:
        logger.info(
            f"Skipping image-callback re-reconcile for lab {lab_id}: "
            "all target nodes are stopped"
        )
        return

    job = models.Job(
        lab_id=lab_id,
        user_id=lab.owner_id,
        action="sync:image-callback",
        status="queued",
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    logger.info(f"Created image-callback job {job.id} for lab {lab_id}")
    safe_create_task(
        run_node_reconcile(job.id, lab_id, runnable_node_ids, provider=provider),
        name=f"imgsync:callback:{job.id}"
    )


def _mark_nodes_sync_failed(
    session: Session,
    lab_id: str,
    node_names: list[str],
    error_msg: str,
) -> None:
    """Mark nodes as failed after image sync failure."""
    from app.services.broadcaster import broadcast_node_state_change
    from app.utils.async_tasks import safe_create_task

    if not node_names:
        return

    # Update node states
    node_states = session.query(models.NodeState).filter(
        models.NodeState.lab_id == lab_id,
        models.NodeState.node_name.in_(node_names),
    ).all()

    for ns in node_states:
        ns.actual_state = "error"
        ns.error_message = f"Image sync failed: {error_msg}"
        ns.image_sync_status = "failed"
        ns.image_sync_message = error_msg
        safe_create_task(
            broadcast_node_state_change(
                lab_id=lab_id,
                node_id=ns.node_id,
                node_name=ns.node_name,
                desired_state=ns.desired_state,
                actual_state=ns.actual_state,
                is_ready=False,
                error_message=ns.error_message,
                image_sync_status="failed",
                image_sync_message=error_msg,
            ),
            name=f"broadcast:imgsync:fail:{lab_id}:{ns.node_id}"
        )

    session.commit()


def _broadcast_nodes_sync_cleared(
    session: Session,
    lab_id: str,
    node_names: list[str],
) -> None:
    """Broadcast cleared image sync status for nodes."""
    from app.services.broadcaster import broadcast_node_state_change
    from app.utils.async_tasks import safe_create_task

    if not node_names:
        return

    # Clear sync status in DB
    update_node_image_sync_status(session, lab_id, node_names, None, None)

    # Broadcast cleared status
    node_states = session.query(models.NodeState).filter(
        models.NodeState.lab_id == lab_id,
        models.NodeState.node_name.in_(node_names),
    ).all()

    for ns in node_states:
        safe_create_task(
            broadcast_node_state_change(
                lab_id=lab_id,
                node_id=ns.node_id,
                node_name=ns.node_name,
                desired_state=ns.desired_state,
                actual_state=ns.actual_state,
                is_ready=ns.is_ready,
                image_sync_status=None,
                image_sync_message=None,
            ),
            name=f"broadcast:imgsync:clear:{lab_id}:{ns.node_id}"
        )


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
        logger.error(f"Error parsing topology: {e}")
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
        logger.error(f"Error parsing topology: {e}")
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
