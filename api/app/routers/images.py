"""Image upload and management endpoints."""
from __future__ import annotations

import lzma
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app import models
from app.auth import get_current_user
from app.image_store import (
    create_image_entry,
    detect_device_from_filename,
    ensure_image_store,
    load_manifest,
    qcow2_path,
    save_manifest,
    update_image_entry,
)

router = APIRouter(prefix="/images", tags=["images"])


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


@router.post("/load")
def load_image(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
):
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
    destination = qcow2_path(Path(file.filename).name)
    try:
        with destination.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
    finally:
        file.file.close()
    # Get file size
    file_size = destination.stat().st_size if destination.exists() else None

    manifest = load_manifest()
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
