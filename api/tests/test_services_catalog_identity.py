"""Tests for catalog identity module (services/catalog_identity.py).

This module tests:
- _register_alias: alias registration with rank-based overwrite logic
- _build_desired_catalog_identity_data: builds desired state from vendor configs and custom devices
- _catalog_identity_stamp: deterministic hash of desired state
- ensure_catalog_identity_synced: full identity sync lifecycle (create/update/retire/deactivate)
- _build_alias_index: builds AliasIndex from DB rows
- get_catalog_compatibility_aliases: returns canonical -> alias list mapping
- get_catalog_identity_map: returns full identity map for frontend consumption
- _resolve_token_to_canonical_set: resolves a token to all compatible canonical IDs
- resolve_catalog_device_id: resolves a token to a single canonical device ID
- resolve_catalog_compatible_device_set: resolves a token to a full compatibility family
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models
from app.services.catalog_identity import (
    DesiredCatalogDevice,
    _build_alias_index,
    _build_desired_catalog_identity_data,
    _catalog_identity_stamp,
    _register_alias,
    _resolve_token_to_canonical_set,
    ensure_catalog_identity_synced,
    get_catalog_compatibility_aliases,
    get_catalog_identity_map,
    resolve_catalog_compatible_device_set,
    resolve_catalog_device_id,
)
from app.services.catalog_service import (
    AliasIndex,
    CatalogAliasConflictError,
    _IDENTITY_SYNC_STAMP_BY_BIND,
)


# ---------------------------------------------------------------------------
# Helpers
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


def _make_desired(
    canonical="test",
    vendor="v",
    runtime_kind="kind",
    display_name="Test",
    device_class="router",
    source="builtin",
    aliases=None,
    memory_mb=None,
    cpu_count=None,
    max_ports=None,
):
    return DesiredCatalogDevice(
        canonical_device_id=canonical,
        vendor_key=vendor,
        runtime_kind=runtime_kind,
        display_name=display_name,
        device_class=device_class,
        source=source,
        lifecycle_status="active",
        metadata_json="{}",
        aliases=aliases or {},
        revision_runtime_kind=runtime_kind,
        revision_memory_mb=memory_mb,
        revision_cpu_count=cpu_count,
        revision_max_ports=max_ports,
        revision_supported_image_kinds_json="[]",
        revision_metadata_json="{}",
    )


def _seed_vendor(session: Session, vendor_key: str, display_name: str | None = None) -> models.CatalogVendor:
    vendor = models.CatalogVendor(
        id=str(uuid4()),
        vendor_key=vendor_key,
        display_name=display_name or vendor_key.title(),
        lifecycle_status="active",
        metadata_json="{}",
    )
    session.add(vendor)
    session.flush()
    return vendor


def _seed_device(
    session: Session,
    vendor: models.CatalogVendor,
    canonical_device_id: str,
    *,
    runtime_kind: str | None = None,
    source: str = "builtin",
    display_name: str | None = None,
) -> models.CatalogDeviceType:
    device = models.CatalogDeviceType(
        id=str(uuid4()),
        canonical_device_id=canonical_device_id,
        vendor_id=vendor.id,
        runtime_kind=runtime_kind,
        display_name=display_name or canonical_device_id,
        source=source,
        lifecycle_status="active",
        metadata_json="{}",
    )
    session.add(device)
    session.flush()
    return device


def _seed_alias(
    session: Session,
    device: models.CatalogDeviceType,
    alias: str,
    *,
    alias_type: str = "explicit",
    is_active: bool = True,
) -> models.CatalogDeviceAlias:
    alias_row = models.CatalogDeviceAlias(
        id=str(uuid4()),
        device_type_id=device.id,
        alias=alias,
        alias_type=alias_type,
        source="test",
        is_active=is_active,
    )
    session.add(alias_row)
    session.flush()
    return alias_row


def _patch_build(monkeypatch, vendor_configs=None, custom_devices=None, compat_aliases=None):
    """Set up monkeypatches for _build_desired_catalog_identity_data dependencies."""
    monkeypatch.setattr("agent.vendors.VENDOR_CONFIGS", vendor_configs or {})
    monkeypatch.setattr(
        "app.image_store.get_image_compatibility_aliases",
        lambda: compat_aliases or {},
    )
    monkeypatch.setattr(
        "app.image_store.load_custom_devices",
        lambda: custom_devices or [],
    )


# ============================================================================
# TestRegisterAlias
# ============================================================================


class TestRegisterAlias:
    """Tests for _register_alias rank-based alias registration."""

    def test_register_new_alias(self):
        alias_map: dict[str, str] = {}
        _register_alias(alias_map, "ceos", "arista_ceos", "explicit")
        assert alias_map["arista_ceos"] == "explicit"

    def test_skip_none_alias(self):
        alias_map: dict[str, str] = {}
        _register_alias(alias_map, "ceos", None, "explicit")
        assert len(alias_map) == 0

    def test_skip_empty_string_alias(self):
        alias_map: dict[str, str] = {}
        _register_alias(alias_map, "ceos", "   ", "explicit")
        assert len(alias_map) == 0

    def test_skip_self_alias(self):
        """Alias matching the canonical ID is ignored."""
        alias_map: dict[str, str] = {}
        _register_alias(alias_map, "ceos", "ceos", "explicit")
        assert "ceos" not in alias_map

    def test_normalizes_alias_to_lowercase(self):
        alias_map: dict[str, str] = {}
        _register_alias(alias_map, "ceos", "AristaCEOS", "explicit")
        assert "aristaceos" in alias_map

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

    def test_runtime_kind_beats_compatibility(self):
        """runtime_kind (rank 1) beats compatibility (rank 0)."""
        alias_map: dict[str, str] = {}
        _register_alias(alias_map, "ceos", "arista", "compatibility")
        _register_alias(alias_map, "ceos", "arista", "runtime_kind")
        assert alias_map["arista"] == "runtime_kind"

    def test_unknown_alias_type_has_negative_rank(self):
        """Unknown alias type gets rank -1 and can still register."""
        alias_map: dict[str, str] = {}
        _register_alias(alias_map, "ceos", "arista", "unknown_type")
        assert alias_map["arista"] == "unknown_type"


# ============================================================================
# TestBuildDesiredCatalogIdentityData
# ============================================================================


class TestBuildDesiredCatalogIdentityData:
    """Tests for _build_desired_catalog_identity_data."""

    def test_empty_configs_returns_empty(self, monkeypatch):
        _patch_build(monkeypatch)
        vendors, devices = _build_desired_catalog_identity_data()
        assert len(vendors) == 0
        assert len(devices) == 0

    def test_builds_vendor_from_config(self, monkeypatch):
        _patch_build(monkeypatch, vendor_configs={
            "ceos": _fake_vendor_config(vendor="Arista", label="cEOS"),
        })
        vendors, devices = _build_desired_catalog_identity_data()
        assert "arista" in vendors
        assert vendors["arista"]["display_name"] == "Arista"

    def test_builds_device_with_revision(self, monkeypatch):
        _patch_build(monkeypatch, vendor_configs={
            "ceos": _fake_vendor_config(
                vendor="Arista",
                label="cEOS",
                kind="ceos",
                memory=4096,
                cpu=2,
                max_ports=64,
                supported_image_kinds=["docker"],
            ),
        })
        vendors, devices = _build_desired_catalog_identity_data()
        assert "ceos" in devices
        d = devices["ceos"]
        assert d.display_name == "cEOS"
        assert d.revision_memory_mb == 4096
        assert d.revision_cpu_count == 2
        assert d.revision_max_ports == 64
        ik = json.loads(d.revision_supported_image_kinds_json)
        assert "docker" in ik

    def test_custom_device_added(self, monkeypatch):
        _patch_build(monkeypatch, custom_devices=[
            {"id": "custom1", "name": "My Custom", "vendor": "MyVendor", "kind": "custom1"},
        ])
        vendors, devices = _build_desired_catalog_identity_data()
        assert "custom1" in devices
        assert devices["custom1"].source == "custom"
        assert "myvendor" in vendors

    def test_builtin_wins_over_custom_same_id(self, monkeypatch):
        _patch_build(
            monkeypatch,
            vendor_configs={"overlap": _fake_vendor_config(label="Built-in", vendor="V")},
            custom_devices=[{"id": "overlap", "name": "Custom Overlap", "vendor": "CV"}],
        )
        _, devices = _build_desired_catalog_identity_data()
        assert devices["overlap"].source == "builtin"

    def test_compatibility_aliases_added(self, monkeypatch):
        _patch_build(
            monkeypatch,
            vendor_configs={"ceos": _fake_vendor_config(vendor="Arista", label="cEOS")},
            compat_aliases={"ceos": ["ceosimage", "arista_ceos"]},
        )
        _, devices = _build_desired_catalog_identity_data()
        aliases = devices["ceos"].aliases
        assert "ceosimage" in aliases
        assert aliases["ceosimage"] == "compatibility"
        assert "arista_ceos" in aliases

    def test_custom_device_without_kind_uses_id(self, monkeypatch):
        """Custom device without 'kind' should use canonical ID as runtime_kind."""
        _patch_build(monkeypatch, custom_devices=[
            {"id": "mything", "name": "My Thing", "vendor": "V"},
        ])
        _, devices = _build_desired_catalog_identity_data()
        assert devices["mything"].runtime_kind == "mything"

    def test_custom_device_skips_non_dict_entries(self, monkeypatch):
        _patch_build(monkeypatch, custom_devices=[
            "not a dict",
            42,
            None,
            {"id": "valid", "name": "Valid", "vendor": "V"},
        ])
        _, devices = _build_desired_catalog_identity_data()
        assert "valid" in devices
        assert len(devices) == 1

    def test_custom_device_skips_empty_id(self, monkeypatch):
        _patch_build(monkeypatch, custom_devices=[
            {"id": "", "name": "No ID", "vendor": "V"},
            {"id": "   ", "name": "Blank ID", "vendor": "V"},
        ])
        _, devices = _build_desired_catalog_identity_data()
        assert len(devices) == 0

    def test_multiple_vendors_from_different_devices(self, monkeypatch):
        _patch_build(monkeypatch, vendor_configs={
            "ceos": _fake_vendor_config(vendor="Arista", label="cEOS"),
            "srlinux": _fake_vendor_config(vendor="Nokia", label="SR Linux"),
        })
        vendors, devices = _build_desired_catalog_identity_data()
        assert "arista" in vendors
        assert "nokia" in vendors
        assert len(devices) == 2


# ============================================================================
# TestCatalogIdentityStamp
# ============================================================================


class TestCatalogIdentityStamp:
    """Tests for _catalog_identity_stamp determinism."""

    def test_deterministic_same_input(self):
        vendors = {"v": {"vendor_key": "v", "display_name": "V", "lifecycle_status": "active", "metadata_json": "{}"}}
        devices = {"test": _make_desired()}
        s1 = _catalog_identity_stamp(vendors, devices)
        s2 = _catalog_identity_stamp(vendors, devices)
        assert s1 == s2
        assert len(s1) == 64  # SHA-256 hex

    def test_different_devices_different_stamp(self):
        vendors = {"v": {"vendor_key": "v", "display_name": "V", "lifecycle_status": "active", "metadata_json": "{}"}}
        d1 = {"a": _make_desired(canonical="a")}
        d2 = {"b": _make_desired(canonical="b")}
        assert _catalog_identity_stamp(vendors, d1) != _catalog_identity_stamp(vendors, d2)

    def test_different_vendors_different_stamp(self):
        v1 = {"v1": {"vendor_key": "v1", "display_name": "V1", "lifecycle_status": "active", "metadata_json": "{}"}}
        v2 = {"v2": {"vendor_key": "v2", "display_name": "V2", "lifecycle_status": "active", "metadata_json": "{}"}}
        devices = {"test": _make_desired()}
        assert _catalog_identity_stamp(v1, devices) != _catalog_identity_stamp(v2, devices)

    def test_alias_order_does_not_affect_stamp(self):
        """Aliases are sorted before hashing, so order doesn't matter."""
        vendors = {"v": {"vendor_key": "v", "display_name": "V", "lifecycle_status": "active", "metadata_json": "{}"}}
        # DesiredCatalogDevice.aliases is a dict; sorted(items()) is used in stamp
        d1 = {"test": _make_desired(aliases={"a": "explicit", "b": "runtime_kind"})}
        d2 = {"test": _make_desired(aliases={"b": "runtime_kind", "a": "explicit"})}
        assert _catalog_identity_stamp(vendors, d1) == _catalog_identity_stamp(vendors, d2)

    def test_empty_input_produces_valid_hex(self):
        stamp = _catalog_identity_stamp({}, {})
        assert len(stamp) == 64
        int(stamp, 16)  # must be valid hex


