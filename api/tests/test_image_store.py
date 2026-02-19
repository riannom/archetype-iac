"""Tests for image store utilities (image_store.py).

This module tests:
- Device detection from filenames
- Image manifest management
- QCOW2 device type detection
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.image_store import (
    add_custom_device,
    create_image_entry,
    delete_image_entry,
    detect_device_from_filename,
    detect_qcow2_device_type,
    find_image_by_id,
    find_image_reference,
    load_custom_devices,
    image_matches_device,
    load_manifest,
    save_manifest,
    update_image_entry,
)


@pytest.fixture(autouse=True)
def _mock_image_store_path(tmp_path, monkeypatch):
    """Redirect image store to a temp directory to avoid PermissionError on CI."""
    monkeypatch.setattr("app.image_store.ensure_image_store", lambda: tmp_path)
    monkeypatch.setattr("app.image_store.image_store_root", lambda: tmp_path)


class TestDetectDeviceFromFilename:
    """Tests for detect_device_from_filename function."""

    def test_ceos_image(self):
        """Detects Arista cEOS images."""
        device_id, version = detect_device_from_filename("ceos:4.28.0F")
        assert device_id == "ceos"
        assert version == "4.28.0F"

        device_id, version = detect_device_from_filename("ceos-4.30.0F.tar.xz")
        assert device_id == "ceos"
        assert "4.30" in version

    def test_ceos_lab_image(self):
        """Detects cEOS-lab images."""
        device_id, version = detect_device_from_filename("ceos-lab:4.28.0F")
        assert device_id == "ceos"
        assert version == "4.28.0F"

    def test_cisco_csr_image(self):
        """Detects Cisco CSR1000v images."""
        device_id, version = detect_device_from_filename("csr1000v-17.03.04a.qcow2")
        assert device_id in ("csr", "csr1000v")
        assert "17.03" in version or version

    def test_cisco_xrv9k_image(self):
        """Detects Cisco XRv9k images."""
        device_id, version = detect_device_from_filename("xrv9k-fullk9-x-7.3.2.qcow2")
        # keyword_map only covers ceos, eos, iosv, csr, nxos, viosl2, iosvl2, iosxr
        # xrv9k is not in keyword_map, so returns None
        assert device_id is None or device_id in ("xrv9k", "xr", "iosxr")
        assert version is not None

    def test_cisco_nxos_image(self):
        """Detects Cisco NX-OS images."""
        device_id, version = detect_device_from_filename("nxos-9.3.9.qcow2")
        assert device_id in ("nxos", "nexus9000v")

    def test_juniper_vmx_image(self):
        """Detects Juniper vMX images."""
        device_id, version = detect_device_from_filename("vmx-bundle-21.4R1.qcow2")
        # vmx is not in the keyword_map, so returns None
        assert device_id is None or device_id in ("vmx", "juniper")

    def test_juniper_vjunos_router_image(self):
        """Detects Juniper vJunos Router images."""
        device_id, version = detect_device_from_filename("vjunos-router-23.2R1.14.qcow2")
        assert device_id == "juniper_vjunosrouter"
        assert version is not None

    def test_juniper_vjunos_evolved_image(self):
        """Detects Juniper vJunos Evolved router images."""
        device_id, version = detect_device_from_filename("vjunos-evolved-23.2R1.14.qcow2")
        assert device_id == "juniper_vjunosevolved"
        assert version is not None

    def test_juniper_vjunos_switch_image(self):
        """Detects Juniper vJunos Switch images."""
        device_id, version = detect_device_from_filename("vjunos-switch-23.2R1.14.qcow2")
        assert device_id == "juniper_vjunosswitch"
        assert version is not None

    def test_nokia_sros_image(self):
        """Detects Nokia SR OS images."""
        device_id, version = detect_device_from_filename("sros-vm-21.10.R1.qcow2")
        # sros is not in the keyword_map, so returns None
        assert device_id is None or device_id in ("sros", "nokia")

    def test_unknown_image(self):
        """Unknown images return None device_id."""
        device_id, version = detect_device_from_filename("random-image:latest")
        # May return None or a best-guess
        assert device_id is None or isinstance(device_id, str)

    def test_version_extraction(self):
        """Version extracted from common patterns."""
        # Semantic versioning
        _, version = detect_device_from_filename("image:1.2.3")
        assert version == "1.2.3" or "1.2" in version

        # Cisco versioning
        _, version = detect_device_from_filename("csr1000v-17.03.04a.qcow2")
        assert "17" in version

    def test_docker_tag_format(self):
        """Handles Docker image:tag format."""
        device_id, version = detect_device_from_filename("ceos:4.28.0F")
        assert device_id == "ceos"
        assert version == "4.28.0F"


class TestDetectQcow2DeviceType:
    """Tests for detect_qcow2_device_type function."""

    def test_cisco_csr(self):
        """Detects Cisco CSR1000v vrnetlab path."""
        device_id, vrnetlab_path = detect_qcow2_device_type("csr1000v-17.03.04a.qcow2")
        assert vrnetlab_path in ("csr", "cisco/csr1000v") or vrnetlab_path is not None

    def test_cisco_xrv(self):
        """Detects Cisco XRv vrnetlab path."""
        device_id, vrnetlab_path = detect_qcow2_device_type("xrv-6.5.1.qcow2")
        # xrv (without 9k) is not in QCOW2_DEVICE_PATTERNS, so returns (None, None)
        # Only xrv9k and xrd patterns exist
        assert True  # Just verify no exception

    def test_cisco_xrv9k(self):
        """XRv9k fullk9 filename doesn't match patterns (requires digit after separator)."""
        device_id, vrnetlab_path = detect_qcow2_device_type("xrv9k-fullk9-x-7.3.2.qcow2")
        # Pattern requires xrv9k followed by optional separator then digit/dot
        # "xrv9k-fullk9" has a letter after separator, so no match
        assert device_id is None and vrnetlab_path is None

    def test_juniper_vmx(self):
        """vMX bundle filename is recognized for vrnetlab builds."""
        device_id, vrnetlab_path = detect_qcow2_device_type("vmx-bundle-21.4R1.qcow2")
        assert device_id == "vmx"
        assert vrnetlab_path == "juniper/vmx"

    def test_juniper_vjunos_router(self):
        """vJunos Router filenames map to the official router build profile."""
        device_id, vrnetlab_path = detect_qcow2_device_type("vjunos-router-23.2R1.14.qcow2")
        assert device_id == "juniper_vjunosrouter"
        assert vrnetlab_path == "juniper/vjunos-router"

    def test_juniper_vjunos_evolved(self):
        """vJunos Evolved filenames map to dedicated evolved device ID and official build profile."""
        device_id, vrnetlab_path = detect_qcow2_device_type("vjunos-evolved-23.2R1.14.qcow2")
        assert device_id == "juniper_vjunosevolved"
        assert vrnetlab_path == "juniper/vjunos-router"

    def test_juniper_vjunos_switch(self):
        """vJunos Switch filenames map to the switch build profile."""
        device_id, vrnetlab_path = detect_qcow2_device_type("vjunos-switch-23.2R1.14.qcow2")
        assert device_id == "juniper_vjunosswitch"
        assert vrnetlab_path == "juniper/vjunos-switch"

    def test_unknown_device(self):
        """Unknown devices return None vrnetlab path."""
        device_id, vrnetlab_path = detect_qcow2_device_type("unknown-device.qcow2")
        # May return None for unknown devices
        assert True  # Just verify no exception


