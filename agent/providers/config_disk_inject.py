"""Create a VFAT disk image containing startup config for vJunOS boot injection.

vJunOS reads initial configuration from a USB-attached VFAT disk labelled
"vmm-data" containing a ``vmm-config.tgz`` tarball with ``config/juniper.conf``
inside.  This matches the mechanism used by vrnetlab's ``make-config.sh``.

Failures are non-fatal — the VM simply boots without a startup config.
"""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def create_config_disk(
    disk_path: Path,
    config_content: str,
    *,
    volume_label: str = "vmm-data",
    disk_size_mb: int = 32,
) -> bool:
    """Create a VFAT disk image with *config_content* as ``juniper.conf``.

    The disk contains a single gzip-compressed tarball ``vmm-config.tgz``
    whose internal structure is ``config/juniper.conf``.

    Args:
        disk_path: Destination path for the raw disk image.
        config_content: Junos configuration text to embed.
        volume_label: VFAT volume label (vJunOS expects "vmm-data").
        disk_size_mb: Sparse disk size in MiB.

    Returns:
        True on success, False on any failure (logged as warning).
    """
    if not config_content or not config_content.strip():
        logger.debug("Empty config content, skipping config disk creation")
        return False

    tmp_dir: str | None = None

    try:
        tmp_dir = tempfile.mkdtemp(prefix="config_disk_")
        tmp = Path(tmp_dir)

        # Build vmm-config.tgz in memory then write to temp dir
        tgz_path = tmp / "vmm-config.tgz"
        config_bytes = config_content.encode("utf-8")
        _create_config_tarball(tgz_path, config_bytes)

        # Create sparse raw disk image
        disk_path = Path(disk_path)
        with open(disk_path, "wb") as f:
            f.truncate(disk_size_mb * 1024 * 1024)

        # Format as VFAT
        mkfs = shutil.which("mkfs.vfat")
        if not mkfs:
            logger.warning("mkfs.vfat not found; cannot create config disk")
            disk_path.unlink(missing_ok=True)
            return False

        subprocess.run(
            [mkfs, "-n", volume_label, str(disk_path)],
            check=True,
            capture_output=True,
            timeout=30,
        )

        # Copy tarball into the VFAT image (no mount required)
        if _mcopy_into_disk(disk_path, tgz_path):
            logger.info(
                "Created config disk %s (%d bytes config, label=%s)",
                disk_path,
                len(config_content),
                volume_label,
            )
            return True

        # Fallback: losetup + mount
        if _mount_copy_into_disk(disk_path, tgz_path):
            logger.info(
                "Created config disk %s via mount (%d bytes config, label=%s)",
                disk_path,
                len(config_content),
                volume_label,
            )
            return True

        logger.warning(
            "Neither mcopy nor mount succeeded; cannot populate config disk"
        )
        disk_path.unlink(missing_ok=True)
        return False

    except Exception:
        logger.warning("Config disk creation failed for %s", disk_path, exc_info=True)
        try:
            if disk_path and Path(disk_path).exists():
                Path(disk_path).unlink()
        except OSError:
            pass
        return False

    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _create_config_tarball(tgz_path: Path, config_bytes: bytes) -> None:
    """Create vmm-config.tgz with ``config/juniper.conf`` inside."""
    with tarfile.open(tgz_path, "w:gz") as tar:
        info = tarfile.TarInfo(name="config/juniper.conf")
        info.size = len(config_bytes)
        tar.addfile(info, io.BytesIO(config_bytes))


def _mcopy_into_disk(disk_path: Path, tgz_path: Path) -> bool:
    """Use mtools ``mcopy`` to copy tarball into VFAT image (no mount)."""
    mcopy = shutil.which("mcopy")
    if not mcopy:
        return False
    try:
        subprocess.run(
            [mcopy, "-i", str(disk_path), str(tgz_path), "::vmm-config.tgz"],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _mount_copy_into_disk(disk_path: Path, tgz_path: Path) -> bool:
    """Fallback: loop-mount the VFAT image and copy the tarball in."""
    losetup = shutil.which("losetup")
    mount = shutil.which("mount")
    umount = shutil.which("umount")
    if not all([losetup, mount, umount]):
        return False

    loop_dev: str | None = None
    mount_point: str | None = None
    try:
        # Attach loop device
        result = subprocess.run(
            [losetup, "--find", "--show", str(disk_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        loop_dev = result.stdout.strip()

        # Mount
        mount_point = tempfile.mkdtemp(prefix="config_disk_mnt_")
        subprocess.run(
            [mount, "-t", "vfat", loop_dev, mount_point],
            check=True,
            capture_output=True,
            timeout=10,
        )

        # Copy tarball
        shutil.copy2(str(tgz_path), Path(mount_point) / "vmm-config.tgz")
        return True

    except Exception:
        return False

    finally:
        if mount_point:
            try:
                subprocess.run(
                    [umount, mount_point],
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                pass
            shutil.rmtree(mount_point, ignore_errors=True)
        if loop_dev:
            try:
                subprocess.run(
                    [losetup, "-d", loop_dev],
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                pass


def remove_config_disk(disk_path: Path) -> bool:
    """Delete a config disk file if it exists.  Idempotent.

    Returns:
        True if the file was deleted or didn't exist, False on error.
    """
    try:
        if disk_path.exists():
            disk_path.unlink()
            logger.debug("Removed config disk %s", disk_path)
        return True
    except Exception:
        logger.warning("Failed to remove config disk %s", disk_path, exc_info=True)
        return False
