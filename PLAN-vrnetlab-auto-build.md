# Plan: Auto-build vrnetlab Docker Images from qcow2 Uploads

## Problem Statement

When users upload qcow2 images for VM-based devices (c8000v, FTDv, cat9800, SD-WAN components), the images are stored but cannot be used by containerlab. Containerlab requires Docker images built via vrnetlab. Currently, users must manually:

1. Clone vrnetlab
2. Copy the qcow2 to the correct directory
3. Run `make docker-image`
4. Manually update the manifest

This should be automated.

## Solution Overview

When a qcow2 file is uploaded:
1. Detect the device type from filename/metadata
2. Queue a background job to build the vrnetlab Docker image
3. Update the manifest with the Docker image reference when complete
4. Notify the user of build status

## Implementation Tasks

### Task 1: Clone vrnetlab During Install

**File:** `install.sh`

Add vrnetlab clone after containerlab installation:

```bash
# Clone vrnetlab for building VM images
VRNETLAB_DIR="/opt/vrnetlab"
if [ ! -d "$VRNETLAB_DIR" ]; then
    log_info "Cloning vrnetlab for VM image building..."
    git clone --depth 1 https://github.com/hellt/vrnetlab.git $VRNETLAB_DIR
else
    log_info "Updating vrnetlab..."
    cd $VRNETLAB_DIR && git pull
fi
```

Also add to the agent Dockerfile or docker-compose to ensure vrnetlab is available in the agent container.

---

### Task 2: Create Device Type Detection for qcow2 Files

**File:** `api/app/image_store.py`

Add a function to detect device type from qcow2 filename:

```python
# Mapping of filename patterns to device IDs and vrnetlab paths
QCOW2_DEVICE_PATTERNS = {
    r"c8000v.*\.qcow2": ("c8000v", "cisco/c8000v"),
    r"cat9800.*\.qcow2": ("cat9800", "cisco/cat9800"),  # May need cat9kv
    r"ftdv.*\.qcow2|Cisco_Secure_Firewall_Threat_Defense.*\.qcow2": ("ftdv", "cisco/ftdv"),
    r"fmcv.*\.qcow2|Cisco_Secure_FW_Mgmt_Center.*\.qcow2": ("fmcv", "cisco/fmcv"),  # Check if vrnetlab supports
    r"viptela-smart.*\.qcow2": ("cat-sdwan-controller", "cisco/sdwan-components"),
    r"viptela-vmanage.*\.qcow2": ("cat-sdwan-manager", "cisco/sdwan-components"),
    r"viptela-bond.*\.qcow2": ("cat-sdwan-validator", "cisco/sdwan-components"),
    r"viptela-edge.*\.qcow2|vedge.*\.qcow2": ("cat-sdwan-vedge", "cisco/sdwan-components"),
    r"csr1000v.*\.qcow2": ("cisco_csr1000v", "cisco/csr1000v"),
    r"vios.*\.qcow2": ("cisco_iosv", "cisco/vios"),
    r"xrv9k.*\.qcow2|iosxrv9000.*\.qcow2": ("cisco_iosxr", "cisco/xrv9k"),
    r"asav.*\.qcow2": ("cisco_asav", "cisco/asav"),
    r"n9kv.*\.qcow2|nexus9.*\.qcow2": ("cisco_n9kv", "cisco/n9kv"),
}

def detect_qcow2_device_type(filename: str) -> tuple[str | None, str | None]:
    """Detect device type and vrnetlab path from qcow2 filename.

    Returns:
        Tuple of (device_id, vrnetlab_path) or (None, None) if unknown
    """
    filename_lower = filename.lower()
    for pattern, (device_id, vrnetlab_path) in QCOW2_DEVICE_PATTERNS.items():
        if re.search(pattern, filename_lower):
            return device_id, vrnetlab_path
    return None, None
```

---

### Task 3: Create vrnetlab Build Job

**File:** `api/app/tasks/vrnetlab_build.py` (new file)

