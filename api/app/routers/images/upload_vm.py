"""qcow2 and IOL upload, detection, registration, build, and diagnostics endpoints."""
from __future__ import annotations

import gzip
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app import models
from app.auth import get_current_admin, get_current_user
from app.image_store import (
    create_image_entry,
    detect_device_from_filename,
    detect_iol_device_type,
    detect_qcow2_device_type,
    find_image_by_id,
    iol_path,
    load_manifest,
    qcow2_path,
    save_manifest,
)
from app.jobs import get_queue
from app.services.resource_monitor import PressureLevel, ResourceMonitor
from app.utils.image_integrity import compute_sha256, validate_qcow2

from ._shared import (
    ImageChunkUploadCompleteResponse,
    _is_supported_qcow2_upload_filename,
    _resolved_qcow2_upload_filename,
    _chunk_upload_lock,
    _chunk_upload_sessions,
)

router = APIRouter(tags=["images"])


def _resolve_qcow2_upload_path(path: Path) -> Path:
    """Resolve the final qcow2 path by removing an optional .gz suffix."""
    resolved_name = _resolved_qcow2_upload_filename(path.name)
    if not resolved_name:
        raise HTTPException(
            status_code=400,
            detail="File must be a qcow2/qcow/img image (optionally .gz-compressed)",
        )
    if resolved_name == path.name:
        return path
    return path.with_name(resolved_name)


def _prepare_qcow2_upload_path(path: Path) -> Path:
    """Decompress a .gz qcow2 upload in place and return the resulting path."""
    resolved_path = _resolve_qcow2_upload_path(path)
    if resolved_path == path:
        return path

    if resolved_path.exists():
        path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=409,
            detail=f"Image '{resolved_path.name}' already exists on disk",
        )

    try:
        with gzip.open(path, "rb") as source, resolved_path.open("wb") as target:
            shutil.copyfileobj(source, target)
    except OSError as exc:
        resolved_path.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Invalid gzip qcow2 image: {exc}") from exc

    path.unlink(missing_ok=True)
    return resolved_path


class Qcow2DetectionResult(BaseModel):
    """Detection results returned after qcow2 upload (two-phase mode)."""

    upload_id: str
    filename: str
    detected_device_id: str | None = None
    detected_version: str | None = None
    confidence: str = "none"  # "high", "medium", "low", "none"
    size_bytes: int | None = None
    sha256: str | None = None
    suggested_metadata: dict[str, object] = Field(default_factory=dict)
    status: str = "awaiting_confirmation"


class Qcow2ConfirmRequest(BaseModel):
    """Request to confirm a staged qcow2 upload with optional overrides."""

    device_id: str | None = None
    version: str | None = None
    auto_build: bool = True
    metadata: dict[str, object] | None = None
    sidecar_yaml: str | None = Field(
        default=None,
        description="Optional VIRL2 node-definition YAML content for metadata extraction",
    )


def _parse_sidecar_metadata(yaml_content: str) -> dict[str, object]:
    """Parse a VIRL2 node-definition YAML sidecar and extract image metadata."""
    from app.iso.virl2_parser import VIRL2Parser

    parser = VIRL2Parser()
    node_def = parser._parse_node_definition(yaml_content, "<sidecar>")
    if not node_def:
        return {}

    metadata: dict[str, object] = {}
    if node_def.ram_mb:
        metadata["memory_mb"] = node_def.ram_mb
    if node_def.cpus:
        metadata["cpu_count"] = node_def.cpus
    if node_def.disk_driver:
        metadata["disk_driver"] = node_def.disk_driver
    if node_def.nic_driver:
        metadata["nic_driver"] = node_def.nic_driver
    if node_def.machine_type:
        metadata["machine_type"] = node_def.machine_type
    if node_def.efi_boot:
        metadata["efi_boot"] = node_def.efi_boot
    if node_def.efi_vars:
        metadata["efi_vars"] = node_def.efi_vars
    if node_def.boot_timeout:
        metadata["boot_timeout"] = node_def.boot_timeout
    if node_def.boot_completed_patterns:
        metadata["readiness_probe"] = "log_pattern"
        metadata["readiness_pattern"] = "|".join(node_def.boot_completed_patterns)
    if node_def.interfaces:
        metadata["max_ports"] = len(node_def.interfaces) or node_def.interface_count_default
        metadata["port_naming"] = node_def.interface_naming_pattern
    if node_def.libvirt_driver:
        metadata["libvirt_driver"] = node_def.libvirt_driver
    return metadata


