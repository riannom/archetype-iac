"""Tests for app/services/device_resolver.py - Unified device identity resolution.

This module tests:
- Alias chain resolution (kind -> canonical key)
- Direct VENDOR_CONFIGS key match
- Case insensitive normalization
- Custom device fallback
- Exception suppression in custom device lookup
- Unresolved device returns normalized input
- LRU cache behavior
- resolve_config returns VendorConfig or None
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch


from app.services.device_resolver import DeviceResolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeVendorConfig:
    """Minimal stand-in for the real VendorConfig dataclass."""
    kind: str
    vendor: str


def _make_resolver(
    vendor_configs: dict | None = None,
    alias_map: dict[str, str] | None = None,
    vendor_map: dict[str, str] | None = None,
) -> DeviceResolver:
    """Build a DeviceResolver with injected lookup tables (bypasses agent import)."""
    resolver = DeviceResolver()
    resolver._vendor_configs = vendor_configs or {}
    resolver._alias_map = alias_map or {}
    resolver._vendor_map = vendor_map or {}
    return resolver


# ============================================================================
# Alias chain resolution
# ============================================================================


class TestAliasChainResolution:
    """Tests for resolution through the alias map."""

    def test_alias_resolves_to_canonical_key(self):
        """An alias (e.g. 'arista_ceos') resolves to its canonical config key."""
        cfg = _FakeVendorConfig(kind="ceos", vendor="Arista")
        resolver = _make_resolver(
            vendor_configs={"ceos": cfg},
            alias_map={"arista_ceos": "ceos", "ceos": "ceos"},
        )

        result = resolver.resolve("arista_ceos")

        assert result.canonical_id == "ceos"
        assert result.vendor_config_key == "ceos"
        assert result.kind == "ceos"
        assert result.vendor == "Arista"
        assert result.is_custom is False

    def test_kind_alias_resolves(self):
        """A kind string in the alias map resolves to the config key."""
        cfg = _FakeVendorConfig(kind="cisco_n9kv", vendor="Cisco")
        resolver = _make_resolver(
            vendor_configs={"cisco_n9kv": cfg},
            alias_map={"nexus9000v": "cisco_n9kv", "cisco_n9kv": "cisco_n9kv"},
        )

        result = resolver.resolve("nexus9000v")

        assert result.canonical_id == "cisco_n9kv"
        assert result.vendor == "Cisco"

    def test_multi_hop_alias(self):
        """Alias map entry must point to a valid vendor_configs key."""
        cfg = _FakeVendorConfig(kind="srl", vendor="Nokia")
        resolver = _make_resolver(
            vendor_configs={"srl": cfg},
            alias_map={"srlinux": "srl", "srl": "srl", "nokia_srl": "srl"},
        )

        result = resolver.resolve("nokia_srl")

        assert result.canonical_id == "srl"
        assert result.vendor_config_key == "srl"


# ============================================================================
# Direct key match
# ============================================================================


class TestDirectKeyMatch:
    """Tests for direct VENDOR_CONFIGS key lookup."""

    def test_direct_key_match(self):
        """A key present in VENDOR_CONFIGS but missing from alias map still resolves."""
        cfg = _FakeVendorConfig(kind="linux", vendor="Generic")
        resolver = _make_resolver(
            vendor_configs={"linux": cfg},
            alias_map={},  # intentionally empty
        )

        result = resolver.resolve("linux")

        assert result.canonical_id == "linux"
        assert result.vendor_config_key == "linux"
        assert result.kind == "linux"
        assert result.vendor == "Generic"

    def test_direct_key_preferred_over_none(self):
        """When alias map returns None, direct key match is used."""
        cfg = _FakeVendorConfig(kind="ceos", vendor="Arista")
        resolver = _make_resolver(
            vendor_configs={"ceos": cfg},
            alias_map={"ceos": None},  # alias returns None
        )

        result = resolver.resolve("ceos")

        # Falls through alias (None not in vendor_configs) to direct match
        assert result.canonical_id == "ceos"
        assert result.vendor_config_key == "ceos"


# ============================================================================
# Case insensitive normalization
# ============================================================================


class TestCaseInsensitiveNormalization:
    """Tests for case normalization during resolution."""

    def test_uppercase_input_normalized(self):
        """Input is lowercased before lookup."""
        cfg = _FakeVendorConfig(kind="ceos", vendor="Arista")
        resolver = _make_resolver(
            vendor_configs={"ceos": cfg},
            alias_map={"ceos": "ceos"},
        )

        result = resolver.resolve("CEOS")

        assert result.canonical_id == "ceos"
        assert result.vendor_config_key == "ceos"

    def test_mixed_case_and_whitespace(self):
        """Input is stripped and lowercased."""
        cfg = _FakeVendorConfig(kind="srl", vendor="Nokia")
        resolver = _make_resolver(
            vendor_configs={"srl": cfg},
            alias_map={"srl": "srl"},
        )

        result = resolver.resolve("  SRL  ")

        assert result.canonical_id == "srl"


# ============================================================================
# Custom device fallback
# ============================================================================


class TestCustomDeviceFallback:
    """Tests for custom device resolution via image_store."""

    def test_custom_device_found(self):
        """Custom device in image_store is resolved with is_custom=True."""
        resolver = _make_resolver(vendor_configs={}, alias_map={})

        with patch("app.image_store.find_custom_device", return_value={
            "id": "my-custom-router",
            "kind": "custom_router",
            "vendor": "Acme",
        }):
            result = resolver.resolve("my-custom-router")

        assert result.canonical_id == "my-custom-router"
        assert result.vendor_config_key is None
        assert result.kind == "custom_router"
        assert result.vendor == "Acme"
        assert result.is_custom is True

    def test_custom_device_exception_suppressed(self):
        """Exceptions from find_custom_device are caught and suppressed."""
        resolver = _make_resolver(vendor_configs={}, alias_map={})

        with patch("app.image_store.find_custom_device", side_effect=RuntimeError("disk error")):
            result = resolver.resolve("broken-device")

        # Should fall through to unresolved, not raise
        assert result.canonical_id == "broken-device"
        assert result.is_custom is False


# ============================================================================
# Unresolved device
# ============================================================================


class TestUnresolvedDevice:
    """Tests for devices that don't match any known category."""

    def test_unresolved_returns_normalized_input(self):
        """Unknown device IDs return normalized input as canonical_id."""
        resolver = _make_resolver(vendor_configs={}, alias_map={})

        with patch("app.image_store.find_custom_device", return_value=None):
            result = resolver.resolve("Unknown_Device_X")

        assert result.canonical_id == "unknown_device_x"
        assert result.vendor_config_key is None
        assert result.kind is None
        assert result.is_custom is False

    def test_none_input_returns_empty(self):
        """None device_id returns empty canonical_id."""
        resolver = _make_resolver()

        result = resolver.resolve(None)

        assert result.canonical_id == ""
        assert result.vendor_config_key is None
        assert result.kind is None
        assert result.is_custom is False

    def test_empty_string_returns_empty(self):
        """Empty string device_id returns empty canonical_id."""
        resolver = _make_resolver()

        result = resolver.resolve("")

        assert result.canonical_id == ""

    def test_unresolved_with_vendor_map_entry(self):
        """Unresolved device can still have a vendor from vendor_map."""
        resolver = _make_resolver(
            vendor_configs={},
            alias_map={},
            vendor_map={"some_cisco_thing": "Cisco"},
        )

        with patch("app.image_store.find_custom_device", return_value=None):
            result = resolver.resolve("some_cisco_thing")

        assert result.canonical_id == "some_cisco_thing"
        assert result.vendor == "Cisco"
        assert result.vendor_config_key is None