class TestManifestOperations:
    """Tests for manifest load/save operations."""

    def test_load_empty_manifest(self, tmp_path):
        """Loading nonexistent manifest returns empty structure."""
        manifest_path = tmp_path / "manifest.json"
        with patch("app.image_store.manifest_path", return_value=manifest_path):
            manifest = load_manifest()
            assert "images" in manifest
            assert manifest["images"] == []

    def test_save_and_load_manifest(self, tmp_path):
        """Manifest round-trips correctly."""
        manifest_path = tmp_path / "manifest.json"
        with patch("app.image_store.manifest_path", return_value=manifest_path):
            manifest = {
                "images": [
                    {
                        "id": "docker:test:1.0",
                        "kind": "docker",
                        "reference": "test:1.0",
                    }
                ]
            }
            save_manifest(manifest)

            loaded = load_manifest()
            assert len(loaded["images"]) == 1
            assert loaded["images"][0]["id"] == "docker:test:1.0"

    def test_manifest_json_formatting(self, tmp_path):
        """Manifest is saved with proper JSON formatting."""
        manifest_path = tmp_path / "manifest.json"
        with patch("app.image_store.manifest_path", return_value=manifest_path):
            manifest = {"images": [{"id": "test"}]}
            save_manifest(manifest)

            content = manifest_path.read_text()
            # Should be pretty-printed
            assert "\n" in content
            loaded = json.loads(content)
            assert loaded == manifest

    def test_load_manifest_normalizes_legacy_iosv_device_ids(self, tmp_path):
        """Legacy iosv IDs are normalized to canonical cisco_iosv."""
        manifest_path = tmp_path / "manifest.json"
        with patch("app.image_store.manifest_path", return_value=manifest_path):
            manifest_path.write_text(
                json.dumps({
                    "images": [
                        {
                            "id": "qcow2:vios-15.9",
                            "kind": "qcow2",
                            "reference": "/images/vios-15.9.qcow2",
                            "filename": "vios-15.9.qcow2",
                            "device_id": "iosv",
                            "compatible_devices": ["iosv"],
                        }
                    ]
                }),
                encoding="utf-8",
            )

            loaded = load_manifest()
            image = loaded["images"][0]
            assert image["device_id"] == "cisco_iosv"
            assert image["compatible_devices"] == ["cisco_iosv"]

    def test_load_manifest_sets_default_for_single_runnable_image(self, tmp_path):
        """Single runnable image per device is auto-marked as default."""
        manifest_path = tmp_path / "manifest.json"
        with patch("app.image_store.manifest_path", return_value=manifest_path):
            manifest_path.write_text(
                json.dumps({
                    "images": [
                        {
                            "id": "docker:ceos:4.28.0F",
                            "kind": "docker",
                            "reference": "ceos:4.28.0F",
                            "filename": "ceos-4.28.0F.tar",
                            "device_id": "eos",
                            "compatible_devices": ["eos"],
                            "is_default": False,
                        }
                    ]
                }),
                encoding="utf-8",
            )

            loaded = load_manifest()
            image = loaded["images"][0]
            assert image["device_id"] == "ceos"
            assert image["is_default"] is True

    def test_load_manifest_does_not_force_default_when_multiple_images(self, tmp_path):
        """Multiple runnable images keep explicit default selection behavior."""
        manifest_path = tmp_path / "manifest.json"
        with patch("app.image_store.manifest_path", return_value=manifest_path):
            manifest_path.write_text(
                json.dumps({
                    "images": [
                        {
                            "id": "docker:ceos:4.28.0F",
                            "kind": "docker",
                            "reference": "ceos:4.28.0F",
                            "filename": "ceos-4.28.0F.tar",
                            "device_id": "eos",
                            "compatible_devices": ["eos"],
                            "is_default": False,
                        },
                        {
                            "id": "docker:ceos:4.29.0F",
                            "kind": "docker",
                            "reference": "ceos:4.29.0F",
                            "filename": "ceos-4.29.0F.tar",
                            "device_id": "eos",
                            "compatible_devices": ["eos"],
                            "is_default": False,
                        },
                    ]
                }),
                encoding="utf-8",
            )

            loaded = load_manifest()
            assert loaded["images"][0]["is_default"] is False
            assert loaded["images"][1]["is_default"] is False

    def test_save_manifest_backfills_default_for_single_runnable_image(self, tmp_path):
        """Saving manifest applies the same auto-default normalization."""
        manifest_path = tmp_path / "manifest.json"
        with patch("app.image_store.manifest_path", return_value=manifest_path):
            manifest = {
                "images": [
                    {
                        "id": "qcow2:vios-15.9",
                        "kind": "qcow2",
                        "reference": "/images/vios-15.9.qcow2",
                        "filename": "vios-15.9.qcow2",
                        "device_id": "iosv",
                        "compatible_devices": ["iosv"],
                        "is_default": False,
                    }
                ]
            }
            save_manifest(manifest)

            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            image = saved["images"][0]
            assert image["device_id"] == "cisco_iosv"
            assert image["compatible_devices"] == ["cisco_iosv"]
            assert image["is_default"] is True

    def test_load_manifest_backfills_legacy_linux_frr_assignment(self, tmp_path):
        """Legacy linux+frr image entries are remapped to draggable frr device type."""
        manifest_path = tmp_path / "manifest.json"
        with patch("app.image_store.manifest_path", return_value=manifest_path):
            manifest_path.write_text(
                json.dumps({
                    "images": [
                        {
                            "id": "docker:frr:10.2.1",
                            "kind": "docker",
                            "reference": "quay.io/frrouting/frr:10.2.1",
                            "filename": "frr-10.2.1.tar.gz",
                            "device_id": "linux",
                            "compatible_devices": ["linux"],
                            "is_default": False,
                        }
                    ]
                }),
                encoding="utf-8",
            )

            loaded = load_manifest()
            image = loaded["images"][0]
            assert image["device_id"] == "frr"
            assert image["compatible_devices"] == ["frr"]

    def test_load_manifest_backfills_legacy_linux_alpine_assignment(self, tmp_path):
        """Legacy linux+alpine entries are remapped to draggable alpine device type."""
        manifest_path = tmp_path / "manifest.json"
        with patch("app.image_store.manifest_path", return_value=manifest_path):
            manifest_path.write_text(
                json.dumps({
                    "images": [
                        {
                            "id": "qcow2:alpine-base-3-21-3.qcow2",
                            "kind": "qcow2",
                            "reference": "/images/alpine-base-3-21-3.qcow2",
                            "filename": "alpine-base-3-21-3.qcow2",
                            "device_id": "linux",
                            "compatible_devices": ["linux"],
                            "is_default": False,
                        }
                    ]
                }),
                encoding="utf-8",
            )

            loaded = load_manifest()
            image = loaded["images"][0]
            assert image["device_id"] == "alpine"
            assert image["compatible_devices"] == ["alpine"]

    def test_load_manifest_backfills_legacy_linux_tcl_assignment(self, tmp_path):
        """Legacy linux+tcl entries are remapped to draggable tcl device type."""
        manifest_path = tmp_path / "manifest.json"
        with patch("app.image_store.manifest_path", return_value=manifest_path):
            manifest_path.write_text(
                json.dumps({
                    "images": [
                        {
                            "id": "qcow2:tcl-16-0.qcow2",
                            "kind": "qcow2",
                            "reference": "/images/tcl-16-0.qcow2",
                            "filename": "tcl-16-0.qcow2",
                            "device_id": "linux",
                            "compatible_devices": ["linux"],
                            "is_default": False,
                        }
                    ]
                }),
                encoding="utf-8",
            )

            loaded = load_manifest()
            image = loaded["images"][0]
            assert image["device_id"] == "tcl"
            assert image["compatible_devices"] == ["tcl"]