def _detect_qcow2(destination: Path) -> dict[str, object]:
    """Validate a qcow2 file and return detection results without registering.

    Used in two-phase upload mode to present detection results for user
    confirmation before committing to the manifest.
    """

    destination = _prepare_qcow2_upload_path(destination)

    manifest = load_manifest()
    potential_id = f"qcow2:{destination.name}"
    if find_image_by_id(manifest, potential_id):
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=409,
            detail=f"Image '{destination.name}' already exists in the library",
        )

    file_size = destination.stat().st_size if destination.exists() else None
    valid, error_msg = validate_qcow2(destination)
    if not valid:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Invalid qcow2 image: {error_msg}")

    file_sha256 = compute_sha256(destination)
    device_id, version = detect_device_from_filename(destination.name)

    # Determine detection confidence.
    if device_id and version:
        confidence = "high"
    elif device_id:
        confidence = "medium"
    else:
        confidence = "none"

    # Build suggested metadata from vendor defaults.
    suggested: dict[str, object] = {}
    if device_id:
        try:
            from app.services.device_service import get_resolver
            resolved = get_resolver().resolve_config(device_id)
            if resolved:
                suggested["memory_mb"] = resolved.memory
                suggested["cpu"] = resolved.cpu
                suggested["disk_driver"] = resolved.disk_driver
                suggested["nic_driver"] = resolved.nic_driver
                suggested["machine_type"] = resolved.machine_type
                suggested["efi_boot"] = resolved.efi_boot
                suggested["max_ports"] = resolved.max_ports
                suggested["vendor"] = resolved.vendor
        except Exception:
            pass

    return {
        "detected_device_id": device_id,
        "detected_version": version,
        "confidence": confidence,
        "size_bytes": file_size,
        "sha256": file_sha256,
        "suggested_metadata": suggested,
    }


def _register_qcow2(
    destination: Path,
    *,
    device_id: str | None = None,
    version: str | None = None,
    sha256: str | None = None,
    size_bytes: int | None = None,
    auto_build: bool = True,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    """Register a validated qcow2 in the manifest and optionally trigger build."""
    manifest = load_manifest()
    image_id = f"qcow2:{destination.name}"

    if find_image_by_id(manifest, image_id):
        raise HTTPException(
            status_code=409,
            detail=f"Image '{destination.name}' already exists in the library",
        )

    if size_bytes is None:
        size_bytes = destination.stat().st_size if destination.exists() else None

    # Merge user metadata overrides into create_image_entry kwargs.
    extra_kwargs: dict[str, object] = {}
    if metadata:
        for key in (
            "memory_mb", "cpu_count", "disk_driver", "nic_driver",
            "machine_type", "efi_boot", "efi_vars", "max_ports",
            "port_naming", "readiness_probe", "readiness_pattern",
            "boot_timeout", "libvirt_driver",
        ):
            if key in metadata:
                extra_kwargs[key] = metadata[key]

    entry = create_image_entry(
        image_id=image_id,
        kind="qcow2",
        reference=str(destination),
        filename=destination.name,
        device_id=device_id,
        version=version,
        size_bytes=size_bytes,
        sha256=sha256,
        **extra_kwargs,
    )
    manifest["images"].append(entry)
    save_manifest(manifest)

    result: dict[str, object] = {"path": str(destination), "filename": destination.name}
    if auto_build:
        vrnetlab_device_id, vrnetlab_path = detect_qcow2_device_type(destination.name)
        if vrnetlab_path:
            from app.tasks.vrnetlab_build import build_vrnetlab_image

            job = get_queue().enqueue(
                build_vrnetlab_image,
                qcow2_path=str(destination),
                device_id=vrnetlab_device_id or device_id,
                vrnetlab_subdir=vrnetlab_path,
                version=version,
                qcow2_image_id=image_id,
                job_timeout=3600,
                result_ttl=3600,
                failure_ttl=86400,
            )
            result["build_job_id"] = job.id
            result["build_status"] = "queued"
            result["message"] = f"Building Docker image for {vrnetlab_device_id or device_id}"

    return result


def _finalize_qcow2_upload(destination: Path, *, auto_build: bool = True) -> dict[str, object]:
    """Validate, register, and optionally build from a saved qcow2 image.

    One-shot convenience that runs detection + registration in sequence.
    Used by auto_confirm=True (default) and the legacy single-file upload.
    """

    destination = _prepare_qcow2_upload_path(destination)

    manifest = load_manifest()
    potential_id = f"qcow2:{destination.name}"
    if find_image_by_id(manifest, potential_id):
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=409,
            detail=f"Image '{destination.name}' already exists in the library",
        )

    file_size = destination.stat().st_size if destination.exists() else None
    valid, error_msg = validate_qcow2(destination)
    if not valid:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Invalid qcow2 image: {error_msg}")

    file_sha256 = compute_sha256(destination)
    device_id, version = detect_device_from_filename(destination.name)

    return _register_qcow2(
        destination,
        device_id=device_id,
        version=version,
        sha256=file_sha256,
        size_bytes=file_size,
        auto_build=auto_build,
    )


