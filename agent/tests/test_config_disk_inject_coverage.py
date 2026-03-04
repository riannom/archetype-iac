"""Coverage tests for config_disk_inject: mocked subprocess paths."""

from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.providers.config_disk_inject import (
    _create_config_tarball,
    _mcopy_into_disk,
    _mount_copy_into_disk,
    create_config_disk,
    remove_config_disk,
)


# ---------------------------------------------------------------------------
# _create_config_tarball
# ---------------------------------------------------------------------------


class TestCreateConfigTarball:
    def test_tarball_structure(self, tmp_path: Path):
        tgz = tmp_path / "vmm-config.tgz"
        content = b"interfaces { ge-0/0/0 { unit 0; } }"
        _create_config_tarball(tgz, content)

        with tarfile.open(tgz, "r:gz") as tar:
            assert tar.getnames() == ["config/juniper.conf"]
            member = tar.getmember("config/juniper.conf")
            assert member.size == len(content)
            assert tar.extractfile(member).read() == content

    def test_empty_content_creates_valid_tarball(self, tmp_path: Path):
        tgz = tmp_path / "vmm-config.tgz"
        _create_config_tarball(tgz, b"")

        with tarfile.open(tgz, "r:gz") as tar:
            member = tar.getmember("config/juniper.conf")
            assert member.size == 0


# ---------------------------------------------------------------------------
# _mcopy_into_disk
# ---------------------------------------------------------------------------


