"""Image upload and management endpoints."""
from __future__ import annotations

import asyncio
import json
import lzma
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from app import models
from app.auth import get_current_user
from app.image_store import (
    create_image_entry,
    delete_image_entry,
    detect_device_from_filename,
    ensure_image_store,
    find_image_by_id,
    load_manifest,
    qcow2_path,
    save_manifest,
    update_image_entry,
)

router = APIRouter(prefix="/images", tags=["images"])

# Track upload progress for streaming status
_upload_progress: dict[str, dict] = {}


def _is_docker_image_tar(tar_path: str) -> bool:
    """Check if tar is a Docker image (has manifest.json) vs raw filesystem.

    Docker images have manifest.json or repositories at the root level,
    typically in the first few entries. We only check the first 20 entries
    to avoid reading the entire tar for large filesystem archives.
    """
    try:
        with tarfile.open(tar_path, "r:*") as tf:
            # Only check first 20 entries - Docker metadata is always at the start
            for i, member in enumerate(tf):
                if i >= 20:
                    break
                name = member.name.lstrip("./")
                if name in ("manifest.json", "repositories"):
                    return True
            return False
    except Exception:
        return False


def _format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def _run_docker_with_progress(
    cmd: list[str],
    progress_callback: callable,
    operation_name: str,
) -> tuple[int, str, str]:
    """Run a docker command and stream progress updates.

    Args:
        cmd: The docker command to run
        progress_callback: Function to call with status updates
        operation_name: Name of the operation for status messages

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    progress_callback(f"Starting {operation_name}...")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    stdout_lines = []
    stderr_lines = []

    # Read output in real-time
    def read_stream(stream, lines_list, is_stderr=False):
        for line in iter(stream.readline, ''):
            line = line.strip()
            if line:
                lines_list.append(line)
                # Parse docker load progress messages
                if not is_stderr:
                    if "Loading layer" in line or "Loaded image" in line:
                        progress_callback(line)
                    elif line.startswith("sha256:"):
                        progress_callback(f"Processing layer: {line[:20]}...")

    # Run reading in threads to handle both streams
    stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, stdout_lines, False))
    stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, stderr_lines, True))

    stdout_thread.start()
    stderr_thread.start()

    # Wait for process to complete
    process.wait()
    stdout_thread.join()
    stderr_thread.join()

    return process.returncode, "\n".join(stdout_lines), "\n".join(stderr_lines)


@router.post("/load")
def load_image(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
    stream: bool = Query(default=False, description="Stream progress updates via SSE"),
):
    """Load a Docker image from a tar archive.

    If stream=true, returns Server-Sent Events with progress updates.
    Otherwise returns a JSON response when complete.
    """
    if stream:
        return StreamingResponse(
            _load_image_streaming(file),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
            },
        )

    # Non-streaming mode (original behavior)
    return _load_image_sync(file)


def _send_sse_event(event_type: str, data: dict) -> str:
    """Format a Server-Sent Event message."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def _load_image_streaming(file: UploadFile) -> AsyncGenerator[str, None]:
    """Stream image loading progress via Server-Sent Events."""
    filename = file.filename or "image.tar"
    suffixes = Path(filename).suffixes
    suffix = "".join(suffixes) if suffixes else ".tar"
    temp_path = ""
    load_path = ""
    decompressed_path = ""

    try:
        # Phase 1: Save uploaded file
        yield _send_sse_event("progress", {
            "phase": "saving",
            "message": f"Saving uploaded file: {filename}...",
            "percent": 5,
        })

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            # Read in chunks and report progress
            chunk_size = 1024 * 1024  # 1MB chunks
            bytes_written = 0
            content = await file.read()
            total_size = len(content)

            # Write in chunks for progress reporting
            for i in range(0, total_size, chunk_size):
                chunk = content[i:i + chunk_size]
                tmp_file.write(chunk)
                bytes_written += len(chunk)
                percent = min(30, 5 + int((bytes_written / total_size) * 25))
                yield _send_sse_event("progress", {
                    "phase": "saving",
                    "message": f"Saving file... {_format_size(bytes_written)} / {_format_size(total_size)}",
                    "percent": percent,
                })

            temp_path = tmp_file.name

        load_path = temp_path
        file_size = os.path.getsize(temp_path)

        yield _send_sse_event("progress", {
            "phase": "saved",
            "message": f"File saved ({_format_size(file_size)}). Checking format...",
            "percent": 30,
        })

        # Phase 2: Decompress if needed
        if filename.lower().endswith((".tar.xz", ".txz", ".xz")):
            yield _send_sse_event("progress", {
                "phase": "decompressing",
                "message": "Decompressing XZ archive (this may take a while for large files)...",
                "percent": 35,
            })

            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".tar") as tmp_tar:
                    with lzma.open(temp_path, "rb") as source:
                        # Read and decompress in chunks
                        chunk_size = 1024 * 1024  # 1MB
                        bytes_decompressed = 0
                        while True:
                            chunk = source.read(chunk_size)
                            if not chunk:
                                break
                            tmp_tar.write(chunk)
                            bytes_decompressed += len(chunk)
                            if bytes_decompressed % (10 * 1024 * 1024) == 0:  # Update every 10MB
                                yield _send_sse_event("progress", {
                                    "phase": "decompressing",
                                    "message": f"Decompressing... {_format_size(bytes_decompressed)} extracted",
                                    "percent": 40,
                                })
                    decompressed_path = tmp_tar.name

                load_path = decompressed_path
                decompressed_size = os.path.getsize(decompressed_path)

                yield _send_sse_event("progress", {
                    "phase": "decompressed",
                    "message": f"Decompression complete ({_format_size(decompressed_size)})",
                    "percent": 50,
                })
            except lzma.LZMAError as exc:
                yield _send_sse_event("error", {
                    "message": f"Failed to decompress archive: {exc}",
                })
                return

        # Phase 3: Detect format
        yield _send_sse_event("progress", {
            "phase": "detecting",
            "message": "Detecting image format...",
            "percent": 55,
        })

        is_docker_image = _is_docker_image_tar(load_path)
        loaded_images = []
        output = ""

        # Phase 4: Load into Docker
        if is_docker_image:
            yield _send_sse_event("progress", {
                "phase": "loading",
                "message": "Docker image detected. Running 'docker load' (this may take several minutes)...",
                "percent": 60,
            })

            # Run docker load with progress tracking
            process = subprocess.Popen(
                ["docker", "load", "-i", load_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            output_lines = []
            layer_count = 0
            for line in iter(process.stdout.readline, ''):
                line = line.strip()
                if line:
                    output_lines.append(line)
                    if "Loading layer" in line:
                        layer_count += 1
                        yield _send_sse_event("progress", {
                            "phase": "loading",
                            "message": f"Loading layer {layer_count}...",
                            "detail": line,
                            "percent": min(95, 60 + layer_count * 3),
                        })
                    elif "Loaded image:" in line:
                        image_name = line.split("Loaded image:", 1)[-1].strip()
                        loaded_images.append(image_name)
                        yield _send_sse_event("progress", {
                            "phase": "loading",
                            "message": f"Loaded: {image_name}",
                            "percent": 95,
                        })
                    elif "Loaded image ID:" in line:
                        image_id = line.split("Loaded image ID:", 1)[-1].strip()
                        loaded_images.append(image_id)

            process.wait()
            output = "\n".join(output_lines)

            if process.returncode != 0:
                yield _send_sse_event("error", {
                    "message": output.strip() or "docker load failed",
                })
                return
        else:
            # Raw filesystem tar (e.g., cEOS) - use docker import
            base_name = Path(filename).stem
            for ext in [".tar", ".gz", ".xz"]:
                if base_name.lower().endswith(ext):
                    base_name = base_name[:-len(ext)]
            image_name = base_name.lower().replace(" ", "-").replace("_", "-")
            image_tag = f"{image_name}:imported"

            yield _send_sse_event("progress", {
                "phase": "importing",
                "message": f"Filesystem archive detected. Importing as '{image_tag}'...",
                "percent": 60,
            })

            result = subprocess.run(
                ["docker", "import", load_path, image_tag],
                capture_output=True,
                text=True,
                check=False,
            )
            output = (result.stdout or "") + (result.stderr or "")

            if result.returncode != 0:
                yield _send_sse_event("error", {
                    "message": output.strip() or "docker import failed",
                })
                return

            image_id = output.strip().split(":")[-1][:12] if output.strip() else ""
            loaded_images.append(image_tag)
            output = f"Imported filesystem as {image_tag} (ID: {image_id})"

            yield _send_sse_event("progress", {
                "phase": "imported",
                "message": f"Import complete: {image_tag}",
                "percent": 95,
            })

        if not loaded_images:
            yield _send_sse_event("error", {
                "message": output.strip() or "No images detected in archive",
            })
            return

        # Phase 5: Update manifest
        yield _send_sse_event("progress", {
            "phase": "finalizing",
            "message": "Updating image library...",
            "percent": 98,
        })

        manifest = load_manifest()

        # Check for duplicates
        for image_ref in loaded_images:
            potential_id = f"docker:{image_ref}"
            if find_image_by_id(manifest, potential_id):
                yield _send_sse_event("error", {
                    "message": f"Image '{image_ref}' already exists in the library",
                })
                return

        for image_ref in loaded_images:
            device_id, version = detect_device_from_filename(image_ref)
            entry = create_image_entry(
                image_id=f"docker:{image_ref}",
                kind="docker",
                reference=image_ref,
                filename=filename,
                device_id=device_id,
                version=version,
                size_bytes=file_size,
            )
            manifest["images"].append(entry)
        save_manifest(manifest)

        # Final success event
        yield _send_sse_event("complete", {
            "message": output.strip() or "Image loaded successfully",
            "images": loaded_images,
            "percent": 100,
        })

    except Exception as e:
        yield _send_sse_event("error", {
            "message": str(e),
        })
    finally:
        await file.close()
        if decompressed_path and os.path.exists(decompressed_path):
            os.unlink(decompressed_path)
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _load_image_sync(file: UploadFile) -> dict:
    """Original synchronous image loading (non-streaming)."""
    filename = file.filename or "image.tar"
    suffixes = Path(filename).suffixes
    suffix = "".join(suffixes) if suffixes else ".tar"
    temp_path = ""
    load_path = ""
    decompressed_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            shutil.copyfileobj(file.file, tmp_file)
            temp_path = tmp_file.name
        load_path = temp_path
        if filename.lower().endswith((".tar.xz", ".txz", ".xz")):
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".tar") as tmp_tar:
                    with lzma.open(temp_path, "rb") as source:
                        shutil.copyfileobj(source, tmp_tar)
                    decompressed_path = tmp_tar.name
                load_path = decompressed_path
            except lzma.LZMAError as exc:
                raise HTTPException(status_code=400, detail=f"Failed to decompress archive: {exc}") from exc

        # Detect tar format and use appropriate command
        is_docker_image = _is_docker_image_tar(load_path)
        loaded_images = []

        if is_docker_image:
            # Standard Docker image from `docker save`
            result = subprocess.run(
                ["docker", "load", "-i", load_path],
                capture_output=True,
                text=True,
                check=False,
            )
            output = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=output.strip() or "docker load failed")
            for line in output.splitlines():
                if "Loaded image:" in line:
                    loaded_images.append(line.split("Loaded image:", 1)[-1].strip())
                elif "Loaded image ID:" in line:
                    loaded_images.append(line.split("Loaded image ID:", 1)[-1].strip())
        else:
            # Raw filesystem tar (e.g., cEOS) - use docker import
            # Derive image name from filename
            base_name = Path(filename).stem
            # Remove common extensions that might remain
            for ext in [".tar", ".gz", ".xz"]:
                if base_name.lower().endswith(ext):
                    base_name = base_name[:-len(ext)]
            # Create a clean image name
            image_name = base_name.lower().replace(" ", "-").replace("_", "-")
            image_tag = f"{image_name}:imported"

            result = subprocess.run(
                ["docker", "import", load_path, image_tag],
                capture_output=True,
                text=True,
                check=False,
            )
            output = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=output.strip() or "docker import failed")
            # docker import outputs the image ID
            image_id = output.strip().split(":")[-1][:12] if output.strip() else ""
            loaded_images.append(image_tag)
            output = f"Imported filesystem as {image_tag} (ID: {image_id})"

        if not loaded_images:
            raise HTTPException(status_code=500, detail=output.strip() or "No images detected in archive")

        # Get file size if available
        file_size = None
        if temp_path and os.path.exists(temp_path):
            file_size = os.path.getsize(temp_path)

        manifest = load_manifest()

        # Check for duplicate images before adding
        for image_ref in loaded_images:
            potential_id = f"docker:{image_ref}"
            if find_image_by_id(manifest, potential_id):
                raise HTTPException(
                    status_code=409,
                    detail=f"Image '{image_ref}' already exists in the library"
                )

        for image_ref in loaded_images:
            device_id, version = detect_device_from_filename(image_ref)
            entry = create_image_entry(
                image_id=f"docker:{image_ref}",
                kind="docker",
                reference=image_ref,
                filename=filename,
                device_id=device_id,
                version=version,
                size_bytes=file_size,
            )
            manifest["images"].append(entry)
        save_manifest(manifest)
        return {"output": output.strip() or "Image loaded", "images": loaded_images}
    finally:
        file.file.close()
        if decompressed_path and os.path.exists(decompressed_path):
            os.unlink(decompressed_path)
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