@router.post("/upload/{upload_id}/confirm")
def confirm_qcow2_upload(
    upload_id: str,
    body: Qcow2ConfirmRequest,
    current_user: models.User = Depends(get_current_admin),
):
    """Confirm a staged qcow2 upload with optional overrides.

    Only valid for sessions in ``awaiting_confirmation`` status (two-phase mode).
    The user can override the detected device_id, version, and pass additional
    metadata that will be stored on the manifest image entry.
    """
    with _chunk_upload_lock:
        session = _chunk_upload_sessions.get(upload_id)
        if not session:
            raise HTTPException(status_code=404, detail="Upload session not found")
        if session["status"] != "awaiting_confirmation":
            raise HTTPException(
                status_code=400,
                detail=f"Upload is '{session['status']}', expected 'awaiting_confirmation'",
            )
        session_copy = dict(session)

    detection = session_copy.get("detection", {})
    final_path = Path(session_copy["final_path"])
    if not final_path.exists():
        with _chunk_upload_lock:
            if upload_id in _chunk_upload_sessions:
                _chunk_upload_sessions[upload_id]["status"] = "failed"
                _chunk_upload_sessions[upload_id]["error_message"] = "File no longer exists"
        raise HTTPException(status_code=410, detail="Uploaded file no longer exists")

    final_path = _prepare_qcow2_upload_path(final_path)

    # Apply overrides: user values take precedence over detection.
    device_id = body.device_id or detection.get("detected_device_id")
    version = body.version or detection.get("detected_version")

    # Merge metadata: sidecar YAML (lowest) -> vendor defaults -> explicit metadata (highest).
    merged_metadata: dict[str, object] = {}
    if body.sidecar_yaml:
        merged_metadata.update(_parse_sidecar_metadata(body.sidecar_yaml))
    if body.metadata:
        merged_metadata.update(body.metadata)

    with _chunk_upload_lock:
        if upload_id in _chunk_upload_sessions:
            _chunk_upload_sessions[upload_id]["status"] = "processing"

    try:
        result = _register_qcow2(
            final_path,
            device_id=device_id,
            version=version,
            sha256=detection.get("sha256"),
            size_bytes=detection.get("size_bytes"),
            auto_build=body.auto_build,
            metadata=merged_metadata or None,
        )
    except Exception as exc:
        with _chunk_upload_lock:
            if upload_id in _chunk_upload_sessions:
                _chunk_upload_sessions[upload_id]["status"] = "failed"
                _chunk_upload_sessions[upload_id]["error_message"] = str(exc)
        raise

    with _chunk_upload_lock:
        if upload_id in _chunk_upload_sessions:
            _chunk_upload_sessions[upload_id]["status"] = "completed"

    return ImageChunkUploadCompleteResponse(
        upload_id=upload_id,
        kind="qcow2",
        filename=session_copy["filename"],
        status="completed",
        result=result,
    )


@router.post("/qcow2")
def upload_qcow2(
    file: UploadFile | str | None = File(default=None),
    current_user: models.User = Depends(get_current_admin),
    auto_build: bool = Query(default=True, description="Auto-trigger vrnetlab Docker build"),
) -> dict[str, object]:

    if ResourceMonitor.check_disk_pressure() == PressureLevel.CRITICAL:
        raise HTTPException(status_code=507, detail="Insufficient disk space for upload")

    filename = None if file is None or isinstance(file, str) else getattr(file, "filename", None)
    if not filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    if not _is_supported_qcow2_upload_filename(filename):
        raise HTTPException(
            status_code=400,
            detail="File must be a qcow2/qcow/img image (optionally .gz-compressed)",
        )

    destination = qcow2_path(Path(filename).name)
    try:
        with destination.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
    finally:
        file.file.close()
    return _finalize_qcow2_upload(destination, auto_build=auto_build)


