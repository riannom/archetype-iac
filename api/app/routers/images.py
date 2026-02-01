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
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import db, models
from app.auth import get_current_user
from app.config import settings
from app.image_store import (
    create_image_entry,
    delete_image_entry,
    detect_device_from_filename,
    detect_qcow2_device_type,
    ensure_image_store,
    find_image_by_id,
    load_manifest,
    qcow2_path,
    save_manifest,
    update_image_entry,
)
from app.jobs import queue

router = APIRouter(prefix="/images", tags=["images"])

# Track upload progress for polling
_upload_progress: dict[str, dict] = {}
_upload_lock = threading.Lock()


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


def _update_progress(upload_id: str, phase: str, message: str, percent: int, **kwargs):
    """Update progress for an upload."""
    with _upload_lock:
        _upload_progress[upload_id] = {
            "phase": phase,
            "message": message,
            "percent": percent,
            "timestamp": time.time(),
            **kwargs
        }


def _get_progress(upload_id: str) -> dict | None:
    """Get progress for an upload."""
    with _upload_lock:
        return _upload_progress.get(upload_id)


def _clear_progress(upload_id: str):
    """Clear progress for an upload."""
    with _upload_lock:
        _upload_progress.pop(upload_id, None)


@router.get("/load/{upload_id}/progress")
def get_upload_progress(
    upload_id: str,
    current_user: models.User = Depends(get_current_user),
):
    """Poll for upload progress."""
    progress = _get_progress(upload_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    return progress


@router.post("/load")
def load_image(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
    stream: bool = Query(default=False, description="Stream progress updates via SSE"),
    background: bool = Query(default=False, description="Run in background with polling"),
):
    """Load a Docker image from a tar archive.

    If background=true, returns immediately with an upload_id for polling progress.
    If stream=true, returns Server-Sent Events with progress updates.
    Otherwise returns a JSON response when complete.
    """
    if background:
        # Background mode with polling
        import uuid
        upload_id = str(uuid.uuid4())[:8]
        filename = file.filename or "image.tar"
        content = file.file.read()
        file.file.close()

        _update_progress(upload_id, "starting", "Upload received, starting import...", 5)

        # Start background thread
        thread = threading.Thread(
            target=_load_image_background,
            args=(upload_id, filename, content),
            daemon=True
        )
        thread.start()

        return {"upload_id": upload_id, "status": "started"}

    if stream:
        # Read file content BEFORE returning StreamingResponse
        # because UploadFile gets closed after endpoint returns
        filename = file.filename or "image.tar"
        content = file.file.read()
        file.file.close()

        return StreamingResponse(
            _load_image_streaming(filename, content),
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


def _write_content_to_tempfile(content: bytes, suffix: str) -> tuple[str, int]:
    """Write content to a temporary file and return (path, size).

    This is a blocking operation meant to run in asyncio.to_thread().
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        tmp_file.write(content)
        temp_path = tmp_file.name
    file_size = os.path.getsize(temp_path)
    return temp_path, file_size


def _decompress_xz_file(source_path: str) -> tuple[str, int]:
    """Decompress an XZ file and return (decompressed_path, size).

    This is a blocking operation meant to run in asyncio.to_thread().
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".tar") as tmp_tar:
        with lzma.open(source_path, "rb") as source:
            shutil.copyfileobj(source, tmp_tar)
        decompressed_path = tmp_tar.name
    decompressed_size = os.path.getsize(decompressed_path)
    return decompressed_path, decompressed_size


def _cleanup_temp_files(*paths: str) -> None:
    """Clean up temporary files.

    This is a blocking operation meant to run in asyncio.to_thread().
    """
    for path in paths:
        if path and os.path.exists(path):
            os.unlink(path)


def _load_image_background(upload_id: str, filename: str, content: bytes):
    """Process image upload in background thread with progress updates."""
    print(f"[UPLOAD {upload_id}] Starting background processing for {filename}")
    suffixes = Path(filename).suffixes
    suffix = "".join(suffixes) if suffixes else ".tar"
    temp_path = ""
    load_path = ""
    decompressed_path = ""

    try:
        # Phase 1: Save to temp file
        print(f"[UPLOAD {upload_id}] Phase 1: Saving file")
        _update_progress(upload_id, "saving", f"Saving {filename}...", 10)

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            tmp_file.write(content)
            temp_path = tmp_file.name

        load_path = temp_path
        file_size = os.path.getsize(temp_path)
        print(f"[UPLOAD {upload_id}] File saved: {file_size} bytes, checking if decompression needed")
        _update_progress(upload_id, "saved", f"File saved ({_format_size(file_size)})", 30)

        # Phase 2: Decompress if needed
        print(f"[UPLOAD {upload_id}] Filename: {filename}, checking for .xz extension")
        if filename.lower().endswith((".tar.xz", ".txz", ".xz")):
            print(f"[UPLOAD {upload_id}] Phase 2: Decompressing XZ archive")
            _update_progress(upload_id, "decompressing", "Decompressing XZ archive...", 35)
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".tar") as tmp_tar:
                    with lzma.open(temp_path, "rb") as source:
                        shutil.copyfileobj(source, tmp_tar)
                    decompressed_path = tmp_tar.name
                load_path = decompressed_path
                decompressed_size = os.path.getsize(decompressed_path)
                print(f"[UPLOAD {upload_id}] Decompression complete: {decompressed_size} bytes")
                _update_progress(upload_id, "decompressed", "Decompression complete", 50)
            except lzma.LZMAError as exc:
                print(f"[UPLOAD {upload_id}] Decompression failed: {exc}")
                _update_progress(upload_id, "error", f"Decompression failed: {exc}", 0, error=True)
                return
        else:
            print(f"[UPLOAD {upload_id}] No decompression needed")

        # Phase 3: Detect format
        print(f"[UPLOAD {upload_id}] Phase 3: Detecting format of {load_path}")
        _update_progress(upload_id, "detecting", "Detecting image format...", 55)
        is_docker_image = _is_docker_image_tar(load_path)
        print(f"[UPLOAD {upload_id}] Is docker image: {is_docker_image}")
        loaded_images = []

        # Phase 4: Import
        if is_docker_image:
            print(f"[UPLOAD {upload_id}] Phase 4: Running docker load")
            _update_progress(upload_id, "loading", "Running docker load...", 60)
            try:
                result = subprocess.run(
                    ["docker", "load", "-i", load_path],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=600,  # 10 minute timeout
                )
                print(f"[UPLOAD {upload_id}] docker load returned: {result.returncode}")
            except subprocess.TimeoutExpired:
                print(f"[UPLOAD {upload_id}] docker load timed out after 600 seconds")
                _update_progress(upload_id, "error", "docker load timed out", 0, error=True)
                return
            output = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0:
                _update_progress(upload_id, "error", output.strip() or "docker load failed", 0, error=True)
                return
            for line in output.splitlines():
                if "Loaded image:" in line:
                    loaded_images.append(line.split("Loaded image:", 1)[-1].strip())
                elif "Loaded image ID:" in line:
                    loaded_images.append(line.split("Loaded image ID:", 1)[-1].strip())
        else:
            base_name = Path(filename).stem
            for ext in [".tar", ".gz", ".xz"]:
                if base_name.lower().endswith(ext):
                    base_name = base_name[:-len(ext)]
            image_name = base_name.lower().replace(" ", "-").replace("_", "-")
            image_tag = f"{image_name}:imported"

            print(f"[UPLOAD {upload_id}] Phase 4: Importing as {image_tag}", flush=True)
            _update_progress(upload_id, "importing", f"Importing as {image_tag}...", 60)

            try:
                # Use file-based output capture to avoid pipe deadlocks in daemon threads
                import sys
                print(f"[UPLOAD {upload_id}] Starting docker import subprocess", flush=True)
                sys.stdout.flush()
                sys.stderr.flush()

                # Create temp files for output capture (avoids pipe blocking)
                with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.out') as stdout_file:
                    with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.err') as stderr_file:
                        stdout_path = stdout_file.name
                        stderr_path = stderr_file.name

                try:
                    with open(stdout_path, 'w') as stdout_f, open(stderr_path, 'w') as stderr_f:
                        print(f"[UPLOAD {upload_id}] Running: docker import {load_path} {image_tag}", flush=True)
                        result = subprocess.run(
                            ["docker", "import", load_path, image_tag],
                            stdout=stdout_f,
                            stderr=stderr_f,
                            timeout=600,
                        )
                        result_returncode = result.returncode

                    # Read output from files
                    with open(stdout_path, 'r') as f:
                        stdout_content = f.read()
                    with open(stderr_path, 'r') as f:
                        stderr_content = f.read()
                    output = stdout_content + stderr_content
                    print(f"[UPLOAD {upload_id}] docker import returned: {result_returncode}", flush=True)
                finally:
                    # Clean up temp output files
                    for p in [stdout_path, stderr_path]:
                        if os.path.exists(p):
                            os.unlink(p)

            except subprocess.TimeoutExpired:
                print(f"[UPLOAD {upload_id}] docker import timed out after 600 seconds", flush=True)
                _update_progress(upload_id, "error", "docker import timed out", 0, error=True)
                return
            except Exception as e:
                import traceback
                print(f"[UPLOAD {upload_id}] docker import exception: {e}", flush=True)
                traceback.print_exc()
                _update_progress(upload_id, "error", f"docker import failed: {e}", 0, error=True)
                return
            if result_returncode != 0:
                print(f"[UPLOAD {upload_id}] docker import failed: {output}", flush=True)
                _update_progress(upload_id, "error", output.strip() or "docker import failed", 0, error=True)
                return
            loaded_images.append(image_tag)
            output = f"Imported as {image_tag}"
            print(f"[UPLOAD {upload_id}] Import successful: {image_tag}")

        if not loaded_images:
            print(f"[UPLOAD {upload_id}] No images detected")
            _update_progress(upload_id, "error", "No images detected", 0, error=True)
            return

        # Phase 5: Update manifest
        print(f"[UPLOAD {upload_id}] Phase 5: Updating manifest")
        _update_progress(upload_id, "finalizing", "Updating image library...", 95)

        manifest = load_manifest()
        for image_ref in loaded_images:
            potential_id = f"docker:{image_ref}"
            if find_image_by_id(manifest, potential_id):
                print(f"[UPLOAD {upload_id}] Image already exists: {image_ref}")
                _update_progress(upload_id, "error", f"Image {image_ref} already exists", 0, error=True)
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

        print(f"[UPLOAD {upload_id}] Complete! Setting progress to 100%")
        _update_progress(upload_id, "complete", output or "Image loaded successfully", 100,
                        images=loaded_images, complete=True)
        print(f"[UPLOAD {upload_id}] Progress updated to complete")

    except Exception as e:
        import traceback
        print(f"[UPLOAD {upload_id}] Exception: {e}")
        traceback.print_exc()
        _update_progress(upload_id, "error", str(e), 0, error=True)
    finally:
        if decompressed_path and os.path.exists(decompressed_path):
            os.unlink(decompressed_path)
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


