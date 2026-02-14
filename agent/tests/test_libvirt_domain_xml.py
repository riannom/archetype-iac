"""Unit tests for libvirt domain XML generation and backing image integrity."""

from __future__ import annotations

import hashlib
import os
import tempfile
import xml.etree.ElementTree as ET

import pytest

import agent.providers.libvirt as libvirt_provider


def _make_provider() -> libvirt_provider.LibvirtProvider:
    p = libvirt_provider.LibvirtProvider.__new__(libvirt_provider.LibvirtProvider)
    p._vlan_allocations = {}
    p._next_vlan = {}
    p._conn = None
    p._uri = "qemu:///system"
    return p


# ---------------------------------------------------------------------------
# Domain XML: disk cache settings
# ---------------------------------------------------------------------------

class TestDiskCacheSettings:
    """Verify cache='none', io='native', discard='unmap' on all disk elements."""

    def _generate_xml(self, data_volume_path=None):
        p = _make_provider()
        node_config = {
            "memory": 2048,
            "cpu": 1,
            "machine_type": "pc-i440fx-6.2",
            "disk_driver": "virtio",
            "nic_driver": "virtio",
            "libvirt_driver": "kvm",
            "efi_boot": False,
            "interface_count": 1,
            "_display_name": "test",
        }
        return p._generate_domain_xml(
            "test-domain",
            node_config,
            overlay_path="/tmp/overlay.qcow2",
            data_volume_path=data_volume_path,
            interface_count=1,
            vlan_tags=[2000],
        )

    def test_boot_disk_has_cache_none(self):
        xml = self._generate_xml()
        root = ET.fromstring(xml)
        disks = root.findall(".//disk[@device='disk']")
        assert len(disks) >= 1
        driver = disks[0].find("driver")
        assert driver.get("cache") == "none"
        assert driver.get("io") == "native"
        assert driver.get("discard") == "unmap"

    def test_data_volume_has_cache_none(self):
        xml = self._generate_xml(data_volume_path="/tmp/data.qcow2")
        root = ET.fromstring(xml)
        disks = root.findall(".//disk[@device='disk']")
        assert len(disks) == 2
        for i, disk in enumerate(disks):
            driver = disk.find("driver")
            assert driver.get("cache") == "none", f"disk {i} missing cache=none"
            assert driver.get("io") == "native", f"disk {i} missing io=native"
            assert driver.get("discard") == "unmap", f"disk {i} missing discard=unmap"

    def test_no_writeback_anywhere(self):
        xml = self._generate_xml(data_volume_path="/tmp/data.qcow2")
        assert "writeback" not in xml
        assert "writethrough" not in xml


# ---------------------------------------------------------------------------
# Domain XML: memballoon and rng
# ---------------------------------------------------------------------------

class TestDeviceDefaults:
    """Verify memballoon=none and virtio-rng are present."""

    def _generate_xml(self):
        p = _make_provider()
        node_config = {
            "memory": 2048,
            "cpu": 1,
            "machine_type": "pc-i440fx-6.2",
            "disk_driver": "virtio",
            "nic_driver": "virtio",
            "libvirt_driver": "kvm",
            "efi_boot": False,
            "interface_count": 1,
            "_display_name": "test",
        }
        return p._generate_domain_xml(
            "test-domain",
            node_config,
            overlay_path="/tmp/overlay.qcow2",
            interface_count=1,
            vlan_tags=[2000],
        )

    def test_memballoon_disabled(self):
        xml = self._generate_xml()
        root = ET.fromstring(xml)
        balloon = root.find(".//memballoon")
        assert balloon is not None, "memballoon element missing"
        assert balloon.get("model") == "none"

    def test_virtio_rng_present(self):
        xml = self._generate_xml()
        root = ET.fromstring(xml)
        rng = root.find(".//rng")
        assert rng is not None, "rng element missing"
        assert rng.get("model") == "virtio"
        backend = rng.find("backend")
        assert backend is not None
        assert backend.get("model") == "random"
        assert backend.text == "/dev/urandom"


# ---------------------------------------------------------------------------
# Backing image integrity check
# ---------------------------------------------------------------------------

class TestVerifyBackingImage:
    """Test _verify_backing_image() SHA256 integrity check logic."""

    def _write_temp_file(self, content: bytes) -> str:
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".qcow2")
        f.write(content)
        f.close()
        return f.name

    def _sha256(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def test_skips_when_no_expected_hash(self):
        """Should silently return when expected_sha256 is None."""
        p = _make_provider()
        # Should not raise
        p._verify_backing_image("/nonexistent/file", None)

    def test_passes_when_hash_matches(self, tmp_path):
        """Should return silently when hashes match."""
        p = _make_provider()
        content = b"test image data for hash verification"
        path = tmp_path / "image.qcow2"
        path.write_bytes(content)
        expected = self._sha256(content)
        # Should not raise
        p._verify_backing_image(str(path), expected)

    def test_raises_on_actual_corruption(self, tmp_path, monkeypatch):
        """Should raise RuntimeError when hash mismatches even after cache drop."""
        p = _make_provider()
        content = b"corrupted image data"
        path = tmp_path / "image.qcow2"
        path.write_bytes(content)
        wrong_hash = self._sha256(b"different data entirely")

        # Mock drop_caches to avoid needing root
        mock_open_calls = []

        original_open = open

        def mock_open_fn(path_arg, *args, **kwargs):
            if str(path_arg) == "/proc/sys/vm/drop_caches":
                mock_open_calls.append(path_arg)
                # Return a no-op writable context manager
                return original_open(os.devnull, "w")
            return original_open(path_arg, *args, **kwargs)

        monkeypatch.setattr("builtins.open", mock_open_fn)

        with pytest.raises(RuntimeError, match="integrity check failed"):
            p._verify_backing_image(str(path), wrong_hash)

        # Verify it attempted to drop caches
        assert len(mock_open_calls) == 1

    def test_recovers_after_cache_drop(self, tmp_path, monkeypatch):
        """Should succeed when second hash (after cache drop) matches."""
        p = _make_provider()
        content = b"good image data"
        path = tmp_path / "image.qcow2"
        path.write_bytes(content)
        correct_hash = self._sha256(content)

        # First call returns wrong hash, second returns correct
        call_count = [0]
        original_compute = p._compute_file_sha256

        def mock_compute(file_path):
            call_count[0] += 1
            if call_count[0] == 1:
                return "deadbeef" * 8  # Wrong hash (64 chars)
            return original_compute(file_path)

        monkeypatch.setattr(p, "_compute_file_sha256", mock_compute)

        # Mock drop_caches
        original_open = open

        def mock_open_fn(path_arg, *args, **kwargs):
            if str(path_arg) == "/proc/sys/vm/drop_caches":
                return original_open(os.devnull, "w")
            return original_open(path_arg, *args, **kwargs)

        monkeypatch.setattr("builtins.open", mock_open_fn)

        # Should not raise — recovery succeeds on second attempt
        p._verify_backing_image(str(path), correct_hash)
        assert call_count[0] == 2

    def test_compute_file_sha256(self, tmp_path):
        """Verify _compute_file_sha256 returns correct hash."""
        p = _make_provider()
        content = b"hello world" * 1000
        path = tmp_path / "test.bin"
        path.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert p._compute_file_sha256(str(path)) == expected