# ============================================================================
# LRU cache behavior
# ============================================================================


class TestLRUCacheBehavior:
    """Tests for caching in the resolve method."""

    def test_cache_returns_same_object(self):
        """Repeated calls return the same cached ResolvedDevice object."""
        cfg = _FakeVendorConfig(kind="ceos", vendor="Arista")
        resolver = _make_resolver(
            vendor_configs={"ceos": cfg},
            alias_map={"ceos": "ceos"},
        )

        result1 = resolver.resolve("ceos")
        result2 = resolver.resolve("ceos")

        # Should be the exact same object from the cache
        assert result1 is result2

    def test_different_instances_have_independent_caches(self):
        """Each DeviceResolver instance has its own LRU cache."""
        cfg = _FakeVendorConfig(kind="ceos", vendor="Arista")
        resolver1 = _make_resolver(
            vendor_configs={"ceos": cfg},
            alias_map={"ceos": "ceos"},
        )
        resolver2 = _make_resolver(
            vendor_configs={},
            alias_map={},
        )

        result1 = resolver1.resolve("ceos")
        with patch("app.image_store.find_custom_device", return_value=None):
            result2 = resolver2.resolve("ceos")

        assert result1.vendor_config_key == "ceos"
        assert result2.vendor_config_key is None


# ============================================================================
# resolve_config
# ============================================================================


class TestResolveConfig:
    """Tests for the resolve_config convenience method."""

    def test_resolve_config_returns_vendor_config(self):
        """resolve_config returns the VendorConfig for known devices."""
        cfg = _FakeVendorConfig(kind="ceos", vendor="Arista")
        resolver = _make_resolver(
            vendor_configs={"ceos": cfg},
            alias_map={"ceos": "ceos"},
        )

        result = resolver.resolve_config("ceos")

        assert result is cfg
        assert result.kind == "ceos"

    def test_resolve_config_returns_none_for_unknown(self):
        """resolve_config returns None for unresolved device IDs."""
        resolver = _make_resolver(vendor_configs={}, alias_map={})

        with patch("app.image_store.find_custom_device", return_value=None):
            result = resolver.resolve_config("nonexistent")

        assert result is None

    def test_resolve_config_returns_none_for_none_input(self):
        """resolve_config returns None when given None."""
        resolver = _make_resolver()

        result = resolver.resolve_config(None)

        assert result is None