async def _load_image_streaming(filename: str, content: bytes) -> AsyncGenerator[str, None]:
    """Stream image loading progress via Server-Sent Events.

    Args:
        filename: Original filename of the uploaded file
        content: File content as bytes (already read from UploadFile)
    """
    suffixes = Path(filename).suffixes
    suffix = "".join(suffixes) if suffixes else ".tar"
    temp_path = ""
    load_path = ""
    decompressed_path = ""

    try:
        # Phase 1: Save uploaded file to temp (run in thread to avoid blocking event loop)
        total_size = len(content)
        yield _send_sse_event("progress", {
            "phase": "saving",
            "message": f"Processing uploaded file: {filename} ({_format_size(total_size)})...",
            "percent": 5,
        })

        # Write content to temp file in a thread to avoid blocking
        temp_path, file_size = await asyncio.to_thread(
            _write_content_to_tempfile, content, suffix
        )
        load_path = temp_path

        yield _send_sse_event("progress", {
            "phase": "saved",
            "message": f"File saved ({_format_size(file_size)}). Checking format...",
            "percent": 30,
        })

        # Phase 2: Decompress if needed (run in thread to avoid blocking event loop)
        if filename.lower().endswith((".tar.xz", ".txz", ".xz")):
            yield _send_sse_event("progress", {
                "phase": "decompressing",
                "message": "Decompressing XZ archive (this may take a while for large files)...",
                "percent": 35,
            })

            try:
                # Decompress in a thread to avoid blocking the event loop
                decompressed_path, decompressed_size = await asyncio.to_thread(
                    _decompress_xz_file, temp_path
                )
                load_path = decompressed_path

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

        # Phase 3: Detect format (run in thread to avoid blocking event loop)
        yield _send_sse_event("progress", {
            "phase": "detecting",
            "message": "Detecting image format...",
            "percent": 55,
        })

        is_docker_image = await asyncio.to_thread(_is_docker_image_tar, load_path)
        loaded_images = []
        output = ""

        # Phase 4: Load into Docker
        if is_docker_image:
            yield _send_sse_event("progress", {
                "phase": "loading",
                "message": "Docker image detected. Running 'docker load' (this may take several minutes)...",
                "percent": 60,
            })

            # Run docker load with async subprocess for progress tracking
            proc = await asyncio.create_subprocess_exec(
                "docker", "load", "-i", load_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            output_lines = []
            layer_count = 0

            # Read output line by line asynchronously
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode().strip()
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

            await proc.wait()
            output = "\n".join(output_lines)

            if proc.returncode != 0:
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
            await asyncio.sleep(0)  # Yield to event loop

            print(f"[SSE] Starting docker import for {image_tag}")

            # Use async subprocess to avoid blocking the event loop
            proc = await asyncio.create_subprocess_exec(
                "docker", "import", load_path, image_tag,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await proc.communicate()
            output = (stdout_bytes.decode() if stdout_bytes else "") + (stderr_bytes.decode() if stderr_bytes else "")

            print(f"[SSE] Docker import completed, returncode={proc.returncode}")

            if proc.returncode != 0:
                print(f"[SSE] Docker import failed: {output}")
                yield _send_sse_event("error", {
                    "message": output.strip() or "docker import failed",
                })
                return

            image_id = output.strip().split(":")[-1][:12] if output.strip() else ""
            loaded_images.append(image_tag)
            output = f"Imported filesystem as {image_tag} (ID: {image_id})"

            print(f"[SSE] Import successful: {image_tag}, sending progress event")
            yield _send_sse_event("progress", {
                "phase": "imported",
                "message": f"Import complete: {image_tag}",
                "percent": 95,
            })
            await asyncio.sleep(0)  # Yield to event loop

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

        # Final success event - add small delay to ensure proper flushing
        await asyncio.sleep(0.1)
        yield _send_sse_event("complete", {
            "message": output.strip() or "Image loaded successfully",
            "images": loaded_images,
            "percent": 100,
        })
        # Extra newline to ensure event boundary
        yield "\n"

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield _send_sse_event("error", {
            "message": str(e),
        })
        yield "\n"
    finally:
        # Clean up temp files (run in thread to avoid blocking event loop)
        await asyncio.to_thread(_cleanup_temp_files, decompressed_path, temp_path)


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
    auto_build: bool = Query(default=True, description="Auto-trigger vrnetlab Docker build"),
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
    image_id = f"qcow2:{destination.name}"
    entry = create_image_entry(
        image_id=image_id,
        kind="qcow2",
        reference=str(destination),
        filename=destination.name,
        device_id=device_id,
        version=version,
        size_bytes=file_size,
    )
    manifest["images"].append(entry)
    save_manifest(manifest)

    result = {"path": str(destination), "filename": destination.name}

    # Auto-trigger vrnetlab build if device type is recognized
    if auto_build:
        vrnetlab_device_id, vrnetlab_path = detect_qcow2_device_type(destination.name)
        if vrnetlab_path:
            from app.tasks.vrnetlab_build import build_vrnetlab_image
            job = queue.enqueue(
                build_vrnetlab_image,
                qcow2_path=str(destination),
                device_id=vrnetlab_device_id or device_id,
                vrnetlab_subdir=vrnetlab_path,
                version=version,
                qcow2_image_id=image_id,
                job_timeout=3600,  # 60 minutes
            )
            result["build_job_id"] = job.id
            result["build_status"] = "queued"
            result["message"] = f"Building Docker image for {vrnetlab_device_id or device_id}"

    return result


@router.post("/library/{image_id}/build-docker")
def trigger_docker_build(
    image_id: str,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Trigger vrnetlab Docker image build for a qcow2 image.

    This queues a background job to build the Docker image using vrnetlab.
    Only works for qcow2 images with recognized device types.

    Returns:
        Dict with job_id and status
    """
    from urllib.parse import unquote
    image_id = unquote(image_id)

    manifest = load_manifest()
    image = find_image_by_id(manifest, image_id)

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    if image.get("kind") != "qcow2":
        raise HTTPException(
            status_code=400,
            detail="Only qcow2 images can be built into Docker images"
        )

    # Get the qcow2 file path
    qcow2_file = image.get("reference")
    if not qcow2_file or not Path(qcow2_file).exists():
        raise HTTPException(
            status_code=400,
            detail="qcow2 file not found on disk"
        )

    # Detect device type and vrnetlab path
    filename = image.get("filename", Path(qcow2_file).name)
    vrnetlab_device_id, vrnetlab_path = detect_qcow2_device_type(filename)

    if not vrnetlab_path:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot determine vrnetlab build path for '{filename}'. "
                   "Device type not recognized for automatic building."
        )

    # Queue the build job
    from app.tasks.vrnetlab_build import build_vrnetlab_image
    job = queue.enqueue(
        build_vrnetlab_image,
        qcow2_path=qcow2_file,
        device_id=vrnetlab_device_id or image.get("device_id"),
        vrnetlab_subdir=vrnetlab_path,
        version=image.get("version"),
        qcow2_image_id=image_id,
        job_timeout=3600,  # 60 minutes
    )

    return {
        "job_id": job.id,
        "status": "queued",
        "message": f"Building Docker image for {vrnetlab_device_id}",
        "device_id": vrnetlab_device_id,
        "vrnetlab_path": vrnetlab_path,
    }


@router.get("/library/{image_id}/build-status")
def get_docker_build_status(
    image_id: str,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Check if a Docker image has been built from a qcow2 image.

    Returns build status and the Docker image reference if built.
    """
    from urllib.parse import unquote
    from app.tasks.vrnetlab_build import get_build_status

    image_id = unquote(image_id)

    manifest = load_manifest()
    image = find_image_by_id(manifest, image_id)

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    if image.get("kind") != "qcow2":
        raise HTTPException(
            status_code=400,
            detail="Build status only available for qcow2 images"
        )

    status = get_build_status(image_id)
    if status:
        return status

    # Check if device is buildable
    filename = image.get("filename", "")
    vrnetlab_device_id, vrnetlab_path = detect_qcow2_device_type(filename)

    return {
        "built": False,
        "buildable": vrnetlab_path is not None,
        "device_id": vrnetlab_device_id,
        "vrnetlab_path": vrnetlab_path,
    }


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


# --- Image Synchronization Endpoints ---

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


@router.post("/library/{image_id}/sync")
async def sync_image_to_hosts(
    image_id: str,
    request: SyncRequest,
    current_user: models.User = Depends(get_current_user),
    database: Session = Depends(db.get_db),
) -> dict:
    """Trigger sync of an image to specific or all hosts.

    This creates sync jobs for each target host and starts the transfer
    process in the background. Returns the created job IDs.
    """
    from urllib.parse import unquote
    image_id = unquote(image_id)

    # Verify image exists
    manifest = load_manifest()
    image = find_image_by_id(manifest, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found in library")

    # Only Docker images can be synced
    if image.get("kind") != "docker":
        raise HTTPException(status_code=400, detail="Only Docker images can be synced")

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
        # Check if already syncing
        existing_job = database.query(models.ImageSyncJob).filter(
            models.ImageSyncJob.image_id == image_id,
            models.ImageSyncJob.host_id == host.id,
            models.ImageSyncJob.status.in_(["pending", "transferring", "loading"])
        ).first()

        if existing_job:
            job_ids.append(existing_job.id)
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

    Streams the Docker image to the agent and updates job progress.
    Uses structured error handling for better error messages.
    """
    import logging
    from app.db import SessionLocal
    from app.errors import ErrorCategory, StructuredError, categorize_httpx_error

    logger = logging.getLogger(__name__)
    logger.info(f"Starting sync job {job_id}: {image_id} -> {host.name}")

    session = SessionLocal()
    try:
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
            raise ValueError("Image has no Docker reference")

        # Get image size from Docker (async subprocess to avoid blocking event loop)
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

        # Stream image to agent
        # Use docker save and pipe to agent
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.image_sync_timeout)) as client:
            # Build agent URL
            agent_url = f"http://{host.address}/images/receive"

            # Use subprocess to stream docker save output
            proc = await asyncio.create_subprocess_exec(
                "docker", "save", reference,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Read all output (for now, TODO: true streaming)
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                error_msg = stderr.decode() if stderr else "docker save failed"
                raise ValueError(error_msg)

            # Update progress
            job.bytes_transferred = len(stdout)
            job.progress_percent = 50
            session.commit()

            # Send to agent
            files = {"file": ("image.tar", stdout, "application/x-tar")}
            params = {
                "image_id": image_id,
                "reference": reference,
                "total_bytes": str(len(stdout)),
                "job_id": job_id,
            }

            try:
                response = await client.post(
                    agent_url,
                    files=files,
                    params=params,
                )
                response.raise_for_status()

                result = response.json()
                if not result.get("success"):
                    raise ValueError(result.get("error", "Agent failed to load image"))

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

    finally:
        session.close()


@router.get("/library/{image_id}/stream")
async def stream_image(
    image_id: str,
    current_user: models.User = Depends(get_current_user),
) -> StreamingResponse:
    """Stream a Docker image tar for agents to pull.

    This endpoint streams the output of `docker save` for the specified image.
    Used by agents in pull mode to fetch images from the controller.
    """
    from urllib.parse import unquote
    image_id = unquote(image_id)

    # Verify image exists
    manifest = load_manifest()
    image = find_image_by_id(manifest, image_id)
    if not image:
        raise HTTPException(status_code=404, detail="Image not found in library")

    if image.get("kind") != "docker":
        raise HTTPException(status_code=400, detail="Only Docker images can be streamed")

    reference = image.get("reference", "")
    if not reference:
        raise HTTPException(status_code=400, detail="Image has no Docker reference")

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
    current_user: models.User = Depends(get_current_user),
    database: Session = Depends(db.get_db),
) -> dict:
    """Cancel a pending or in-progress sync job.

    Only jobs in pending, transferring, or loading status can be cancelled.
    Completed or failed jobs cannot be cancelled.
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
