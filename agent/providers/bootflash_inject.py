"""Inject startup-config into a VM overlay disk's bootflash partition.

Uses qemu-nbd to mount the qcow2 overlay, writes the config file, then
cleanly disconnects.  Failures are non-fatal — the VM simply boots without
a startup-config (same as before this feature existed).

Safety:
  - A process-wide lock serialises NBD access (only one /dev/nbd0 at a time).
  - LVM global_filter in /etc/lvm/lvm.conf (set by install.sh) prevents LVM
    from auto-activating logical volumes inside the connected disk image.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Process-wide lock — only one NBD mount at a time.
_nbd_lock = threading.Lock()

_NBD_DEVICE = "/dev/nbd0"
_LOCK_TIMEOUT = 30  # seconds
_PARTITION_WAIT = 5  # seconds to wait for partition devices


def inject_startup_config(
    overlay_path: Path,
    config_content: str,
    *,
    partition: int = 0,
    fs_type: str = "ext2",
    config_path: str = "/startup-config",
) -> bool:
    """Write *config_content* into the bootflash partition of *overlay_path*.

    Args:
        overlay_path: Path to the qcow2 overlay disk image.
        config_content: Startup-config text to write.
        partition: Partition number (1-based).  0 means auto-detect.
        fs_type: Expected filesystem type for the target partition.
        config_path: Absolute path within the mounted filesystem.

    Returns:
        True on success, False on any failure (logged as warning).
    """
    if not overlay_path.exists():
        logger.warning("Overlay image does not exist: %s", overlay_path)
        return False

    if not config_content or not config_content.strip():
        logger.debug("Empty config content, skipping injection")
        return False

    acquired = _nbd_lock.acquire(timeout=_LOCK_TIMEOUT)
    if not acquired:
        logger.warning("Could not acquire NBD lock within %ds", _LOCK_TIMEOUT)
        return False

    mount_dir: str | None = None
    nbd_connected = False
    mounted = False

    try:
        # Load nbd module (idempotent if already loaded)
        _run(["modprobe", "nbd", "max_part=16"])

        # Connect overlay to NBD device
        _run(["qemu-nbd", "-c", _NBD_DEVICE, str(overlay_path)])
        nbd_connected = True

        # Wait for partition devices to appear
        part_dev = _resolve_partition(partition, fs_type)
        if not part_dev:
            logger.warning(
                "No suitable bootflash partition found in %s", overlay_path
            )
            return False

        # Mount the partition
        mount_dir = tempfile.mkdtemp(prefix="bootflash_")
        _run(["mount", "-t", fs_type, part_dev, mount_dir])
        mounted = True

        # Write startup-config
        dest = Path(mount_dir) / config_path.lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(config_content)

        # Flush to disk
        _run(["sync"])

        logger.info(
            "Wrote %d bytes to %s on %s",
            len(config_content),
            config_path,
            part_dev,
        )
        return True

    except Exception:
        logger.warning(
            "Config injection failed for %s", overlay_path, exc_info=True
        )
        return False

    finally:
        if mounted and mount_dir:
            try:
                _run(["umount", mount_dir])
            except Exception:
                logger.warning("Failed to unmount %s", mount_dir, exc_info=True)

        if nbd_connected:
            try:
                _run(["qemu-nbd", "-d", _NBD_DEVICE])
            except Exception:
                logger.warning("Failed to disconnect %s", _NBD_DEVICE, exc_info=True)

        if mount_dir:
            try:
                Path(mount_dir).rmdir()
            except OSError:
                pass

        _nbd_lock.release()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a command, raising on non-zero exit."""
    return subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        timeout=timeout,
    )


def _resolve_partition(partition: int, fs_type: str) -> str | None:
    """Return the /dev/nbd0pN device path for the target partition.

    If *partition* > 0, uses that directly (after waiting for it to appear).
    If *partition* == 0, auto-detects by scanning blkid output.
    """
    # Wait for kernel to create partition device nodes
    _wait_for_partitions()

    if partition > 0:
        dev = f"{_NBD_DEVICE}p{partition}"
        if Path(dev).exists():
            return dev
        logger.warning("Partition device %s does not exist", dev)
        return None

    return _find_bootflash_partition(fs_type)


def _wait_for_partitions() -> None:
    """Poll until at least one /dev/nbd0p* device appears."""
    deadline = time.monotonic() + _PARTITION_WAIT
    while time.monotonic() < deadline:
        # Trigger partition re-read
        try:
            _run(["partprobe", _NBD_DEVICE])
        except Exception:
            pass
        parts = list(Path("/dev").glob("nbd0p*"))
        if parts:
            return
        time.sleep(0.3)
    logger.debug("No partition devices appeared within %ds", _PARTITION_WAIT)


def _find_bootflash_partition(fs_type: str) -> str | None:
    """Auto-detect bootflash partition using blkid.

    Skips LVM physical volumes to avoid triggering LVM activation.
    """
    parts = sorted(Path("/dev").glob("nbd0p*"))
    if not parts:
        return None

    for part in parts:
        dev = str(part)
        try:
            result = _run(["blkid", "-o", "export", dev])
            info = _parse_blkid(result.stdout.decode())
        except Exception:
            continue

        # Skip LVM physical volumes
        if info.get("TYPE") == "LVM2_member":
            logger.debug("Skipping LVM partition %s", dev)
            continue

        if info.get("TYPE", "").lower() == fs_type.lower():
            logger.debug("Auto-detected bootflash partition: %s (%s)", dev, fs_type)
            return dev

    return None


def _parse_blkid(output: str) -> dict[str, str]:
    """Parse blkid -o export output into a dict."""
    result: dict[str, str] = {}
    for line in output.strip().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result
