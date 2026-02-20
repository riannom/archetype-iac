"""Unit tests for image provider detection functions.

Pure unit tests — no database needed. Tests get_image_provider() from image_store.py
which determines whether an image reference requires Docker or libvirt/QEMU.
"""
from __future__ import annotations

import pytest

from app.image_store import get_image_provider


# ---------------------------------------------------------------------------
# TestGetImageProvider
# ---------------------------------------------------------------------------


class TestGetImageProvider:
    """Test provider detection from image references."""

    def test_none_returns_docker(self):
        assert get_image_provider(None) == "docker"

    def test_empty_string_returns_docker(self):
        assert get_image_provider("") == "docker"

    def test_docker_tag_returns_docker(self):
        assert get_image_provider("ceos:4.28.0F") == "docker"

    def test_nginx_tag_returns_docker(self):
        assert get_image_provider("nginx:latest") == "docker"

    def test_qcow2_path_returns_libvirt(self):
        assert get_image_provider("/path/to/image.qcow2") == "libvirt"

    def test_qcow2_filename_returns_libvirt(self):
        """Bare filename with qcow2 extension should be libvirt."""
        assert get_image_provider("iosv.qcow2") == "libvirt"

    def test_img_path_returns_libvirt(self):
        assert get_image_provider("/path/to/image.img") == "libvirt"

    def test_unknown_extension_returns_docker(self):
        """Unknown file extensions default to docker."""
        assert get_image_provider("image.bin") == "docker"

    def test_docker_registry_path(self):
        """Full registry paths are Docker images."""
        assert get_image_provider("registry.example.com/image:v1") == "docker"

    def test_img_bare_filename(self):
        assert get_image_provider("disk.img") == "libvirt"

    def test_absolute_path_qcow2(self):
        assert get_image_provider("/var/lib/archetype/images/n9kv-10.3.qcow2") == "libvirt"


# ---------------------------------------------------------------------------
# TestImageReferenceEdgeCases
# ---------------------------------------------------------------------------


class TestImageReferenceEdgeCases:
    """Edge cases in provider detection logic."""

    def test_qcow2_case_sensitive(self):
        """Extension check should be case-sensitive (lowercase qcow2 only)."""
        # Current implementation uses .endswith() which is case-sensitive
        result = get_image_provider("image.QCOW2")
        # This documents the current behavior — uppercase is treated as docker
        assert result == "docker"

    def test_path_with_dots_in_directory(self):
        """.qcow2 extension must be at end, not in directory name."""
        assert get_image_provider("/images/v1.qcow2/something") == "docker"

    def test_img_extension_only(self):
        """Just '.img' without a name is still recognized."""
        assert get_image_provider(".img") == "libvirt"

    def test_empty_after_slash(self):
        """Paths that don't end with known extensions → docker."""
        assert get_image_provider("/var/lib/images/") == "docker"

    def test_docker_image_with_sha(self):
        """Docker image references with SHA digests → docker."""
        assert get_image_provider("ceos@sha256:abc123") == "docker"

    def test_iol_binary_returns_docker(self):
        """IOL binary images are Docker-based."""
        assert get_image_provider("iol.bin") == "docker"

    def test_whitespace_handling(self):
        """Whitespace-only string should be falsy → docker."""
        # Empty-ish but not None
        assert get_image_provider("   ") == "docker"
