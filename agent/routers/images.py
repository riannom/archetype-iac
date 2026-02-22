"""Image synchronization and management endpoints."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path

import docker
import httpx
from fastapi import APIRouter, HTTPException, UploadFile

from agent.config import settings
from agent.docker_client import get_docker_client
from agent.helpers import _get_docker_images
from agent.http_client import get_controller_auth_headers, get_http_client
from agent.schemas import (
    DockerImageInfo,
    ImageExistsResponse,
    ImageInventoryResponse,
    ImagePullProgress,
    ImagePullRequest,
    ImagePullResponse,
    ImageReceiveResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["images"])

# --- Module-level state ---

# Track active image pull jobs
_image_pull_jobs: dict[str, ImagePullProgress] = {}
_agent_start_time: float = time.time()
_TRANSFER_STATE_FILE = Path(settings.workspace_path) / ".active_transfers.json"


def _persist_transfer_state() -> None:
    """Write active (non-terminal) transfer jobs to disk for crash recovery."""
    active = {
        job_id: progress.model_dump()
        for job_id, progress in _image_pull_jobs.items()
        if progress.status in ("pending", "transferring", "loading")
    }
    if active:
        try:
            _TRANSFER_STATE_FILE.write_text(json.dumps(active))
        except OSError as e:
            logger.debug(f"Failed to persist transfer state: {e}")
    else:
        _clear_persisted_transfer_state()


def _clear_persisted_transfer_state() -> None:
    """Remove the persisted state file when no active jobs remain."""
    try:
        _TRANSFER_STATE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _load_persisted_transfer_state() -> None:
    """On startup, load persisted state and mark interrupted jobs as failed."""
    if not _TRANSFER_STATE_FILE.exists():
        return
    try:
        data = json.loads(_TRANSFER_STATE_FILE.read_text())
        for job_id, entry in data.items():
            _image_pull_jobs[job_id] = ImagePullProgress(
                job_id=job_id,
                status="failed",
                progress_percent=entry.get("progress_percent", 0),
                bytes_transferred=entry.get("bytes_transferred", 0),
                total_bytes=entry.get("total_bytes", 0),
                error="Agent restarted during transfer",
                started_at=entry.get("started_at"),
            )
        if data:
            logger.info(
                f"Recovered {len(data)} interrupted transfer(s) from previous run"
            )
        _TRANSFER_STATE_FILE.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Failed to load persisted transfer state: {e}")


# --- Endpoints ---


@router.get("/images")
def list_images() -> ImageInventoryResponse:
    """List all Docker images on this agent.

    Returns a list of images with their tags, sizes, and IDs.
    Used by controller to check image availability before deployment.
    """
    images = _get_docker_images()
    return ImageInventoryResponse(images=images)


@router.post("/images/backfill-checksums")
async def backfill_image_checksums() -> dict:
    """Compute SHA256 sidecars for existing file-based images missing them."""
    import asyncio
    import hashlib
    import glob as globmod

    image_dir = "/var/lib/archetype/images"
    if not os.path.isdir(image_dir):
        return {"updated": 0, "errors": []}

    def _backfill():
        updated = 0
        errors = []
        for path in globmod.glob(os.path.join(image_dir, "*.qcow2")) + \
                     globmod.glob(os.path.join(image_dir, "*.img")):
            sidecar = path + ".sha256"
            if os.path.exists(sidecar):
                continue
            try:
                h = hashlib.sha256()
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(1024 * 1024)
                        if not chunk:
                            break
                        h.update(chunk)
                with open(sidecar, "w") as sf:
                    sf.write(h.hexdigest())
                updated += 1
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
        return {"updated": updated, "errors": errors}

    return await asyncio.to_thread(_backfill)


@router.post("/images/receive")
async def receive_image(
    file: UploadFile,
    image_id: str = "",
    reference: str = "",
    total_bytes: int = 0,
    job_id: str = "",
    sha256: str = "",
) -> ImageReceiveResponse:
    """Receive a streamed Docker image tar from controller.

    This endpoint accepts a Docker image tar file (from `docker save`)
    and loads it into the local Docker daemon.

    Args:
        file: The image tar file
        image_id: Library image ID for tracking
        reference: Docker reference (e.g., "ceos:4.28.0F")
        total_bytes: Expected size for progress
        job_id: Sync job ID for progress reporting

    Returns:
        Result of loading the image
    """
    import os
    import subprocess
    import tempfile

    logger.info(f"Receiving image: {reference} ({total_bytes} bytes)")
    is_file_based = reference.startswith("/") or reference.endswith((".qcow2", ".img", ".iol"))

    # Update progress if job_id provided
    _transfer_started_at = time.time()
    if job_id:
        _image_pull_jobs[job_id] = ImagePullProgress(
            job_id=job_id,
            status="transferring",
            progress_percent=0,
            bytes_transferred=0,
            total_bytes=total_bytes,
            started_at=_transfer_started_at,
        )
        _persist_transfer_state()

    try:
        if is_file_based:
            if reference.endswith((".qcow2", ".img")) and not settings.enable_libvirt:
                return ImageReceiveResponse(success=False, error="libvirt is not enabled on target agent")
            if reference.endswith(".iol") and not settings.enable_docker:
                return ImageReceiveResponse(success=False, error="docker is not enabled on target agent")
            if not reference.startswith("/"):
                return ImageReceiveResponse(
                    success=False,
                    error="file-based image sync requires an absolute destination path",
                )

            destination = Path(reference)
            allowed_bases = [
                Path(settings.workspace_path).resolve(),
                Path("/var/lib/archetype/images").resolve(),
            ]
            if not any(destination.resolve().is_relative_to(b) for b in allowed_bases):
                raise HTTPException(status_code=400, detail="Invalid destination path")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp_destination = destination.with_name(
                f"{destination.name}.part-{uuid.uuid4().hex[:8]}"
            )

            bytes_written = 0
            chunk_size = 1024 * 1024  # 1MB chunks
            try:
                with temp_destination.open("wb") as tmp_out:
                    while True:
                        chunk = await file.read(chunk_size)
                        if not chunk:
                            break
                        tmp_out.write(chunk)
                        bytes_written += len(chunk)

                        if job_id and total_bytes > 0:
                            percent = min(95, int((bytes_written / total_bytes) * 95))
                            _image_pull_jobs[job_id] = ImagePullProgress(
                                job_id=job_id,
                                status="transferring",
                                progress_percent=percent,
                                bytes_transferred=bytes_written,
                                total_bytes=total_bytes,
                                started_at=_transfer_started_at,
                            )

                os.replace(temp_destination, destination)
            except Exception:
                if temp_destination.exists():
                    temp_destination.unlink(missing_ok=True)
                raise

            # Compute and verify checksum for file-based images
            import hashlib
            import asyncio

            def _compute_file_sha256():
                h = hashlib.sha256()
                with open(destination, "rb") as hf:
                    while True:
                        block = hf.read(1024 * 1024)
                        if not block:
                            break
                        h.update(block)
                return h.hexdigest()

            actual_hash = await asyncio.to_thread(_compute_file_sha256)
            if sha256 and actual_hash != sha256:
                destination.unlink(missing_ok=True)
                return ImageReceiveResponse(
                    success=False,
                    error=f"Checksum mismatch: expected {sha256[:16]}..., got {actual_hash[:16]}...",
                )
            # Write sidecar checksum file for future checks
            Path(str(destination) + ".sha256").write_text(actual_hash)

            if job_id:
                _image_pull_jobs[job_id] = ImagePullProgress(
                    job_id=job_id,
                    status="completed",
                    progress_percent=100,
                    bytes_transferred=bytes_written,
                    total_bytes=total_bytes,
                )
                _persist_transfer_state()

            logger.info(f"Stored file image to {destination} ({bytes_written} bytes)")
            return ImageReceiveResponse(success=True, loaded_images=[str(destination)])

        # Save uploaded file to temp
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".tar") as tmp_file:
                bytes_written = 0
                chunk_size = 1024 * 1024  # 1MB chunks

                while True:
                    chunk = await file.read(chunk_size)
                    if not chunk:
                        break
                    tmp_file.write(chunk)
                    bytes_written += len(chunk)

                    # Update progress
                    if job_id and total_bytes > 0:
                        percent = min(90, int((bytes_written / total_bytes) * 90))
                        _image_pull_jobs[job_id] = ImagePullProgress(
                            job_id=job_id,
                            status="transferring",
                            progress_percent=percent,
                            bytes_transferred=bytes_written,
                            total_bytes=total_bytes,
                            started_at=_transfer_started_at,
                        )

                tmp_path = tmp_file.name

            logger.debug(f"Saved {bytes_written} bytes to {tmp_path}")

            # Update status to loading
            if job_id:
                _image_pull_jobs[job_id] = ImagePullProgress(
                    job_id=job_id,
                    status="loading",
                    progress_percent=90,
                    bytes_transferred=bytes_written,
                    total_bytes=total_bytes,
                )

            # Load into Docker (wrapped in thread to avoid blocking)
            def _sync_docker_load():
                return subprocess.run(
                    ["docker", "load", "-i", tmp_path],
                    capture_output=True,
                    text=True,
                    timeout=600,  # 10 minute timeout for large images
                )

            result = await asyncio.to_thread(_sync_docker_load)

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "docker load failed"
                logger.error(f"Docker load failed for {reference}: {error_msg}")
                if job_id:
                    _image_pull_jobs[job_id] = ImagePullProgress(
                        job_id=job_id,
                        status="failed",
                        progress_percent=0,
                        error=error_msg,
                    )
                return ImageReceiveResponse(success=False, error=error_msg)

            # Parse loaded images from output
            output = (result.stdout or "") + (result.stderr or "")
            loaded_images = []
            for line in output.splitlines():
                if "Loaded image:" in line:
                    loaded_images.append(line.split("Loaded image:", 1)[-1].strip())
                elif "Loaded image ID:" in line:
                    loaded_images.append(line.split("Loaded image ID:", 1)[-1].strip())

            logger.info(f"Successfully loaded images: {loaded_images}")

            # Update final status
            if job_id:
                _image_pull_jobs[job_id] = ImagePullProgress(
                    job_id=job_id,
                    status="completed",
                    progress_percent=100,
                    bytes_transferred=bytes_written,
                    total_bytes=total_bytes,
                )
                _persist_transfer_state()

            return ImageReceiveResponse(success=True, loaded_images=loaded_images)

        finally:
            # Always clean up temp file, even on exceptions
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    except subprocess.TimeoutExpired:
        error_msg = "docker load timed out"
        logger.error(f"Docker load timeout for {reference}")
        if job_id:
            _image_pull_jobs[job_id] = ImagePullProgress(
                job_id=job_id,
                status="failed",
                error=error_msg,
            )
            _persist_transfer_state()
        return ImageReceiveResponse(success=False, error=error_msg)

    except Exception as e:
        logger.error(f"Error receiving image {reference}: {e}", exc_info=True)
        error_msg = str(e)
        if job_id:
            _image_pull_jobs[job_id] = ImagePullProgress(
                job_id=job_id,
                status="failed",
                error=error_msg,
            )
            _persist_transfer_state()
        return ImageReceiveResponse(success=False, error=error_msg)


@router.post("/images/pull")
async def pull_image(request: ImagePullRequest) -> ImagePullResponse:
    """Initiate pulling an image from the controller.

    This endpoint starts an async pull operation where the agent
    fetches the image from the controller's stream endpoint.

    Args:
        request: Image ID and reference to pull

    Returns:
        Job ID for tracking progress
    """
    import uuid

    job_id = str(uuid.uuid4())[:8]

    # Initialize job status
    _image_pull_jobs[job_id] = ImagePullProgress(
        job_id=job_id,
        status="pending",
    )

    # Start async pull task
    asyncio.create_task(_execute_pull_from_controller(
        job_id=job_id,
        image_id=request.image_id,
        reference=request.reference,
    ))

    return ImagePullResponse(job_id=job_id, status="pending")


async def _execute_pull_from_controller(job_id: str, image_id: str, reference: str):
    """Execute image pull from controller in background.

    Fetches the image stream from the controller and loads it locally.
    """
    import tempfile
    import subprocess
    import os

    logger.info(f"Starting pull from controller: {reference}")

    tmp_path = None
    _pull_started_at = time.time()
    try:
        _image_pull_jobs[job_id] = ImagePullProgress(
            job_id=job_id,
            status="transferring",
            progress_percent=5,
            started_at=_pull_started_at,
        )
        _persist_transfer_state()

        # Build stream URL - encode the image_id for the URL
        from urllib.parse import quote
        encoded_image_id = quote(image_id, safe='')
        stream_url = f"{settings.controller_url}/images/library/{encoded_image_id}/stream"

        logger.debug(f"Fetching from: {stream_url}")

        # Stream the image from controller
        client = get_http_client()
        async with client.stream(
            "GET", stream_url,
            headers=get_controller_auth_headers(),
            timeout=httpx.Timeout(600.0),
        ) as response:
            if response.status_code != 200:
                error_msg = f"Controller returned {response.status_code}"
                _image_pull_jobs[job_id] = ImagePullProgress(
                    job_id=job_id,
                    status="failed",
                    error=error_msg,
                )
                return

            # Get content length if available
            total_bytes = int(response.headers.get("content-length", 0))

            # Save to temp file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".tar") as tmp_file:
                tmp_path = tmp_file.name
                bytes_written = 0
                async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                    tmp_file.write(chunk)
                    bytes_written += len(chunk)

                    # Update progress
                    if total_bytes > 0:
                        percent = min(85, int((bytes_written / total_bytes) * 85))
                    else:
                        percent = min(85, bytes_written // (1024 * 1024))  # 1% per MB
                    _image_pull_jobs[job_id] = ImagePullProgress(
                        job_id=job_id,
                        status="transferring",
                        progress_percent=percent,
                        bytes_transferred=bytes_written,
                        total_bytes=total_bytes,
                        started_at=_pull_started_at,
                    )

        logger.debug(f"Downloaded {bytes_written} bytes")

        # Update to loading status
        _image_pull_jobs[job_id] = ImagePullProgress(
            job_id=job_id,
            status="loading",
            progress_percent=90,
            bytes_transferred=bytes_written,
            total_bytes=total_bytes,
        )

        # Load into Docker (wrapped in thread to avoid blocking)
        def _sync_docker_load():
            return subprocess.run(
                ["docker", "load", "-i", tmp_path],
                capture_output=True,
                text=True,
                timeout=600,
            )

        result = await asyncio.to_thread(_sync_docker_load)

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "docker load failed"
            logger.error(f"Docker load failed for {reference}: {error_msg}")
            _image_pull_jobs[job_id] = ImagePullProgress(
                job_id=job_id,
                status="failed",
                error=error_msg,
            )
            _persist_transfer_state()
            return

        logger.info(f"Successfully loaded image: {reference}")
        _image_pull_jobs[job_id] = ImagePullProgress(
            job_id=job_id,
            status="completed",
            progress_percent=100,
            bytes_transferred=bytes_written,
            total_bytes=total_bytes,
        )
        _persist_transfer_state()

    except Exception as e:
        logger.error(f"Error pulling image {reference}: {e}", exc_info=True)
        _image_pull_jobs[job_id] = ImagePullProgress(
            job_id=job_id,
            status="failed",
            error=str(e),
        )
        _persist_transfer_state()
    finally:
        # Always clean up temp file
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# CRITICAL: get_pull_progress and get_active_transfers MUST be defined
# BEFORE check_image because the catch-all {reference:path} would swallow them.

@router.get("/images/pull/{job_id}/progress")
def get_pull_progress(job_id: str) -> ImagePullProgress:
    """Get progress of an image pull operation.

    Args:
        job_id: The job ID from the pull request

    Returns:
        Current progress of the pull operation. If the job is not found,
        returns a response with status="unknown" instead of 404, as the
        agent may have restarted and lost in-memory job state.
    """
    if job_id not in _image_pull_jobs:
        # Return informative response instead of 404
        # This helps diagnose cases where the agent restarted during a transfer
        return ImagePullProgress(
            job_id=job_id,
            status="unknown",
            progress_percent=0,
            bytes_transferred=0,
            total_bytes=0,
            error="Job not found - agent may have restarted. Check controller for current job status.",
        )
    return _image_pull_jobs[job_id]


@router.get("/images/active-transfers")
def get_active_transfers() -> dict:
    """Report active (non-terminal) image transfers and agent uptime.

    Used by the API health check to verify whether transfers are genuinely
    active before marking stuck jobs as failed.
    """
    import glob as glob_mod

    active = {
        job_id: progress.model_dump()
        for job_id, progress in _image_pull_jobs.items()
        if progress.status in ("pending", "transferring", "loading")
    }

    # Also report stale .tar temp files in /tmp as evidence of in-progress work
    stale_temp_files = []
    for path_str in glob_mod.glob("/tmp/tmp*.tar"):
        try:
            stat = os.stat(path_str)
            stale_temp_files.append({
                "path": path_str,
                "size_bytes": stat.st_size,
                "age_seconds": int(time.time() - stat.st_mtime),
            })
        except OSError:
            pass

    return {
        "active_jobs": active,
        "temp_files": stale_temp_files,
        "agent_uptime_seconds": int(time.time() - _agent_start_time),
    }


# MUST BE LAST: catch-all path parameter swallows any /images/* route defined after it.
@router.get("/images/{reference:path}")
def check_image(reference: str) -> ImageExistsResponse:
    """Check if a specific image exists on this agent.

    Args:
        reference: Docker image reference or file path

    Returns:
        Whether the image exists and its details if found.
    """
    try:
        # File-based images (qcow2/img/iol): check path existence on agent.
        if reference.startswith("/") or reference.endswith((".qcow2", ".img", ".iol")):
            # qcow2/img requires libvirt provider support.
            if reference.endswith((".qcow2", ".img")) and not settings.enable_libvirt:
                return ImageExistsResponse(exists=False)
            exists = os.path.exists(reference)
            file_sha256 = None
            if exists:
                sidecar = reference + ".sha256"
                if os.path.exists(sidecar):
                    try:
                        file_sha256 = open(sidecar).read().strip()
                    except OSError:
                        pass
            return ImageExistsResponse(exists=exists, sha256=file_sha256)

        client = get_docker_client()

        # Try to get the image
        try:
            img = client.images.get(reference)
            return ImageExistsResponse(
                exists=True,
                image=DockerImageInfo(
                    id=img.id,
                    tags=img.tags or [],
                    size_bytes=img.attrs.get("Size", 0),
                    created=img.attrs.get("Created", None),
                ),
            )
        except docker.errors.ImageNotFound:
            return ImageExistsResponse(exists=False)

    except Exception as e:
        logger.error(f"Error checking image {reference}: {e}")
        return ImageExistsResponse(exists=False)
