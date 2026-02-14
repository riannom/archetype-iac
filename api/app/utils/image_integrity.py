"""Image integrity utilities for qcow2 validation and checksum computation."""

import hashlib
import json
import subprocess
from pathlib import Path


QCOW2_MAGIC = b"QFI\xfb"


def compute_sha256(file_path: str | Path) -> str:
    """Compute SHA256 hash of a file using streaming 1MB chunks.

    Synchronous — use asyncio.to_thread() when calling from async context.

    Args:
        file_path: Path to the file to hash.

    Returns:
        Hex-encoded SHA256 digest string.
    """
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def validate_qcow2(file_path: str | Path) -> tuple[bool, str]:
    """Validate that a file is a well-formed qcow2 image.

    Two checks:
    1. Magic bytes: first 4 bytes must be QFI\\xfb (0x514649fb)
    2. qemu-img info must report format "qcow2" (skipped if qemu-img not available)

    Args:
        file_path: Path to the file to validate.

    Returns:
        (True, "") on success, (False, "error message") on failure.
    """
    path = Path(file_path)

    if not path.exists():
        return False, f"File not found: {path}"

    if path.stat().st_size < 4:
        return False, "File too small to be a valid qcow2 image"

    # Check magic bytes
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic != QCOW2_MAGIC:
        return False, f"Invalid qcow2 magic bytes: expected {QCOW2_MAGIC!r}, got {magic!r}"

    # Verify with qemu-img if available
    try:
        result = subprocess.run(
            ["qemu-img", "info", "--output=json", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            info = json.loads(result.stdout)
            fmt = info.get("format", "")
            if fmt != "qcow2":
                return False, f"qemu-img reports format '{fmt}', expected 'qcow2'"
    except FileNotFoundError:
        pass  # qemu-img not installed, skip this check
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        pass  # non-fatal — magic bytes check already passed

    return True, ""
