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
    detect_device_from_filename,
    ensure_image_store,
    load_manifest,
    qcow2_path,
    save_manifest,
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
        manifest = load_manifest()
        for image_ref in loaded_images:
            device_id, version = detect_device_from_filename(image_ref)
            manifest["images"].append(
                {
                    "id": f"docker:{image_ref}",
                    "kind": "docker",
                    "reference": image_ref,
                    "device_id": device_id,
                    "version": version,
                    "filename": filename,
                }
            )
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
    manifest = load_manifest()
    device_id, version = detect_device_from_filename(destination.name)
    manifest["images"].append(
        {
            "id": f"qcow2:{destination.name}",
            "kind": "qcow2",
            "reference": str(destination),
            "device_id": device_id,
            "version": version,
            "filename": destination.name,
        }
    )
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
    manifest = load_manifest()
    device_id = payload.get("device_id")
    version = payload.get("version")
    updated = None
    for item in manifest.get("images", []):
        if item.get("id") == image_id:
            item["device_id"] = device_id
            if version is not None:
                item["version"] = version
            updated = item
            break
    if not updated:
        raise HTTPException(status_code=404, detail="Image not found")
    save_manifest(manifest)
    return {"image": updated}