# ============================================================================
# TestBuildAliasIndex
# ============================================================================


class TestBuildAliasIndex:
    """Tests for _build_alias_index from DB rows."""

    def test_empty_tables_returns_empty_index(self, test_db: Session):
        index = _build_alias_index(test_db)
        assert len(index.canonical_by_type_id) == 0
        assert len(index.type_id_by_canonical) == 0
        assert len(index.canonical_to_aliases) == 0
        assert len(index.alias_to_canonicals) == 0

    def test_device_with_runtime_kind_creates_bidirectional_mapping(self, test_db: Session):
        vendor = _seed_vendor(test_db, "arista")
        device = _seed_device(test_db, vendor, "ceos", runtime_kind="arista_ceos")
        test_db.commit()

        index = _build_alias_index(test_db)
        assert index.canonical_by_type_id[device.id] == "ceos"
        assert index.type_id_by_canonical["ceos"] == device.id
        assert "arista_ceos" in index.canonical_to_aliases["ceos"]
        assert "ceos" in index.alias_to_canonicals["arista_ceos"]

    def test_device_runtime_kind_same_as_canonical_not_aliased(self, test_db: Session):
        """When runtime_kind equals canonical_device_id, it should not appear as alias."""
        vendor = _seed_vendor(test_db, "v")
        _seed_device(test_db, vendor, "linux", runtime_kind="linux")
        test_db.commit()

        index = _build_alias_index(test_db)
        assert "linux" not in index.canonical_to_aliases.get("linux", set())

    def test_explicit_alias_rows_added(self, test_db: Session):
        vendor = _seed_vendor(test_db, "arista")
        device = _seed_device(test_db, vendor, "ceos")
        _seed_alias(test_db, device, "ceosimage")
        test_db.commit()

        index = _build_alias_index(test_db)
        assert "ceosimage" in index.canonical_to_aliases["ceos"]
        assert "ceos" in index.alias_to_canonicals["ceosimage"]

    def test_inactive_aliases_excluded(self, test_db: Session):
        vendor = _seed_vendor(test_db, "arista")
        device = _seed_device(test_db, vendor, "ceos")
        _seed_alias(test_db, device, "old_alias", is_active=False)
        test_db.commit()

        index = _build_alias_index(test_db)
        assert "old_alias" not in index.canonical_to_aliases.get("ceos", set())

    def test_multiple_devices_share_alias(self, test_db: Session):
        """An alias token can map to multiple canonical device IDs."""
        vendor = _seed_vendor(test_db, "cisco")
        dev_a = _seed_device(test_db, vendor, "iosv")
        dev_b = _seed_device(test_db, vendor, "iosvl2")
        _seed_alias(test_db, dev_a, "ios", alias_type="compatibility")
        _seed_alias(test_db, dev_b, "ios", alias_type="compatibility")
        test_db.commit()

        index = _build_alias_index(test_db)
        assert "iosv" in index.alias_to_canonicals["ios"]
        assert "iosvl2" in index.alias_to_canonicals["ios"]