@router.post("/iol")
def upload_iol(
    file: UploadFile | str | None = File(default=None),
    current_user: models.User = Depends(get_current_admin),
) -> dict[str, object]:
    """Upload an IOL binary and automatically build a Docker image.

    IOL (IOS on Linux) binaries are raw Linux ELF executables. This endpoint
    saves the binary and triggers an async Docker image build that wraps it
    with IOUYAP, screen, and boot orchestration.

    Requires admin access.
    """
    if ResourceMonitor.check_disk_pressure() == PressureLevel.CRITICAL:
        raise HTTPException(status_code=507, detail="Insufficient disk space for upload")

    filename = None if file is None or isinstance(file, str) else getattr(file, "filename", None)
    if not filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    # Detect device type from filename
    device_id = detect_iol_device_type(filename)
    if not device_id:
        raise HTTPException(
            status_code=400,
            detail="Could not detect IOL device type from filename. "
                   "Expected filename containing 'l3-' (for iol-xe) or 'l2-' (for iol-l2)."
        )

    # Check for duplicate
    manifest = load_manifest()
    potential_id = f"iol:{Path(filename).name}"
    if find_image_by_id(manifest, potential_id):
        raise HTTPException(
            status_code=409,
            detail=f"IOL image '{filename}' already exists in the library"
        )

    # Save the binary
    destination = iol_path(Path(filename).name)
    try:
        with destination.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
    finally:
        file.file.close()

    file_size = destination.stat().st_size if destination.exists() else None

    # Extract version from filename
    _, version = detect_device_from_filename(destination.name)

    # Register the raw IOL binary in manifest
    image_id = f"iol:{destination.name}"
    entry = create_image_entry(
        image_id=image_id,
        kind="iol",
        reference=str(destination),
        filename=destination.name,
        device_id=device_id,
        version=version,
        size_bytes=file_size,
    )
    manifest["images"].append(entry)

    result = {"path": str(destination), "filename": destination.name, "device_id": device_id}

    # Trigger async Docker image build and persist queue metadata on the source IOL entry.
    build_result = _enqueue_iol_build_job(manifest, entry)
    save_manifest(manifest)
    result.update(build_result)

    return result


@router.post("/library/{image_id}/build-docker")
def trigger_docker_build(
    image_id: str,
    current_user: models.User = Depends(get_current_admin),
) -> dict:
    """Trigger vrnetlab Docker image build for a qcow2 image.

    This queues a background job to build the Docker image using vrnetlab.
    Only works for qcow2 images with recognized device types.
    Requires admin access.

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
    job = get_queue().enqueue(
        build_vrnetlab_image,
        qcow2_path=qcow2_file,
        device_id=vrnetlab_device_id or image.get("device_id"),
        vrnetlab_subdir=vrnetlab_path,
        version=image.get("version"),
        qcow2_image_id=image_id,
        job_timeout=3600,  # 60 minutes
        result_ttl=3600, failure_ttl=86400,
    )

    return {
        "job_id": job.id,
        "status": "queued",
        "message": f"Building Docker image for {vrnetlab_device_id}",
        "device_id": vrnetlab_device_id,
        "vrnetlab_path": vrnetlab_path,
    }


def _normalize_rq_build_status(raw_status: str | None) -> str | None:
    """Map RQ job statuses to UI-friendly build states."""
    if not raw_status:
        return None
    status = raw_status.lower()
    if status in {"queued", "deferred", "scheduled"}:
        return "queued"
    if status == "started":
        return "building"
    if status in {"failed", "stopped", "canceled"}:
        return "failed"
    if status == "finished":
        return "complete"
    return None


def _to_iso8601(value: object | None) -> str | None:
    """Serialize datetime-like values to ISO8601 strings."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if hasattr(value, "isoformat"):
        try:
            return str(value.isoformat()).replace("+00:00", "Z")  # type: ignore[attr-defined]
        except Exception:
            return str(value)
    return str(value)


def _tail_text(value: object | None, max_chars: int = 8000) -> str | None:
    """Return the last chunk of text for large log/traceback fields."""
    if value is None:
        return None
    text = str(value)
    return text[-max_chars:] if len(text) > max_chars else text


