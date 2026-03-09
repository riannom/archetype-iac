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
from app.db import get_session
from app.image_store import (
    create_image_entry,
    detect_device_from_filename,
    load_manifest,
    save_manifest,
)

logger = logging.getLogger(__name__)


class ImageReconciliationResult:
    """Results from an image reconciliation run."""

    def __init__(self):
        self.orphaned_hosts_removed = 0
        self.missing_hosts_created = 0
        self.status_updates = 0
        self.images_discovered = 0
        self.remote_deletions = 0
        self.errors: list[str] = []

    def to_dict(self) -> dict:
        return {
            "orphaned_hosts_removed": self.orphaned_hosts_removed,
            "missing_hosts_created": self.missing_hosts_created,
            "status_updates": self.status_updates,
            "images_discovered": self.images_discovered,
            "remote_deletions": self.remote_deletions,
            "errors": self.errors,
        }


def _active_image_references_by_host(session) -> dict[str, set[str]]:
    """Build the set of image references still needed per host."""
    refs_by_host: dict[str, set[str]] = {}

    placements = (
        session.query(models.NodePlacement.host_id, models.Node.image)
        .join(models.Node, models.Node.id == models.NodePlacement.node_definition_id)
        .filter(
            models.NodePlacement.host_id.isnot(None),
            models.Node.image.isnot(None),
        )
        .all()
    )
    for host_id, image_ref in placements:
        if host_id and image_ref:
            refs_by_host.setdefault(host_id, set()).add(image_ref)

    planned_nodes = (
        session.query(models.Node.host_id, models.Node.image)
        .filter(
            models.Node.host_id.isnot(None),
            models.Node.image.isnot(None),
        )
        .all()
    )
    for host_id, image_ref in planned_nodes:
        if host_id and image_ref:
            refs_by_host.setdefault(host_id, set()).add(image_ref)

    return refs_by_host


async def cleanup_deleted_image_from_agents(
    image_id: str,
    reference: str,
    host_ids: list[str] | None = None,
) -> ImageReconciliationResult:
    """Best-effort cleanup of a deleted/orphaned image from online agents."""
    result = ImageReconciliationResult()

    with get_session() as session:
        active_refs_by_host = _active_image_references_by_host(session)
        image_hosts_query = session.query(models.ImageHost).filter(
            models.ImageHost.image_id == image_id
        )
        if host_ids:
            image_hosts_query = image_hosts_query.filter(models.ImageHost.host_id.in_(host_ids))
        image_hosts = image_hosts_query.all()

        for image_host in image_hosts:
            host = session.get(models.Host, image_host.host_id)
            if not host:
                session.delete(image_host)
                result.orphaned_hosts_removed += 1
                continue
            if reference in active_refs_by_host.get(image_host.host_id, set()):
                logger.info(
                    "Deferring remote image cleanup for %s on host %s because it is still referenced by nodes",
                    reference,
                    host.name,
                )
                continue
            if host.status != "online" or not agent_client.is_agent_online(host):
                result.errors.append(
                    f"Host {host.name} is offline; deferred cleanup for {reference}"
                )
                continue

            delete_result = await agent_client.delete_image_on_agent(host, reference)
            if delete_result.get("success"):
                session.delete(image_host)
                result.orphaned_hosts_removed += 1
                if delete_result.get("deleted"):
                    result.remote_deletions += 1
            else:
                image_host.status = "failed"
                image_host.error_message = delete_result.get("error") or "Remote delete failed"
                result.errors.append(
                    f"Host {host.name}: {image_host.error_message}"
                )

        session.commit()

    return result