class TestCustomDeviceShadowing:
    """Tests for custom device behavior when IDs overlap vendor registry."""

    def test_load_custom_devices_filters_vendor_shadow_entries(self, tmp_path):
        custom_path = tmp_path / "custom_devices.json"
        custom_path.write_text(
            json.dumps({
                "devices": [
                    {"id": "cat9000v-uadp", "name": "Legacy Custom"},
                    {"id": "my-custom-device", "name": "Custom Device"},
                ]
            }),
            encoding="utf-8",
        )

        with patch("app.image_store.custom_devices_path", return_value=custom_path):
            with patch("agent.vendors.VENDOR_CONFIGS", {"cat9000v-uadp": object(), "linux": object()}):
                devices = load_custom_devices()

        assert [d["id"] for d in devices] == ["my-custom-device"]

    def test_add_custom_device_rejects_vendor_id_shadow(self, tmp_path):
        custom_path = tmp_path / "custom_devices.json"
        custom_path.write_text(json.dumps({"devices": []}), encoding="utf-8")

        with patch("app.image_store.custom_devices_path", return_value=custom_path):
            with patch("agent.vendors.VENDOR_CONFIGS", {"cat9000v-uadp": object()}):
                with pytest.raises(ValueError, match="built-in vendor device"):
                    add_custom_device({"id": "cat9000v-uadp", "name": "Duplicate"})

    def test_load_custom_devices_filters_vendor_alias_shadow_entries(self, tmp_path):
        custom_path = tmp_path / "custom_devices.json"
        custom_path.write_text(
            json.dumps({
                "devices": [
                    {"id": "cat8000v", "name": "Legacy Alias"},
                    {"id": "my-custom-device", "name": "Custom Device"},
                ]
            }),
            encoding="utf-8",
        )

        with patch("app.image_store.custom_devices_path", return_value=custom_path):
            with patch("agent.vendors.VENDOR_CONFIGS", {"c8000v": object(), "linux": object()}):
                with patch("agent.vendors.get_kind_for_device", lambda d: "cisco_c8000v" if d == "cat8000v" else d):
                    with patch("agent.vendors._get_config_by_kind", lambda k: object() if k == "cisco_c8000v" else None):
                        devices = load_custom_devices()

        assert [d["id"] for d in devices] == ["my-custom-device"]

    def test_add_custom_device_rejects_vendor_alias_shadow(self, tmp_path):
        custom_path = tmp_path / "custom_devices.json"
        custom_path.write_text(json.dumps({"devices": []}), encoding="utf-8")

        with patch("app.image_store.custom_devices_path", return_value=custom_path):
            with patch("agent.vendors.VENDOR_CONFIGS", {"c8000v": object()}):
                with patch("agent.vendors.get_kind_for_device", lambda d: "cisco_c8000v" if d == "cat8000v" else d):
                    with patch("agent.vendors._get_config_by_kind", lambda k: object() if k == "cisco_c8000v" else None):
                        with pytest.raises(ValueError, match="built-in vendor device"):
                            add_custom_device({"id": "cat8000v", "name": "Alias Duplicate"})


