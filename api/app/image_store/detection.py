"""QCOW2/IOL device detection and filename-based classification."""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


# =============================================================================
# QCOW2 DEVICE DETECTION FOR VRNETLAB BUILDS
# =============================================================================

# Derived from VENDOR_CONFIGS -- single source of truth.
# Additional legacy patterns for devices not yet in VENDOR_CONFIGS are kept below.
def _build_qcow2_device_patterns() -> dict[str, tuple[str, str]]:
    """Build qcow2 detection patterns from VENDOR_CONFIGS + legacy entries."""
    try:
        from agent.vendors import _DERIVED_QCOW2_DEVICE_PATTERNS
        patterns = dict(_DERIVED_QCOW2_DEVICE_PATTERNS)
    except ImportError:
        patterns = {}

    # Legacy patterns for devices without VENDOR_CONFIGS entries.
    # These will be removed once corresponding entries are added.
    _LEGACY_QCOW2_PATTERNS: dict[str, tuple[str, str]] = {
        r"c8000v[_-]?sdwan.*\.qcow2": ("cat-sdwan-cedge", "cisco/sdwan"),
        r"vmx.*\.qcow2": ("vmx", "juniper/vmx"),
        r"vqfx.*\.qcow2": ("juniper_vqfx", "juniper/vqfx"),
        r"veos[_-]?[\d\.]+.*\.qcow2": ("veos", "arista/veos"),
        r"sros[_-]?[\d\.]+.*\.qcow2": ("sros", "nokia/sros"),
    }
    for pattern, value in _LEGACY_QCOW2_PATTERNS.items():
        if pattern not in patterns:
            patterns[pattern] = value

    return patterns


QCOW2_DEVICE_PATTERNS: dict[str, tuple[str, str]] = _build_qcow2_device_patterns()


def detect_iol_device_type(filename: str) -> str | None:
    """Detect IOL device type from filename.

    Args:
        filename: The IOL filename (e.g., "i86bi-linux-l3-adventerprisek9-15.6.1T.bin")

    Returns:
        Device ID ("iol-xe" or "iol-l2") or None if not recognized as IOL.
    """
    name = filename.lower()
    if "l2-" in name or "ioll2" in name or "iol_l2" in name:
        return "iol-l2"
    if "l3-" in name or "iol" in name:
        return "iol-xe"
    return None


def detect_qcow2_device_type(filename: str) -> tuple[str | None, str | None]:
    """Detect device type and vrnetlab path from qcow2 filename.

    Args:
        filename: The qcow2 filename (e.g., "c8000v-17.16.01a.qcow2")

    Returns:
        Tuple of (device_id, vrnetlab_path) or (None, None) if unknown.
        device_id: The device type identifier (e.g., "c8000v")
        vrnetlab_path: The vrnetlab subdirectory to use (e.g., "cisco/c8000v")
    """
    filename_lower = filename.lower()
    for pattern, (device_id, vrnetlab_path) in QCOW2_DEVICE_PATTERNS.items():
        if device_id is None:  # Skip the catch-all pattern
            continue
        if re.search(pattern, filename_lower, re.IGNORECASE):
            return device_id, vrnetlab_path
    return None, None


def _get_filename_keyword_map() -> dict[str, str]:
    """Get the filename keyword map, derived from VENDOR_CONFIGS."""
    try:
        from agent.vendors import _DERIVED_FILENAME_KEYWORD_MAP
        return _DERIVED_FILENAME_KEYWORD_MAP
    except ImportError:
        return {}


def _extract_version(filename: str) -> str | None:
    match = re.search(r"(\d+(?:\.\d+){1,3}[A-Za-z0-9]*)", filename)
    if not match:
        return None

    version = match.group(1)

    # For file artifacts, trim trailing single-letter train suffixes (for example
    # "4.28.0F.tar" -> "4.28.0"). Keep docker tag suffixes intact.
    if ":" not in filename and re.fullmatch(r"\d+(?:\.\d+){1,3}[A-Za-z]", version):
        return version[:-1]

    return version


def detect_device_from_filename(filename: str) -> tuple[str | None, str | None]:
    from .custom_devices import load_rules

    name = filename.lower()
    for rule in load_rules():
        pattern = rule.get("pattern")
        device_id = rule.get("device_id")
        if not pattern or not device_id:
            continue
        if re.search(pattern, name):
            return device_id, _extract_version(filename)
    keyword_map = _get_filename_keyword_map()
    for keyword, device_id in keyword_map.items():
        if keyword in name:
            return device_id, _extract_version(filename)
    return None, _extract_version(filename)


def classify_image_file(filename: str) -> tuple[str | None, str | None, str | None]:
    """Classify an image file by filename.

    Returns:
        Tuple of (device_id, version, vrnetlab_path)
    """
    # Try qcow2 detection first
    if filename.lower().endswith('.qcow2'):
        device_id, vrnetlab_path = detect_qcow2_device_type(filename)
        if device_id:
            return device_id, _extract_version(filename), vrnetlab_path

    # Try IOL detection
    if filename.lower().endswith(('.bin', '.iol')):
        device_id = detect_iol_device_type(filename)
        if device_id:
            return device_id, _extract_version(filename), None

    # Fall back to filename keyword detection
    device_id, version = detect_device_from_filename(filename)
    return device_id, version, None
