"""Device override management."""
from __future__ import annotations

import json
import logging

from .paths import device_overrides_path

logger = logging.getLogger(__name__)


def load_device_overrides() -> dict[str, dict]:
    """Load all device configuration overrides from storage."""
    path = device_overrides_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("overrides", {})


def save_device_overrides(overrides: dict[str, dict]) -> None:
    """Save device configuration overrides to storage."""
    path = device_overrides_path()
    path.write_text(json.dumps({"overrides": overrides}, indent=2), encoding="utf-8")


def get_device_override(device_id: str) -> dict | None:
    """Get configuration override for a specific device.

    Returns:
        Override dictionary or None if no override exists
    """
    overrides = load_device_overrides()
    return overrides.get(device_id)


def set_device_override(device_id: str, override: dict) -> dict:
    """Update configuration override for a device.

    Args:
        device_id: ID of the device to update
        override: Dictionary of override values

    Returns:
        The updated override entry
    """
    overrides = load_device_overrides()
    if device_id in overrides:
        overrides[device_id].update(override)
    else:
        overrides[device_id] = override
    save_device_overrides(overrides)
    return overrides[device_id]


def delete_device_override(device_id: str) -> bool:
    """Remove configuration override for a device (reset to defaults).

    Returns:
        True if override was removed, False if not found
    """
    overrides = load_device_overrides()
    if device_id not in overrides:
        return False
    del overrides[device_id]
    save_device_overrides(overrides)
    return True
