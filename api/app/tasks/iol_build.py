"""Background job for building IOL Docker images from IOL binary files.

IOL (IOS on Linux) binaries are raw Linux ELF executables that need to be
wrapped in a Docker container with IOUYAP (networking bridge) and boot
orchestration. This module builds that container image.

The resulting Docker image contains:
- Debian bookworm-slim base
- IOUYAP for network bridging (IOL uses Unix sockets, IOUYAP bridges to ethX)
- screen for console access
- An entrypoint.sh that dynamically generates NETMAP, iouyap.ini, and handles
  startup config injection at boot time
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.image_store import (
    create_image_entry,
    find_image_by_id,
    load_manifest,
    save_manifest,
)

logger = logging.getLogger(__name__)

# Path to Dockerfile and entrypoint.sh template
_IOL_ASSETS_DIR = Path(__file__).parent / "iol"


def build_iol_image(
    iol_path: str,
    device_id: str,
    version: str | None = None,
    iol_image_id: str | None = None,
) -> dict:
    """Build a Docker image wrapping an IOL binary.

    This function is designed to run as an RQ background job. It:
    1. Creates a temp build directory with Dockerfile + entrypoint.sh
    2. Copies the IOL binary as iol.bin
    3. Runs docker build to create the image
    4. Updates the manifest with the new Docker image reference

    Args:
        iol_path: Absolute path to the IOL binary file
        device_id: Device type ID ("iol-xe" or "iol-l2")
        version: Optional version string (extracted from filename if not provided)
        iol_image_id: Optional ID of the IOL image in the manifest to link

    Returns:
        Dict with build result:
        - success: bool
        - docker_image: str (the built image reference)
        - device_id: str
        - error: str (if failed)
    """
    iol_file = Path(iol_path)

    logger.info(f"Starting IOL Docker build for {iol_file.name}")
    logger.info(f"  Device ID: {device_id}")
    logger.info(f"  Version: {version}")

    # Build tag: archetype/iol-xe:17.12.01 or archetype/iol-l2:15.6.1T
    tag_version = version or "latest"
    image_tag = f"archetype/{device_id}:{tag_version}"

    if not iol_file.exists():
        error_msg = f"IOL binary not found: {iol_path}"
        logger.error(error_msg)
        _mark_iol_build_status(iol_image_id, "failed", error_msg)
        return {"success": False, "error": error_msg, "device_id": device_id}

    # Verify Dockerfile assets exist
    dockerfile = _IOL_ASSETS_DIR / "Dockerfile"
    entrypoint = _IOL_ASSETS_DIR / "entrypoint.sh"
    if not dockerfile.exists() or not entrypoint.exists():
        error_msg = f"IOL build assets missing in {_IOL_ASSETS_DIR}"
        logger.error(error_msg)
        _mark_iol_build_status(iol_image_id, "failed", error_msg)
        return {"success": False, "error": error_msg, "device_id": device_id}

    # Idempotency: skip build if Docker image already exists
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image_tag],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0:
            logger.info(f"Docker image {image_tag} already exists, updating manifest")
            _update_manifest_with_iol_image(iol_path, image_tag, device_id, version, iol_image_id)
            _mark_iol_build_status(iol_image_id, "complete")
            return {"success": True, "docker_image": image_tag, "device_id": device_id}
    except Exception:
        pass  # Image doesn't exist or docker not reachable, proceed with build

    # Mark build as in-progress
    _mark_iol_build_status(iol_image_id, "building")

    build_dir = None
    try:
        # Create temp build directory
        build_dir = Path(tempfile.mkdtemp(prefix="iol-build-"))
        logger.info(f"Build directory: {build_dir}")

        # Copy build assets
        shutil.copy2(dockerfile, build_dir / "Dockerfile")
        shutil.copy2(entrypoint, build_dir / "entrypoint.sh")

        # Copy IOL binary as iol.bin (Dockerfile expects this name)
        shutil.copy2(iol_file, build_dir / "iol.bin")

        # Run docker build
        logger.info(f"Running 'docker build -t {image_tag}' in {build_dir}")
        result = subprocess.run(
            ["docker", "build", "-t", image_tag, "."],
            cwd=build_dir,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
        )

        if result.stdout:
            logger.info(f"Build stdout:\n{result.stdout[-2000:]}")
        if result.stderr:
            logger.warning(f"Build stderr:\n{result.stderr[-2000:]}")

        if result.returncode != 0:
            error_msg = f"IOL Docker build failed with code {result.returncode}"
            if result.stderr:
                error_msg += f": {result.stderr[-500:]}"
            logger.error(error_msg)
            _mark_iol_build_status(iol_image_id, "failed", error_msg)
            return {"success": False, "error": error_msg, "device_id": device_id}

        logger.info(f"Built Docker image: {image_tag}")

        # Update manifest
        _update_manifest_with_iol_image(
            iol_path=iol_path,
            docker_image=image_tag,
            device_id=device_id,
            version=version,
            iol_image_id=iol_image_id,
        )
        _mark_iol_build_status(iol_image_id, "complete")

        return {
            "success": True,
            "docker_image": image_tag,
            "device_id": device_id,
        }

    except subprocess.TimeoutExpired:
        error_msg = "IOL Docker build timed out after 10 minutes"
        logger.error(error_msg)
        _mark_iol_build_status(iol_image_id, "failed", error_msg)
        return {"success": False, "error": error_msg, "device_id": device_id}
    except Exception as exc:
        error_msg = f"IOL Docker build failed: {exc}"
        logger.exception(error_msg)
        _mark_iol_build_status(iol_image_id, "failed", error_msg)
        return {"success": False, "error": error_msg, "device_id": device_id}
    finally:
        if build_dir and build_dir.exists():
            try:
                shutil.rmtree(build_dir)
                logger.info(f"Cleaned up build directory {build_dir}")
            except Exception as e:
                logger.warning(f"Failed to clean up {build_dir}: {e}")


def _mark_iol_build_status(
    iol_image_id: str | None,
    status: str,
    error_msg: str | None = None,
) -> None:
    """Update build status on the source IOL manifest entry.

    Args:
        iol_image_id: ID of the IOL image in the manifest (e.g., "iol:some-id")
        status: Build status ("building", "complete", "failed")
        error_msg: Error message on failure
    """
    if not iol_image_id:
        return
    try:
        manifest = load_manifest()
        entry = find_image_by_id(manifest, iol_image_id)
        if entry:
            entry["build_status"] = status
            if error_msg:
                entry["build_error"] = error_msg
            elif "build_error" in entry:
                del entry["build_error"]
            save_manifest(manifest)
            logger.info(f"IOL build status for {iol_image_id}: {status}")
    except Exception as e:
        logger.warning(f"Failed to update IOL build status: {e}")


def _update_manifest_with_iol_image(
    iol_path: str,
    docker_image: str,
    device_id: str,
    version: str | None,
    iol_image_id: str | None = None,
) -> None:
    """Add Docker image entry to manifest after successful IOL build.

    Creates a new Docker image entry and optionally links to the source IOL binary.
    """
    manifest = load_manifest()

    new_id = f"docker:{docker_image}"

    # Check if entry already exists
    existing = find_image_by_id(manifest, new_id)
    if existing:
        logger.info(f"Docker image already in manifest: {docker_image}")
        existing["is_default"] = True
        save_manifest(manifest)
        return

    new_entry = create_image_entry(
        image_id=new_id,
        kind="docker",
        reference=docker_image,
        filename=Path(iol_path).name,
        device_id=device_id,
        version=version,
        notes="Built automatically from IOL binary",
    )
    new_entry["is_default"] = True
    new_entry["built_from"] = iol_image_id

    # Clear is_default from other images for same device
    for img in manifest.get("images", []):
        if img.get("device_id") == device_id and img.get("id") != new_id:
            img["is_default"] = False

    manifest["images"].append(new_entry)
    save_manifest(manifest)
    logger.info(f"Added IOL Docker image to manifest: {docker_image}")


def get_iol_build_status(iol_image_id: str) -> dict | None:
    """Check if a Docker image was built from an IOL binary.

    Args:
        iol_image_id: The ID of the IOL image in the manifest

    Returns:
        Dict with build status or None if no build found
    """
    manifest = load_manifest()

    for img in manifest.get("images", []):
        if img.get("built_from") == iol_image_id:
            return {
                "built": True,
                "docker_image_id": img.get("id"),
                "docker_reference": img.get("reference"),
            }

    return None
