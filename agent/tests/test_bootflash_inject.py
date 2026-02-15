"""Unit tests for bootflash config injection (qemu-nbd mount+write)."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from agent.providers.bootflash_inject import (
    _find_bootflash_partition,
    _parse_blkid,
    inject_startup_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _blkid_output(fs_type: str = "ext2") -> str:
    """Simulate blkid -o export output."""
    return f"DEVNAME=/dev/nbd0p3\nTYPE={fs_type}\nUUID=abcd-1234\n"


def _lvm_blkid_output() -> str:
    return "DEVNAME=/dev/nbd0p1\nTYPE=LVM2_member\n"


# ---------------------------------------------------------------------------
# _parse_blkid
# ---------------------------------------------------------------------------


def test_parse_blkid_basic():
    info = _parse_blkid("DEVNAME=/dev/nbd0p3\nTYPE=ext2\nUUID=1234\n")
    assert info["TYPE"] == "ext2"
    assert info["DEVNAME"] == "/dev/nbd0p3"


def test_parse_blkid_empty():
    assert _parse_blkid("") == {}


# ---------------------------------------------------------------------------
# inject_startup_config — success path
# ---------------------------------------------------------------------------


@patch("agent.providers.bootflash_inject.Path.rmdir")
@patch("agent.providers.bootflash_inject.Path.write_text")
@patch("agent.providers.bootflash_inject.Path.mkdir")
@patch("agent.providers.bootflash_inject.tempfile.mkdtemp", return_value="/tmp/bootflash_abc")
@patch("agent.providers.bootflash_inject._resolve_partition", return_value="/dev/nbd0p3")
@patch("agent.providers.bootflash_inject._run")
def test_inject_success(mock_run, mock_resolve, mock_mkdtemp, mock_mkdir, mock_write, mock_rmdir, tmp_path):
    overlay = tmp_path / "test.qcow2"
    # Use open() to avoid hitting the mocked Path.write_text
    with open(overlay, "w") as f:
        f.write("fake")

    result = inject_startup_config(overlay, "hostname N9K\n")

    assert result is True

    # Verify command sequence: modprobe, qemu-nbd -c, mount, sync, umount, qemu-nbd -d
    calls = mock_run.call_args_list
    assert calls[0] == call(["modprobe", "nbd", "max_part=16"])
    assert calls[1] == call(["qemu-nbd", "-c", "/dev/nbd0", str(overlay)])
    assert calls[2] == call(["mount", "-t", "ext2", "/dev/nbd0p3", "/tmp/bootflash_abc"])
    assert calls[3] == call(["sync"])
    # Cleanup
    assert calls[4] == call(["umount", "/tmp/bootflash_abc"])
    assert calls[5] == call(["qemu-nbd", "-d", "/dev/nbd0"])


# ---------------------------------------------------------------------------
# inject_startup_config — nbd connect failure
# ---------------------------------------------------------------------------


@patch("agent.providers.bootflash_inject.Path.rmdir")
@patch("agent.providers.bootflash_inject._run")
def test_nbd_connect_fails(mock_run, mock_rmdir, tmp_path):
    overlay = tmp_path / "test.qcow2"
    overlay.write_text("fake")

    def side_effect(cmd, **kwargs):
        if cmd[0] == "qemu-nbd" and "-c" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        return MagicMock()

    mock_run.side_effect = side_effect
    result = inject_startup_config(overlay, "hostname N9K\n")

    assert result is False
    # qemu-nbd -d should NOT be called since connect failed
    disconnect_calls = [c for c in mock_run.call_args_list if c == call(["qemu-nbd", "-d", "/dev/nbd0"])]
    assert len(disconnect_calls) == 0


# ---------------------------------------------------------------------------
# inject_startup_config — mount failure
# ---------------------------------------------------------------------------


@patch("agent.providers.bootflash_inject.Path.rmdir")
@patch("agent.providers.bootflash_inject.tempfile.mkdtemp", return_value="/tmp/bootflash_abc")
@patch("agent.providers.bootflash_inject._resolve_partition", return_value="/dev/nbd0p3")
@patch("agent.providers.bootflash_inject._run")
def test_mount_fails(mock_run, mock_resolve, mock_mkdtemp, mock_rmdir, tmp_path):
    overlay = tmp_path / "test.qcow2"
    overlay.write_text("fake")

    def side_effect(cmd, **kwargs):
        if cmd[0] == "mount":
            raise subprocess.CalledProcessError(1, cmd)
        return MagicMock()

    mock_run.side_effect = side_effect
    result = inject_startup_config(overlay, "hostname N9K\n")

    assert result is False
    # NBD should still be disconnected on failure
    disconnect_calls = [c for c in mock_run.call_args_list if c == call(["qemu-nbd", "-d", "/dev/nbd0"])]
    assert len(disconnect_calls) == 1


# ---------------------------------------------------------------------------
# inject_startup_config — partition not found
# ---------------------------------------------------------------------------


@patch("agent.providers.bootflash_inject.Path.rmdir")
@patch("agent.providers.bootflash_inject._resolve_partition", return_value=None)
@patch("agent.providers.bootflash_inject._run")
def test_partition_not_found(mock_run, mock_resolve, mock_rmdir, tmp_path):
    overlay = tmp_path / "test.qcow2"
    overlay.write_text("fake")

    result = inject_startup_config(overlay, "hostname N9K\n")

    assert result is False
    # NBD should be disconnected in cleanup
    disconnect_calls = [c for c in mock_run.call_args_list if c == call(["qemu-nbd", "-d", "/dev/nbd0"])]
    assert len(disconnect_calls) == 1


# ---------------------------------------------------------------------------
# inject_startup_config — lock timeout
# ---------------------------------------------------------------------------


@patch("agent.providers.bootflash_inject._LOCK_TIMEOUT", 0.1)
def test_lock_timeout(tmp_path):
    """If the lock is already held, injection returns False immediately."""
    overlay = tmp_path / "test.qcow2"
    overlay.write_text("fake")

    from agent.providers import bootflash_inject

    # Hold the lock from another context
    bootflash_inject._nbd_lock.acquire()
    try:
        result = inject_startup_config(overlay, "hostname N9K\n")
        assert result is False
    finally:
        bootflash_inject._nbd_lock.release()


# ---------------------------------------------------------------------------
# _find_bootflash_partition — skips LVM
# ---------------------------------------------------------------------------


@patch("agent.providers.bootflash_inject._run")
@patch("agent.providers.bootflash_inject.Path.glob")
def test_find_bootflash_skips_lvm(mock_glob, mock_run):
    """LVM physical volume partitions should be skipped during auto-detect."""
    mock_glob.return_value = [Path("/dev/nbd0p1"), Path("/dev/nbd0p2"), Path("/dev/nbd0p3")]

    def blkid_side_effect(cmd, **kwargs):
        dev = cmd[-1]
        result = MagicMock()
        if dev == "/dev/nbd0p1":
            result.stdout = b"DEVNAME=/dev/nbd0p1\nTYPE=LVM2_member\n"
        elif dev == "/dev/nbd0p2":
            result.stdout = b"DEVNAME=/dev/nbd0p2\nTYPE=swap\n"
        elif dev == "/dev/nbd0p3":
            result.stdout = b"DEVNAME=/dev/nbd0p3\nTYPE=ext2\n"
        return result

    mock_run.side_effect = blkid_side_effect

    result = _find_bootflash_partition("ext2")
    assert result == "/dev/nbd0p3"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_inject_nonexistent_overlay():
    """Non-existent overlay path should return False."""
    result = inject_startup_config(Path("/nonexistent/overlay.qcow2"), "config")
    assert result is False


def test_inject_empty_config(tmp_path):
    """Empty config content should return False."""
    overlay = tmp_path / "test.qcow2"
    overlay.write_text("fake")
    result = inject_startup_config(overlay, "")
    assert result is False


def test_inject_whitespace_only_config(tmp_path):
    """Whitespace-only config should return False."""
    overlay = tmp_path / "test.qcow2"
    overlay.write_text("fake")
    result = inject_startup_config(overlay, "   \n  \n  ")
    assert result is False
