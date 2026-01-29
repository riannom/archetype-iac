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
from app.db import SessionLocal
from app.image_store import find_image_by_id, load_manifest


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
    own_session = database is None
    if own_session:
        database = SessionLocal()

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
    finally:
        if own_session:
            database.close()


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
    own_session = database is None
    if own_session:
        database = SessionLocal()

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
    finally:
        if own_session:
            database.close()


async def push_image_on_upload(image_id: str, database: Session | None = None):
    """Push a newly uploaded image to all agents with 'push' strategy.

    Called after an image is uploaded to the controller.

    Args:
        image_id: The newly uploaded image ID
        database: Optional database session
    """
    if not settings.image_sync_enabled:
        return

    own_session = database is None
    if own_session:
        database = SessionLocal()

    try:
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

    finally:
        if own_session:
            database.close()


async def pull_images_on_registration(host_id: str, database: Session | None = None):
    """Pull all library images to a newly registered agent with 'pull' strategy.

    Called when an agent registers with the controller.

    Args:
        host_id: The newly registered agent's host ID
        database: Optional database session
    """
    if not settings.image_sync_enabled:
        return

    own_session = database is None
    if own_session:
        database = SessionLocal()

    try:
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

    finally:
        if own_session:
            database.close()


async def ensure_images_for_deployment(
    host_id: str,
    image_references: list[str],
    timeout: int | None = None,
    database: Session | None = None,
) -> tuple[bool, list[str]]:
    """Ensure all required images exist on agent before deployment.

    This is the pre-deploy check that ensures images are available
    on the target agent. If images are missing and on_demand sync
    is enabled, it will sync them.

    Args:
        host_id: Target agent's host ID
        image_references: List of Docker image references needed
        timeout: Max seconds to wait for sync (default from settings)
        database: Optional database session

    Returns:
        Tuple of (all_ready, missing_images)
        - all_ready: True if all images are available
        - missing_images: List of image references that are still missing
    """
    if not settings.image_sync_pre_deploy_check:
        return True, []

    if timeout is None:
        timeout = settings.image_sync_timeout

    own_session = database is None
    if own_session:
        database = SessionLocal()

    try:
        host = database.get(models.Host, host_id)
        if not host or host.status != "online":
            return False, image_references

        # Check which images are missing
        missing = []
        for reference in image_references:
            exists = await check_agent_has_image(host, reference)
            if not exists:
                missing.append(reference)

        if not missing:
            return True, []

        print(f"Agent {host.name} missing images: {missing}")

        # Check if on_demand sync is enabled
        strategy = host.image_sync_strategy or settings.image_sync_fallback_strategy
        if strategy == "disabled":
            return False, missing

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
            return False, missing

        # Wait for syncs to complete with timeout
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*[t[1] for t in sync_tasks], return_exceptions=True),
                timeout=timeout
            )

            # Check results
            still_missing = []
            for (reference, _), result in zip(sync_tasks, results):
                if isinstance(result, Exception):
                    still_missing.append(reference)
                elif isinstance(result, tuple):
                    success, error = result
                    if not success:
                        still_missing.append(reference)

            return len(still_missing) == 0, still_missing

        except asyncio.TimeoutError:
            print(f"Image sync timed out after {timeout}s")
            return False, missing

    finally:
        if own_session:
            database.close()


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