# ============================================================================
# TestGetCatalogCompatibilityAliases
# ============================================================================


class TestGetCatalogCompatibilityAliases:
    """Tests for get_catalog_compatibility_aliases."""

    def test_returns_sorted_aliases(self, test_db: Session):
        vendor = _seed_vendor(test_db, "arista")
        device = _seed_device(test_db, vendor, "ceos", runtime_kind="arista_ceos")
        _seed_alias(test_db, device, "ceosimage")
        _seed_alias(test_db, device, "arista_eos")
        test_db.commit()

        result = get_catalog_compatibility_aliases(test_db)
        assert "ceos" in result
        aliases = result["ceos"]
        assert aliases == sorted(aliases)
        assert "ceosimage" in aliases
        assert "arista_eos" in aliases

    def test_empty_catalog_returns_empty(self, test_db: Session):
        result = get_catalog_compatibility_aliases(test_db)
        assert result == {}

    def test_device_without_aliases_excluded(self, test_db: Session):
        vendor = _seed_vendor(test_db, "v")
        _seed_device(test_db, vendor, "plain", runtime_kind="plain")  # same as canonical
        test_db.commit()

        result = get_catalog_compatibility_aliases(test_db)
        assert "plain" not in result


# ============================================================================
# TestGetCatalogIdentityMap
# ============================================================================


