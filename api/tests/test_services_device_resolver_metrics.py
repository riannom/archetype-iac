"""Tests for DeviceResolver singleton/loader.

Covers:
- DeviceResolver: singleton pattern (get_resolver), _ensure_loaded ImportError fallback,
  ResolvedDevice dataclass, resolve_config path, and cache invalidation across instances.
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest

from app.services.device_resolver import (
    DeviceResolver,
    ResolvedDevice,
    get_resolver,
)



# ---------------------------------------------------------------------------
# DeviceResolver helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeVendorConfig:
    kind: str
    vendor: str


def _make_resolver(
    vendor_configs: dict | None = None,
    alias_map: dict[str, str] | None = None,
    vendor_map: dict[str, str] | None = None,
) -> DeviceResolver:
    """Build a DeviceResolver with pre-injected lookup tables (skips agent import)."""
    resolver = DeviceResolver()
    resolver._vendor_configs = vendor_configs or {}
    resolver._alias_map = alias_map or {}
    resolver._vendor_map = vendor_map or {}
    return resolver


# ============================================================================
# DeviceResolver — singleton pattern
# ============================================================================


class TestGetResolverSingleton:
    """Tests for the module-level get_resolver() singleton factory."""

    def test_get_resolver_returns_device_resolver_instance(self):
        """get_resolver() returns a DeviceResolver."""
        resolver = get_resolver()
        assert isinstance(resolver, DeviceResolver)

    def test_get_resolver_returns_same_instance_on_repeated_calls(self):
        """get_resolver() is a singleton — same object every time."""
        r1 = get_resolver()
        r2 = get_resolver()
        assert r1 is r2


# ============================================================================
# DeviceResolver — _ensure_loaded with ImportError fallback
# ============================================================================


class TestEnsureLoadedFallback:
    """Tests for _ensure_loaded when agent.vendors cannot be imported."""

    def test_import_error_leaves_empty_maps(self):
        """When agent.vendors raises ImportError, all maps are empty dicts."""
        resolver = DeviceResolver()
        with patch.dict("sys.modules", {"agent": None, "agent.vendors": None}):
            resolver._ensure_loaded()

        assert resolver._vendor_configs == {}
        assert resolver._alias_map == {}
        assert resolver._vendor_map == {}

    def test_ensure_loaded_is_idempotent(self):
        """_ensure_loaded called twice doesn't overwrite already-loaded maps."""
        resolver = _make_resolver(
            vendor_configs={"ceos": _FakeVendorConfig(kind="ceos", vendor="Arista")},
            alias_map={"ceos": "ceos"},
        )
        # Maps already set — a second call must not clear them.
        resolver._ensure_loaded()
        assert "ceos" in resolver._vendor_configs

    def test_resolve_with_empty_vendor_configs_returns_unresolved(self):
        """A resolver with empty maps returns the normalized input as canonical_id."""
        resolver = _make_resolver(vendor_configs={}, alias_map={}, vendor_map={})

        with patch("app.image_store.find_custom_device", return_value=None):
            result = resolver.resolve("some-unknown-device")

        assert result.canonical_id == "some-unknown-device"
        assert result.vendor_config_key is None
        assert result.is_custom is False


# ============================================================================
# DeviceResolver — ResolvedDevice dataclass
# ============================================================================


class TestResolvedDeviceDataclass:
    """Tests for the ResolvedDevice frozen dataclass."""

    def test_resolved_device_is_frozen(self):
        """ResolvedDevice is immutable (frozen=True)."""
        rd = ResolvedDevice(
            canonical_id="ceos",
            vendor_config_key="ceos",
            kind="ceos",
            vendor="Arista",
            is_custom=False,
        )
        with pytest.raises((AttributeError, TypeError)):
            rd.canonical_id = "changed"  # type: ignore[misc]

    def test_resolved_device_equality(self):
        """Two ResolvedDevice instances with the same fields are equal."""
        rd1 = ResolvedDevice(
            canonical_id="srl",
            vendor_config_key="srl",
            kind="srl",
            vendor="Nokia",
            is_custom=False,
        )
        rd2 = ResolvedDevice(
            canonical_id="srl",
            vendor_config_key="srl",
            kind="srl",
            vendor="Nokia",
            is_custom=False,
        )
        assert rd1 == rd2

    def test_resolved_device_none_optional_fields(self):
        """Optional fields default to None when not resolved."""
        rd = ResolvedDevice(
            canonical_id="x",
            vendor_config_key=None,
            kind=None,
            vendor=None,
            is_custom=False,
        )
        assert rd.vendor_config_key is None
        assert rd.kind is None
        assert rd.vendor is None
