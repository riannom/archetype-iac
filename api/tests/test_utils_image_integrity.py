"""Tests for image integrity utilities (utils/image_integrity.py).

Covers:
- compute_sha256: correct hash, streaming chunks, file not found
- validate_qcow2: valid magic, invalid magic, too small, qemu-img not installed,
  wrong format reported by qemu-img, timeout silently skipped
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from unittest.mock import patch

import pytest

from app.utils.image_integrity import QCOW2_MAGIC, compute_sha256, validate_qcow2


class TestComputeSha256:
    """Tests for compute_sha256 function."""

    def test_hash_matches_known_content(self, tmp_path):
        """SHA256 of known content should match hashlib reference."""
        content = b"hello world\n"
        expected = hashlib.sha256(content).hexdigest()

        f = tmp_path / "test.bin"
        f.write_bytes(content)

        result = compute_sha256(str(f))
        assert result == expected

    def test_large_file_streams_in_chunks(self, tmp_path):
        """Large file should be hashed correctly via streaming (not loaded all at once)."""
        # Create a file larger than the 1MB chunk size
        chunk = b"A" * (1024 * 1024)  # 1 MB
        f = tmp_path / "large.bin"
        with open(f, "wb") as fh:
            for _ in range(3):
                fh.write(chunk)

        expected = hashlib.sha256(chunk * 3).hexdigest()
        result = compute_sha256(str(f))
        assert result == expected

    def test_file_not_found_raises(self, tmp_path):
        """Missing file should raise FileNotFoundError."""
        missing = tmp_path / "nonexistent.qcow2"
        with pytest.raises(FileNotFoundError):
            compute_sha256(str(missing))

    def test_empty_file(self, tmp_path):
        """Empty file should return the SHA256 of empty bytes."""
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")

        expected = hashlib.sha256(b"").hexdigest()
        result = compute_sha256(str(f))
        assert result == expected

    def test_accepts_path_object(self, tmp_path):
        """Should accept pathlib.Path as well as str."""
        from pathlib import Path

        content = b"path object test"
        f = tmp_path / "pathobj.bin"
        f.write_bytes(content)

        expected = hashlib.sha256(content).hexdigest()
        result = compute_sha256(Path(f))
        assert result == expected


class TestValidateQcow2:
    """Tests for validate_qcow2 function."""

    def _write_qcow2_header(self, path, magic=QCOW2_MAGIC, extra=b"\x00" * 100):
        """Write a minimal file with the given magic bytes."""
        path.write_bytes(magic + extra)

    def test_valid_magic_passes(self, tmp_path):
        """File with correct QFI magic bytes should pass validation."""
        f = tmp_path / "valid.qcow2"
        self._write_qcow2_header(f)

        with patch("app.utils.image_integrity.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout=json.dumps({"format": "qcow2"}),
                stderr="",
            )
            ok, msg = validate_qcow2(str(f))

        assert ok is True
        assert msg == ""

    def test_invalid_magic_fails(self, tmp_path):
        """File with wrong magic bytes should fail."""
        f = tmp_path / "bad.qcow2"
        f.write_bytes(b"\x00\x00\x00\x00" + b"\x00" * 100)

        ok, msg = validate_qcow2(str(f))

        assert ok is False
        assert "Invalid qcow2 magic" in msg

    def test_too_small_file(self, tmp_path):
        """File smaller than 4 bytes should fail."""
        f = tmp_path / "tiny.qcow2"
        f.write_bytes(b"QF")  # Only 2 bytes

        ok, msg = validate_qcow2(str(f))

        assert ok is False
        assert "too small" in msg.lower()

    def test_file_not_found(self, tmp_path):
        """Missing file should return False with descriptive message."""
        missing = tmp_path / "ghost.qcow2"

        ok, msg = validate_qcow2(str(missing))

        assert ok is False
        assert "not found" in msg.lower()

    def test_qemu_img_not_installed_passes_magic_only(self, tmp_path):
        """When qemu-img is not installed, magic-only check should still pass."""
        f = tmp_path / "no_qemu.qcow2"
        self._write_qcow2_header(f)

        with patch(
            "app.utils.image_integrity.subprocess.run",
            side_effect=FileNotFoundError("qemu-img not found"),
        ):
            ok, msg = validate_qcow2(str(f))

        assert ok is True
        assert msg == ""

    def test_wrong_format_fails(self, tmp_path):
        """qemu-img reporting a non-qcow2 format should fail."""
        f = tmp_path / "raw_disguised.qcow2"
        self._write_qcow2_header(f)

        with patch("app.utils.image_integrity.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout=json.dumps({"format": "raw"}),
                stderr="",
            )
            ok, msg = validate_qcow2(str(f))

        assert ok is False
        assert "raw" in msg
        assert "expected 'qcow2'" in msg

    def test_timeout_silently_skipped(self, tmp_path):
        """subprocess.TimeoutExpired should be silently skipped (magic already passed)."""
        f = tmp_path / "slow.qcow2"
        self._write_qcow2_header(f)

        with patch(
            "app.utils.image_integrity.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="qemu-img", timeout=30),
        ):
            ok, msg = validate_qcow2(str(f))

        assert ok is True
        assert msg == ""

    def test_qemu_img_json_decode_error_skipped(self, tmp_path):
        """Malformed JSON from qemu-img should be silently skipped."""
        f = tmp_path / "badjson.qcow2"
        self._write_qcow2_header(f)

        with patch("app.utils.image_integrity.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout="not valid json{{{",
                stderr="",
            )
            ok, msg = validate_qcow2(str(f))

        assert ok is True
        assert msg == ""

    def test_qemu_img_nonzero_return_code_skipped(self, tmp_path):
        """Non-zero return code from qemu-img should be silently skipped."""
        f = tmp_path / "errqemu.qcow2"
        self._write_qcow2_header(f)

        with patch("app.utils.image_integrity.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1,
                stdout="",
                stderr="error opening file",
            )
            ok, msg = validate_qcow2(str(f))

        # Non-zero return code means qemu-img check is skipped; magic passed
        assert ok is True
        assert msg == ""