class TestGetCatalogIdentityMap:
    """Tests for get_catalog_identity_map."""

    def test_includes_canonical_to_runtime_kind(self, test_db: Session):
        vendor = _seed_vendor(test_db, "arista")
        _seed_device(test_db, vendor, "ceos", runtime_kind="arista_ceos")
        test_db.commit()

        result = get_catalog_identity_map(test_db)
        assert result["canonical_to_runtime_kind"]["ceos"] == "arista_ceos"

    def test_includes_alias_to_canonicals(self, test_db: Session):
        vendor = _seed_vendor(test_db, "arista")
        device = _seed_device(test_db, vendor, "ceos")
        _seed_alias(test_db, device, "ceosimage")
        test_db.commit()

        result = get_catalog_identity_map(test_db)
        assert "ceosimage" in result["alias_to_canonicals"]
        assert "ceos" in result["alias_to_canonicals"]["ceosimage"]

    def test_interface_aliases_canonical_self_maps(self, test_db: Session):
        vendor = _seed_vendor(test_db, "v")
        _seed_device(test_db, vendor, "linux")
        test_db.commit()

        result = get_catalog_identity_map(test_db)
        assert result["interface_aliases"]["linux"] == "linux"

    def test_interface_aliases_unambiguous_alias(self, test_db: Session):
        """An alias mapping to exactly one canonical should appear in interface_aliases."""
        vendor = _seed_vendor(test_db, "arista")
        device = _seed_device(test_db, vendor, "ceos")
        _seed_alias(test_db, device, "ceosimage")
        test_db.commit()

        result = get_catalog_identity_map(test_db)
        assert result["interface_aliases"]["ceosimage"] == "ceos"

    def test_interface_aliases_ambiguous_alias_excluded(self, test_db: Session):
        """An alias mapping to multiple canonicals should NOT be in interface_aliases."""
        vendor = _seed_vendor(test_db, "cisco")
        dev_a = _seed_device(test_db, vendor, "iosv")
        dev_b = _seed_device(test_db, vendor, "iosvl2")
        _seed_alias(test_db, dev_a, "ios")
        _seed_alias(test_db, dev_b, "ios")
        test_db.commit()

        result = get_catalog_identity_map(test_db)
        assert "ios" not in result["interface_aliases"]

    def test_empty_catalog_returns_empty_dicts(self, test_db: Session):
        result = get_catalog_identity_map(test_db)
        assert result["canonical_to_runtime_kind"] == {}
        assert result["canonical_to_aliases"] == {}
        assert result["alias_to_canonicals"] == {}
        assert result["interface_aliases"] == {}


