"""Tests for netlab command execution (netlab.py).

This module tests:
- run_netlab_command() subprocess wrapper
- Command execution with and without workspace
- Return value handling (returncode, stdout, stderr)
- Error handling for subprocess failures
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.netlab import run_netlab_command


class TestRunNetlabCommand:
    """Tests for run_netlab_command function."""

    def test_returns_tuple_of_three_elements(self):
        """Test that function returns a tuple with (returncode, stdout, stderr)."""
        with patch("app.netlab.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="output",
                stderr="errors",
            )

            result = run_netlab_command(["echo", "test"])

            assert isinstance(result, tuple)
            assert len(result) == 3

    def test_returns_correct_returncode(self):
        """Test that function returns the process returncode."""
        with patch("app.netlab.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=42,
                stdout="",
                stderr="",
            )

            returncode, stdout, stderr = run_netlab_command(["cmd"])

            assert returncode == 42

    def test_returns_stdout(self):
        """Test that function returns captured stdout."""
        with patch("app.netlab.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="This is stdout content\nWith multiple lines",
                stderr="",
            )

            returncode, stdout, stderr = run_netlab_command(["cmd"])

            assert stdout == "This is stdout content\nWith multiple lines"

    def test_returns_stderr(self):
        """Test that function returns captured stderr."""
        with patch("app.netlab.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="Error: Something went wrong",
            )

            returncode, stdout, stderr = run_netlab_command(["cmd"])

            assert stderr == "Error: Something went wrong"

    def test_passes_args_to_subprocess(self):
        """Test that command arguments are passed to subprocess."""
        with patch("app.netlab.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr=""
            )

            run_netlab_command(["docker", "compose", "up", "-d"])

            mock_run.assert_called_once()
            args, kwargs = mock_run.call_args
            assert args[0] == ["docker", "compose", "up", "-d"]

    def test_workspace_sets_cwd(self):
        """Test that workspace parameter sets the working directory."""
        with patch("app.netlab.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr=""
            )

            workspace = Path("/var/lib/archetype/lab-123")
            run_netlab_command(["cmd"], workspace=workspace)

            mock_run.assert_called_once()
            args, kwargs = mock_run.call_args
            assert kwargs["cwd"] == "/var/lib/archetype/lab-123"

    def test_workspace_none_sets_cwd_none(self):
        """Test that workspace=None results in cwd=None."""
        with patch("app.netlab.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr=""
            )

            run_netlab_command(["cmd"], workspace=None)

            mock_run.assert_called_once()
            args, kwargs = mock_run.call_args
            assert kwargs["cwd"] is None

    def test_capture_output_enabled(self):
        """Test that capture_output is enabled."""
        with patch("app.netlab.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr=""
            )

            run_netlab_command(["cmd"])

            args, kwargs = mock_run.call_args
            assert kwargs["capture_output"] is True

    def test_text_mode_enabled(self):
        """Test that text mode is enabled (returns strings not bytes)."""
        with patch("app.netlab.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr=""
            )

            run_netlab_command(["cmd"])

            args, kwargs = mock_run.call_args
            assert kwargs["text"] is True

    def test_check_disabled(self):
        """Test that check=False (doesn't raise on non-zero exit)."""
        with patch("app.netlab.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="Error"
            )

            # Should not raise even with non-zero return code
            returncode, stdout, stderr = run_netlab_command(["cmd"])

            args, kwargs = mock_run.call_args
            assert kwargs["check"] is False
            assert returncode == 1


class TestRunNetlabCommandIntegration:
    """Integration tests that run actual commands."""

    def test_echo_command(self):
        """Test running a simple echo command."""
        returncode, stdout, stderr = run_netlab_command(["echo", "hello"])

        assert returncode == 0
        assert stdout.strip() == "hello"
        assert stderr == ""

    def test_command_with_workspace(self, tmp_path):
        """Test running command in specific workspace directory."""
        # Create a test file in the temp directory
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        # Run ls in that directory
        returncode, stdout, stderr = run_netlab_command(["ls"], workspace=tmp_path)

        assert returncode == 0
        assert "test.txt" in stdout

    def test_nonexistent_command(self):
        """Test running a command that doesn't exist."""
        # This should raise FileNotFoundError, which is not caught
        with pytest.raises(FileNotFoundError):
            run_netlab_command(["nonexistent_command_xyz"])

    def test_failing_command(self):
        """Test running a command that fails (non-zero exit)."""
        returncode, stdout, stderr = run_netlab_command(["ls", "/nonexistent_path_xyz"])

        assert returncode != 0
        # stderr should contain error message
        assert len(stderr) > 0

    def test_command_with_stdin_closed(self):
        """Test that command runs without stdin (non-interactive)."""
        # cat without arguments would hang waiting for stdin
        # but with capture_output it shouldn't
        returncode, stdout, stderr = run_netlab_command(
            ["python", "-c", "import sys; print(sys.stdin.isatty())"]
        )

        assert returncode == 0
        # stdin should not be a tty (False)
        assert "False" in stdout

    def test_multiline_output(self):
        """Test capturing multiline output."""
        returncode, stdout, stderr = run_netlab_command(
            ["python", "-c", "print('line1'); print('line2'); print('line3')"]
        )

        assert returncode == 0
        lines = stdout.strip().split("\n")
        assert len(lines) == 3
        assert lines == ["line1", "line2", "line3"]

    def test_unicode_output(self):
        """Test capturing unicode characters in output."""
        returncode, stdout, stderr = run_netlab_command(
            ["python", "-c", "print('Hello World')"]
        )

        assert returncode == 0
        # Unicode should be preserved
        assert "Hello" in stdout

    def test_empty_output(self):
        """Test command with no output."""
        returncode, stdout, stderr = run_netlab_command(["true"])

        assert returncode == 0
        assert stdout == ""
        assert stderr == ""


class TestRunNetlabCommandEdgeCases:
    """Edge case tests for run_netlab_command."""

    def test_empty_args_list(self):
        """Test with empty args list raises error."""
        with pytest.raises((IndexError, FileNotFoundError)):
            run_netlab_command([])

    def test_workspace_as_string_path(self):
        """Test that workspace can be a Path object."""
        with patch("app.netlab.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr=""
            )

            workspace = Path("/some/path")
            run_netlab_command(["cmd"], workspace=workspace)

            args, kwargs = mock_run.call_args
            assert kwargs["cwd"] == "/some/path"

    def test_large_output(self):
        """Test handling large output."""
        # Generate 10KB of output
        returncode, stdout, stderr = run_netlab_command(
            ["python", "-c", "print('x' * 10000)"]
        )

        assert returncode == 0
        assert len(stdout.strip()) == 10000

    def test_stderr_and_stdout_separate(self):
        """Test that stdout and stderr are captured separately."""
        returncode, stdout, stderr = run_netlab_command(
            [
                "python",
                "-c",
                "import sys; print('stdout'); print('stderr', file=sys.stderr)",
            ]
        )

        assert returncode == 0
        assert "stdout" in stdout
        assert "stderr" in stderr
        assert "stderr" not in stdout
        assert "stdout" not in stderr

    def test_exit_code_preserved(self):
        """Test various exit codes are preserved."""
        for expected_code in [0, 1, 2, 42, 127]:
            returncode, _, _ = run_netlab_command(
                ["python", "-c", f"import sys; sys.exit({expected_code})"]
            )
            assert returncode == expected_code

    def test_workspace_with_spaces(self, tmp_path):
        """Test workspace path with spaces."""
        # Create directory with spaces
        space_dir = tmp_path / "path with spaces"
        space_dir.mkdir()
        test_file = space_dir / "test.txt"
        test_file.write_text("content")

        returncode, stdout, stderr = run_netlab_command(["ls"], workspace=space_dir)

        assert returncode == 0
        assert "test.txt" in stdout
