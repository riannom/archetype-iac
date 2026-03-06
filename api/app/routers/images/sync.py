"""Image synchronization endpoints: host status, push, stream, sync jobs."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import db, models
from app.auth import get_current_admin, get_current_user
from app.config import settings
from app.image_store import (
    find_image_by_id,
    load_manifest,
)

router = APIRouter(tags=["images"])


# --- Pydantic models ---

class ImageHostStatus(BaseModel):
    """Status of an image on a specific host."""
    host_id: str
    host_name: str
    status: str  # synced, syncing, failed, missing, unknown
    size_bytes: int | None = None
    synced_at: datetime | None = None
    error_message: str | None = None


class ImageHostsResponse(BaseModel):
    """Response for image hosts endpoint."""
    image_id: str
    hosts: list[ImageHostStatus] = Field(default_factory=list)


class SyncRequest(BaseModel):
    """Request to sync an image to hosts."""
    host_ids: list[str] | None = None  # None means all hosts


class SyncJobOut(BaseModel):
    """Sync job information for API responses."""
    id: str
    image_id: str
    host_id: str
    host_name: str | None = None
    status: str
    progress_percent: int = 0
    bytes_transferred: int = 0
    total_bytes: int = 0
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


# --- Endpoints ---

@router.get("/library/{image_id}/hosts")
def get_image_hosts(
    image_id: str,
    current_user: models.User = Depends(get_current_user),
    database: Session = Depends(db.get_db),
) -> ImageHostsResponse:
    """List all hosts with sync status for an image.

    Returns the current sync status of the image on each registered agent.
    This includes whether the image exists, when it was synced, and any errors.
    """
    # URL-decode the image_id (it may contain colons which get encoded)
    from urllib.parse import unquote
    image_id = unquote(image_id)

    # Verify image exists in manifest
    manifest = load_manifest()
    image = find_image_by_id(manifest, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found in library")

    # Get all online hosts
    hosts = database.query(models.Host).filter(models.Host.status == "online").all()

    # Get existing ImageHost records
    image_hosts = database.query(models.ImageHost).filter(
        models.ImageHost.image_id == image_id
    ).all()
    hosts_by_id = {ih.host_id: ih for ih in image_hosts}

    # Build response
    result = []
    for host in hosts:
        if host.id in hosts_by_id:
            ih = hosts_by_id[host.id]
            result.append(ImageHostStatus(
                host_id=host.id,
                host_name=host.name,
                status=ih.status,
                size_bytes=ih.size_bytes,
                synced_at=ih.synced_at,
                error_message=ih.error_message,
            ))
        else:
            # No record yet - status unknown
            result.append(ImageHostStatus(
                host_id=host.id,
                host_name=host.name,
                status="unknown",
            ))

    return ImageHostsResponse(image_id=image_id, hosts=result)


@router.post("/library/{image_id}/push")
async def push_image_to_hosts(
    image_id: str,
    request: SyncRequest,
    current_user: models.User = Depends(get_current_admin),
    database: Session = Depends(db.get_db),
) -> dict:
    """Push an image to specific or all hosts.

    This creates transfer jobs for each target host and starts the image
    push process in the background. Returns the created job IDs.
    Requires admin access.
    """

    from urllib.parse import unquote
    image_id = unquote(image_id)

    # Verify image exists
    manifest = load_manifest()
    image = find_image_by_id(manifest, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found in library")

    # Get target hosts
    if request.host_ids:
        hosts = database.query(models.Host).filter(
            models.Host.id.in_(request.host_ids),
            models.Host.status == "online"
        ).all()
    else:
        hosts = database.query(models.Host).filter(
            models.Host.status == "online"
        ).all()

    if not hosts:
        raise HTTPException(status_code=400, detail="No online hosts to sync to")

    # Create sync jobs
    job_ids = []
    for host in hosts:
        # Atomic dedup: SELECT FOR UPDATE prevents concurrent requests from
        # creating duplicate jobs for the same image+host combination
        existing_job = (
            database.query(models.ImageSyncJob)
            .filter(
                models.ImageSyncJob.image_id == image_id,
                models.ImageSyncJob.host_id == host.id,
                models.ImageSyncJob.status.in_(["pending", "transferring", "loading"]),
            )
            .with_for_update(skip_locked=True)
            .first()
        )

        if existing_job:
            job_ids.append(existing_job.id)
            continue

        # Enforce image_sync_max_concurrent: skip this host if it already
        # has too many active sync jobs
        active_count = (
            database.query(func.count(models.ImageSyncJob.id))
            .filter(
                models.ImageSyncJob.host_id == host.id,
                models.ImageSyncJob.status.in_(["pending", "transferring", "loading"]),
            )
            .scalar()
        )
        if active_count >= settings.image_sync_max_concurrent:
            continue

        # Create new job
        job = models.ImageSyncJob(
            id=str(uuid4()),
            image_id=image_id,
            host_id=host.id,
            status="pending",
        )
        database.add(job)
        job_ids.append(job.id)

        # Update or create ImageHost record
        image_host = database.query(models.ImageHost).filter(
            models.ImageHost.image_id == image_id,
            models.ImageHost.host_id == host.id
        ).first()

        if image_host:
            image_host.status = "syncing"
            image_host.error_message = None
        else:
            image_host = models.ImageHost(
                id=str(uuid4()),
                image_id=image_id,
                host_id=host.id,
                reference=image.get("reference", ""),
                status="syncing",
            )
            database.add(image_host)

    database.commit()

    # Start sync tasks in background
    for job_id in job_ids:
        job = database.get(models.ImageSyncJob, job_id)
        if job and job.status == "pending":
            host = database.get(models.Host, job.host_id)
            if host:
                asyncio.create_task(_execute_sync_job(job_id, image_id, image, host))

    return {"jobs": job_ids, "count": len(job_ids)}


async def _execute_sync_job(job_id: str, image_id: str, image: dict, host: models.Host):
    """Execute a sync job in the background.

    Streams Docker or file-based images to the agent and updates job progress.
    Uses structured error handling for better error messages.
    """
    import logging
    from app.db import get_session
    from app.errors import categorize_httpx_error

    logger = logging.getLogger(__name__)

    # Capture host ID before session closes -- re-fetch inside our own session
    host_id = host.id

    with get_session() as session:
        try:
            host = session.get(models.Host, host_id)
            if not host:
                logger.warning(f"Host {host_id} not found for sync job {job_id}")
                return

            logger.info(f"Starting sync job {job_id}: {image_id} -> {host.name}")

            # Get fresh job record
            job = session.get(models.ImageSyncJob, job_id)
            if not job:
                logger.warning(f"Sync job {job_id} not found")
                return

            job.status = "transferring"
            job.started_at = datetime.now(timezone.utc)
            session.commit()

            # Get image reference
            reference = image.get("reference", "")
            if not reference:
                raise ValueError("Image has no reference")

            image_kind = image.get("kind", "docker")
            is_file_based = reference.startswith("/") or reference.endswith((".qcow2", ".img", ".iol"))

            # Build agent URL once
            agent_url = f"http://{host.address}/images/receive"
            from app.agent_client import _get_agent_auth_headers
            _auth_headers = _get_agent_auth_headers()
            _docker_tmp_path = None
            async with httpx.AsyncClient(timeout=httpx.Timeout(settings.image_sync_timeout), headers=_auth_headers) as client:
                if is_file_based:
                    source_path = Path(reference)
                    if not source_path.exists() or not source_path.is_file():
                        raise ValueError(f"Source image file not found: {reference}")

                    size_bytes = source_path.stat().st_size
                    job.total_bytes = size_bytes
                    session.commit()

                    pull_response = await client.post(
                        f"http://{host.address}/images/pull",
                        json={
                            "image_id": image_id,
                            "reference": reference,
                            "sha256": image.get("sha256", ""),
                            "device_id": image.get("device_id", ""),
                        },
                    )
                    pull_response.raise_for_status()
                    pull_job_id = pull_response.json().get("job_id")
                    if not pull_job_id:
                        raise ValueError("Agent did not return a pull job ID")

                    progress_url = f"http://{host.address}/images/pull/{pull_job_id}/progress"
                    deadline = datetime.now(timezone.utc).timestamp() + settings.image_sync_timeout
                    while True:
                        progress_response = await client.get(progress_url)
                        progress_response.raise_for_status()
                        progress = progress_response.json()
                        job.progress_percent = progress.get("progress_percent", 0)
                        job.bytes_transferred = progress.get("bytes_transferred", 0)
                        if progress.get("total_bytes"):
                            job.total_bytes = progress["total_bytes"]
                        session.commit()

                        status = progress.get("status")
                        if status == "completed":
                            break
                        if status in {"failed", "unknown"}:
                            raise ValueError(progress.get("error") or "Agent pull failed")
                        if datetime.now(timezone.utc).timestamp() >= deadline:
                            raise ValueError("Timed out waiting for agent pull to complete")
                        await asyncio.sleep(2)
                elif image_kind == "docker":
                    # Get image size from Docker
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            "docker", "inspect", "--format", "{{.Size}}", reference,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
                        if proc.returncode == 0:
                            job.total_bytes = int(stdout.decode().strip())
                            session.commit()
                    except Exception as e:
                        logger.warning(f"Could not get image size for {reference}: {e}")

                    # Stream docker save to temp file to avoid loading
                    # the entire image into memory
                    import tempfile as _tempfile
                    tmp_fd, _docker_tmp_path = _tempfile.mkstemp(suffix=".tar")
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            "docker", "save", reference,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        saved_bytes = 0
                        chunk_size = settings.image_sync_chunk_size
                        with os.fdopen(tmp_fd, "wb") as tmp_out:
                            while True:
                                chunk = await proc.stdout.read(chunk_size)
                                if not chunk:
                                    break
                                tmp_out.write(chunk)
                                saved_bytes += len(chunk)
                        # Wait for process to finish (stdout already consumed)
                        _, stderr = await proc.communicate()
                        if proc.returncode != 0:
                            error_msg = stderr.decode() if stderr else "docker save failed"
                            raise ValueError(error_msg)
                    except BaseException:
                        # Clean up on any error during docker save
                        try:
                            os.unlink(_docker_tmp_path)
                        except OSError:
                            pass
                        _docker_tmp_path = None
                        raise

                    # Update progress while upload begins
                    job.bytes_transferred = saved_bytes
                    job.progress_percent = 50
                    session.commit()

                    tmp_file_obj = open(_docker_tmp_path, "rb")
                    files = {"file": ("image.tar", tmp_file_obj, "application/x-tar")}
                    params = {
                        "image_id": image_id,
                        "reference": reference,
                        "total_bytes": str(saved_bytes),
                        "job_id": job_id,
                        "device_id": image.get("device_id", ""),
                    }
                else:
                    raise ValueError(f"Unsupported image kind for sync: {image_kind}")

                if not is_file_based:
                    try:
                        response = await client.post(agent_url, files=files, params=params)
                        response.raise_for_status()
                        result = response.json()
                        if not result.get("success"):
                            raise ValueError(result.get("error", "Agent failed to receive image"))
                    except httpx.TimeoutException as e:
                        structured_error = categorize_httpx_error(
                            e, host_name=host.name, agent_id=host.id, job_id=job_id
                        )
                        raise ValueError(structured_error.to_error_message()) from e
                    except httpx.ConnectError as e:
                        structured_error = categorize_httpx_error(
                            e, host_name=host.name, agent_id=host.id, job_id=job_id
                        )
                        raise ValueError(structured_error.to_error_message()) from e
                    except httpx.HTTPStatusError as e:
                        structured_error = categorize_httpx_error(
                            e, host_name=host.name, agent_id=host.id, job_id=job_id
                        )
                        raise ValueError(structured_error.to_error_message()) from e
                    finally:
                        # Close file descriptors and clean up temp files
                        if image_kind == "docker" and _docker_tmp_path:
                            try:
                                tmp_file_obj.close()
                            except Exception:
                                pass
                            try:
                                os.unlink(_docker_tmp_path)
                            except OSError:
                                pass

            # Success
            job.status = "completed"
            job.progress_percent = 100
            job.completed_at = datetime.now(timezone.utc)
            session.commit()

            # Update ImageHost record
            image_host = session.query(models.ImageHost).filter(
                models.ImageHost.image_id == image_id,
                models.ImageHost.host_id == host.id
            ).first()
            if image_host:
                image_host.status = "synced"
                image_host.synced_at = datetime.now(timezone.utc)
                image_host.size_bytes = job.total_bytes
                image_host.error_message = None
                session.commit()

            logger.info(f"Sync job {job_id} completed successfully: {image_id} -> {host.name}")

        except Exception as e:
            import traceback
            logger.error(f"Sync job {job_id} failed: {e}")
            logger.debug(traceback.format_exc())

            # Determine error message - use structured error if available
            error_message = str(e)

            # Update job status
            job = session.get(models.ImageSyncJob, job_id)
            if job:
                job.status = "failed"
                job.error_message = error_message
                job.completed_at = datetime.now(timezone.utc)
                session.commit()

            # Update ImageHost status
            image_host = session.query(models.ImageHost).filter(
                models.ImageHost.image_id == image_id,
                models.ImageHost.host_id == host.id
            ).first()
            if image_host:
                image_host.status = "failed"
                image_host.error_message = error_message
                session.commit()


@router.get("/library/{image_id}/stream")
async def stream_image(
    image_id: str,
    current_user: models.User = Depends(get_current_user),
) -> StreamingResponse:
    """Stream a syncable image for agents to pull."""
    from urllib.parse import unquote
    image_id = unquote(image_id)

    # Verify image exists
    manifest = load_manifest()
    image = find_image_by_id(manifest, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found in library")

    reference = image.get("reference", "")
    if not reference:
        raise HTTPException(status_code=400, detail="Image has no reference")

    if image.get("kind") != "docker" and not (reference.startswith("/") or reference.endswith((".qcow2", ".img", ".iol"))):
        raise HTTPException(status_code=400, detail="Only Docker and file-based images can be streamed")

    if reference.startswith("/") or reference.endswith((".qcow2", ".img", ".iol")):
        source_path = Path(reference)
        if not source_path.exists() or not source_path.is_file():
            raise HTTPException(status_code=404, detail="Source image file not found")

        def generate_file():
            with source_path.open("rb") as f:
                while True:
                    chunk = f.read(settings.image_sync_chunk_size)
                    if not chunk:
                        break
                    yield chunk

        return StreamingResponse(
            generate_file(),
            media_type="application/octet-stream",
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(source_path.stat().st_size),
            },
        )

    # Get image size for Content-Length header (async subprocess to avoid blocking)
    content_length = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "--format", "{{.Size}}", reference,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode == 0:
            # Note: This is the image size, not the tar size
            # Tar size is usually slightly larger
            content_length = int(stdout.decode().strip())
    except Exception:
        pass

    async def generate():
        """Stream docker save output."""
        proc = await asyncio.create_subprocess_exec(
            "docker", "save", reference,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            while True:
                chunk = await proc.stdout.read(settings.image_sync_chunk_size)
                if not chunk:
                    break
                yield chunk

            # Check for errors
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                error_msg = stderr.decode() if stderr else "docker save failed"
                print(f"Error streaming image: {error_msg}")

        except Exception as e:
            print(f"Error streaming image: {e}")
            proc.kill()
            raise

    headers = {"Content-Type": "application/x-tar"}
    if content_length:
        headers["Content-Length"] = str(content_length)

    return StreamingResponse(
        generate(),
        media_type="application/x-tar",
        headers=headers,
    )


@router.get("/sync-jobs")
def list_sync_jobs(
    status: str | None = None,
    image_id: str | None = None,
    host_id: str | None = None,
    limit: int = 50,
    current_user: models.User = Depends(get_current_user),
    database: Session = Depends(db.get_db),
) -> list[SyncJobOut]:
    """List image sync jobs with optional filters.

    Filters:
    - status: Filter by job status (pending, transferring, loading, completed, failed)
    - image_id: Filter by image ID
    - host_id: Filter by target host ID
    """
    query = database.query(models.ImageSyncJob)

    if status:
        query = query.filter(models.ImageSyncJob.status == status)
    if image_id:
        from urllib.parse import unquote
        query = query.filter(models.ImageSyncJob.image_id == unquote(image_id))
    if host_id:
        query = query.filter(models.ImageSyncJob.host_id == host_id)

    jobs = query.order_by(models.ImageSyncJob.created_at.desc()).limit(limit).all()

    # Get host names
    host_ids = {j.host_id for j in jobs}
    hosts = database.query(models.Host).filter(models.Host.id.in_(host_ids)).all()
    host_names = {h.id: h.name for h in hosts}

    return [
        SyncJobOut(
            id=job.id,
            image_id=job.image_id,
            host_id=job.host_id,
            host_name=host_names.get(job.host_id),
            status=job.status,
            progress_percent=job.progress_percent,
            bytes_transferred=job.bytes_transferred,
            total_bytes=job.total_bytes,
            error_message=job.error_message,
            started_at=job.started_at,
            completed_at=job.completed_at,
            created_at=job.created_at,
        )
        for job in jobs
    ]


@router.get("/sync-jobs/{job_id}")
def get_sync_job(
    job_id: str,
    current_user: models.User = Depends(get_current_user),
    database: Session = Depends(db.get_db),
) -> SyncJobOut:
    """Get details of a specific sync job."""
    job = database.get(models.ImageSyncJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")

    host = database.get(models.Host, job.host_id)

    return SyncJobOut(
        id=job.id,
        image_id=job.image_id,
        host_id=job.host_id,
        host_name=host.name if host else None,
        status=job.status,
        progress_percent=job.progress_percent,
        bytes_transferred=job.bytes_transferred,
        total_bytes=job.total_bytes,
        error_message=job.error_message,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at,
    )


@router.delete("/sync-jobs/{job_id}")
def cancel_sync_job(
    job_id: str,
    current_user: models.User = Depends(get_current_admin),
    database: Session = Depends(db.get_db),
) -> dict:
    """Cancel a pending or in-progress sync job.

    Only jobs in pending, transferring, or loading status can be cancelled.
    Completed or failed jobs cannot be cancelled.
    Requires admin access.
    """

    job = database.get(models.ImageSyncJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")

    if job.status in ("completed", "failed", "cancelled"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel job with status '{job.status}'"
        )

    job.status = "cancelled"
    job.completed_at = datetime.now(timezone.utc)
    database.commit()

    # Update ImageHost status back to unknown or missing
    image_host = database.query(models.ImageHost).filter(
        models.ImageHost.image_id == job.image_id,
        models.ImageHost.host_id == job.host_id
    ).first()
    if image_host:
        image_host.status = "unknown"
        database.commit()

    return {"status": "cancelled"}
