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
from typing import Any

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
    diagnostics: dict[str, Any] | None = None,
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
    diag = diagnostics if diagnostics is not None else {}
    diag.clear()
    diag["overlay_path"] = str(overlay_path)
    diag["fs_type"] = fs_type
    diag["requested_config_path"] = config_path
    diag["partition_hint"] = partition

    if not overlay_path.exists():
        diag["error"] = "overlay_missing"
        logger.warning("Overlay image does not exist: %s", overlay_path)
        return False

    if not config_content or not config_content.strip():
        diag["error"] = "empty_config"
        logger.debug("Empty config content, skipping injection")
        return False

    diag["bytes"] = len(config_content)

    acquired = _nbd_lock.acquire(timeout=_LOCK_TIMEOUT)
    if not acquired:
        diag["error"] = "nbd_lock_timeout"
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
        diag["resolved_partition"] = part_dev
        if not part_dev:
            diag["error"] = "partition_not_found"
            logger.warning(
                "No suitable bootflash partition found in %s", overlay_path
            )
            return False

        # Mount the partition
        mount_dir = tempfile.mkdtemp(prefix="bootflash_")
        _run(["mount", "-t", fs_type, part_dev, mount_dir])
        mounted = True

        # Write startup-config. For N9Kv images we mirror to both common
        # paths because some builds expect /startup-config while others
        # look under /bootflash/startup-config.
        requested = "/" + config_path.lstrip("/")
        write_targets = [requested]
        if requested == "/startup-config":
            write_targets.append("/bootflash/startup-config")
        elif requested == "/bootflash/startup-config":
            write_targets.append("/startup-config")
        diag["write_targets"] = write_targets

        written_paths: list[str] = []
        for rel_path in write_targets:
            dest = Path(mount_dir) / rel_path.lstrip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(config_content)
            written_paths.append(rel_path)
        diag["written_paths"] = written_paths

        # Flush to disk
        _run(["sync"])
        diag["sync"] = True

        logger.info(
            "Wrote %d bytes to %s on %s",
            len(config_content),
            ",".join(written_paths),
            part_dev,
        )
        diag["success"] = True
        return True

    except Exception as exc:
        diag["error"] = "exception"
        diag["exception"] = str(exc)
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

    expected = fs_type.lower()
    candidates: list[str] = []

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

        part_type = info.get("TYPE", "").lower()
        sec_type = info.get("SEC_TYPE", "").lower()
        if part_type == expected or sec_type == expected:
            candidates.append(dev)

    if not candidates:
        return None

    if len(candidates) == 1:
        logger.debug(
            "Auto-detected bootflash partition: %s (%s)",
            candidates[0],
            fs_type,
        )
        return candidates[0]

    # Heuristic for multi-partition images (e.g. newer N9Kv):
    # prefer the partition that looks like NX-OS bootflash.
    for dev in candidates:
        if _partition_has_bootflash_markers(dev, fs_type):
            logger.debug(
                "Auto-detected bootflash partition via markers: %s (%s)",
                dev,
                fs_type,
            )
            return dev

    # No markers found. Prefer the largest candidate partition since N9Kv
    # bootflash/data partitions are typically larger than helper partitions.
    largest = max(candidates, key=_partition_size_bytes)
    logger.debug(
        "Bootflash marker heuristic found no match; falling back to largest candidate %s",
        largest,
    )
    return largest


def _partition_size_bytes(part_dev: str) -> int:
    """Return partition size in bytes from sysfs.

    Returns 0 on failure, allowing safe use in max()/sorting.
    """
    try:
        name = Path(part_dev).name
        sectors = Path(f"/sys/class/block/{name}/size").read_text().strip()
        return int(sectors) * 512
    except Exception:
        return 0


def _partition_has_bootflash_markers(part_dev: str, fs_type: str) -> bool:
    """Check whether a partition resembles NX-OS bootflash.

    Marker set is intentionally small and conservative to avoid false positives:
    - existing /startup-config file
    - /bootflash directory
    - nxos*.bin image file at partition root
    """
    mount_dir = tempfile.mkdtemp(prefix="bootflash_probe_")
    mounted = False
    try:
        for mount_type in (fs_type, "auto"):
            try:
                _run(["mount", "-o", "ro", "-t", mount_type, part_dev, mount_dir])
                mounted = True
                break
            except Exception:
                continue

        if not mounted:
            return False

        root = Path(mount_dir)
        if (root / "startup-config").exists():
            return True
        if (root / "bootflash").is_dir():
            return True
        for f in root.glob("*.bin"):
            if f.name.lower().startswith("nxos"):
                return True
        return False
    except Exception:
        return False
    finally:
        if mounted:
            try:
                _run(["umount", mount_dir])
            except Exception:
                logger.debug("Failed to unmount probe dir %s", mount_dir, exc_info=True)
        try:
            Path(mount_dir).rmdir()
        except OSError:
            pass


def _parse_blkid(output: str) -> dict[str, str]:
    """Parse blkid -o export output into a dict."""
    result: dict[str, str] = {}
    for line in output.strip().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result