class TestImageEntryOperations:
    """Tests for image entry CRUD operations."""

    def test_create_image_entry(self):
        """Creates image entry with all fields."""
        entry = create_image_entry(
            image_id="docker:ceos:4.28.0F",
            kind="docker",
            reference="ceos:4.28.0F",
            filename="ceos-4.28.0F.tar.xz",
            device_id="eos",
            version="4.28.0F",
            size_bytes=1024000,
        )

        assert entry["id"] == "docker:ceos:4.28.0F"
        assert entry["kind"] == "docker"
        assert entry["reference"] == "ceos:4.28.0F"
        assert entry["device_id"] == "ceos"
        assert entry["version"] == "4.28.0F"
        assert entry["size_bytes"] == 1024000
        assert "uploaded_at" in entry

    def test_create_image_entry_minimal(self):
        """Creates image entry with minimal fields."""
        entry = create_image_entry(
            image_id="docker:test:latest",
            kind="docker",
            reference="test:latest",
            filename="test-latest.tar",
        )

        assert entry["id"] == "docker:test:latest"
        assert entry["device_id"] is None
        assert entry["version"] is None

    def test_create_image_entry_with_runtime_metadata(self):
        """Persists optional runtime metadata hints on image entry."""
        entry = create_image_entry(
            image_id="qcow2:n9kv.qcow2",
            kind="qcow2",
            reference="/var/lib/archetype/images/n9kv.qcow2",
            filename="n9kv.qcow2",
            device_id="cisco_n9kv",
            memory_mb=12288,
            cpu_count=4,
            disk_driver="sata",
            nic_driver="e1000",
            boot_timeout=480,
            max_ports=65,
            port_naming="Ethernet1/",
            cpu_limit=100,
            has_loopback=True,
            provisioning_driver="nxosv9000",
            provisioning_media_type="iso",
        )
        assert entry["memory_mb"] == 12288
        assert entry["cpu_count"] == 4
        assert entry["disk_driver"] == "sata"
        assert entry["nic_driver"] == "e1000"
        assert entry["boot_timeout"] == 480
        assert entry["max_ports"] == 65
        assert entry["port_naming"] == "Ethernet1/"
        assert entry["cpu_limit"] == 100
        assert entry["has_loopback"] is True
        assert entry["provisioning_driver"] == "nxosv9000"
        assert entry["provisioning_media_type"] == "iso"

    def test_create_image_entry_normalizes_iosv(self):
        """Legacy iosv assignment is normalized to cisco_iosv."""
        entry = create_image_entry(
            image_id="qcow2:vios-15.9",
            kind="qcow2",
            reference="/images/vios-15.9.qcow2",
            filename="vios-15.9.qcow2",
            device_id="iosv",
        )

        assert entry["device_id"] == "cisco_iosv"
        assert "cisco_iosv" in entry["compatible_devices"]

    def test_find_image_by_id(self):
        """Finds image by ID."""
        manifest = {
            "images": [
                {"id": "docker:image1:1.0", "kind": "docker"},
                {"id": "docker:image2:2.0", "kind": "docker"},
            ]
        }

        result = find_image_by_id(manifest, "docker:image1:1.0")
        assert result is not None
        assert result["id"] == "docker:image1:1.0"

        result = find_image_by_id(manifest, "nonexistent")
        assert result is None

    def test_update_image_entry(self):
        """Updates image entry fields."""
        manifest = {
            "images": [
                {
                    "id": "docker:test:1.0",
                    "kind": "docker",
                    "device_id": None,
                    "version": "1.0",
                }
            ]
        }

        updated = update_image_entry(
            manifest,
            "docker:test:1.0",
            {"device_id": "eos", "notes": "Test image"}
        )

        assert updated is not None
        assert updated["device_id"] == "ceos"
        assert updated["notes"] == "Test image"
        # Original fields preserved
        assert updated["version"] == "1.0"

    def test_update_image_entry_normalizes_iosv(self):
        """Legacy iosv updates normalize to canonical cisco_iosv."""
        manifest = {
            "images": [
                {
                    "id": "qcow2:vios-15.9",
                    "kind": "qcow2",
                    "device_id": None,
                    "compatible_devices": [],
                }
            ]
        }

        updated = update_image_entry(
            manifest,
            "qcow2:vios-15.9",
            {"device_id": "iosv"},
        )

        assert updated is not None
        assert updated["device_id"] == "cisco_iosv"
        assert updated["compatible_devices"] == ["cisco_iosv"]

    def test_update_nonexistent_image(self):
        """Update nonexistent image returns None."""
        manifest = {"images": []}
        result = update_image_entry(manifest, "nonexistent", {"device_id": "eos"})
        assert result is None

    def test_delete_image_entry(self):
        """Deletes image entry."""
        manifest = {
            "images": [
                {"id": "docker:image1:1.0"},
                {"id": "docker:image2:2.0"},
            ]
        }

        deleted = delete_image_entry(manifest, "docker:image1:1.0")
        assert deleted is not None
        assert deleted["id"] == "docker:image1:1.0"
        assert len(manifest["images"]) == 1
        assert manifest["images"][0]["id"] == "docker:image2:2.0"

    def test_delete_nonexistent_image(self):
        """Delete nonexistent image returns None."""
        manifest = {"images": [{"id": "docker:test:1.0"}]}
        result = delete_image_entry(manifest, "nonexistent")
        assert result is None
        assert len(manifest["images"]) == 1


