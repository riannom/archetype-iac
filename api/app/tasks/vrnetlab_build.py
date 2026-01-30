"""Background job for building vrnetlab Docker images from qcow2 files.

This module provides functionality to:
1. Copy a qcow2 image to the appropriate vrnetlab build directory
2. Run `make docker-image` to build the vrnetlab Docker image
3. Update the image manifest with the new Docker image reference
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from app.config import settings
from app.image_store import (
    create_image_entry,
    find_image_by_id,
    load_manifest,
    save_manifest,
)

logger = logging.getLogger(__name__)


def _get_vrnetlab_path() -> str:
    """Get vrnetlab path from environment or settings."""
    return os.environ.get("VRNETLAB_PATH", settings.vrnetlab_path)


def build_vrnetlab_image(
    qcow2_path: str,
    device_id: str,
    vrnetlab_subdir: str,
    version: str | None = None,
    qcow2_image_id: str | None = None,
) -> dict:
    """Build a vrnetlab Docker image from a qcow2 file.

    This function is designed to run as an RQ background job. It:
    1. Copies the qcow2 file to the vrnetlab build directory
    2. Runs `make docker-image` to build the Docker image
    3. Updates the manifest with the new Docker image reference
    4. Cleans up the copied qcow2 file

    Args:
        qcow2_path: Absolute path to the qcow2 file
        device_id: Device type ID (e.g., 'c8000v')
        vrnetlab_subdir: Subdirectory in vrnetlab (e.g., 'cisco/c8000v')
        version: Optional version string (extracted from filename if not provided)
        qcow2_image_id: Optional ID of the qcow2 image in the manifest to link

    Returns:
        Dict with build result:
        - success: bool
        - docker_image: str (the built image reference)
        - device_id: str
        - error: str (if failed)
    """
    qcow2_file = Path(qcow2_path)
    dest_path: Path | None = None

    logger.info(f"Starting vrnetlab build for {qcow2_file.name}")
    logger.info(f"  Device ID: {device_id}")
    logger.info(f"  vrnetlab subdir: {vrnetlab_subdir}")
    logger.info(f"  Version: {version}")

    if not qcow2_file.exists():
        error_msg = f"qcow2 file not found: {qcow2_path}"
        logger.error(error_msg)
        return {"success": False, "error": error_msg, "device_id": device_id}

    vrnetlab_dir = Path(_get_vrnetlab_path()) / vrnetlab_subdir
    if not vrnetlab_dir.exists():
        error_msg = f"vrnetlab directory not found: {vrnetlab_dir}"
        logger.error(error_msg)
        return {"success": False, "error": error_msg, "device_id": device_id}

    try:
        # Copy qcow2 to vrnetlab build directory
        logger.info(f"Copying {qcow2_file.name} to {vrnetlab_dir}")
        dest_path = vrnetlab_dir / qcow2_file.name
        shutil.copy2(qcow2_file, dest_path)
        logger.info(f"Copied qcow2 to {dest_path}")

        # Run make docker-image
        logger.info(f"Running 'make docker-image' in {vrnetlab_dir}")
        result = subprocess.run(
            ["make", "docker-image"],
            cwd=vrnetlab_dir,
            capture_output=True,
            text=True,
            timeout=3600,  # 60 minute timeout for large images
        )

        # Log output for debugging
        if result.stdout:
            logger.info(f"Build stdout:\n{result.stdout[-2000:]}")  # Last 2000 chars
        if result.stderr:
            logger.warning(f"Build stderr:\n{result.stderr[-2000:]}")

        if result.returncode != 0:
            error_msg = f"vrnetlab build failed with code {result.returncode}"
            if result.stderr:
                error_msg += f": {result.stderr[-500:]}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg, "device_id": device_id}

        # Parse the built image name from output
        # vrnetlab typically outputs lines like:
        # "naming to docker.io/vrnetlab/vr-c8000v:17.16.01a"
        # or "Successfully tagged vrnetlab/vr-c8000v:17.16.01a"
        docker_image = _parse_docker_image_from_output(
            result.stdout + result.stderr, device_id, vrnetlab_subdir, version
        )

        if not docker_image:
            error_msg = "Could not determine built Docker image name from output"
            logger.error(error_msg)
            return {"success": False, "error": error_msg, "device_id": device_id}

        logger.info(f"Built Docker image: {docker_image}")

        # Update the manifest with the new Docker image
        _update_manifest_with_docker_image(
            qcow2_path=qcow2_path,
            docker_image=docker_image,
            device_id=device_id,
            version=version,
            qcow2_image_id=qcow2_image_id,
        )

        return {
            "success": True,
            "docker_image": docker_image,
            "device_id": device_id,
        }

    except subprocess.TimeoutExpired:
        error_msg = "vrnetlab build timed out after 60 minutes"
        logger.error(error_msg)
        return {"success": False, "error": error_msg, "device_id": device_id}
    except Exception as exc:
        error_msg = f"vrnetlab build failed: {exc}"
        logger.exception(error_msg)
        return {"success": False, "error": error_msg, "device_id": device_id}
    finally:
        # Clean up copied qcow2 from vrnetlab directory
        if dest_path and dest_path.exists():
            try:
                dest_path.unlink()
                logger.info(f"Cleaned up {dest_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up {dest_path}: {e}")


def _parse_docker_image_from_output(
    output: str,
    device_id: str,
    vrnetlab_subdir: str,
    version: str | None,
) -> str | None:
    """Parse the Docker image reference from vrnetlab build output.

    Looks for patterns like:
    - "naming to docker.io/vrnetlab/vr-c8000v:17.16.01a"
    - "Successfully tagged vrnetlab/vr-c8000v:17.16.01a"
    - "=> exporting to image" followed by image name

    Returns:
        Docker image reference or None if not found
    """
    # Pattern 1: "naming to docker.io/..."
    match = re.search(r"naming to docker\.io/([\w\-/]+:[\w\.\-]+)", output)
    if match:
        return match.group(1)

    # Pattern 2: "Successfully tagged ..."
    match = re.search(r"Successfully tagged ([\w\-/]+:[\w\.\-]+)", output)
    if match:
        return match.group(1)

    # Pattern 3: Look for vrnetlab image pattern with version
    match = re.search(r"(vrnetlab/vr-[\w\-]+:[\w\.\-]+)", output)
    if match:
        return match.group(1)

    # Pattern 4: Look for any image tagged with the version
    if version:
        match = re.search(rf"([\w\-/]+:{re.escape(version)})", output, re.IGNORECASE)
        if match:
            return match.group(1)

    # Fallback: construct expected image name based on vrnetlab conventions
    # vrnetlab typically creates images like: vrnetlab/vr-<device>:<version>
    if version:
        # Extract device name from subdir (e.g., "cisco/c8000v" -> "c8000v")
        device_name = vrnetlab_subdir.split("/")[-1]
        return f"vrnetlab/vr-{device_name}:{version}"

    return None


def _update_manifest_with_docker_image(
    qcow2_path: str,
    docker_image: str,
    device_id: str,
    version: str | None,
    qcow2_image_id: str | None = None,
) -> None:
    """Add Docker image entry to manifest after successful build.

    Creates a new Docker image entry in the manifest and optionally
    links it to the source qcow2 image.
    """
    manifest = load_manifest()

    # Generate ID for the new Docker image
    new_id = f"docker:{docker_image}"

    # Check if entry already exists
    existing = find_image_by_id(manifest, new_id)
    if existing:
        logger.info(f"Docker image already in manifest: {docker_image}")
        # Update to mark as default if it isn't already
        existing["is_default"] = True
        save_manifest(manifest)
        return

    # Create new Docker entry
    new_entry = create_image_entry(
        image_id=new_id,
        kind="docker",
        reference=docker_image,
        filename=Path(qcow2_path).name,
        device_id=device_id,
        version=version,
        notes="Built automatically from qcow2 with vrnetlab",
    )
    new_entry["is_default"] = True  # Make the Docker image the default
    new_entry["built_from"] = qcow2_image_id  # Track source qcow2

    # Clear is_default from other images for same device
    for img in manifest.get("images", []):
        if img.get("device_id") == device_id and img.get("id") != new_id:
            img["is_default"] = False

    manifest["images"].append(new_entry)
    save_manifest(manifest)
    logger.info(f"Added Docker image to manifest: {docker_image}")


def get_build_status(qcow2_image_id: str) -> dict | None:
    """Check if a Docker image was built from a qcow2 image.

    Args:
        qcow2_image_id: The ID of the qcow2 image

    Returns:
        Dict with build status or None if no build found
    """
    manifest = load_manifest()

    for img in manifest.get("images", []):
        if img.get("built_from") == qcow2_image_id:
            return {
                "built": True,
                "docker_image_id": img.get("id"),
                "docker_reference": img.get("reference"),
            }

    return None
