"""Unit tests for app.image_store.metadata module and related runtime metadata.

Directly tests:
- ImageMetadata dataclass and to_entry()
- create_image_entry() — field mapping, canonicalization, vendor resolution
- update_image_entry() — field updates, device_id normalization, default handling
- delete_image_entry() — removal from manifest
- get_image_runtime_metadata() (device_service) — hw_specs resolution, boot_timeout mapping
- Handling unknown device types
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.image_store.metadata import (
    ImageMetadata,
    create_image_entry,
    delete_image_entry,
    update_image_entry,
)


@pytest.fixture(autouse=True)
def _isolate_image_store(tmp_path, monkeypatch):
    """Redirect image store paths to tmp_path and bypass catalog DB mode."""
    monkeypatch.setattr("app.image_store.paths.ensure_image_store", lambda: tmp_path)
    monkeypatch.setattr("app.image_store.paths.image_store_root", lambda: tmp_path)
    monkeypatch.setattr(
        "app.image_store.manifest.manifest_path", lambda: tmp_path / "manifest.json"
    )
    monkeypatch.setattr(
        "app.services.catalog_service.catalog_is_seeded",
        lambda _session: False,
    )


class TestImageMetadata:
    """Tests for the ImageMetadata dataclass."""

    def test_basic_construction(self):
        """Construct ImageMetadata with required fields."""
        meta = ImageMetadata(
            image_id="docker:ceos:4.28",
            kind="docker",
            reference="ceos:4.28",
            filename="ceos.tar",
        )
        assert meta.image_id == "docker:ceos:4.28"
        assert meta.kind == "docker"
        assert meta.device_id is None
        assert meta.memory_mb is None

    def test_optional_vm_hints(self):
        """Optional VM runtime hints are stored correctly."""
        meta = ImageMetadata(
            image_id="qcow2:iosv",
            kind="qcow2",
            reference="/images/iosv.qcow2",
            filename="iosv.qcow2",
            device_id="cisco_iosv",
            memory_mb=2048,
            cpu_count=2,
            disk_driver="virtio",
            nic_driver="e1000",
            boot_timeout=300,
            efi_boot=True,
            efi_vars="stateless",
        )
        assert meta.memory_mb == 2048
        assert meta.cpu_count == 2
        assert meta.efi_boot is True
        assert meta.efi_vars == "stateless"

    def test_to_entry_returns_dict(self):
        """to_entry() delegates to create_image_entry and returns a dict."""
        meta = ImageMetadata(
            image_id="docker:linux:latest",
            kind="docker",
            reference="linux:latest",
            filename="linux.tar",
            device_id="linux",
        )
        entry = meta.to_entry()
        assert isinstance(entry, dict)
        assert entry["id"] == "docker:linux:latest"
        assert entry["device_id"] == "linux"
        assert "uploaded_at" in entry

    def test_to_entry_passes_runtime_hints(self):
        """to_entry() passes VM runtime hints through to the entry dict."""
        meta = ImageMetadata(
            image_id="qcow2:n9kv",
            kind="qcow2",
            reference="/images/n9kv.qcow2",
            filename="n9kv.qcow2",
            device_id="cisco_n9kv",
            memory_mb=12288,
            cpu_count=4,
            boot_timeout=600,
            max_ports=65,
        )
        entry = meta.to_entry()
        assert entry["memory_mb"] == 12288
        assert entry["cpu_count"] == 4
        assert entry["boot_timeout"] == 600
        assert entry["max_ports"] == 65


class TestCreateImageEntry:
    """Tests for create_image_entry()."""

    def test_required_fields(self):
        """Entry has all required fields populated."""
        entry = create_image_entry(
            image_id="docker:test:1.0",
            kind="docker",
            reference="test:1.0",
            filename="test.tar",
        )
        assert entry["id"] == "docker:test:1.0"
        assert entry["kind"] == "docker"
        assert entry["reference"] == "test:1.0"
        assert entry["filename"] == "test.tar"
        assert entry["is_default"] is False
        assert entry["default_for_devices"] == []

    def test_canonicalize_device_id(self):
        """Legacy device IDs are canonicalized (iosv -> cisco_iosv)."""
        entry = create_image_entry(
            image_id="qcow2:vios",
            kind="qcow2",
            reference="/images/vios.qcow2",
            filename="vios.qcow2",
            device_id="iosv",
        )
        assert entry["device_id"] == "cisco_iosv"
        assert "cisco_iosv" in entry["compatible_devices"]

    def test_vendor_set_for_known_device(self):
        """Vendor field is set based on canonical device ID."""
        entry = create_image_entry(
            image_id="docker:ceos:4.28",
            kind="docker",
            reference="ceos:4.28",
            filename="ceos.tar",
            device_id="ceos",
        )
        assert entry["vendor"] == "Arista"

    def test_vendor_none_for_unknown_device(self):
        """Vendor is None when device_id is not set."""
        entry = create_image_entry(
            image_id="docker:custom:1.0",
            kind="docker",
            reference="custom:1.0",
            filename="custom.tar",
        )
        assert entry["vendor"] is None

    def test_device_id_added_to_compatible_devices(self):
        """device_id is always included in compatible_devices."""
        entry = create_image_entry(
            image_id="docker:test",
            kind="docker",
            reference="test:latest",
            filename="test.tar",
            device_id="linux",
            compatible_devices=[],
        )
        assert "linux" in entry["compatible_devices"]

    def test_compatible_devices_deduplication(self):
        """Duplicate compatible_devices are not created."""
        entry = create_image_entry(
            image_id="docker:test",
            kind="docker",
            reference="test:latest",
            filename="test.tar",
            device_id="linux",
            compatible_devices=["linux", "linux"],
        )
        assert entry["compatible_devices"].count("linux") == 1

    def test_sha256_stored(self):
        """SHA256 hash is stored in the entry."""
        entry = create_image_entry(
            image_id="docker:test",
            kind="docker",
            reference="test:latest",
            filename="test.tar",
            sha256="abc123",
        )
        assert entry["sha256"] == "abc123"

    def test_all_runtime_hints_stored(self):
        """All optional runtime hint fields are stored."""
        entry = create_image_entry(
            image_id="qcow2:test",
            kind="qcow2",
            reference="/images/test.qcow2",
            filename="test.qcow2",
            memory_mb=4096,
            cpu_count=2,
            disk_driver="sata",
            nic_driver="virtio",
            machine_type="pc-q35",
            libvirt_driver="kvm",
            boot_timeout=300,
            readiness_probe="log_pattern",
            readiness_pattern="login:",
            efi_boot=True,
            efi_vars="stateful",
            max_ports=16,
            port_naming="GigabitEthernet",
            cpu_limit=100,
            has_loopback=True,
            provisioning_driver="iosv",
            provisioning_media_type="cdrom",
        )
        assert entry["memory_mb"] == 4096
        assert entry["cpu_count"] == 2
        assert entry["disk_driver"] == "sata"
        assert entry["nic_driver"] == "virtio"
        assert entry["machine_type"] == "pc-q35"
        assert entry["libvirt_driver"] == "kvm"
        assert entry["boot_timeout"] == 300
        assert entry["readiness_probe"] == "log_pattern"
        assert entry["readiness_pattern"] == "login:"
        assert entry["efi_boot"] is True
        assert entry["efi_vars"] == "stateful"
        assert entry["max_ports"] == 16
        assert entry["port_naming"] == "GigabitEthernet"
        assert entry["cpu_limit"] == 100
        assert entry["has_loopback"] is True
        assert entry["provisioning_driver"] == "iosv"
        assert entry["provisioning_media_type"] == "cdrom"


class TestUpdateImageEntry:
    """Tests for update_image_entry()."""

    def _make_manifest(self, **overrides):
        """Create a minimal manifest with one image entry."""
        base = {
            "id": "docker:test:1.0",
            "kind": "docker",
            "device_id": "linux",
            "version": "1.0",
            "compatible_devices": ["linux"],
            "is_default": False,
            "default_for_devices": [],
        }
        base.update(overrides)
        return {"images": [base]}

    def test_update_notes(self):
        """Updating notes preserves other fields."""
        manifest = self._make_manifest()
        updated = update_image_entry(manifest, "docker:test:1.0", {"notes": "Updated"})
        assert updated is not None
        assert updated["notes"] == "Updated"
        assert updated["version"] == "1.0"

    def test_update_device_id_canonicalizes(self):
        """Updating device_id canonicalizes the value."""
        manifest = self._make_manifest()
        updated = update_image_entry(
            manifest, "docker:test:1.0", {"device_id": "eos"}
        )
        assert updated is not None
        assert updated["device_id"] == "ceos"

    def test_update_device_id_sets_vendor(self):
        """Updating device_id also sets the vendor field."""
        manifest = self._make_manifest()
        updated = update_image_entry(
            manifest, "docker:test:1.0", {"device_id": "ceos"}
        )
        assert updated is not None
        assert updated["vendor"] == "Arista"

    def test_update_nonexistent_returns_none(self):
        """Updating a non-existent image returns None."""
        manifest = {"images": []}
        result = update_image_entry(manifest, "missing", {"notes": "x"})
        assert result is None

    def test_update_compatible_devices_canonicalized(self):
        """Compatible devices are canonicalized on update."""
        manifest = self._make_manifest()
        updated = update_image_entry(
            manifest,
            "docker:test:1.0",
            {"compatible_devices": ["iosv", "ceos"]},
        )
        assert updated is not None
        assert "cisco_iosv" in updated["compatible_devices"]
        assert "ceos" in updated["compatible_devices"]

    def test_set_is_default_true(self):
        """Setting is_default=True populates default_for_devices."""
        manifest = self._make_manifest()
        updated = update_image_entry(
            manifest, "docker:test:1.0", {"is_default": True}
        )
        assert updated is not None
        assert updated["is_default"] is True
        assert "linux" in updated["default_for_devices"]

    def test_set_is_default_false_clears_scope(self):
        """Setting is_default=False removes the scope from default_for_devices."""
        manifest = self._make_manifest(
            is_default=True, default_for_devices=["linux"]
        )
        updated = update_image_entry(
            manifest, "docker:test:1.0", {"is_default": False}
        )
        assert updated is not None
        assert updated["is_default"] is False
        assert "linux" not in updated.get("default_for_devices", [])

    def test_update_replaces_default_in_other_images(self):
        """Setting default for a device removes it from other images."""
        manifest = {
            "images": [
                {
                    "id": "img-a",
                    "kind": "docker",
                    "device_id": "linux",
                    "compatible_devices": ["linux"],
                    "is_default": True,
                    "default_for_devices": ["linux"],
                },
                {
                    "id": "img-b",
                    "kind": "docker",
                    "device_id": "linux",
                    "compatible_devices": ["linux"],
                    "is_default": False,
                    "default_for_devices": [],
                },
            ]
        }
        update_image_entry(manifest, "img-b", {"is_default": True})

        img_a = next(i for i in manifest["images"] if i["id"] == "img-a")
        img_b = next(i for i in manifest["images"] if i["id"] == "img-b")
        assert "linux" not in img_a["default_for_devices"]
        assert img_a["is_default"] is False
        assert "linux" in img_b["default_for_devices"]
        assert img_b["is_default"] is True


class TestDeleteImageEntry:
    """Tests for delete_image_entry()."""

    def test_delete_existing(self):
        manifest = {"images": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
        deleted = delete_image_entry(manifest, "b")
        assert deleted is not None
        assert deleted["id"] == "b"
        assert len(manifest["images"]) == 2
        assert all(i["id"] != "b" for i in manifest["images"])

    def test_delete_missing_returns_none(self):
        manifest = {"images": [{"id": "a"}]}
        result = delete_image_entry(manifest, "nonexistent")
        assert result is None
        assert len(manifest["images"]) == 1

    def test_delete_from_empty_manifest(self):
        manifest = {"images": []}
        result = delete_image_entry(manifest, "any")
        assert result is None


class TestGetImageRuntimeMetadata:
    """Tests for get_image_runtime_metadata() in device_service.

    This function resolves image manifest entries to hw_specs dicts
    used by the deployment pipeline.
    """

    def test_none_reference_returns_empty(self):
        from app.services.device_service import get_image_runtime_metadata

        assert get_image_runtime_metadata(None) == {}

    def test_empty_reference_returns_empty(self):
        from app.services.device_service import get_image_runtime_metadata

        assert get_image_runtime_metadata("") == {}

    def test_resolves_by_reference(self, tmp_path):
        """Finds image by reference and returns mapped hw_specs."""
        from app.image_store.manifest import save_manifest
        from app.services.device_service import get_image_runtime_metadata

        save_manifest(
            {
                "images": [
                    {
                        "id": "qcow2:n9kv",
                        "kind": "qcow2",
                        "reference": "/var/lib/archetype/images/n9kv.qcow2",
                        "filename": "n9kv.qcow2",
                        "device_id": "cisco_n9kv",
                        "compatible_devices": ["cisco_n9kv"],
                        "memory_mb": 12288,
                        "cpu_count": 4,
                        "boot_timeout": 480,
                        "disk_driver": "sata",
                        "nic_driver": "e1000",
                        "efi_boot": True,
                        "efi_vars": "stateless",
                        "is_default": False,
                        "default_for_devices": [],
                    }
                ]
            }
        )

        result = get_image_runtime_metadata(
            "/var/lib/archetype/images/n9kv.qcow2"
        )
        assert result["memory"] == 12288
        assert result["cpu"] == 4
        assert result["readiness_timeout"] == 480  # boot_timeout maps here
        assert result["disk_driver"] == "sata"
        assert result["nic_driver"] == "e1000"
        assert result["efi_boot"] is True
        assert result["efi_vars"] == "stateless"

    def test_resolves_by_id(self, tmp_path):
        """Falls back to find_image_by_id when reference doesn't match."""
        from app.image_store.manifest import save_manifest
        from app.services.device_service import get_image_runtime_metadata

        save_manifest(
            {
                "images": [
                    {
                        "id": "qcow2:iosv",
                        "kind": "qcow2",
                        "reference": "/images/iosv.qcow2",
                        "filename": "iosv.qcow2",
                        "device_id": "cisco_iosv",
                        "compatible_devices": ["cisco_iosv"],
                        "memory_mb": 2048,
                        "cpu_count": 1,
                        "is_default": False,
                        "default_for_devices": [],
                    }
                ]
            }
        )

        result = get_image_runtime_metadata("qcow2:iosv")
        assert result["memory"] == 2048
        assert result["cpu"] == 1

    def test_unknown_reference_returns_nones(self, tmp_path):
        """Unknown reference returns dict with None values (not empty)."""
        from app.image_store.manifest import save_manifest
        from app.services.device_service import get_image_runtime_metadata

        save_manifest({"images": []})

        result = get_image_runtime_metadata("nonexistent:image")
        # All values should be None since no image matched
        assert result.get("memory") is None
        assert result.get("cpu") is None
        assert result.get("readiness_timeout") is None

    def test_boot_timeout_mapped_to_readiness_timeout(self, tmp_path):
        """Image manifest boot_timeout is exposed as readiness_timeout in hw_specs."""
        from app.image_store.manifest import save_manifest
        from app.services.device_service import get_image_runtime_metadata

        save_manifest(
            {
                "images": [
                    {
                        "id": "docker:slow-device",
                        "kind": "docker",
                        "reference": "slow-device:1.0",
                        "filename": "slow.tar",
                        "device_id": "linux",
                        "compatible_devices": ["linux"],
                        "boot_timeout": 900,
                        "is_default": False,
                        "default_for_devices": [],
                    }
                ]
            }
        )

        result = get_image_runtime_metadata("slow-device:1.0")
        assert result["readiness_timeout"] == 900

    def test_resolves_by_basename(self, tmp_path):
        """Falls back to basename matching for file path references."""
        from app.image_store.manifest import save_manifest
        from app.services.device_service import get_image_runtime_metadata

        save_manifest(
            {
                "images": [
                    {
                        "id": "qcow2:custom",
                        "kind": "qcow2",
                        "reference": "/opt/images/custom-router.qcow2",
                        "filename": "custom-router.qcow2",
                        "device_id": "linux",
                        "compatible_devices": ["linux"],
                        "memory_mb": 1024,
                        "is_default": False,
                        "default_for_devices": [],
                    }
                ]
            }
        )

        # Search by basename only
        result = get_image_runtime_metadata("custom-router.qcow2")
        assert result["memory"] == 1024