# ============================================================================
# TestResolveTokenToCanonicalSet
# ============================================================================


class TestResolveTokenToCanonicalSet:
    """Tests for _resolve_token_to_canonical_set."""

    def _build_index(self, test_db: Session) -> AliasIndex:
        return _build_alias_index(test_db)

    def test_resolves_canonical_directly(self, test_db: Session):
        vendor = _seed_vendor(test_db, "v")
        _seed_device(test_db, vendor, "ceos")
        test_db.commit()

        index = self._build_index(test_db)
        result = _resolve_token_to_canonical_set(index, "ceos")
        assert "ceos" in result

    def test_resolves_alias_to_canonical(self, test_db: Session):
        vendor = _seed_vendor(test_db, "v")
        device = _seed_device(test_db, vendor, "ceos")
        _seed_alias(test_db, device, "ceosimage")
        test_db.commit()

        index = self._build_index(test_db)
        result = _resolve_token_to_canonical_set(index, "ceosimage")
        assert "ceos" in result

    def test_resolves_shared_alias_to_family(self, test_db: Session):
        """A shared alias should resolve to all canonical IDs in the family."""
        vendor = _seed_vendor(test_db, "v")
        dev_a = _seed_device(test_db, vendor, "ceos")
        dev_b = _seed_device(test_db, vendor, "ceos_lab")
        _seed_alias(test_db, dev_a, "arista")
        _seed_alias(test_db, dev_b, "arista")
        test_db.commit()

        index = self._build_index(test_db)
        result = _resolve_token_to_canonical_set(index, "arista")
        assert "ceos" in result
        assert "ceos_lab" in result

    def test_none_returns_empty_set(self, test_db: Session):
        index = self._build_index(test_db)
        result = _resolve_token_to_canonical_set(index, None)
        assert result == set()

    def test_unknown_token_returns_empty_set(self, test_db: Session):
        index = self._build_index(test_db)
        result = _resolve_token_to_canonical_set(index, "nonexistent")
        assert result == set()