async def cleanup_unused_agent_images() -> ImageReconciliationResult:
    """Remove agent images no longer referenced by the controller."""
    result = ImageReconciliationResult()
    manifest = load_manifest()
    catalog_refs = {
        img.get("reference")
        for img in manifest.get("images", [])
        if img.get("reference")
    }

    with get_session() as session:
        online_hosts = (
            session.query(models.Host)
            .filter(models.Host.status == "online")
            .all()
        )
        active_refs_by_host = _active_image_references_by_host(session)

        for host in online_hosts:
            if not agent_client.is_agent_online(host):
                continue
            keep_refs = set(catalog_refs)
            keep_refs.update(active_refs_by_host.get(host.id, set()))
            try:
                images_response = await agent_client.get_agent_images(host)
            except Exception as e:
                result.errors.append(f"Host {host.name}: {e}")
                continue

            for img_info in images_response.get("images", []):
                if img_info.get("kind") == "docker" and img_info.get("in_use"):
                    continue
                candidate_refs: list[str] = []
                reference = img_info.get("reference")
                if reference:
                    candidate_refs.append(reference)
                for tag in img_info.get("tags", []):
                    if tag:
                        candidate_refs.append(tag)

                for candidate in candidate_refs:
                    if candidate in keep_refs or "<none>" in candidate:
                        continue
                    delete_result = await agent_client.delete_image_on_agent(host, candidate)
                    if delete_result.get("success"):
                        result.remote_deletions += 1 if delete_result.get("deleted") else 0
                    else:
                        result.errors.append(
                            f"Host {host.name}: failed to delete stale image {candidate}: "
                            f"{delete_result.get('error') or 'unknown error'}"
                        )

    return result


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
                orphan_records = image_host_map[orphan_id]
                orphan_reference = next(
                    (ih.reference for ih in orphan_records if ih.reference),
                    "",
                )
                if orphan_reference:
                    cleanup_result = await cleanup_deleted_image_from_agents(
                        orphan_id,
                        orphan_reference,
                        host_ids=[ih.host_id for ih in orphan_records],
                    )
                    result.orphaned_hosts_removed += cleanup_result.orphaned_hosts_removed
                    result.remote_deletions += cleanup_result.remote_deletions
                    result.errors.extend(cleanup_result.errors)
                else:
                    for ih in orphan_records:
                        logger.info(
                            f"Removing orphaned ImageHost record without reference: image={orphan_id}, host={ih.host_id}"
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
                reference = img.get("reference")
                if not image_id or not reference:
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
                        reference=reference,
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


async def discover_unmanifested_images() -> int:
    """Discover Docker images on agents that are not in the manifest.

    Queries each online agent for Docker images and uses agent-reported
    ``device_id`` (set during image sync) for deterministic identification.
    Images without metadata are skipped — all images must be synced through
    the API to be tracked.

    Returns:
        Number of new manifest entries created.
    """
    manifest = load_manifest()
    known_references: set[str] = set()
    for img in manifest.get("images", []):
        ref = img.get("reference")
        if ref:
            known_references.add(ref)

    with get_session() as session:
        online_hosts = (
            session.query(models.Host)
            .filter(models.Host.status == "online")
            .all()
        )
        # Eagerly load attributes before session closes
        for h in online_hosts:
            _ = h.id, h.name, h.address, h.status, h.last_heartbeat, h.capabilities
        session.expunge_all()

    # Collect images with agent-reported device_id
    # Key: tag, Value: dict with device_ids per host
    agent_images: dict[str, dict[str, str]] = {}
    for host in online_hosts:
        if not agent_client.is_agent_online(host):
            continue
        try:
            images_response = await agent_client.get_agent_images(host)
            for img_info in images_response.get("images", []):
                reported_device_id = img_info.get("device_id")
                if not reported_device_id:
                    continue  # No metadata — skip
                for tag in img_info.get("tags", []):
                    if tag not in agent_images:
                        agent_images[tag] = {}
                    agent_images[tag][host.name] = reported_device_id
        except Exception as e:
            logger.warning(f"Failed to query images on agent {host.name}: {e}")

    discovered = 0
    for tag in sorted(agent_images):
        # Skip dangling images
        if tag == "<none>:<none>" or ":<none>" in tag or "<none>:" in tag:
            continue

        # Skip already-manifested references
        if tag in known_references:
            continue

        device_ids = agent_images[tag]
        unique_ids = set(device_ids.values())

        if len(unique_ids) > 1:
            logger.warning(
                f"Conflicting device_id for {tag}: {device_ids} — skipping"
            )
            continue

        device_id = unique_ids.pop()
        _, version = detect_device_from_filename(tag)

        image_id = f"docker:{tag}"
        entry = create_image_entry(
            image_id=image_id,
            kind="docker",
            reference=tag,
            filename=tag,
            device_id=device_id,
            version=version,
            source="agent-discovery",
        )
        manifest.setdefault("images", []).append(entry)
        known_references.add(tag)
        discovered += 1
        logger.info(
            f"Discovered Docker image: {tag} -> device={device_id}, version={version}"
        )

    if discovered > 0:
        save_manifest(manifest)

    return discovered


async def verify_image_status_on_agents(run_sha256_check: bool = False) -> ImageReconciliationResult:
    """Query agents to verify actual image status matches ImageHost records.

    This is a more expensive operation that contacts each agent to verify
    which images they actually have.

    Args:
        run_sha256_check: If True, verify SHA256 integrity for file-based images
                         (qcow2/iol) using the agent's sidecar checksum files.

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

            # Load manifest for image lookups by kind
            manifest = load_manifest()
            docker_images = {
                img.get("id"): img.get("reference")
                for img in manifest.get("images", [])
                if img.get("kind") == "docker"
            }
            qcow2_images = {
                img.get("id"): img.get("reference")
                for img in manifest.get("images", [])
                if img.get("kind") == "qcow2"
            }
            iol_images = {
                img.get("id"): img.get("reference")
                for img in manifest.get("images", [])
                if img.get("kind") == "iol"
            }

            # Build reference -> sha256 map for integrity checks
            file_image_sha256: dict[str, str | None] = {}
            if run_sha256_check:
                for img in manifest.get("images", []):
                    if img.get("kind") in ("qcow2", "iol"):
                        ref = img.get("reference", "")
                        if ref:
                            file_image_sha256[img.get("id")] = img.get("sha256")

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

                    # Get agent's capabilities from host record
                    caps = host.get_capabilities()
                    host_providers = caps.get("providers", [])

                    for ih in host_image_records:
                        old_status = ih.status

                        # Check Docker images against agent's image list
                        if ih.image_id in docker_images:
                            reference = docker_images[ih.image_id]
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

                        # qcow2 images: verify file exists every cycle, and
                        # only promote back to synced when the agent confirms it.
                        elif ih.image_id in qcow2_images:
                            if "libvirt" in host_providers:
                                from app.tasks.image_sync import check_agent_has_image
                                ref = qcow2_images[ih.image_id]
                                exists = await check_agent_has_image(host, ref)
                                if not exists:
                                    if ih.status != "missing" or ih.error_message != "Image not found on agent":
                                        ih.status = "missing"
                                        ih.error_message = "Image not found on agent"
                                        result.status_updates += 1
                                        logger.debug(
                                            f"Updated ImageHost status (qcow2): image={ih.image_id}, "
                                            f"host={host.id}, {old_status} -> missing"
                                        )
                                    continue

                                if run_sha256_check:
                                    expected_sha = file_image_sha256.get(ih.image_id)
                                    if expected_sha:
                                        intact = await check_agent_has_image(
                                            host, ref, expected_sha256=expected_sha
                                        )
                                        if not intact:
                                            ih.status = "failed"
                                            ih.error_message = "SHA256 mismatch — image may be corrupted"
                                            result.status_updates += 1
                                            logger.warning(
                                                f"SHA256 mismatch (qcow2): image={ih.image_id}, "
                                                f"host={host.id} — marking failed"
                                            )
                                            continue

                                if ih.status != "synced":
                                    ih.status = "synced"
                                    ih.synced_at = datetime.now(timezone.utc)
                                    ih.error_message = None
                                    result.status_updates += 1
                                    logger.debug(
                                        f"Updated ImageHost status (qcow2): image={ih.image_id}, "
                                        f"host={host.id}, {old_status} -> synced"
                                    )
                            else:
                                if ih.status != "missing":
                                    ih.status = "missing"
                                    ih.error_message = "Agent lacks libvirt provider for qcow2 images"
                                    result.status_updates += 1
                                    logger.debug(
                                        f"Updated ImageHost status (qcow2): image={ih.image_id}, "
                                        f"host={host.id}, {old_status} -> missing (no libvirt)"
                                    )

                        # IOL images: verify file exists every cycle, and only
                        # promote back to synced when the agent confirms it.
                        elif ih.image_id in iol_images:
                            if "docker" in host_providers:
                                from app.tasks.image_sync import check_agent_has_image
                                ref = iol_images[ih.image_id]
                                exists = await check_agent_has_image(host, ref)
                                if not exists:
                                    if ih.status != "missing" or ih.error_message != "Image not found on agent":
                                        ih.status = "missing"
                                        ih.error_message = "Image not found on agent"
                                        result.status_updates += 1
                                        logger.debug(
                                            f"Updated ImageHost status (IOL): image={ih.image_id}, "
                                            f"host={host.id}, {old_status} -> missing"
                                        )
                                    continue

                                if run_sha256_check:
                                    expected_sha = file_image_sha256.get(ih.image_id)
                                    if expected_sha:
                                        intact = await check_agent_has_image(
                                            host, ref, expected_sha256=expected_sha
                                        )
                                        if not intact:
                                            ih.status = "failed"
                                            ih.error_message = "SHA256 mismatch — image may be corrupted"
                                            result.status_updates += 1
                                            logger.warning(
                                                f"SHA256 mismatch (IOL): image={ih.image_id}, "
                                                f"host={host.id} — marking failed"
                                            )
                                            continue

                                if ih.status != "synced":
                                    ih.status = "synced"
                                    ih.synced_at = datetime.now(timezone.utc)
                                    ih.error_message = None
                                    result.status_updates += 1
                                    logger.debug(
                                        f"Updated ImageHost status (IOL): image={ih.image_id}, "
                                        f"host={host.id}, {old_status} -> synced"
                                    )
                            else:
                                if ih.status != "missing":
                                    ih.status = "missing"
                                    ih.error_message = "Agent lacks docker provider for IOL images"
                                    result.status_updates += 1

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

    Combines discover_unmanifested_images(), reconcile_image_hosts(),
    verify_image_status_on_agents(), and metadata backfill.
    """
    result = ImageReconciliationResult()

    # Discover Docker images on agents not yet in manifest
    try:
        result.images_discovered = await discover_unmanifested_images()
    except Exception as e:
        logger.error(f"Error discovering unmanifested images: {e}")
        result.errors.append(f"Discovery: {e}")

    # Reconcile the ImageHost table
    host_result = await reconcile_image_hosts()
    result.orphaned_hosts_removed = host_result.orphaned_hosts_removed
    result.missing_hosts_created = host_result.missing_hosts_created
    result.errors.extend(host_result.errors)

    # Verify actual status on agents (if no errors so far)
    if not result.errors:
        status_result = await verify_image_status_on_agents()
        result.status_updates = status_result.status_updates
        result.errors.extend(status_result.errors)

    # Backfill metadata for manifest images that agents have but lack metadata for
    try:
        await _backfill_agent_metadata()
    except Exception as e:
        logger.warning(f"Metadata backfill error: {e}")

    try:
        cleanup_result = await cleanup_unused_agent_images()
        result.remote_deletions += cleanup_result.remote_deletions
        result.errors.extend(cleanup_result.errors)
    except Exception as e:
        logger.warning(f"Unused-image cleanup error: {e}")

    return result


async def _backfill_agent_metadata() -> None:
    """Push known device_ids to agents for images missing metadata.

    For each online agent, compares agent-reported images (those without
    device_id) against the manifest. If the manifest knows the device_id
    for a tag the agent has, sends a backfill request so subsequent
    queries return deterministic device identification.
    """
    manifest = load_manifest()
    # Build reference -> device_id lookup from manifest
    manifest_device_map: dict[str, str] = {}
    for img in manifest.get("images", []):
        ref = img.get("reference")
        did = img.get("device_id")
        if ref and did:
            manifest_device_map[ref] = did

    if not manifest_device_map:
        return

    with get_session() as session:
        online_hosts = (
            session.query(models.Host)
            .filter(models.Host.status == "online")
            .all()
        )
        for h in online_hosts:
            _ = h.id, h.name, h.address, h.status, h.last_heartbeat, h.capabilities
        session.expunge_all()

    for host in online_hosts:
        if not agent_client.is_agent_online(host):
            continue
        try:
            images_response = await agent_client.get_agent_images(host)
            backfill: dict[str, str] = {}
            for img_info in images_response.get("images", []):
                if img_info.get("device_id"):
                    continue  # Already has metadata
                for tag in img_info.get("tags", []):
                    if tag in manifest_device_map:
                        backfill[tag] = manifest_device_map[tag]
            if backfill:
                await agent_client.backfill_image_metadata(host, backfill)
                logger.debug(
                    f"Backfilled {len(backfill)} image metadata entries on {host.name}"
                )
        except Exception as e:
            logger.warning(f"Metadata backfill failed on {host.name}: {e}")


async def image_reconciliation_monitor():
    """Background task to periodically reconcile image state.

    Runs every image_reconciliation_interval seconds and ensures
    ImageHost records are consistent with manifest.json.

    Every image_sha256_check_interval_cycles cycles, also runs SHA256
    integrity verification for file-based images (qcow2/iol).
    """
    interval = getattr(settings, "image_reconciliation_interval", 300)  # 5 minutes default
    sha256_interval = getattr(settings, "image_sha256_check_interval_cycles", 6)
    logger.info(
        f"Image reconciliation monitor started "
        f"(interval: {interval}s, SHA256 check every {sha256_interval} cycles)"
    )

    cycle = 0
    while True:
        try:
            await asyncio.sleep(interval)
            cycle += 1

            # Discover unmanifested Docker images on agents
            try:
                discovered = await discover_unmanifested_images()
                if discovered > 0:
                    logger.info(f"Image discovery: {discovered} new image(s) registered")
            except Exception as e:
                logger.error(f"Error discovering unmanifested images: {e}")

            result = await reconcile_image_hosts()
            if result.orphaned_hosts_removed > 0 or result.missing_hosts_created > 0:
                logger.info(
                    f"Image reconciliation: removed {result.orphaned_hosts_removed} orphans, "
                    f"created {result.missing_hosts_created} records"
                )

            # Run SHA256 verification on configured cycle interval
            run_sha256 = (cycle % sha256_interval == 0)
            status_result = await verify_image_status_on_agents(run_sha256_check=run_sha256)
            if status_result.status_updates > 0:
                logger.info(f"Image status verification: {status_result.status_updates} update(s)")

            # Backfill metadata for images agents have but lack device_id for
            try:
                await _backfill_agent_metadata()
            except Exception as e:
                logger.warning(f"Metadata backfill error: {e}")

            try:
                cleanup_result = await cleanup_unused_agent_images()
                if cleanup_result.remote_deletions > 0:
                    logger.info(
                        "Unused image cleanup: deleted %s image(s) from agents",
                        cleanup_result.remote_deletions,
                    )
                if cleanup_result.errors:
                    logger.warning(
                        "Unused image cleanup encountered %s error(s)",
                        len(cleanup_result.errors),
                    )
            except Exception as e:
                logger.warning(f"Unused image cleanup error: {e}")

        except asyncio.CancelledError:
            logger.info("Image reconciliation monitor stopped")
            break
        except Exception as e:
            logger.error(f"Error in image reconciliation monitor: {e}")
