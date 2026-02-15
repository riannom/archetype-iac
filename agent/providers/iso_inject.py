"""Create an ISO image containing startup configuration for VM boot injection.

Some platforms (e.g., Cisco IOS-XR via CVAC) read configuration from a
CD-ROM at first boot.  This module creates a minimal ISO 9660 image with
the configuration file so the VM can apply it automatically.

Failures are non-fatal — the VM simply boots without a startup config.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# ISO 9660 limits volume labels to 32 characters.
_MAX_VOLUME_LABEL_LEN = 32


def create_config_iso(
    iso_path: Path,
    config_content: str,
    *,
    volume_label: str = "config",
    filename: str = "startup-config",
) -> bool:
    """Create an ISO image containing *config_content* at *filename*.

    Args:
        iso_path: Destination path for the ISO file.
        config_content: Configuration text to embed.
        volume_label: ISO 9660 volume label (truncated to 32 chars).
        filename: Name of the config file inside the ISO.

    Returns:
        True on success, False on any failure (logged as warning).
    """
    if not config_content or not config_content.strip():
        logger.debug("Empty config content, skipping ISO creation")
        return False

    volume_label = volume_label[:_MAX_VOLUME_LABEL_LEN]
    tmp_dir: str | None = None

    try:
        tmp_dir = tempfile.mkdtemp(prefix="iso_inject_")
        config_file = Path(tmp_dir) / filename
        config_file.write_text(config_content)

        # Try mkisofs first, fall back to genisoimage (same tool, different package)
        for tool in ("mkisofs", "genisoimage"):
            tool_path = shutil.which(tool)
            if not tool_path:
                continue

            subprocess.run(
                [
                    tool_path,
                    "-V", volume_label,
                    "-r",       # Rock Ridge extensions
                    "-J",       # Joliet extensions
                    "-o", str(iso_path),
                    tmp_dir,
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )

            logger.info(
                "Created config ISO %s (%d bytes config, label=%s, tool=%s)",
                iso_path,
                len(config_content),
                volume_label,
                tool,
            )
            return True

        logger.warning(
            "Neither mkisofs nor genisoimage found; cannot create config ISO"
        )
        return False

    except Exception:
        logger.warning("Config ISO creation failed for %s", iso_path, exc_info=True)
        # Clean up partial ISO on failure
        try:
            if iso_path.exists():
                iso_path.unlink()
        except OSError:
            pass
        return False

    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def remove_config_iso(iso_path: Path) -> bool:
    """Delete a config ISO file if it exists.  Idempotent.

    Returns:
        True if the file was deleted or didn't exist, False on error.
    """
    try:
        if iso_path.exists():
            iso_path.unlink()
            logger.debug("Removed config ISO %s", iso_path)
        return True
    except Exception:
        logger.warning("Failed to remove config ISO %s", iso_path, exc_info=True)
        return False
