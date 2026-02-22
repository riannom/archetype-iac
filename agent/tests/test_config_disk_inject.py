"""Unit tests for config_disk_inject module."""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from agent.providers.config_disk_inject import (
    create_config_disk,
    remove_config_disk,
    _create_config_tarball,
)


class TestCreateConfigTarball:
    """Verify the tarball structure matches vJunOS expectations."""

    def test_tarball_contains_config_juniper_conf(self, tmp_path):
        tgz = tmp_path / "vmm-config.tgz"
        content = b"system { host-name test; }"
        _create_config_tarball(tgz, content)

        with tarfile.open(tgz, "r:gz") as tar:
            names = tar.getnames()
            assert "config/juniper.conf" in names
            member = tar.getmember("config/juniper.conf")
            assert member.size == len(content)
            extracted = tar.extractfile(member)
            assert extracted.read() == content

    def test_tarball_only_has_one_entry(self, tmp_path):
        tgz = tmp_path / "vmm-config.tgz"
        _create_config_tarball(tgz, b"test")

        with tarfile.open(tgz, "r:gz") as tar:
            assert len(tar.getnames()) == 1


class TestCreateConfigDisk:
    """Tests for create_config_disk()."""

    def test_empty_config_returns_false(self, tmp_path):
        disk = tmp_path / "test.img"
        assert create_config_disk(disk, "") is False
        assert not disk.exists()

    def test_whitespace_only_returns_false(self, tmp_path):
        disk = tmp_path / "test.img"
        assert create_config_disk(disk, "   \n  ") is False

    def test_creates_disk_file(self, tmp_path):
        """Integration test — requires mkfs.vfat and mcopy (or mount)."""
        import shutil

        if not shutil.which("mkfs.vfat"):
            pytest.skip("mkfs.vfat not available")
        if not shutil.which("mcopy"):
            pytest.skip("mcopy (mtools) not available")

        disk = tmp_path / "config.img"
        config = "system {\n    host-name vjunos-test;\n}\n"
        result = create_config_disk(disk, config)
        assert result is True
        assert disk.exists()
        # Disk should be 32MB sparse
        assert disk.stat().st_size == 32 * 1024 * 1024


class TestRemoveConfigDisk:
    """Tests for remove_config_disk()."""

    def test_remove_existing_file(self, tmp_path):
        disk = tmp_path / "config.img"
        disk.write_bytes(b"\x00" * 100)
        assert remove_config_disk(disk) is True
        assert not disk.exists()

    def test_remove_nonexistent_is_idempotent(self, tmp_path):
        disk = tmp_path / "no-such-file.img"
        assert remove_config_disk(disk) is True

    def test_remove_already_deleted(self, tmp_path):
        disk = tmp_path / "config.img"
        assert remove_config_disk(disk) is True
        assert remove_config_disk(disk) is True
