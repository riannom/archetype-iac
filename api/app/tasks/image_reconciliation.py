"""Image reconciliation background task.

This task runs periodically to ensure consistency between:
1. manifest.json (source of truth for image metadata)
2. ImageHost table (tracks which images exist on which agents)

Key scenarios handled:
1. Orphaned ImageHost records - References to images deleted from manifest
2. Missing ImageHost records - Images in manifest without host tracking
3. Stale sync status - ImageHost status not matching agent reality
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app import agent_client, models
from app.config import settings
from app.db import SessionLocal, get_session
from app.image_store import load_manifest

logger = logging.getLogger(__name__)


class ImageReconciliationResult:
    """Results from an image reconciliation run."""

    def __init__(self):
        self.orphaned_hosts_removed = 0
        self.missing_hosts_created = 0
        self.status_updates = 0
        self.errors: list[str] = []

    def to_dict(self) -> dict:
        return {
            "orphaned_hosts_removed": self.orphaned_hosts_removed,
            "missing_hosts_created": self.missing_hosts_created,
            "status_updates": self.status_updates,
            "errors": self.errors,
        }


async def reconcile_image_hosts() -> ImageReconciliationResult:
    """Reconcile ImageHost table with manifest.json.

    This function:
    1. Removes ImageHost records for images no longer in manifest
    2. Creates ImageHost records for images missing host tracking
    3. Updates status for hosts where agent reports different state

    Returns:
        ImageReconciliationResult with counts of changes made
    """
    result = ImageReconciliationResult()

    with get_session() as session:
        try:
            # Load image manifest (source of truth for image metadata)
            manifest = load_manifest()
            manifest_image_ids = {img.get("id") for img in manifest.get("images", [])}

            # Get all ImageHost records
            all_image_hosts = session.query(models.ImageHost).all()
            image_host_map: dict[str, list[models.ImageHost]] = {}
            for ih in all_image_hosts:
                if ih.image_id not in image_host_map:
                    image_host_map[ih.image_id] = []
                image_host_map[ih.image_id].append(ih)

            # 1. Remove orphaned ImageHost records (image no longer in manifest)
            orphaned_image_ids = set(image_host_map.keys()) - manifest_image_ids
            for orphan_id in orphaned_image_ids:
                for ih in image_host_map[orphan_id]:
                    logger.info(
                        f"Removing orphaned ImageHost record: image={orphan_id}, host={ih.host_id}"
                    )
                    session.delete(ih)
                    result.orphaned_hosts_removed += 1

            # 2. Get all online hosts for creating missing records
            online_hosts = (
                session.query(models.Host)
                .filter(models.Host.status == "online")
                .all()
            )
            host_ids = {h.id for h in online_hosts}

            # 3. For images in manifest, ensure ImageHost records exist for online hosts
            for img in manifest.get("images", []):
                image_id = img.get("id")
                if not image_id:
                    continue

                # Get existing host records for this image
                existing_host_ids = {
                    ih.host_id for ih in image_host_map.get(image_id, [])
                }

                # Create missing ImageHost records with "unknown" status
                missing_host_ids = host_ids - existing_host_ids
                for host_id in missing_host_ids:
                    logger.info(
                        f"Creating ImageHost record: image={image_id}, host={host_id}"
                    )
                    new_ih = models.ImageHost(
                        image_id=image_id,
                        host_id=host_id,
                        status="unknown",
                    )
                    session.add(new_ih)
                    result.missing_hosts_created += 1

            session.commit()

        except Exception as e:
            logger.error(f"Error in image reconciliation: {e}")
            result.errors.append(str(e))
            try:
                session.rollback()
            except Exception:
                pass

    return result


async def verify_image_status_on_agents() -> ImageReconciliationResult:
    """Query agents to verify actual image status matches ImageHost records.

    This is a more expensive operation that contacts each agent to verify
    which images they actually have.

    Returns:
        ImageReconciliationResult with counts of status updates
    """
    result = ImageReconciliationResult()

    with get_session() as session:
        try:
            # Get all online hosts
            online_hosts = (
                session.query(models.Host)
                .filter(models.Host.status == "online")
                .all()
            )

            # Load manifest for Docker image lookups
            manifest = load_manifest()
            docker_images = {
                img.get("id"): img.get("reference")
                for img in manifest.get("images", [])
                if img.get("kind") == "docker"
            }

            for host in online_hosts:
                if not agent_client.is_agent_online(host):
                    continue

                try:
                    # Query agent for Docker images it has
                    images_response = await agent_client.get_agent_images(host)

                    # Build set of all image tags on this agent
                    # Agent returns list of DockerImageInfo objects with 'tags' list
                    agent_image_tags: set[str] = set()
                    for img_info in images_response.get("images", []):
                        for tag in img_info.get("tags", []):
                            agent_image_tags.add(tag)

                    # Update ImageHost records for this host
                    host_image_records = (
                        session.query(models.ImageHost)
                        .filter(models.ImageHost.host_id == host.id)
                        .all()
                    )

                    for ih in host_image_records:
                        # Get the Docker reference for this image
                        reference = docker_images.get(ih.image_id)
                        if not reference:
                            continue  # Skip non-Docker images

                        # Check if agent has this image (by tag/reference)
                        old_status = ih.status
                        if reference in agent_image_tags:
                            if ih.status != "synced":
                                ih.status = "synced"
                                ih.synced_at = datetime.now(timezone.utc)
                                ih.error_message = None
                                result.status_updates += 1
                                logger.debug(
                                    f"Updated ImageHost status: image={ih.image_id}, "
                                    f"host={host.id}, {old_status} -> synced"
                                )
                        else:
                            if ih.status == "synced":
                                ih.status = "missing"
                                ih.error_message = "Image not found on agent"
                                result.status_updates += 1
                                logger.debug(
                                    f"Updated ImageHost status: image={ih.image_id}, "
                                    f"host={host.id}, synced -> missing"
                                )

                except Exception as e:
                    logger.warning(f"Failed to verify images on agent {host.name}: {e}")
                    result.errors.append(f"Agent {host.name}: {e}")
                    try:
                        session.rollback()
                    except Exception:
                        pass

            session.commit()

        except Exception as e:
            logger.error(f"Error verifying image status: {e}")
            result.errors.append(str(e))
            try:
                session.rollback()
            except Exception:
                pass

    return result


async def full_image_reconciliation() -> ImageReconciliationResult:
    """Run full image reconciliation: host records and status verification.

    Combines reconcile_image_hosts() and verify_image_status_on_agents().
    """
    # First reconcile the ImageHost table
    result = await reconcile_image_hosts()

    # Then verify actual status on agents (if no errors so far)
    if not result.errors:
        status_result = await verify_image_status_on_agents()
        result.status_updates = status_result.status_updates
        result.errors.extend(status_result.errors)

    return result


async def image_reconciliation_monitor():
    """Background task to periodically reconcile image state.

    Runs every image_reconciliation_interval seconds and ensures
    ImageHost records are consistent with manifest.json.
    """
    interval = getattr(settings, "image_reconciliation_interval", 300)  # 5 minutes default
    logger.info(f"Image reconciliation monitor started (interval: {interval}s)")

    while True:
        try:
            await asyncio.sleep(interval)
            result = await reconcile_image_hosts()
            if result.orphaned_hosts_removed > 0 or result.missing_hosts_created > 0:
                logger.info(
                    f"Image reconciliation: removed {result.orphaned_hosts_removed} orphans, "
                    f"created {result.missing_hosts_created} records"
                )
        except asyncio.CancelledError:
            logger.info("Image reconciliation monitor stopped")
            break
        except Exception as e:
            logger.error(f"Error in image reconciliation monitor: {e}")