@router.post("/qcow2")
def upload_qcow2(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    if not file.filename.lower().endswith((".qcow2", ".qcow")):
        raise HTTPException(status_code=400, detail="File must be a qcow2 image")

    # Check for duplicate before saving
    manifest = load_manifest()
    potential_id = f"qcow2:{Path(file.filename).name}"
    if find_image_by_id(manifest, potential_id):
        raise HTTPException(
            status_code=409,
            detail=f"Image '{file.filename}' already exists in the library"
        )

    destination = qcow2_path(Path(file.filename).name)
    try:
        with destination.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
    finally:
        file.file.close()
    # Get file size
    file_size = destination.stat().st_size if destination.exists() else None

    device_id, version = detect_device_from_filename(destination.name)
    entry = create_image_entry(
        image_id=f"qcow2:{destination.name}",
        kind="qcow2",
        reference=str(destination),
        filename=destination.name,
        device_id=device_id,
        version=version,
        size_bytes=file_size,
    )
    manifest["images"].append(entry)
    save_manifest(manifest)
    return {"path": str(destination), "filename": destination.name}


@router.get("/qcow2")
def list_qcow2(
    current_user: models.User = Depends(get_current_user),
) -> dict[str, list[dict[str, str]]]:
    root = ensure_image_store()
    files = []
    for path in sorted(root.glob("*.qcow2")) + sorted(root.glob("*.qcow")):
        files.append({"filename": path.name, "path": str(path)})
    return {"files": files}


@router.get("/library")
def list_image_library(
    current_user: models.User = Depends(get_current_user),
) -> dict[str, list[dict[str, object]]]:
    manifest = load_manifest()
    return {"images": manifest.get("images", [])}


@router.post("/library/{image_id}")
def update_image_library(
    image_id: str,
    payload: dict,
    current_user: models.User = Depends(get_current_user),
) -> dict[str, object]:
    """Update an image's metadata (device_id, version, notes, is_default, etc.)."""
    manifest = load_manifest()

    # Build updates from payload
    updates = {}
    if "device_id" in payload:
        updates["device_id"] = payload["device_id"]
    if "version" in payload:
        updates["version"] = payload["version"]
    if "notes" in payload:
        updates["notes"] = payload["notes"]
    if "is_default" in payload:
        updates["is_default"] = payload["is_default"]
    if "compatible_devices" in payload:
        updates["compatible_devices"] = payload["compatible_devices"]

    updated = update_image_entry(manifest, image_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Image not found")

    save_manifest(manifest)
    return {"image": updated}


@router.post("/library/{image_id}/assign")
def assign_image_to_device(
    image_id: str,
    payload: dict,
    current_user: models.User = Depends(get_current_user),
) -> dict[str, object]:
    """Assign an image to a device type.

    Body: { "device_id": "eos", "is_default": true }
    """
    manifest = load_manifest()

    device_id = payload.get("device_id")
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")

    is_default = payload.get("is_default", False)

    updates = {
        "device_id": device_id,
        "is_default": is_default,
    }

    # Add to compatible_devices if not already there
    for item in manifest.get("images", []):
        if item.get("id") == image_id:
            compatible = item.get("compatible_devices", [])
            if device_id not in compatible:
                compatible.append(device_id)
            updates["compatible_devices"] = compatible
            break

    updated = update_image_entry(manifest, image_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Image not found")

    save_manifest(manifest)
    return {"image": updated}


@router.post("/library/{image_id}/unassign")
def unassign_image_from_device(
    image_id: str,
    current_user: models.User = Depends(get_current_user),
) -> dict[str, object]:
    """Unassign an image from its current device type."""
    manifest = load_manifest()

    updates = {
        "device_id": None,
        "is_default": False,
    }

    updated = update_image_entry(manifest, image_id, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Image not found")

    save_manifest(manifest)
    return {"image": updated}


@router.delete("/library/{image_id}")
def delete_image(
    image_id: str,
    current_user: models.User = Depends(get_current_user),
) -> dict[str, str]:
    """Delete an image from the library.

    For QCOW2 images, also deletes the file from disk.
    For Docker images, only removes from manifest (does not remove from Docker).
    """
    manifest = load_manifest()

    # Find the image first to get its details
    image = find_image_by_id(manifest, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # If it's a QCOW2 image, delete the file from disk
    if image.get("kind") == "qcow2":
        file_path = Path(image.get("reference", ""))
        if file_path.exists():
            try:
                file_path.unlink()
            except OSError as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to delete file: {e}"
                )

    # Remove from manifest
    deleted = delete_image_entry(manifest, image_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Image not found")

    save_manifest(manifest)
    return {"message": f"Image '{image_id}' deleted successfully"}


@router.get("/devices/{device_id}/images")
def get_images_for_device(
    device_id: str,
    current_user: models.User = Depends(get_current_user),
) -> dict[str, list[dict]]:
    """Get all images assigned to or compatible with a device type."""
    manifest = load_manifest()
    images = []

    # Normalize device_id for matching
    normalized = device_id.lower()
    if normalized in ("ceos", "arista_ceos", "arista_eos"):
        normalized = "eos"

    for item in manifest.get("images", []):
        item_device = (item.get("device_id") or "").lower()
        if item_device in ("ceos", "arista_ceos", "arista_eos"):
            item_device = "eos"

        # Check if assigned to this device
        if item_device == normalized:
            images.append(item)
            continue

        # Check if in compatible_devices list
        compatible = [d.lower() for d in item.get("compatible_devices", [])]
        if normalized in compatible:
            images.append(item)

    return {"images": images}
