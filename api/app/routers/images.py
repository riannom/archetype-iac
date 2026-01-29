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
        # Phase 1: Save uploaded file to temp
        total_size = len(content)
        yield _send_sse_event("progress", {
            "phase": "saving",
            "message": f"Processing uploaded file: {filename} ({_format_size(total_size)})...",
            "percent": 5,
        })

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            # Write in chunks for progress reporting
            chunk_size = 1024 * 1024  # 1MB chunks
            bytes_written = 0

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
        # Clean up temp files
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