# ============================================================================
# TestResolveCatalogDeviceId
# ============================================================================


class TestResolveCatalogDeviceId:
    """Tests for resolve_catalog_device_id."""

    def test_resolves_canonical_directly(self, test_db: Session):
        vendor = _seed_vendor(test_db, "v")
        _seed_device(test_db, vendor, "ceos")
        test_db.commit()

        result = resolve_catalog_device_id(test_db, "ceos")
        assert result == "ceos"

    def test_resolves_alias_to_single_canonical(self, test_db: Session):
        vendor = _seed_vendor(test_db, "v")
        device = _seed_device(test_db, vendor, "ceos")
        _seed_alias(test_db, device, "ceosimage")
        test_db.commit()

        result = resolve_catalog_device_id(test_db, "ceosimage")
        assert result == "ceos"

    def test_ambiguous_alias_raises_conflict(self, test_db: Session):
        """An alias mapping to multiple canonical IDs raises CatalogAliasConflictError."""
        vendor = _seed_vendor(test_db, "v")
        dev_a = _seed_device(test_db, vendor, "iosv")
        dev_b = _seed_device(test_db, vendor, "iosvl2")
        _seed_alias(test_db, dev_a, "ios")
        _seed_alias(test_db, dev_b, "ios")
        test_db.commit()

        with pytest.raises(CatalogAliasConflictError, match="maps to multiple"):
            resolve_catalog_device_id(test_db, "ios")

    def test_unknown_with_allow_unknown_returns_normalized(self, test_db: Session):
        result = resolve_catalog_device_id(test_db, "UnknownDevice", allow_unknown=True)
        assert result == "unknowndevice"

    def test_unknown_with_disallow_unknown_returns_none(self, test_db: Session):
        result = resolve_catalog_device_id(test_db, "unknowndevice", allow_unknown=False)
        assert result is None

    def test_none_input_returns_none(self, test_db: Session):
        result = resolve_catalog_device_id(test_db, None)
        assert result is None

    def test_empty_string_returns_none(self, test_db: Session):
        result = resolve_catalog_device_id(test_db, "  ")
        assert result is None

    def test_case_insensitive_resolution(self, test_db: Session):
        vendor = _seed_vendor(test_db, "v")
        _seed_device(test_db, vendor, "ceos")
        test_db.commit()

        result = resolve_catalog_device_id(test_db, "CEOS")
        assert result == "ceos"


# ============================================================================
# TestResolveCatalogCompatibleDeviceSet
# ============================================================================


class TestResolveCatalogCompatibleDeviceSet:
    """Tests for resolve_catalog_compatible_device_set."""

    def test_single_device_returns_itself(self, test_db: Session):
        vendor = _seed_vendor(test_db, "v")
        _seed_device(test_db, vendor, "ceos")
        test_db.commit()

        result = resolve_catalog_compatible_device_set(test_db, "ceos")
        assert "ceos" in result

    def test_alias_resolves_full_family(self, test_db: Session):
        vendor = _seed_vendor(test_db, "v")
        dev_a = _seed_device(test_db, vendor, "ceos")
        dev_b = _seed_device(test_db, vendor, "ceos_lab")
        _seed_alias(test_db, dev_a, "arista")
        _seed_alias(test_db, dev_b, "arista")
        test_db.commit()

        result = resolve_catalog_compatible_device_set(test_db, "arista")
        assert "ceos" in result
        assert "ceos_lab" in result

    def test_unknown_returns_empty(self, test_db: Session):
        result = resolve_catalog_compatible_device_set(test_db, "nonexistent")
        assert result == set()

    def test_none_returns_empty(self, test_db: Session):
        result = resolve_catalog_compatible_device_set(test_db, None)
        assert result == set()


# ============================================================================
# TestEnsureCatalogIdentitySynced
# ============================================================================


