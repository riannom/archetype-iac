"""Extended tests for app.services.device_service.

Covers hardware spec resolution layers, device info lookup, override merging,
custom device CRUD edge cases, and get_image_runtime_metadata edge cases.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.services.device_service as device_service
from app.services.device_service import (
    DeviceConflictError,
    DeviceNotFoundError,
    DeviceService,
    DeviceValidationError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vendor_config(**overrides) -> SimpleNamespace:
    """Build a fake VendorConfig namespace with sensible defaults."""
    defaults = {
        "kind": "test_device",
        "vendor": "TestVendor",
        "label": "Test Device",
        "device_type": SimpleNamespace(value="router"),
        "icon": "fa-router",
        "versions": ["1.0"],
        "is_active": True,
        "port_naming": "Ethernet",
        "port_start_index": 0,
        "max_ports": 24,
        "memory": 4096,
        "cpu": 2,
        "disk_driver": "virtio",
        "nic_driver": "virtio-net-pci",
        "machine_type": "pc",
        "supported_image_kinds": ["qcow2"],
        "requires_image": True,
        "documentation_url": None,
        "license_required": False,
        "tags": [],
        "notes": None,
        "console_shell": None,
        "readiness_probe": None,
        "readiness_pattern": None,
        "readiness_timeout": 300,
        "efi_boot": None,
        "efi_vars": None,
        "data_volume_gb": None,
        "aliases": [],
        "management_interface": None,
        "reserved_nics": 0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Tests: get_config_by_device — fallback chain
# ---------------------------------------------------------------------------

class TestGetConfigByDevice:
    def test_returns_config_from_get_config_by_kind(self, monkeypatch) -> None:
        cfg = _make_vendor_config()
        monkeypatch.setattr(device_service, "_get_config_by_kind", lambda d: cfg)
        monkeypatch.setattr(device_service, "get_kind_for_device", lambda d: d)
        result = device_service.get_config_by_device("ceos")
        assert result is cfg

    def test_falls_back_to_canonical_kind(self, monkeypatch) -> None:
        cfg = _make_vendor_config()
        monkeypatch.setattr(
            device_service, "_get_config_by_kind",
            lambda d: cfg if d == "arista_ceos" else None,
        )
        monkeypatch.setattr(device_service, "get_kind_for_device", lambda d: "arista_ceos")
        result = device_service.get_config_by_device("ceos")
        assert result is cfg

    def test_returns_none_when_not_found(self, monkeypatch) -> None:
        monkeypatch.setattr(device_service, "_get_config_by_kind", lambda d: None)
        monkeypatch.setattr(device_service, "get_kind_for_device", lambda d: d)
        result = device_service.get_config_by_device("nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: get_image_runtime_metadata
# ---------------------------------------------------------------------------

class TestGetImageRuntimeMetadata:
    def test_none_reference_returns_empty(self) -> None:
        result = device_service.get_image_runtime_metadata(None)
        assert result == {}

    def test_empty_reference_returns_empty(self) -> None:
        result = device_service.get_image_runtime_metadata("")
        assert result == {}

    def test_returns_fields_from_manifest(self, monkeypatch) -> None:
        manifest = {
            "images": [
                {
                    "id": "qcow2:myimg.qcow2",
                    "reference": "/images/myimg.qcow2",
                    "memory_mb": 8192,
                    "cpu_count": 4,
                    "disk_driver": "sata",
                    "nic_driver": "e1000",
                    "boot_timeout": 600,
                    "efi_boot": True,
                    "efi_vars": "stateless",
                }
            ]
        }
        monkeypatch.setattr("app.image_store.load_manifest", lambda: manifest)
        result = device_service.get_image_runtime_metadata("/images/myimg.qcow2")
        assert result["memory"] == 8192
        assert result["cpu"] == 4
        assert result["readiness_timeout"] == 600
        assert result["efi_boot"] is True

    def test_handles_exception_gracefully(self, monkeypatch) -> None:
        monkeypatch.setattr("app.image_store.load_manifest", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        result = device_service.get_image_runtime_metadata("/images/broken.qcow2")
        assert result == {}

    def test_basename_matching(self, monkeypatch) -> None:
        manifest = {
            "images": [
                {
                    "id": "qcow2:vios.qcow2",
                    "reference": "/var/lib/archetype/images/vios.qcow2",
                    "memory_mb": 2048,
                }
            ]
        }
        monkeypatch.setattr("app.image_store.load_manifest", lambda: manifest)
        # Match by basename only
        result = device_service.get_image_runtime_metadata("vios.qcow2")
        assert result["memory"] == 2048


# ---------------------------------------------------------------------------
# Tests: DeviceService.resolve_hardware_specs — layered resolution
# ---------------------------------------------------------------------------

class TestResolveHardwareSpecs:
    def test_layer1_vendor_config(self, monkeypatch) -> None:
        service = DeviceService()
        cfg = _make_vendor_config(memory=8192, cpu=4, max_ports=32)
        monkeypatch.setattr(device_service, "_get_config_by_kind", lambda d: cfg)
        monkeypatch.setattr(device_service, "get_kind_for_device", lambda d: d)
        monkeypatch.setattr(device_service, "find_custom_device", lambda d: None)
        monkeypatch.setattr(device_service, "get_device_override", lambda d: {})
        monkeypatch.setattr(device_service, "get_image_runtime_metadata", lambda r: {})
        monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda d: d)
        monkeypatch.setattr("app.image_store.find_image_reference", lambda d, v=None: None)

        specs = service.resolve_hardware_specs("test_device")
        assert specs["memory"] == 8192
        assert specs["cpu"] == 4
        assert specs["max_ports"] == 32

    def test_layer1b_custom_device(self, monkeypatch) -> None:
        service = DeviceService()
        monkeypatch.setattr(device_service, "_get_config_by_kind", lambda d: None)
        monkeypatch.setattr(device_service, "get_kind_for_device", lambda d: d)
        monkeypatch.setattr(device_service, "find_custom_device", lambda d: {
            "id": "custom_dev", "memory": 2048, "cpu": 1, "maxPorts": 8,
        })
        monkeypatch.setattr(device_service, "get_device_override", lambda d: {})
        monkeypatch.setattr(device_service, "get_image_runtime_metadata", lambda r: {})
        monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda d: d)
        monkeypatch.setattr("app.image_store.find_image_reference", lambda d, v=None: None)

        specs = service.resolve_hardware_specs("custom_dev")
        assert specs["memory"] == 2048
        assert specs["cpu"] == 1
        assert specs["max_ports"] == 8

    def test_layer1c_image_meta_overrides_vendor(self, monkeypatch) -> None:
        service = DeviceService()
        cfg = _make_vendor_config(memory=4096, cpu=2)
        monkeypatch.setattr(device_service, "_get_config_by_kind", lambda d: cfg)
        monkeypatch.setattr(device_service, "get_kind_for_device", lambda d: d)
        monkeypatch.setattr(device_service, "find_custom_device", lambda d: None)
        monkeypatch.setattr(device_service, "get_device_override", lambda d: {})
        monkeypatch.setattr(device_service, "get_image_runtime_metadata", lambda r: {
            "memory": 8192, "cpu": 4
        })
        monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda d: d)
        monkeypatch.setattr("app.image_store.find_image_reference", lambda d, v=None: None)

        specs = service.resolve_hardware_specs("test_device", image_reference="/img.qcow2")
        assert specs["memory"] == 8192
        assert specs["cpu"] == 4

    def test_layer2_device_override(self, monkeypatch) -> None:
        service = DeviceService()
        cfg = _make_vendor_config(memory=4096, cpu=2)
        monkeypatch.setattr(device_service, "_get_config_by_kind", lambda d: cfg)
        monkeypatch.setattr(device_service, "get_kind_for_device", lambda d: d)
        monkeypatch.setattr(device_service, "find_custom_device", lambda d: None)
        monkeypatch.setattr(device_service, "get_device_override", lambda d: {"memory": 16384})
        monkeypatch.setattr(device_service, "get_image_runtime_metadata", lambda r: {})
        monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda d: d)
        monkeypatch.setattr("app.image_store.find_image_reference", lambda d, v=None: None)

        specs = service.resolve_hardware_specs("test_device")
        assert specs["memory"] == 16384
        assert specs["cpu"] == 2  # unchanged

    def test_layer3_node_config_overrides_all(self, monkeypatch) -> None:
        service = DeviceService()
        cfg = _make_vendor_config(memory=4096, cpu=2)
        monkeypatch.setattr(device_service, "_get_config_by_kind", lambda d: cfg)
        monkeypatch.setattr(device_service, "get_kind_for_device", lambda d: d)
        monkeypatch.setattr(device_service, "find_custom_device", lambda d: None)
        monkeypatch.setattr(device_service, "get_device_override", lambda d: {"memory": 8192})
        monkeypatch.setattr(device_service, "get_image_runtime_metadata", lambda r: {})
        monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda d: d)
        monkeypatch.setattr("app.image_store.find_image_reference", lambda d, v=None: None)

        specs = service.resolve_hardware_specs(
            "test_device",
            node_config_json={"memory": 32768, "cpu": 8},
        )
        assert specs["memory"] == 32768
        assert specs["cpu"] == 8

    def test_libvirt_driver_set_for_qcow2_device(self, monkeypatch) -> None:
        service = DeviceService()
        cfg = _make_vendor_config(supported_image_kinds=["qcow2"])
        monkeypatch.setattr(device_service, "_get_config_by_kind", lambda d: cfg)
        monkeypatch.setattr(device_service, "get_kind_for_device", lambda d: d)
        monkeypatch.setattr(device_service, "find_custom_device", lambda d: None)
        monkeypatch.setattr(device_service, "get_device_override", lambda d: {})
        monkeypatch.setattr(device_service, "get_image_runtime_metadata", lambda r: {})
        monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda d: d)
        monkeypatch.setattr("app.image_store.find_image_reference", lambda d, v=None: None)

        specs = service.resolve_hardware_specs("test_device")
        assert specs["libvirt_driver"] == "kvm"

    def test_vendor_probe_none_blocks_image_probe_override(self, monkeypatch) -> None:
        """When vendor config has readiness_probe='none', image metadata should not override it."""
        service = DeviceService()
        cfg = _make_vendor_config(readiness_probe="none")
        monkeypatch.setattr(device_service, "_get_config_by_kind", lambda d: cfg)
        monkeypatch.setattr(device_service, "get_kind_for_device", lambda d: d)
        monkeypatch.setattr(device_service, "find_custom_device", lambda d: None)
        monkeypatch.setattr(device_service, "get_device_override", lambda d: {})
        monkeypatch.setattr(device_service, "get_image_runtime_metadata", lambda r: {
            "readiness_probe": "pexpect",
            "readiness_pattern": "login:",
        })
        monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda d: d)
        monkeypatch.setattr("app.image_store.find_image_reference", lambda d, v=None: None)

        specs = service.resolve_hardware_specs("test_device", image_reference="/img.qcow2")
        assert specs["readiness_probe"] == "none"

    def test_data_volume_gb_from_vendor_config(self, monkeypatch) -> None:
        service = DeviceService()
        cfg = _make_vendor_config(data_volume_gb=10)
        monkeypatch.setattr(device_service, "_get_config_by_kind", lambda d: cfg)
        monkeypatch.setattr(device_service, "get_kind_for_device", lambda d: d)
        monkeypatch.setattr(device_service, "find_custom_device", lambda d: None)
        monkeypatch.setattr(device_service, "get_device_override", lambda d: {})
        monkeypatch.setattr(device_service, "get_image_runtime_metadata", lambda r: {})
        monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda d: d)
        monkeypatch.setattr("app.image_store.find_image_reference", lambda d, v=None: None)

        specs = service.resolve_hardware_specs("test_device")
        assert specs["data_volume_gb"] == 10


# ---------------------------------------------------------------------------
# Tests: DeviceService CRUD — edge cases
# ---------------------------------------------------------------------------

class TestDeviceServiceCrudEdge:
    def test_add_already_exists_as_custom(self, monkeypatch) -> None:
        service = DeviceService()
        monkeypatch.setattr(device_service, "get_config_by_device", lambda d: None)
        monkeypatch.setattr("app.image_store.find_custom_device", lambda d: {"id": d})
        monkeypatch.setattr("app.image_store.add_custom_device", lambda p: p)

        with pytest.raises(DeviceConflictError, match="already exists"):
            service.add_custom_device({"id": "existing", "name": "Existing"})

    def test_update_builtin_raises(self, monkeypatch) -> None:
        service = DeviceService()
        monkeypatch.setattr(device_service, "get_config_by_device", lambda d: object())

        with pytest.raises(DeviceValidationError, match="Cannot modify"):
            service.update_custom_device("builtin", {"name": "Changed"})

    def test_update_nonexistent_custom_raises(self, monkeypatch) -> None:
        service = DeviceService()
        monkeypatch.setattr(device_service, "get_config_by_device", lambda d: None)
        monkeypatch.setattr("app.image_store.find_custom_device", lambda d: None)

        with pytest.raises(DeviceNotFoundError):
            service.update_custom_device("ghost", {"name": "Ghost"})

    def test_delete_custom_device(self, monkeypatch) -> None:
        service = DeviceService()
        monkeypatch.setattr(device_service, "get_config_by_device", lambda d: None)
        monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda d: d)
        monkeypatch.setattr("app.image_store.get_device_image_count", lambda d: 0)
        monkeypatch.setattr("app.image_store.find_custom_device", lambda d: {"id": d})
        monkeypatch.setattr("app.image_store.delete_custom_device", lambda d: True)

        result = service.delete_device("my_custom")
        assert "deleted" in result["message"].lower()

    def test_delete_nonexistent_raises(self, monkeypatch) -> None:
        service = DeviceService()
        monkeypatch.setattr(device_service, "get_config_by_device", lambda d: None)
        monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda d: d)
        monkeypatch.setattr("app.image_store.get_device_image_count", lambda d: 0)
        monkeypatch.setattr("app.image_store.find_custom_device", lambda d: None)

        with pytest.raises(DeviceNotFoundError):
            service.delete_device("ghost")

    def test_hide_non_builtin_raises(self, monkeypatch) -> None:
        service = DeviceService()
        monkeypatch.setattr(device_service, "get_config_by_device", lambda d: None)
        monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda d: d)

        with pytest.raises(DeviceValidationError, match="Only built-in"):
            service.hide_device("custom_only")

    def test_hide_already_hidden_raises(self, monkeypatch) -> None:
        service = DeviceService()
        monkeypatch.setattr(device_service, "get_config_by_device", lambda d: object())
        monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda d: d)
        monkeypatch.setattr("app.image_store.is_device_hidden", lambda d: True)

        with pytest.raises(DeviceValidationError, match="already hidden"):
            service.hide_device("ceos")

    def test_restore_not_hidden_raises(self, monkeypatch) -> None:
        service = DeviceService()
        monkeypatch.setattr(device_service, "get_config_by_device", lambda d: object())
        monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda d: d)
        monkeypatch.setattr("app.image_store.is_device_hidden", lambda d: False)

        with pytest.raises(DeviceValidationError, match="not hidden"):
            service.restore_device("ceos")

    def test_restore_non_builtin_raises(self, monkeypatch) -> None:
        service = DeviceService()
        monkeypatch.setattr(device_service, "get_config_by_device", lambda d: None)
        monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda d: d)

        with pytest.raises(DeviceValidationError, match="Only built-in"):
            service.restore_device("custom_only")


# ---------------------------------------------------------------------------
# Tests: DeviceService.update_device_config edge cases
# ---------------------------------------------------------------------------

class TestUpdateDeviceConfig:
    def test_no_valid_fields_raises(self, monkeypatch) -> None:
        service = DeviceService()
        monkeypatch.setattr(device_service, "get_config_by_device", lambda d: object())
        monkeypatch.setattr("app.image_store.find_custom_device", lambda d: None)

        with pytest.raises(DeviceValidationError, match="No valid override"):
            service.update_device_config("ceos", {"invalid_field": 42})

    def test_filters_to_allowed_fields_only(self, monkeypatch) -> None:
        service = DeviceService()
        captured = {}

        monkeypatch.setattr(device_service, "get_config_by_device", lambda d: object())
        monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda d: d)
        monkeypatch.setattr("app.image_store.find_custom_device", lambda d: None)
        monkeypatch.setattr(
            "app.image_store.set_device_override",
            lambda d, p: captured.update({"payload": p}),
        )
        monkeypatch.setattr(
            service, "get_device_config",
            lambda d: {"base": {}, "overrides": {}, "effective": {}},
        )

        service.update_device_config("ceos", {"memory": 8192, "invalid": 999, "cpu": 4})
        assert "memory" in captured["payload"]
        assert "cpu" in captured["payload"]
        assert "invalid" not in captured["payload"]


# ---------------------------------------------------------------------------
# Tests: DeviceService.reset_device_config
# ---------------------------------------------------------------------------

class TestResetDeviceConfig:
    def test_reset_no_overrides(self, monkeypatch) -> None:
        service = DeviceService()
        monkeypatch.setattr(device_service, "get_config_by_device", lambda d: object())
        monkeypatch.setattr("app.image_store.canonicalize_device_id", lambda d: d)
        monkeypatch.setattr("app.image_store.delete_device_override", lambda d: False)
        monkeypatch.setattr("app.image_store.find_custom_device", lambda d: None)

        result = service.reset_device_config("ceos")
        assert "no overrides" in result["message"].lower()

    def test_reset_nonexistent_raises(self, monkeypatch) -> None:
        service = DeviceService()
        monkeypatch.setattr(device_service, "get_config_by_device", lambda d: None)
        monkeypatch.setattr("app.image_store.find_custom_device", lambda d: None)

        with pytest.raises(DeviceNotFoundError):
            service.reset_device_config("ghost")


# ---------------------------------------------------------------------------
# Tests: get_device_service singleton
# ---------------------------------------------------------------------------

class TestGetDeviceServiceSingleton:
    def test_returns_same_instance(self, monkeypatch) -> None:
        monkeypatch.setattr(device_service, "_device_service", None)
        s1 = device_service.get_device_service()
        s2 = device_service.get_device_service()
        assert s1 is s2
        assert isinstance(s1, DeviceService)

    def test_fresh_after_reset(self, monkeypatch) -> None:
        monkeypatch.setattr(device_service, "_device_service", None)
        s1 = device_service.get_device_service()
        monkeypatch.setattr(device_service, "_device_service", None)
        s2 = device_service.get_device_service()
        assert s1 is not s2
