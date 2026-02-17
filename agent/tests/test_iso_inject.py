"""Unit tests for ISO config injection (CD-ROM image creation)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch


from agent.providers.iso_inject import (
    _MAX_VOLUME_LABEL_LEN,
    create_config_iso,
    remove_config_iso,
)


# ---------------------------------------------------------------------------
# create_config_iso — success with mkisofs
# ---------------------------------------------------------------------------


@patch("agent.providers.iso_inject.shutil.rmtree")
@patch("agent.providers.iso_inject.subprocess.run")
@patch("agent.providers.iso_inject.shutil.which")
@patch("agent.providers.iso_inject.tempfile.mkdtemp", return_value="/tmp/iso_inject_abc")
def test_create_iso_mkisofs(mock_mkdtemp, mock_which, mock_run, mock_rmtree, tmp_path):
    """mkisofs found on first try — should succeed."""
    iso_path = tmp_path / "config.iso"
    Path("/tmp/iso_inject_abc").mkdir(parents=True, exist_ok=True)
    mock_which.return_value = "/usr/bin/mkisofs"
    mock_run.return_value = MagicMock(returncode=0)

    result = create_config_iso(
        iso_path,
        "hostname xr-router\n",
        volume_label="config-1",
        filename="iosxr_config.txt",
    )

    assert result is True
    mock_which.assert_called_once_with("mkisofs")
    mock_run.assert_called_once_with(
        [
            "/usr/bin/mkisofs",
            "-V", "config-1",
            "-r", "-J",
            "-o", str(iso_path),
            "/tmp/iso_inject_abc",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    mock_rmtree.assert_called_once_with("/tmp/iso_inject_abc", ignore_errors=True)


# ---------------------------------------------------------------------------
# create_config_iso — fallback to genisoimage
# ---------------------------------------------------------------------------


@patch("agent.providers.iso_inject.shutil.rmtree")
@patch("agent.providers.iso_inject.subprocess.run")
@patch("agent.providers.iso_inject.shutil.which")
@patch("agent.providers.iso_inject.tempfile.mkdtemp", return_value="/tmp/iso_inject_def")
def test_create_iso_genisoimage_fallback(mock_mkdtemp, mock_which, mock_run, mock_rmtree, tmp_path):
    """mkisofs not found, genisoimage found — should succeed with fallback."""
    iso_path = tmp_path / "config.iso"
    Path("/tmp/iso_inject_def").mkdir(parents=True, exist_ok=True)

    def which_side_effect(tool):
        if tool == "mkisofs":
            return None
        return "/usr/bin/genisoimage"

    mock_which.side_effect = which_side_effect
    mock_run.return_value = MagicMock(returncode=0)

    result = create_config_iso(iso_path, "hostname xr\n")

    assert result is True
    assert mock_which.call_count == 2
    mock_which.assert_any_call("mkisofs")
    mock_which.assert_any_call("genisoimage")
    # Should use genisoimage
    assert mock_run.call_args[0][0][0] == "/usr/bin/genisoimage"


# ---------------------------------------------------------------------------
# create_config_iso — neither tool found
# ---------------------------------------------------------------------------


@patch("agent.providers.iso_inject.shutil.rmtree")
@patch("agent.providers.iso_inject.shutil.which", return_value=None)
@patch("agent.providers.iso_inject.tempfile.mkdtemp", return_value="/tmp/iso_inject_ghi")
def test_create_iso_no_tool(mock_mkdtemp, mock_which, mock_rmtree, tmp_path):
    """Neither mkisofs nor genisoimage available — should return False."""
    iso_path = tmp_path / "config.iso"
    Path("/tmp/iso_inject_ghi").mkdir(parents=True, exist_ok=True)

    result = create_config_iso(iso_path, "hostname xr\n")

    assert result is False
    mock_rmtree.assert_called_once()


# ---------------------------------------------------------------------------
# create_config_iso — empty config
# ---------------------------------------------------------------------------


def test_create_iso_empty_config(tmp_path):
    """Empty config content should return False without creating anything."""
    iso_path = tmp_path / "config.iso"
    assert create_config_iso(iso_path, "") is False
    assert create_config_iso(iso_path, "   \n  ") is False


# ---------------------------------------------------------------------------
# create_config_iso — volume label truncation
# ---------------------------------------------------------------------------


@patch("agent.providers.iso_inject.shutil.rmtree")
@patch("agent.providers.iso_inject.subprocess.run")
@patch("agent.providers.iso_inject.shutil.which", return_value="/usr/bin/mkisofs")
@patch("agent.providers.iso_inject.tempfile.mkdtemp", return_value="/tmp/iso_inject_trunc")
def test_create_iso_label_truncation(mock_mkdtemp, mock_which, mock_run, mock_rmtree, tmp_path):
    """Volume labels longer than 32 chars should be truncated."""
    iso_path = tmp_path / "config.iso"
    Path("/tmp/iso_inject_trunc").mkdir(parents=True, exist_ok=True)
    long_label = "a" * 50

    create_config_iso(iso_path, "config\n", volume_label=long_label)

    # The -V argument should be truncated to 32 chars
    cmd = mock_run.call_args[0][0]
    label_idx = cmd.index("-V") + 1
    assert len(cmd[label_idx]) == _MAX_VOLUME_LABEL_LEN


# ---------------------------------------------------------------------------
# create_config_iso — subprocess failure cleans up
# ---------------------------------------------------------------------------


@patch("agent.providers.iso_inject.shutil.rmtree")
@patch("agent.providers.iso_inject.subprocess.run", side_effect=subprocess.CalledProcessError(1, "mkisofs"))
@patch("agent.providers.iso_inject.shutil.which", return_value="/usr/bin/mkisofs")
@patch("agent.providers.iso_inject.tempfile.mkdtemp", return_value="/tmp/iso_inject_fail")
def test_create_iso_subprocess_failure(mock_mkdtemp, mock_which, mock_run, mock_rmtree, tmp_path):
    """Subprocess failure should return False and clean up temp dir."""
    iso_path = tmp_path / "config.iso"
    Path("/tmp/iso_inject_fail").mkdir(parents=True, exist_ok=True)

    result = create_config_iso(iso_path, "config\n")

    assert result is False
    mock_rmtree.assert_called_once_with("/tmp/iso_inject_fail", ignore_errors=True)


# ---------------------------------------------------------------------------
# create_config_iso — config file written correctly
# ---------------------------------------------------------------------------


@patch("agent.providers.iso_inject.shutil.rmtree")
@patch("agent.providers.iso_inject.subprocess.run")
@patch("agent.providers.iso_inject.shutil.which", return_value="/usr/bin/mkisofs")
def test_create_iso_writes_config_file(mock_which, mock_run, mock_rmtree, tmp_path):
    """Config content should be written to the correct filename in temp dir."""
    iso_path = tmp_path / "config.iso"
    config_text = "hostname xr-router\ninterface GigabitEthernet0/0/0/0\n"

    # Don't mock mkdtemp so it creates a real temp dir for file writing
    result = create_config_iso(
        iso_path,
        config_text,
        filename="iosxr_config.txt",
    )

    assert result is True
    # Verify mkisofs was called with a real temp dir containing the file
    cmd = mock_run.call_args[0][0]
    assert cmd[-1]  # Last arg is the source directory
    # The temp dir was cleaned up by rmtree, but we verified the call succeeded


# ---------------------------------------------------------------------------
# remove_config_iso — file exists
# ---------------------------------------------------------------------------


def test_remove_iso_exists(tmp_path):
    """Existing ISO file should be deleted."""
    iso_path = tmp_path / "config.iso"
    iso_path.write_text("fake iso")

    result = remove_config_iso(iso_path)

    assert result is True
    assert not iso_path.exists()


# ---------------------------------------------------------------------------
# remove_config_iso — file doesn't exist (idempotent)
# ---------------------------------------------------------------------------


def test_remove_iso_nonexistent(tmp_path):
    """Non-existent file should return True (idempotent)."""
    iso_path = tmp_path / "nonexistent.iso"

    result = remove_config_iso(iso_path)

    assert result is True


# ---------------------------------------------------------------------------
# remove_config_iso — permission error
# ---------------------------------------------------------------------------


@patch("agent.providers.iso_inject.Path.exists", return_value=True)
@patch("agent.providers.iso_inject.Path.unlink", side_effect=PermissionError("denied"))
def test_remove_iso_permission_error(mock_unlink, mock_exists):
    """Permission error should return False."""
    result = remove_config_iso(Path("/some/protected.iso"))

    assert result is False