class TestMcopyIntoDisk:
    def test_mcopy_success(self, tmp_path: Path):
        disk = tmp_path / "disk.img"
        tgz = tmp_path / "vmm-config.tgz"
        disk.write_bytes(b"\x00")
        tgz.write_bytes(b"\x00")

        with patch("agent.providers.config_disk_inject.shutil.which", return_value="/usr/bin/mcopy"):
            with patch("agent.providers.config_disk_inject.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                assert _mcopy_into_disk(disk, tgz) is True
                mock_run.assert_called_once()
                args = mock_run.call_args[0][0]
                assert args[0] == "/usr/bin/mcopy"
                assert "-i" in args

    def test_mcopy_not_found(self, tmp_path: Path):
        disk = tmp_path / "disk.img"
        tgz = tmp_path / "vmm-config.tgz"
        with patch("agent.providers.config_disk_inject.shutil.which", return_value=None):
            assert _mcopy_into_disk(disk, tgz) is False

    def test_mcopy_subprocess_error(self, tmp_path: Path):
        disk = tmp_path / "disk.img"
        tgz = tmp_path / "vmm-config.tgz"
        disk.write_bytes(b"\x00")
        tgz.write_bytes(b"\x00")

        with patch("agent.providers.config_disk_inject.shutil.which", return_value="/usr/bin/mcopy"):
            with patch(
                "agent.providers.config_disk_inject.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "mcopy"),
            ):
                assert _mcopy_into_disk(disk, tgz) is False

    def test_mcopy_file_not_found(self, tmp_path: Path):
        disk = tmp_path / "disk.img"
        tgz = tmp_path / "vmm-config.tgz"

        with patch("agent.providers.config_disk_inject.shutil.which", return_value="/usr/bin/mcopy"):
            with patch(
                "agent.providers.config_disk_inject.subprocess.run",
                side_effect=FileNotFoundError("mcopy not found"),
            ):
                assert _mcopy_into_disk(disk, tgz) is False


# ---------------------------------------------------------------------------
# _mount_copy_into_disk
# ---------------------------------------------------------------------------


class TestMountCopyIntoDisk:
    def _mock_which(self, cmd: str) -> str | None:
        mapping = {
            "losetup": "/sbin/losetup",
            "mount": "/bin/mount",
            "umount": "/bin/umount",
        }
        return mapping.get(cmd)

    def test_mount_success(self, tmp_path: Path):
        disk = tmp_path / "disk.img"
        tgz = tmp_path / "vmm-config.tgz"
        disk.write_bytes(b"\x00")
        tgz.write_bytes(b"\x00")

        with patch("agent.providers.config_disk_inject.shutil.which", side_effect=self._mock_which):
            with patch("agent.providers.config_disk_inject.subprocess.run") as mock_run:
                # losetup returns /dev/loop0
                losetup_result = MagicMock(returncode=0, stdout="/dev/loop0\n")
                mount_result = MagicMock(returncode=0)
                umount_result = MagicMock(returncode=0)
                detach_result = MagicMock(returncode=0)
                mock_run.side_effect = [losetup_result, mount_result, umount_result, detach_result]

                with patch("agent.providers.config_disk_inject.shutil.copy2"):
                    result = _mount_copy_into_disk(disk, tgz)

                assert result is True

    def test_mount_missing_tools(self, tmp_path: Path):
        disk = tmp_path / "disk.img"
        tgz = tmp_path / "vmm-config.tgz"

        # losetup missing
        with patch("agent.providers.config_disk_inject.shutil.which", return_value=None):
            assert _mount_copy_into_disk(disk, tgz) is False

    def test_mount_losetup_failure(self, tmp_path: Path):
        disk = tmp_path / "disk.img"
        tgz = tmp_path / "vmm-config.tgz"
        disk.write_bytes(b"\x00")
        tgz.write_bytes(b"\x00")

        with patch("agent.providers.config_disk_inject.shutil.which", side_effect=self._mock_which):
            with patch(
                "agent.providers.config_disk_inject.subprocess.run",
                side_effect=Exception("losetup failed"),
            ):
                assert _mount_copy_into_disk(disk, tgz) is False

    def test_mount_partial_tools(self, tmp_path: Path):
        disk = tmp_path / "disk.img"
        tgz = tmp_path / "vmm-config.tgz"

        def _partial_which(cmd: str):
            return "/sbin/losetup" if cmd == "losetup" else None

        with patch("agent.providers.config_disk_inject.shutil.which", side_effect=_partial_which):
            assert _mount_copy_into_disk(disk, tgz) is False


# ---------------------------------------------------------------------------
# create_config_disk — fallback chain
# ---------------------------------------------------------------------------


class TestCreateConfigDiskFallback:
    def test_empty_config(self, tmp_path: Path):
        assert create_config_disk(tmp_path / "disk.img", "") is False
        assert create_config_disk(tmp_path / "disk.img", None) is False

    def test_no_mkfs_vfat(self, tmp_path: Path):
        disk = tmp_path / "disk.img"
        with patch("agent.providers.config_disk_inject.shutil.which", return_value=None):
            assert create_config_disk(disk, "hostname test;") is False

    def test_mcopy_success_skips_mount(self, tmp_path: Path):
        disk = tmp_path / "disk.img"

        def _which(cmd):
            return f"/usr/bin/{cmd}" if cmd in ("mkfs.vfat",) else None

        with patch("agent.providers.config_disk_inject.shutil.which", side_effect=_which):
            with patch("agent.providers.config_disk_inject.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                with patch("agent.providers.config_disk_inject._mcopy_into_disk", return_value=True):
                    result = create_config_disk(disk, "hostname test;")

        assert result is True

    def test_mcopy_fails_falls_back_to_mount(self, tmp_path: Path):
        disk = tmp_path / "disk.img"

        with patch("agent.providers.config_disk_inject.shutil.which", return_value="/usr/bin/mkfs.vfat"):
            with patch("agent.providers.config_disk_inject.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                with patch("agent.providers.config_disk_inject._mcopy_into_disk", return_value=False):
                    with patch("agent.providers.config_disk_inject._mount_copy_into_disk", return_value=True):
                        result = create_config_disk(disk, "hostname test;")

        assert result is True

    def test_both_methods_fail(self, tmp_path: Path):
        disk = tmp_path / "disk.img"

        with patch("agent.providers.config_disk_inject.shutil.which", return_value="/usr/bin/mkfs.vfat"):
            with patch("agent.providers.config_disk_inject.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                with patch("agent.providers.config_disk_inject._mcopy_into_disk", return_value=False):
                    with patch("agent.providers.config_disk_inject._mount_copy_into_disk", return_value=False):
                        result = create_config_disk(disk, "hostname test;")

        assert result is False

    def test_exception_during_creation(self, tmp_path: Path):
        disk = tmp_path / "disk.img"

        with patch("agent.providers.config_disk_inject.shutil.which", return_value="/usr/bin/mkfs.vfat"):
            with patch(
                "agent.providers.config_disk_inject.subprocess.run",
                side_effect=RuntimeError("boom"),
            ):
                result = create_config_disk(disk, "hostname test;")

        assert result is False


# ---------------------------------------------------------------------------
# remove_config_disk
# ---------------------------------------------------------------------------


class TestRemoveConfigDisk:
    def test_remove_existing(self, tmp_path: Path):
        disk = tmp_path / "config.img"
        disk.write_bytes(b"\x00" * 64)
        assert remove_config_disk(disk) is True
        assert not disk.exists()

    def test_remove_nonexistent(self, tmp_path: Path):
        disk = tmp_path / "does-not-exist.img"
        assert remove_config_disk(disk) is True

    def test_remove_permission_error(self, tmp_path: Path):
        disk = tmp_path / "config.img"
        # Simulate an exception during removal
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "unlink", side_effect=PermissionError("denied")):
                assert remove_config_disk(disk) is False