def _enqueue_iol_build_job(
    manifest: dict,
    image: dict,
    *,
    force_rebuild: bool = False,
    reject_if_active: bool = False,
) -> dict[str, object]:
    """Queue an IOL Docker build and update manifest metadata."""
    if image.get("kind") != "iol":
        raise HTTPException(status_code=400, detail="Only iol images can use IOL build jobs")

    iol_ref = image.get("reference")
    iol_file = Path(iol_ref) if isinstance(iol_ref, str) else None
    if not iol_file or not iol_file.exists():
        raise HTTPException(status_code=400, detail="IOL file not found on disk")

    device_id = image.get("device_id") or detect_iol_device_type(image.get("filename", iol_file.name))
    if not device_id:
        raise HTTPException(status_code=400, detail="Could not determine IOL device type")

    if reject_if_active and image.get("build_job_id"):
        existing_job = get_queue().fetch_job(str(image["build_job_id"]))
        if existing_job:
            normalized = _normalize_rq_build_status(existing_job.get_status(refresh=True))
            if normalized in {"queued", "building"}:
                raise HTTPException(status_code=409, detail="IOL build is already in progress")

    from app.tasks.iol_build import build_iol_image

    enqueue_kwargs = {
        "iol_path": str(iol_file),
        "device_id": device_id,
        "version": image.get("version"),
        "iol_image_id": image.get("id"),
    }
    if force_rebuild:
        enqueue_kwargs["force_rebuild"] = True

    job = get_queue().enqueue(
        build_iol_image,
        **enqueue_kwargs,
        job_timeout=600,  # 10 minutes
        result_ttl=3600, failure_ttl=86400,
    )

    image["device_id"] = device_id
    image["build_job_id"] = job.id
    image["build_status"] = "queued"
    image["build_requested_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    image.pop("build_error", None)
    image.pop("build_ignored_at", None)
    image.pop("build_ignored_by", None)

    return {
        "build_job_id": job.id,
        "build_status": "queued",
        "message": f"Building Docker image for {device_id}",
        "device_id": device_id,
    }


def _get_iol_build_status(
    image_id: str,
    image: dict,
) -> dict[str, object]:
    """Return detailed build status for an IOL source image."""
    from app.tasks.iol_build import get_iol_build_status

    built_status = get_iol_build_status(image_id)
    build_job_id = image.get("build_job_id")
    build_error = image.get("build_error")
    manifest_status = str(image.get("build_status") or "").strip().lower() or None
    status = "complete" if built_status else (manifest_status or "not_started")
    rq_status = None

    if build_job_id:
        rq_job = get_queue().fetch_job(str(build_job_id))
        if rq_job:
            rq_status = rq_job.get_status(refresh=True)
            mapped = _normalize_rq_build_status(rq_status)
            # Prefer active/failed queue state over stale manifest status.
            if mapped in {"queued", "building"}:
                status = mapped
            elif mapped == "failed" and manifest_status != "ignored":
                status = mapped
            elif mapped == "complete" and status == "not_started":
                status = mapped
            if mapped == "failed" and not build_error and rq_job.exc_info:
                build_error = str(rq_job.exc_info).splitlines()[-1][-500:]

    iol_ref = image.get("reference")
    buildable = bool(iol_ref and Path(iol_ref).exists())

    return {
        "built": bool(built_status),
        "buildable": buildable,
        "status": status,
        "build_status": status,
        "build_error": build_error,
        "build_job_id": build_job_id,
        "rq_status": rq_status,
        "build_ignored_at": image.get("build_ignored_at"),
        "build_ignored_by": image.get("build_ignored_by"),
        "docker_image_id": built_status.get("docker_image_id") if built_status else None,
        "docker_reference": built_status.get("docker_reference") if built_status else None,
    }


@router.get("/library/{image_id}/build-status")
def get_docker_build_status(
    image_id: str,
    current_user: models.User = Depends(get_current_user),
) -> dict:
    """Check Docker build status for qcow2 or IOL source images.

    For qcow2 sources, returns whether a vrnetlab image has been built.
    For IOL sources, returns queue-backed build status and built image details.
    """
    from urllib.parse import unquote
    from app.tasks.vrnetlab_build import get_build_status

    image_id = unquote(image_id)

    manifest = load_manifest()
    image = find_image_by_id(manifest, image_id)

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    kind = image.get("kind")
    if kind == "iol":
        return _get_iol_build_status(image_id, image)
    if kind != "qcow2":
        raise HTTPException(status_code=400, detail="Build status only available for qcow2/iol images")

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


@router.get("/library/{image_id}/build-diagnostics")
def get_iol_build_diagnostics(
    image_id: str,
    current_user: models.User = Depends(get_current_user),
) -> dict[str, object]:
    """Return queue diagnostics for an IOL build job."""
    from urllib.parse import unquote

    image_id = unquote(image_id)
    manifest = load_manifest()
    image = find_image_by_id(manifest, image_id)

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    if image.get("kind") != "iol":
        raise HTTPException(status_code=400, detail="Build diagnostics are only available for iol images")

    build_status = _get_iol_build_status(image_id, image)
    build_job_id = build_status.get("build_job_id")

    queue_job: dict[str, object] | None = None
    if build_job_id:
        rq_job = get_queue().fetch_job(str(build_job_id))
        if rq_job:
            result_value = getattr(rq_job, "result", None)
            if not isinstance(result_value, (dict, list, str, int, float, bool)) and result_value is not None:
                result_value = str(result_value)
            queue_job = {
                "id": str(build_job_id),
                "status": _normalize_rq_build_status(rq_job.get_status(refresh=True)) or rq_job.get_status(),
                "created_at": _to_iso8601(getattr(rq_job, "created_at", None)),
                "enqueued_at": _to_iso8601(getattr(rq_job, "enqueued_at", None)),
                "started_at": _to_iso8601(getattr(rq_job, "started_at", None)),
                "ended_at": _to_iso8601(getattr(rq_job, "ended_at", None)),
                "last_heartbeat": _to_iso8601(getattr(rq_job, "last_heartbeat", None)),
                "result": result_value,
                "error_log": _tail_text(getattr(rq_job, "exc_info", None)),
            }

    recommended_action = None
    if build_status.get("status") == "failed":
        recommended_action = (
            "Retry the build. If it still fails with the same error, use Force rebuild "
            "or verify the IOL binary and host Docker capacity."
        )
    elif build_status.get("status") == "ignored":
        recommended_action = "This failure is currently ignored. Use Retry to re-enable active build tracking."

    return {
        "image_id": image_id,
        "filename": image.get("filename"),
        "reference": image.get("reference"),
        "built": bool(build_status.get("built")),
        "buildable": bool(build_status.get("buildable")),
        "status": build_status.get("status"),
        "build_status": build_status.get("build_status"),
        "build_error": build_status.get("build_error"),
        "build_job_id": build_status.get("build_job_id"),
        "rq_status": build_status.get("rq_status"),
        "build_ignored_at": build_status.get("build_ignored_at"),
        "build_ignored_by": build_status.get("build_ignored_by"),
        "docker_image_id": build_status.get("docker_image_id"),
        "docker_reference": build_status.get("docker_reference"),
        "queue_job": queue_job,
        "recommended_action": recommended_action,
    }


@router.post("/library/{image_id}/ignore-build-failure")
def ignore_iol_build_failure(
    image_id: str,
    current_user: models.User = Depends(get_current_admin),
) -> dict[str, object]:
    """Mark a failed IOL build as ignored so users can manage noise themselves."""
    from urllib.parse import unquote

    image_id = unquote(image_id)
    manifest = load_manifest()
    image = find_image_by_id(manifest, image_id)

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    if image.get("kind") != "iol":
        raise HTTPException(status_code=400, detail="Ignore build failure is only available for iol images")

    if image.get("build_job_id"):
        existing_job = get_queue().fetch_job(str(image["build_job_id"]))
        if existing_job:
            normalized = _normalize_rq_build_status(existing_job.get_status(refresh=True))
            if normalized in {"queued", "building"}:
                raise HTTPException(status_code=409, detail="Cannot ignore an IOL build while it is in progress")

    image["build_status"] = "ignored"
    image["build_ignored_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    image["build_ignored_by"] = getattr(current_user, "username", None) or str(current_user.id)
    save_manifest(manifest)

    return {
        "image_id": image_id,
        "build_status": "ignored",
        "message": "IOL build failure marked as ignored",
        "build_ignored_at": image["build_ignored_at"],
        "build_ignored_by": image["build_ignored_by"],
    }


@router.post("/library/{image_id}/retry-build")
def retry_iol_build(
    image_id: str,
    force_rebuild: bool = Query(default=False, description="Force rebuild even if Docker image exists"),
    current_user: models.User = Depends(get_current_admin),
) -> dict[str, object]:
    """Retry an IOL Docker build for a raw IOL source image."""
    from urllib.parse import unquote

    image_id = unquote(image_id)
    manifest = load_manifest()
    image = find_image_by_id(manifest, image_id)

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    if image.get("kind") != "iol":
        raise HTTPException(status_code=400, detail="Retry build is only available for iol images")

    result = _enqueue_iol_build_job(
        manifest,
        image,
        force_rebuild=force_rebuild,
        reject_if_active=True,
    )
    save_manifest(manifest)
    return result