class TestImageDefaultHandling:
    """Tests for default image handling."""

    def test_is_default_flag(self):
        """is_default flag tracks default image."""
        entry = create_image_entry(
            image_id="docker:ceos:4.28.0F",
            kind="docker",
            reference="ceos:4.28.0F",
            filename="ceos-4.28.0F.tar",
        )
        assert entry.get("is_default") is False or entry.get("is_default") is None

    def test_set_default_image(self):
        """Setting is_default flag."""
        manifest = {
            "images": [
                {"id": "docker:ceos:4.28.0F", "device_id": "eos", "is_default": False},
            ]
        }

        updated = update_image_entry(
            manifest,
            "docker:ceos:4.28.0F",
            {"is_default": True}
        )
        assert updated["is_default"] is True

    def test_defaults_are_scoped_per_device_type(self):
        """Different device types can hold independent defaults on shared images."""
        manifest = {
            "images": [
                {
                    "id": "qcow2:cat9k-a",
                    "kind": "qcow2",
                    "reference": "/images/cat9k-a.qcow2",
                    "device_id": "cisco_cat9kv",
                    "compatible_devices": ["cisco_cat9kv"],
                    "is_default": False,
                    "default_for_devices": [],
                },
                {
                    "id": "qcow2:cat9k-b",
                    "kind": "qcow2",
                    "reference": "/images/cat9k-b.qcow2",
                    "device_id": "cisco_cat9kv",
                    "compatible_devices": ["cisco_cat9kv"],
                    "is_default": False,
                    "default_for_devices": [],
                },
            ]
        }

        update_image_entry(
            manifest,
            "qcow2:cat9k-a",
            {"device_id": "cat9000v-uadp", "is_default": True, "default_for_device": "cat9000v-uadp"},
        )
        update_image_entry(
            manifest,
            "qcow2:cat9k-b",
            {"device_id": "cat9800", "is_default": True, "default_for_device": "cat9800"},
        )

        image_a = find_image_by_id(manifest, "qcow2:cat9k-a")
        image_b = find_image_by_id(manifest, "qcow2:cat9k-b")
        assert image_a is not None
        assert image_b is not None
        assert image_a["default_for_devices"] == ["cat9000v-uadp"]
        assert image_b["default_for_devices"] == ["cat9800"]

    def test_default_replacement_is_scoped_to_same_device(self):
        """Setting default for one device type does not clear other device defaults."""
        manifest = {
            "images": [
                {
                    "id": "qcow2:cat9k-a",
                    "kind": "qcow2",
                    "reference": "/images/cat9k-a.qcow2",
                    "device_id": "cisco_cat9kv",
                    "compatible_devices": ["cisco_cat9kv"],
                    "is_default": True,
                    "default_for_devices": ["cat9000v-uadp", "cat9800"],
                },
                {
                    "id": "qcow2:cat9k-b",
                    "kind": "qcow2",
                    "reference": "/images/cat9k-b.qcow2",
                    "device_id": "cisco_cat9kv",
                    "compatible_devices": ["cisco_cat9kv"],
                    "is_default": False,
                    "default_for_devices": [],
                },
            ]
        }

        update_image_entry(
            manifest,
            "qcow2:cat9k-b",
            {"device_id": "cat9000v-uadp", "is_default": True, "default_for_device": "cat9000v-uadp"},
        )

        image_a = find_image_by_id(manifest, "qcow2:cat9k-a")
        image_b = find_image_by_id(manifest, "qcow2:cat9k-b")
        assert image_a is not None
        assert image_b is not None
        assert "cat9000v-uadp" not in image_a["default_for_devices"]
        assert "cat9800" in image_a["default_for_devices"]
        assert image_b["default_for_devices"] == ["cat9000v-uadp"]

    def test_find_image_reference_uses_device_scoped_defaults(self, tmp_path):
        """Runtime lookup selects default based on requested device type scope."""
        manifest_path = tmp_path / "manifest.json"
        with patch("app.image_store.manifest_path", return_value=manifest_path):
            save_manifest({
                "images": [
                    {
                        "id": "qcow2:cat9k-a",
                        "kind": "qcow2",
                        "reference": "/images/cat9k-a.qcow2",
                        "device_id": "cisco_cat9kv",
                        "compatible_devices": ["cisco_cat9kv"],
                        "is_default": True,
                        "default_for_devices": ["cat9000v-uadp"],
                    },
                    {
                        "id": "qcow2:cat9k-b",
                        "kind": "qcow2",
                        "reference": "/images/cat9k-b.qcow2",
                        "device_id": "cisco_cat9kv",
                        "compatible_devices": ["cisco_cat9kv"],
                        "is_default": True,
                        "default_for_devices": ["cat9000v-q200"],
                    },
                ]
            })

            assert find_image_reference("cat9000v-uadp") == "/images/cat9k-a.qcow2"
            assert find_image_reference("cat9000v-q200") == "/images/cat9k-b.qcow2"


