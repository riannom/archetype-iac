"""Tests for catalog service identity management (services/catalog_service.py).

This module tests:
- Helper functions: normalize_token, vendor_key, json_load/dump, to_int, parse_timestamp
- Alias registration and rank ordering
- Cache management: invalidate, cache key from session
- catalog_is_seeded check
- _build_desired_catalog_identity_data from vendor configs + custom devices
- _catalog_identity_stamp determinism
- ensure_catalog_identity_synced lifecycle
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models
from app.services.catalog_service import (
    _normalize_token,
    _vendor_key,
    _json_load,
    _json_dump,
    _to_int,
    _parse_timestamp,
    _register_alias,
    _bind_cache_key,
    _catalog_identity_stamp,
    _IMAGE_INDEX_CACHE_BY_BIND,
    _IDENTITY_SYNC_STAMP_BY_BIND,
    DesiredCatalogDevice,
    invalidate_image_index_cache,
    catalog_is_seeded,
    ensure_catalog_identity_synced,
)


# ---------------------------------------------------------------------------
# Fake vendor config for monkeypatching
# ---------------------------------------------------------------------------

def _fake_vendor_config(
    *,
    label="Fake Router",
    kind="fake_router",
    vendor="FakeCo",
    aliases=None,
    tags=None,
    icon=None,
    vendor_options=None,
    device_type=None,
    memory=None,
    cpu=None,
    max_ports=None,
    supported_image_kinds=None,
):
    return SimpleNamespace(
        label=label,
        kind=kind,
        vendor=vendor,
        aliases=aliases or [],
        tags=tags or [],
        icon=icon,
        vendor_options=vendor_options,
        device_type=device_type,
        memory=memory,
        cpu=cpu,
        max_ports=max_ports,
        supported_image_kinds=supported_image_kinds or [],
    )


# ============================================================================
# TestHelperFunctions
# ============================================================================


class TestHelperFunctions:
    """Tests for small pure-function helpers."""

    def test_normalize_token_lowercases_and_strips(self):
        assert _normalize_token("  FoO  ") == "foo"

    def test_normalize_token_none_returns_none(self):
        assert _normalize_token(None) is None

    def test_normalize_token_empty_string_returns_none(self):
        assert _normalize_token("  ") is None

    def test_normalize_token_int_coercion(self):
        assert _normalize_token(42) == "42"

    def test_vendor_key_normalizes(self):
        assert _vendor_key("Arista Networks") == "arista_networks"

    def test_vendor_key_none_returns_unknown(self):
        assert _vendor_key(None) == "unknown"

    def test_vendor_key_empty_returns_unknown(self):
        assert _vendor_key("") == "unknown"

    def test_json_load_valid(self):
        assert _json_load('{"a": 1}') == {"a": 1}

    def test_json_load_none_returns_empty(self):
        assert _json_load(None) == {}

    def test_json_load_invalid_json_returns_empty(self):
        assert _json_load("not json") == {}

    def test_json_load_non_dict_returns_empty(self):
        assert _json_load("[1,2,3]") == {}

    def test_json_dump_deterministic(self):
        result = _json_dump({"b": 2, "a": 1})
        assert result == '{"a":1,"b":2}'

    def test_to_int_valid(self):
        assert _to_int("42") == 42
        assert _to_int(42) == 42

    def test_to_int_none_returns_none(self):
        assert _to_int(None) is None

    def test_to_int_invalid_returns_none(self):
        assert _to_int("abc") is None

    def test_parse_timestamp_iso(self):
        result = _parse_timestamp("2025-01-15T12:30:00+00:00")
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_parse_timestamp_z_suffix(self):
        result = _parse_timestamp("2025-01-15T12:30:00Z")
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_parse_timestamp_none_returns_none(self):
        assert _parse_timestamp(None) is None

    def test_parse_timestamp_empty_returns_none(self):
        assert _parse_timestamp("  ") is None

    def test_parse_timestamp_invalid_returns_none(self):
        assert _parse_timestamp("not-a-date") is None

    def test_parse_timestamp_naive_gets_utc(self):
        result = _parse_timestamp("2025-01-15T12:30:00")
        assert result is not None
        assert result.tzinfo == timezone.utc


# ============================================================================
# TestAliasRegistration
# ============================================================================


class TestAliasRegistration:
    """Tests for _register_alias rank logic."""

    def test_register_new_alias(self):
        alias_map: dict[str, str] = {}
        _register_alias(alias_map, "ceos", "arista_ceos", "explicit")
        assert alias_map["arista_ceos"] == "explicit"

    def test_skip_self_alias(self):
        """Alias matching the canonical ID is ignored."""
        alias_map: dict[str, str] = {}
        _register_alias(alias_map, "ceos", "ceos", "explicit")
        assert "ceos" not in alias_map

    def test_skip_none_alias(self):
        alias_map: dict[str, str] = {}
        _register_alias(alias_map, "ceos", None, "explicit")
        assert len(alias_map) == 0

    def test_higher_rank_overwrites_lower(self):
        """Explicit (rank 2) beats compatibility (rank 0)."""
        alias_map: dict[str, str] = {}
        _register_alias(alias_map, "ceos", "arista", "compatibility")
        assert alias_map["arista"] == "compatibility"
        _register_alias(alias_map, "ceos", "arista", "explicit")
        assert alias_map["arista"] == "explicit"

    def test_lower_rank_does_not_overwrite(self):
        """Compatibility (rank 0) does not beat explicit (rank 2)."""
        alias_map: dict[str, str] = {}
        _register_alias(alias_map, "ceos", "arista", "explicit")
        _register_alias(alias_map, "ceos", "arista", "compatibility")
        assert alias_map["arista"] == "explicit"

    def test_same_rank_overwrites(self):
        """Equal rank should overwrite (>=)."""
        alias_map: dict[str, str] = {}
        _register_alias(alias_map, "ceos", "x", "runtime_kind")
        _register_alias(alias_map, "ceos", "x", "runtime_kind")
        assert alias_map["x"] == "runtime_kind"


# ============================================================================
# TestCacheManagement
# ============================================================================


class TestCacheManagement:
    """Tests for invalidate_image_index_cache and cache key helpers."""

    def test_invalidate_all(self):
        _IMAGE_INDEX_CACHE_BY_BIND[999] = "dummy"
        invalidate_image_index_cache(session=None)
        assert 999 not in _IMAGE_INDEX_CACHE_BY_BIND

    def test_invalidate_specific_session(self, test_db: Session):
        key = _bind_cache_key(test_db)
        _IMAGE_INDEX_CACHE_BY_BIND[key] = "dummy"
        invalidate_image_index_cache(session=test_db)
        assert key not in _IMAGE_INDEX_CACHE_BY_BIND

    def test_invalidate_specific_noop_when_missing(self, test_db: Session):
        """Invalidating a key not in cache should not raise."""
        invalidate_image_index_cache(session=test_db)  # no error

    def test_bind_cache_key_returns_int(self, test_db: Session):
        key = _bind_cache_key(test_db)
        assert isinstance(key, int)


# ============================================================================
# TestCatalogIsSeeded
# ============================================================================


class TestCatalogIsSeeded:
    """Tests for catalog_is_seeded check."""

    def test_empty_catalog_not_seeded(self, test_db: Session):
        """Empty CatalogDeviceType table means not seeded."""
        assert catalog_is_seeded(test_db) is False

    def test_seeded_with_device(self, test_db: Session):
        """A single device row means seeded."""
        vendor = models.CatalogVendor(
            id=str(uuid4()),
            vendor_key="test",
            display_name="Test",
            lifecycle_status="active",
            metadata_json="{}",
        )
        device = models.CatalogDeviceType(
            id=str(uuid4()),
            canonical_device_id="test-device",
            vendor_id=vendor.id,
            runtime_kind="test",
            display_name="Test Device",
            source="builtin",
            lifecycle_status="active",
            metadata_json="{}",
        )
        test_db.add(vendor)
        test_db.add(device)
        test_db.commit()
        assert catalog_is_seeded(test_db) is True

    def test_handles_exception_gracefully(self, test_db: Session, monkeypatch):
        """Exception during query returns False rather than propagating."""
        monkeypatch.setattr(
            test_db, "query",
            MagicMock(side_effect=Exception("boom")),
        )
        assert catalog_is_seeded(test_db) is False


# ============================================================================
# TestBuildDesiredCatalogIdentityData
# ============================================================================


class TestBuildDesiredCatalogIdentityData:
    """Tests for _build_desired_catalog_identity_data."""

    def test_builds_from_vendor_configs(self, monkeypatch):
        from app.services import catalog_service

        fake_configs = {
            "fake-router": _fake_vendor_config(
                label="Fake Router",
                kind="fake_kind",
                vendor="TestVendor",
                aliases=["alias1"],
                memory=4096,
                cpu=2,
                max_ports=8,
                supported_image_kinds=["qcow2"],
            ),
        }
        monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", fake_configs)
        monkeypatch.setattr("app.image_store.get_image_compatibility_aliases", lambda: {})
        monkeypatch.setattr("app.image_store.load_custom_devices", lambda: [])

        vendors, devices = catalog_service._build_desired_catalog_identity_data()
        assert "testvendor" in vendors
        assert "fake-router" in devices
        device = devices["fake-router"]
        assert device.display_name == "Fake Router"
        assert device.revision_memory_mb == 4096
        assert device.revision_cpu_count == 2
        assert device.revision_max_ports == 8
        assert "alias1" in device.aliases

    def test_includes_custom_devices(self, monkeypatch):
        from app.services import catalog_service

        monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", {})
        monkeypatch.setattr("app.image_store.get_image_compatibility_aliases", lambda: {})
        monkeypatch.setattr("app.image_store.load_custom_devices", lambda: [
            {"id": "custom1", "name": "Custom Device", "vendor": "CustomVendor", "kind": "custom_kind"},
        ])

        vendors, devices = catalog_service._build_desired_catalog_identity_data()
        assert "custom1" in devices
        assert devices["custom1"].source == "custom"
        assert devices["custom1"].display_name == "Custom Device"

    def test_builtin_wins_over_custom(self, monkeypatch):
        from app.services import catalog_service

        fake_configs = {
            "overlap": _fake_vendor_config(label="Built-in", vendor="V"),
        }
        monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", fake_configs)
        monkeypatch.setattr("app.image_store.get_image_compatibility_aliases", lambda: {})
        monkeypatch.setattr("app.image_store.load_custom_devices", lambda: [
            {"id": "overlap", "name": "Custom Overlap", "vendor": "CV"},
        ])

        _, devices = catalog_service._build_desired_catalog_identity_data()
        assert devices["overlap"].source == "builtin"

    def test_compatibility_aliases_injected(self, monkeypatch):
        from app.services import catalog_service

        fake_configs = {
            "ceos": _fake_vendor_config(label="cEOS", vendor="Arista"),
        }
        monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", fake_configs)
        monkeypatch.setattr("app.image_store.get_image_compatibility_aliases", lambda: {
            "ceos": ["arista_ceos", "ceosimage"],
        })
        monkeypatch.setattr("app.image_store.load_custom_devices", lambda: [])

        _, devices = catalog_service._build_desired_catalog_identity_data()
        aliases = devices["ceos"].aliases
        assert "arista_ceos" in aliases
        assert "ceosimage" in aliases


# ============================================================================
# TestCatalogIdentityStamp
# ============================================================================


class TestCatalogIdentityStamp:
    """Tests for _catalog_identity_stamp determinism."""

    def _make_desired(self, canonical="test", vendor="v"):
        return DesiredCatalogDevice(
            canonical_device_id=canonical,
            vendor_key=vendor,
            runtime_kind="kind",
            display_name="Test",
            device_class="router",
            source="builtin",
            lifecycle_status="active",
            metadata_json="{}",
            aliases={},
            revision_runtime_kind="kind",
            revision_memory_mb=None,
            revision_cpu_count=None,
            revision_max_ports=None,
            revision_supported_image_kinds_json="[]",
            revision_metadata_json="{}",
        )

    def test_deterministic_same_input(self):
        vendors = {"v": {"vendor_key": "v", "display_name": "V", "lifecycle_status": "active", "metadata_json": "{}"}}
        devices = {"test": self._make_desired()}
        s1 = _catalog_identity_stamp(vendors, devices)
        s2 = _catalog_identity_stamp(vendors, devices)
        assert s1 == s2
        assert len(s1) == 64  # SHA-256 hex

    def test_changes_with_different_input(self):
        vendors = {"v": {"vendor_key": "v", "display_name": "V", "lifecycle_status": "active", "metadata_json": "{}"}}
        d1 = {"test": self._make_desired()}
        d2 = {"test2": self._make_desired(canonical="test2")}
        s1 = _catalog_identity_stamp(vendors, d1)
        s2 = _catalog_identity_stamp(vendors, d2)
        assert s1 != s2


# ============================================================================
# TestEnsureCatalogIdentitySynced
# ============================================================================


class TestEnsureCatalogIdentitySynced:
    """Tests for ensure_catalog_identity_synced full lifecycle."""

    def _patch_build(self, monkeypatch, vendor_configs=None, custom_devices=None, compat_aliases=None):
        """Set up monkeypatches for _build_desired_catalog_identity_data dependencies."""
        monkeypatch.setattr(
            "agent.vendors.VENDOR_CONFIGS",
            vendor_configs or {},
        )
        monkeypatch.setattr(
            "app.image_store.get_image_compatibility_aliases",
            lambda: compat_aliases or {},
        )
        monkeypatch.setattr(
            "app.image_store.load_custom_devices",
            lambda: custom_devices or [],
        )

    def test_creates_vendors_and_devices(self, test_db: Session, monkeypatch):
        """First sync should create vendor and device rows."""
        self._patch_build(monkeypatch, vendor_configs={
            "mydevice": _fake_vendor_config(
                label="My Device",
                kind="my_kind",
                vendor="MyVendor",
                max_ports=4,
            ),
        })
        # Clear any cached stamp from previous tests
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()

        result = ensure_catalog_identity_synced(test_db, force=True)
        assert result["applied"] is True
        assert result["vendors_created"] >= 1
        assert result["devices_created"] >= 1

        # Verify vendor in DB
        vendor = test_db.query(models.CatalogVendor).filter(
            models.CatalogVendor.vendor_key == "myvendor"
        ).first()
        assert vendor is not None

        # Verify device in DB
        device = test_db.query(models.CatalogDeviceType).filter(
            models.CatalogDeviceType.canonical_device_id == "mydevice"
        ).first()
        assert device is not None
        assert device.display_name == "My Device"

    def test_creates_aliases(self, test_db: Session, monkeypatch):
        """Sync creates alias rows for device aliases."""
        self._patch_build(monkeypatch, vendor_configs={
            "mydevice": _fake_vendor_config(
                label="My Device",
                kind="my_kind",
                vendor="MyVendor",
                aliases=["myalias"],
            ),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()

        result = ensure_catalog_identity_synced(test_db, force=True)
        assert result["aliases_created"] >= 1

        alias = test_db.query(models.CatalogDeviceAlias).filter(
            models.CatalogDeviceAlias.alias == "myalias"
        ).first()
        assert alias is not None
        assert alias.is_active is True

    def test_deactivates_stale_aliases(self, test_db: Session, monkeypatch):
        """Aliases no longer desired get deactivated."""
        # First sync: device with alias
        self._patch_build(monkeypatch, vendor_configs={
            "mydevice": _fake_vendor_config(
                label="My Device",
                kind="my_kind",
                vendor="MyVendor",
                aliases=["oldalias"],
            ),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()
        ensure_catalog_identity_synced(test_db, force=True)

        alias = test_db.query(models.CatalogDeviceAlias).filter(
            models.CatalogDeviceAlias.alias == "oldalias"
        ).first()
        assert alias is not None
        assert alias.is_active is True

        # Second sync: device without that alias
        self._patch_build(monkeypatch, vendor_configs={
            "mydevice": _fake_vendor_config(
                label="My Device",
                kind="my_kind",
                vendor="MyVendor",
                aliases=[],
            ),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()
        ensure_catalog_identity_synced(test_db, force=True)

        test_db.refresh(alias)
        assert alias.is_active is False

    def test_cache_hit_skips_sync(self, test_db: Session, monkeypatch):
        """When stamp matches cached stamp, sync is skipped."""
        self._patch_build(monkeypatch, vendor_configs={
            "cached": _fake_vendor_config(label="Cached", vendor="V"),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()

        # First sync populates cache
        result1 = ensure_catalog_identity_synced(test_db, force=True)
        assert result1["applied"] is True

        # Second sync should be a cache hit (not forced)
        result2 = ensure_catalog_identity_synced(test_db, force=False)
        assert result2["applied"] is False
        assert result2["reason"] == "cache_hit"

    def test_force_ignores_cache(self, test_db: Session, monkeypatch):
        """force=True skips cache check and runs full sync."""
        self._patch_build(monkeypatch, vendor_configs={
            "forced": _fake_vendor_config(label="Forced", vendor="V"),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()

        # First sync
        ensure_catalog_identity_synced(test_db, force=True)

        # Forced second sync should not return cache_hit
        result = ensure_catalog_identity_synced(test_db, force=True)
        assert result["reason"] != "cache_hit"

    def test_tables_unavailable_returns_early(self, test_db: Session, monkeypatch):
        """When catalog tables are missing, returns early without error."""
        from app.services import catalog_service

        monkeypatch.setattr(
            catalog_service, "_catalog_tables_available", lambda session: False
        )
        result = ensure_catalog_identity_synced(test_db)
        assert result["applied"] is False
        assert result["reason"] == "catalog_tables_unavailable"

    def test_retires_removed_devices(self, test_db: Session, monkeypatch):
        """Devices removed from vendor configs get lifecycle_status=retired."""
        self._patch_build(monkeypatch, vendor_configs={
            "willretire": _fake_vendor_config(label="Will Retire", vendor="V"),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()
        ensure_catalog_identity_synced(test_db, force=True)

        device = test_db.query(models.CatalogDeviceType).filter(
            models.CatalogDeviceType.canonical_device_id == "willretire"
        ).first()
        assert device is not None
        assert device.lifecycle_status == "active"

        # Now remove the device from vendor configs
        self._patch_build(monkeypatch, vendor_configs={})
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()
        ensure_catalog_identity_synced(test_db, force=True)

        test_db.refresh(device)
        assert device.lifecycle_status == "retired"
