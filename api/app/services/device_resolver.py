"""Unified device identity resolution.

Single entry-point for resolving any device identifier (key, kind, alias,
custom device ID) to its canonical form and associated metadata.

All code paths that need to resolve device identity should use DeviceResolver
instead of the legacy per-function lookups scattered across image_store.py,
vendors.py, and iso/mapper.py.
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedDevice:
    """Result of device identity resolution."""

    # The canonical VENDOR_CONFIGS key (e.g., "ceos", "cisco_n9kv").
    # For custom/unknown devices, this is the normalized input.
    canonical_id: str
    # The VENDOR_CONFIGS key if the device is a built-in vendor device, else None.
    vendor_config_key: Optional[str]
    # Runtime kind (from VendorConfig.kind). Same as vendor_config_key for most entries.
    kind: Optional[str]
    # Vendor display name (e.g., "Arista", "Cisco").
    vendor: Optional[str]
    # True if this is a custom (user-created) device, not in VENDOR_CONFIGS.
    is_custom: bool


class DeviceResolver:
    """Unified device identity resolver.

    Resolution order (explicit, documented):
      1. Exact VENDOR_CONFIGS key match
      2. Derived alias chain resolution (kinds + aliases)
      3. Custom device lookup
      4. Return unresolved (canonical_id = normalized input)
    """

    def __init__(self) -> None:
        self._vendor_configs: dict | None = None
        self._alias_map: dict[str, str] | None = None
        self._vendor_map: dict[str, str] | None = None

    def _ensure_loaded(self) -> None:
        """Lazy-load VENDOR_CONFIGS and derived maps."""
        if self._vendor_configs is not None:
            return
        try:
            from agent.vendors import (
                VENDOR_CONFIGS,
                _DERIVED_DEVICE_ID_ALIASES,
                _DERIVED_DEVICE_VENDOR_MAP,
            )
            self._vendor_configs = VENDOR_CONFIGS
            self._alias_map = _DERIVED_DEVICE_ID_ALIASES
            self._vendor_map = _DERIVED_DEVICE_VENDOR_MAP
        except ImportError:
            self._vendor_configs = {}
            self._alias_map = {}
            self._vendor_map = {}

    @functools.lru_cache(maxsize=512)
    def resolve(self, device_id: str | None) -> ResolvedDevice:
        """Resolve a device identifier to its canonical form.

        Args:
            device_id: Any known device identifier (key, kind, alias, custom ID).

        Returns:
            ResolvedDevice with resolved metadata.
        """
        if not device_id:
            return ResolvedDevice(
                canonical_id="",
                vendor_config_key=None,
                kind=None,
                vendor=None,
                is_custom=False,
            )

        self._ensure_loaded()
        assert self._vendor_configs is not None
        assert self._alias_map is not None
        assert self._vendor_map is not None

        normalized = device_id.strip().lower()

        # 1. Check derived alias map (covers keys, kinds, and aliases).
        resolved_key = self._alias_map.get(normalized)
        if resolved_key and resolved_key in self._vendor_configs:
            cfg = self._vendor_configs[resolved_key]
            return ResolvedDevice(
                canonical_id=resolved_key,
                vendor_config_key=resolved_key,
                kind=cfg.kind,
                vendor=cfg.vendor,
                is_custom=False,
            )

        # 2. Direct VENDOR_CONFIGS key match (shouldn't miss if alias map is complete).
        if normalized in self._vendor_configs:
            cfg = self._vendor_configs[normalized]
            return ResolvedDevice(
                canonical_id=normalized,
                vendor_config_key=normalized,
                kind=cfg.kind,
                vendor=cfg.vendor,
                is_custom=False,
            )

        # 3. Custom device lookup.
        try:
            from app.image_store import find_custom_device
            custom = find_custom_device(device_id)
            if custom:
                return ResolvedDevice(
                    canonical_id=device_id,
                    vendor_config_key=None,
                    kind=custom.get("kind"),
                    vendor=custom.get("vendor"),
                    is_custom=True,
                )
        except Exception:
            pass

        # 4. Unresolved — return normalized input.
        vendor = self._vendor_map.get(normalized)
        return ResolvedDevice(
            canonical_id=normalized,
            vendor_config_key=None,
            kind=None,
            vendor=vendor,
            is_custom=False,
        )

    def resolve_config(self, device_id: str | None):
        """Resolve a device identifier to its VendorConfig.

        Args:
            device_id: Any known device identifier.

        Returns:
            VendorConfig if found, None otherwise.
        """
        if not device_id:
            return None
        self._ensure_loaded()
        assert self._vendor_configs is not None

        resolved = self.resolve(device_id)
        if resolved.vendor_config_key:
            return self._vendor_configs.get(resolved.vendor_config_key)
        return None


# Module-level singleton for convenience.
_resolver: DeviceResolver | None = None


def get_resolver() -> DeviceResolver:
    """Get the module-level DeviceResolver singleton."""
    global _resolver
    if _resolver is None:
        _resolver = DeviceResolver()
    return _resolver