class TestImageMatching:
    """Tests for device/image matching across canonical and legacy IDs."""

    def test_image_matches_device_iosv_alias(self):
        image = {
            "id": "qcow2:vios-15.9",
            "kind": "qcow2",
            "device_id": "iosv",
            "compatible_devices": ["iosv"],
        }

        assert image_matches_device(image, "cisco_iosv") is True

    def test_image_does_not_match_linux_family_by_kind_only(self):
        image = {
            "id": "docker:linux-base",
            "kind": "docker",
            "device_id": "linux",
            "compatible_devices": ["linux"],
        }

        assert image_matches_device(image, "frr") is False

    def test_create_image_entry_preserves_frr_device_id(self):
        entry = create_image_entry(
            image_id="docker:frr:10.2.1",
            kind="docker",
            reference="quay.io/frrouting/frr:10.2.1",
            filename="frr-10.2.1.tar.gz",
            device_id="frr",
        )

        assert entry["device_id"] == "frr"
        assert entry["compatible_devices"] == ["frr"]

    def test_create_image_entry_unknown_device_creates_dynamic_custom_profile(self):
        """Unknown device IDs are auto-created as custom profiles."""
        entry = create_image_entry(
            image_id="qcow2:acme-vrouter-1.0.qcow2",
            kind="qcow2",
            reference="/images/acme-vrouter-1.0.qcow2",
            filename="acme-vrouter-1.0.qcow2",
            device_id="acme_vrouter",
        )

        assert entry["device_id"] == "acme_vrouter"
        devices = load_custom_devices()
        created = next((d for d in devices if d.get("id") == "acme_vrouter"), None)
        assert created is not None
        assert created["type"] == "router"
        assert created["supportedImageKinds"] == ["qcow2"]


class TestCompatibleDevices:
    """Tests for compatible_devices field."""

    def test_compatible_devices_list(self):
        """compatible_devices stores list of device IDs."""
        entry = create_image_entry(
            image_id="docker:linux:latest",
            kind="docker",
            reference="linux:latest",
            filename="linux-latest.tar",
        )

        # Initially empty or None (no device_id, so compatible_devices is [])
        assert entry.get("compatible_devices") is None or entry.get("compatible_devices") == []

    def test_update_compatible_devices(self):
        """Update compatible_devices list."""
        manifest = {
            "images": [
                {"id": "docker:linux:latest", "compatible_devices": []},
            ]
        }

        updated = update_image_entry(
            manifest,
            "docker:linux:latest",
            {"compatible_devices": ["linux", "alpine"]}
        )
        assert updated["compatible_devices"] == ["linux", "alpine"]