class TestEnsureCatalogIdentitySynced:
    """Tests for ensure_catalog_identity_synced full lifecycle."""

    def test_creates_vendor_and_device_on_first_sync(self, test_db: Session, monkeypatch):
        _patch_build(monkeypatch, vendor_configs={
            "mydevice": _fake_vendor_config(
                label="My Device",
                kind="my_kind",
                vendor="MyVendor",
                max_ports=4,
            ),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()

        result = ensure_catalog_identity_synced(test_db, force=True)
        assert result["applied"] is True
        assert result["vendors_created"] >= 1
        assert result["devices_created"] >= 1

        vendor = test_db.query(models.CatalogVendor).filter(
            models.CatalogVendor.vendor_key == "myvendor"
        ).first()
        assert vendor is not None

        device = test_db.query(models.CatalogDeviceType).filter(
            models.CatalogDeviceType.canonical_device_id == "mydevice"
        ).first()
        assert device is not None
        assert device.display_name == "My Device"

    def test_creates_revision_on_first_sync(self, test_db: Session, monkeypatch):
        _patch_build(monkeypatch, vendor_configs={
            "rdev": _fake_vendor_config(
                label="Rev Device",
                vendor="V",
                memory=2048,
                cpu=1,
                max_ports=8,
            ),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()

        result = ensure_catalog_identity_synced(test_db, force=True)
        assert result["revisions_created"] >= 1

        device = test_db.query(models.CatalogDeviceType).filter(
            models.CatalogDeviceType.canonical_device_id == "rdev"
        ).first()
        revision = test_db.query(models.CatalogDeviceRevision).filter(
            models.CatalogDeviceRevision.device_type_id == device.id,
            models.CatalogDeviceRevision.version_tag == "current",
        ).first()
        assert revision is not None
        assert revision.memory_mb == 2048
        assert revision.cpu_count == 1
        assert revision.max_ports == 8
        assert revision.is_current is True

    def test_creates_aliases(self, test_db: Session, monkeypatch):
        _patch_build(monkeypatch, vendor_configs={
            "mydevice": _fake_vendor_config(
                label="My Device",
                kind="my_kind",
                vendor="V",
                aliases=["myalias", "otheralias"],
            ),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()

        result = ensure_catalog_identity_synced(test_db, force=True)
        assert result["aliases_created"] >= 2

        aliases = test_db.query(models.CatalogDeviceAlias).filter(
            models.CatalogDeviceAlias.alias.in_(["myalias", "otheralias"])
        ).all()
        assert len(aliases) == 2
        for alias in aliases:
            assert alias.is_active is True

    def test_deactivates_stale_aliases(self, test_db: Session, monkeypatch):
        # First sync with alias
        _patch_build(monkeypatch, vendor_configs={
            "mydevice": _fake_vendor_config(
                label="My Device", kind="my_kind", vendor="V", aliases=["oldalias"],
            ),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()
        ensure_catalog_identity_synced(test_db, force=True)

        alias = test_db.query(models.CatalogDeviceAlias).filter(
            models.CatalogDeviceAlias.alias == "oldalias"
        ).first()
        assert alias.is_active is True

        # Second sync without that alias
        _patch_build(monkeypatch, vendor_configs={
            "mydevice": _fake_vendor_config(
                label="My Device", kind="my_kind", vendor="V", aliases=[],
            ),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()
        ensure_catalog_identity_synced(test_db, force=True)

        test_db.refresh(alias)
        assert alias.is_active is False

    def test_retires_removed_devices(self, test_db: Session, monkeypatch):
        _patch_build(monkeypatch, vendor_configs={
            "willretire": _fake_vendor_config(label="Will Retire", vendor="V"),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()
        ensure_catalog_identity_synced(test_db, force=True)

        device = test_db.query(models.CatalogDeviceType).filter(
            models.CatalogDeviceType.canonical_device_id == "willretire"
        ).first()
        assert device.lifecycle_status == "active"

        # Remove the device from configs
        _patch_build(monkeypatch)
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()
        ensure_catalog_identity_synced(test_db, force=True)

        test_db.refresh(device)
        assert device.lifecycle_status == "retired"

    def test_cache_hit_skips_sync(self, test_db: Session, monkeypatch):
        _patch_build(monkeypatch, vendor_configs={
            "cached": _fake_vendor_config(label="Cached", vendor="V"),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()

        result1 = ensure_catalog_identity_synced(test_db, force=True)
        assert result1["applied"] is True

        result2 = ensure_catalog_identity_synced(test_db, force=False)
        assert result2["applied"] is False
        assert result2["reason"] == "cache_hit"

    def test_force_ignores_cache(self, test_db: Session, monkeypatch):
        _patch_build(monkeypatch, vendor_configs={
            "forced": _fake_vendor_config(label="Forced", vendor="V"),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()

        ensure_catalog_identity_synced(test_db, force=True)
        result = ensure_catalog_identity_synced(test_db, force=True)
        assert result["reason"] != "cache_hit"

    def test_tables_unavailable_returns_early(self, test_db: Session, monkeypatch):
        from app.services import catalog_identity as _ci_mod

        monkeypatch.setattr(_ci_mod, "_catalog_tables_available", lambda session: False)
        result = ensure_catalog_identity_synced(test_db)
        assert result["applied"] is False
        assert result["reason"] == "catalog_tables_unavailable"

    def test_updates_existing_device_on_attribute_change(self, test_db: Session, monkeypatch):
        """Re-sync with changed display_name should update the existing row."""
        _patch_build(monkeypatch, vendor_configs={
            "evolving": _fake_vendor_config(label="Old Name", vendor="V"),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()
        ensure_catalog_identity_synced(test_db, force=True)

        device = test_db.query(models.CatalogDeviceType).filter(
            models.CatalogDeviceType.canonical_device_id == "evolving"
        ).first()
        assert device.display_name == "Old Name"

        _patch_build(monkeypatch, vendor_configs={
            "evolving": _fake_vendor_config(label="New Name", vendor="V"),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()
        result = ensure_catalog_identity_synced(test_db, force=True)
        assert result["devices_updated"] >= 1

        test_db.refresh(device)
        assert device.display_name == "New Name"

    def test_updates_revision_on_spec_change(self, test_db: Session, monkeypatch):
        """Changed memory/cpu specs should update the current revision."""
        _patch_build(monkeypatch, vendor_configs={
            "specdev": _fake_vendor_config(label="Spec", vendor="V", memory=1024, cpu=1),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()
        ensure_catalog_identity_synced(test_db, force=True)

        device = test_db.query(models.CatalogDeviceType).filter(
            models.CatalogDeviceType.canonical_device_id == "specdev"
        ).first()
        rev = test_db.query(models.CatalogDeviceRevision).filter(
            models.CatalogDeviceRevision.device_type_id == device.id,
        ).first()
        assert rev.memory_mb == 1024

        _patch_build(monkeypatch, vendor_configs={
            "specdev": _fake_vendor_config(label="Spec", vendor="V", memory=4096, cpu=2),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()
        result = ensure_catalog_identity_synced(test_db, force=True)
        assert result["revisions_updated"] >= 1

        test_db.refresh(rev)
        assert rev.memory_mb == 4096
        assert rev.cpu_count == 2

    def test_idempotent_no_changes(self, test_db: Session, monkeypatch):
        """Running sync twice with same data should report no changes on second run."""
        _patch_build(monkeypatch, vendor_configs={
            "stable": _fake_vendor_config(label="Stable", vendor="V"),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()
        ensure_catalog_identity_synced(test_db, force=True)

        _IDENTITY_SYNC_STAMP_BY_BIND.clear()
        result = ensure_catalog_identity_synced(test_db, force=True)
        assert result["applied"] is False
        assert result["reason"] == "already_current"

    def test_records_ingest_event_on_changes(self, test_db: Session, monkeypatch):
        """Sync should create a CatalogIngestEvent when changes are made."""
        _patch_build(monkeypatch, vendor_configs={
            "eventdev": _fake_vendor_config(label="Event Device", vendor="V"),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()
        ensure_catalog_identity_synced(test_db, force=True)

        events = test_db.query(models.CatalogIngestEvent).filter(
            models.CatalogIngestEvent.event_type == "identity_sync"
        ).all()
        assert len(events) >= 1

    def test_custom_source_recorded(self, test_db: Session, monkeypatch):
        """Custom source parameter should be stored on alias rows."""
        _patch_build(monkeypatch, vendor_configs={
            "srcdev": _fake_vendor_config(label="Src", vendor="V", aliases=["srcalias"]),
        })
        _IDENTITY_SYNC_STAMP_BY_BIND.clear()
        ensure_catalog_identity_synced(test_db, force=True, source="custom_sync")

        alias = test_db.query(models.CatalogDeviceAlias).filter(
            models.CatalogDeviceAlias.alias == "srcalias"
        ).first()
        assert alias is not None
        assert alias.source == "custom_sync"