```python
"""Background job for building vrnetlab Docker images from qcow2 files."""

import logging
import os
import shutil
import subprocess
from pathlib import Path

from rq import get_current_job

from app.image_store import load_manifest, save_manifest, create_image_entry
from app.config import settings

logger = logging.getLogger(__name__)

VRNETLAB_PATH = os.environ.get("VRNETLAB_PATH", "/opt/vrnetlab")


def build_vrnetlab_image(
    qcow2_path: str,
    device_id: str,
    vrnetlab_subdir: str,
    version: str | None = None,
) -> dict:
    """Build a vrnetlab Docker image from a qcow2 file.

    Args:
        qcow2_path: Path to the qcow2 file
        device_id: Device type ID (e.g., 'c8000v')
        vrnetlab_subdir: Subdirectory in vrnetlab (e.g., 'cisco/c8000v')
        version: Optional version string (extracted from filename if not provided)

    Returns:
        Dict with build result including docker_image reference
    """
    job = get_current_job()
    qcow2_file = Path(qcow2_path)

    if not qcow2_file.exists():
        raise FileNotFoundError(f"qcow2 file not found: {qcow2_path}")

    vrnetlab_dir = Path(VRNETLAB_PATH) / vrnetlab_subdir
    if not vrnetlab_dir.exists():
        raise FileNotFoundError(f"vrnetlab directory not found: {vrnetlab_dir}")

    # Copy qcow2 to vrnetlab build directory
    logger.info(f"Copying {qcow2_file.name} to {vrnetlab_dir}")
    dest_path = vrnetlab_dir / qcow2_file.name
    shutil.copy2(qcow2_file, dest_path)

    try:
        # Run make docker-image
        logger.info(f"Building vrnetlab image in {vrnetlab_dir}")
        result = subprocess.run(
            ["make", "docker-image"],
            cwd=vrnetlab_dir,
            capture_output=True,
            text=True,
            timeout=1800,  # 30 minute timeout
        )

        if result.returncode != 0:
            logger.error(f"vrnetlab build failed: {result.stderr}")
            raise RuntimeError(f"vrnetlab build failed: {result.stderr}")

        # Parse the built image name from output
        # vrnetlab outputs: "naming to docker.io/vrnetlab/cisco_c8000v:17.16.01a"
        docker_image = None
        for line in result.stdout.split('\n'):
            if 'naming to docker.io/' in line:
                # Extract image reference
                parts = line.split('naming to docker.io/')
                if len(parts) > 1:
                    docker_image = parts[1].strip().split()[0]
                    break

        if not docker_image:
            # Try to construct it from the device type and version
            # This is a fallback if we can't parse the output
            docker_image = f"vrnetlab/{vrnetlab_subdir.replace('/', '_')}:{version or 'latest'}"

        logger.info(f"Built Docker image: {docker_image}")

        # Update the manifest
        _update_manifest_with_docker_image(
            qcow2_path=qcow2_path,
            docker_image=docker_image,
            device_id=device_id,
            version=version,
        )

        return {
            "success": True,
            "docker_image": docker_image,
            "device_id": device_id,
        }

    finally:
        # Clean up copied qcow2
        if dest_path.exists():
            dest_path.unlink()


def _update_manifest_with_docker_image(
    qcow2_path: str,
    docker_image: str,
    device_id: str,
    version: str | None,
) -> None:
    """Add Docker image entry to manifest after successful build."""
    manifest = load_manifest()

    # Create new Docker entry
    new_entry = create_image_entry(
        image_id=f"docker:{docker_image}",
        kind="docker",
        reference=docker_image,
        filename=Path(qcow2_path).name,
        device_id=device_id,
        version=version,
        notes="Built automatically with vrnetlab",
    )
    new_entry["is_default"] = True  # Make the Docker image the default

    # Check if entry already exists
    existing_ids = [img["id"] for img in manifest.get("images", [])]
    if new_entry["id"] not in existing_ids:
        manifest["images"].append(new_entry)
        save_manifest(manifest)
        logger.info(f"Added Docker image to manifest: {docker_image}")
```

---

### Task 4: Create API Endpoint to Trigger Build

**File:** `api/app/routers/images.py`

Add endpoint to trigger vrnetlab build:

```python
@router.post("/images/{image_id}/build-docker")
async def build_docker_image(
    image_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Trigger vrnetlab Docker image build for a qcow2 image.

    This queues a background job to build the Docker image.
    """
    manifest = load_manifest()
    image = find_image_by_id(manifest, image_id)

    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    if image.get("kind") != "qcow2":
        raise HTTPException(status_code=400, detail="Only qcow2 images can be built")

    # Detect device type and vrnetlab path
    device_id, vrnetlab_path = detect_qcow2_device_type(image.get("filename", ""))
    if not vrnetlab_path:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot determine vrnetlab build path for this image"
        )

    # Queue the build job
    from app.tasks.vrnetlab_build import build_vrnetlab_image
    job = queue.enqueue(
        build_vrnetlab_image,
        qcow2_path=image.get("reference"),
        device_id=device_id,
        vrnetlab_subdir=vrnetlab_path,
        version=image.get("version"),
        job_timeout=1800,  # 30 minutes
    )

    return {
        "job_id": job.id,
        "status": "queued",
        "message": f"Building Docker image for {device_id}",
    }
```

---

### Task 5: Auto-trigger Build on qcow2 Upload

**File:** `api/app/routers/images.py`

Modify the existing upload endpoint to auto-trigger vrnetlab build:

```python
# In the upload handler, after saving qcow2:
if kind == "qcow2":
    device_id, vrnetlab_path = detect_qcow2_device_type(filename)
    if vrnetlab_path:
        # Auto-queue vrnetlab build
        from app.tasks.vrnetlab_build import build_vrnetlab_image
        job = queue.enqueue(
            build_vrnetlab_image,
            qcow2_path=str(file_path),
            device_id=device_id,
            vrnetlab_subdir=vrnetlab_path,
            version=detected_version,
            job_timeout=1800,
        )
        logger.info(f"Queued vrnetlab build job {job.id} for {filename}")
```

---

### Task 6: Update find_image_reference to Handle Build Status

**File:** `api/app/image_store.py`

The current `find_image_reference()` only looks for Docker images. We should keep this behavior since Docker images are what containerlab needs. The vrnetlab build will add the Docker entry when complete.

No changes needed here - the auto-build will populate Docker entries automatically.

---

### Task 7: Add vrnetlab to Docker Compose / Agent Container

**File:** `docker-compose.gui.yml`

The worker container needs access to:
1. vrnetlab repository
2. Docker socket (to build images)

Add volume mount for vrnetlab:

```yaml
worker:
  # ... existing config ...
  volumes:
    - /opt/vrnetlab:/opt/vrnetlab:ro  # vrnetlab for building images
    - /var/run/docker.sock:/var/run/docker.sock  # Already there
  environment:
    - VRNETLAB_PATH=/opt/vrnetlab
```

---

### Task 8: Add Build Status to UI (Optional Enhancement)

**Files:**
- `web/src/pages/CatalogPage.tsx`
- `api/app/routers/images.py`

Add UI indicators for:
- qcow2 images that are building
- Build success/failure status
- "Build Docker Image" button for manual trigger

This is optional for initial implementation.

---

## File Changes Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `install.sh` | Modify | Clone vrnetlab during install |
| `api/app/image_store.py` | Modify | Add `detect_qcow2_device_type()` and `QCOW2_DEVICE_PATTERNS` |
| `api/app/tasks/vrnetlab_build.py` | New | Background job for vrnetlab builds |
| `api/app/routers/images.py` | Modify | Add build endpoint, auto-trigger on upload |
| `docker-compose.gui.yml` | Modify | Mount vrnetlab volume |

---

## Testing Plan

1. **Unit Tests:**
   - Test `detect_qcow2_device_type()` with various filenames
   - Test manifest update logic

2. **Integration Tests:**
   - Upload a qcow2 file and verify build job is queued
   - Verify Docker image is created and manifest is updated
   - Verify `find_image_reference()` returns the Docker image

3. **Manual Testing:**
   - Upload c8000v qcow2 via UI
   - Wait for build to complete
   - Create a lab with c8000v node
   - Verify deployment succeeds

---

## Rollback Plan

If issues arise:
1. Remove auto-trigger from upload endpoint
2. Keep manual build endpoint as fallback
3. Users can still manually build vrnetlab images

---

## Implementation Order

1. Task 1: Clone vrnetlab in install script
2. Task 7: Add vrnetlab volume mount to docker-compose
3. Task 2: Add device detection function
4. Task 3: Create vrnetlab build job
5. Task 4: Add manual build endpoint
6. Task 5: Add auto-trigger on upload
7. Task 8: (Optional) UI enhancements

---

## Notes

- vrnetlab builds can take 2-10 minutes depending on the device type
- Some devices (c8000v) have an "install" phase that boots the VM during build
- The worker needs Docker socket access to build images
- Consider disk space - vrnetlab images can be large (2-6GB each)
- SD-WAN components use a shared vrnetlab directory (`cisco/sdwan-components`)
