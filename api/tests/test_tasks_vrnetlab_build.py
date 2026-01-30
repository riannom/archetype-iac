"""Tests for app/tasks/vrnetlab_build.py - vrnetlab Docker image build tasks."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestGetVrnetlabPath:
    """Tests for the _get_vrnetlab_path function."""

    def test_returns_env_variable_when_set(self, monkeypatch):
        """Should return VRNETLAB_PATH environment variable when set."""
        from app.tasks.vrnetlab_build import _get_vrnetlab_path

        monkeypatch.setenv("VRNETLAB_PATH", "/custom/vrnetlab/path")

        result = _get_vrnetlab_path()

        assert result == "/custom/vrnetlab/path"

    def test_returns_settings_default(self, monkeypatch):
        """Should return settings.vrnetlab_path when env not set."""
        from app.tasks.vrnetlab_build import _get_vrnetlab_path
        from app.config import settings

        monkeypatch.delenv("VRNETLAB_PATH", raising=False)

        result = _get_vrnetlab_path()

        assert result == settings.vrnetlab_path


class TestBuildVrnetlabImage:
    """Tests for the build_vrnetlab_image function."""

    def test_returns_error_when_qcow2_not_found(self, tmp_path):
        """Should return error when qcow2 file doesn't exist."""
        from app.tasks.vrnetlab_build import build_vrnetlab_image

        result = build_vrnetlab_image(
            qcow2_path="/nonexistent/path/image.qcow2",
            device_id="c8000v",
            vrnetlab_subdir="cisco/c8000v",
        )

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_returns_error_when_vrnetlab_dir_not_found(self, tmp_path):
        """Should return error when vrnetlab directory doesn't exist."""
        from app.tasks.vrnetlab_build import build_vrnetlab_image

        # Create a qcow2 file
        qcow2_file = tmp_path / "test.qcow2"
        qcow2_file.touch()

        with patch("app.tasks.vrnetlab_build._get_vrnetlab_path") as mock_path:
            mock_path.return_value = "/nonexistent/vrnetlab"

            result = build_vrnetlab_image(
                qcow2_path=str(qcow2_file),
                device_id="c8000v",
                vrnetlab_subdir="cisco/c8000v",
            )

            assert result["success"] is False
            assert "not found" in result["error"].lower()

    def test_returns_error_on_build_failure(self, tmp_path):
        """Should return error when make docker-image fails."""
        from app.tasks.vrnetlab_build import build_vrnetlab_image

        # Create qcow2 file and vrnetlab directory
        qcow2_file = tmp_path / "test.qcow2"
        qcow2_file.touch()
        vrnetlab_dir = tmp_path / "vrnetlab" / "cisco" / "c8000v"
        vrnetlab_dir.mkdir(parents=True)

        with patch("app.tasks.vrnetlab_build._get_vrnetlab_path") as mock_path:
            mock_path.return_value = str(tmp_path / "vrnetlab")
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1,
                    stdout="",
                    stderr="Build failed: missing dependencies",
                )

                result = build_vrnetlab_image(
                    qcow2_path=str(qcow2_file),
                    device_id="c8000v",
                    vrnetlab_subdir="cisco/c8000v",
                )

                assert result["success"] is False
                assert "failed" in result["error"].lower()

    def test_returns_error_on_timeout(self, tmp_path):
        """Should return error when build times out."""
        from app.tasks.vrnetlab_build import build_vrnetlab_image

        # Create qcow2 file and vrnetlab directory
        qcow2_file = tmp_path / "test.qcow2"
        qcow2_file.touch()
        vrnetlab_dir = tmp_path / "vrnetlab" / "cisco" / "c8000v"
        vrnetlab_dir.mkdir(parents=True)

        with patch("app.tasks.vrnetlab_build._get_vrnetlab_path") as mock_path:
            mock_path.return_value = str(tmp_path / "vrnetlab")
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = subprocess.TimeoutExpired("make", 3600)

                result = build_vrnetlab_image(
                    qcow2_path=str(qcow2_file),
                    device_id="c8000v",
                    vrnetlab_subdir="cisco/c8000v",
                )

                assert result["success"] is False
                assert "timed out" in result["error"].lower()

    def test_returns_error_when_image_name_not_parseable(self, tmp_path):
        """Should return error when can't determine built image name."""
        from app.tasks.vrnetlab_build import build_vrnetlab_image

        # Create qcow2 file and vrnetlab directory
        qcow2_file = tmp_path / "test.qcow2"
        qcow2_file.touch()
        vrnetlab_dir = tmp_path / "vrnetlab" / "cisco" / "c8000v"
        vrnetlab_dir.mkdir(parents=True)

        with patch("app.tasks.vrnetlab_build._get_vrnetlab_path") as mock_path:
            mock_path.return_value = str(tmp_path / "vrnetlab")
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="Build completed successfully",
                    stderr="",
                )

                result = build_vrnetlab_image(
                    qcow2_path=str(qcow2_file),
                    device_id="c8000v",
                    vrnetlab_subdir="cisco/c8000v",
                    # No version provided, and output doesn't contain image name
                )

                assert result["success"] is False
                assert "could not determine" in result["error"].lower()

    def test_success_with_parsed_image_name(self, tmp_path):
        """Should succeed when image name is parsed from output."""
        from app.tasks.vrnetlab_build import build_vrnetlab_image

        # Create qcow2 file and vrnetlab directory
        qcow2_file = tmp_path / "test.qcow2"
        qcow2_file.touch()
        vrnetlab_dir = tmp_path / "vrnetlab" / "cisco" / "c8000v"
        vrnetlab_dir.mkdir(parents=True)

        with patch("app.tasks.vrnetlab_build._get_vrnetlab_path") as mock_path:
            mock_path.return_value = str(tmp_path / "vrnetlab")
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="Successfully tagged vrnetlab/vr-c8000v:17.16.01a",
                    stderr="",
                )
                with patch("app.tasks.vrnetlab_build._update_manifest_with_docker_image"):
                    result = build_vrnetlab_image(
                        qcow2_path=str(qcow2_file),
                        device_id="c8000v",
                        vrnetlab_subdir="cisco/c8000v",
                    )

                    assert result["success"] is True
                    assert result["docker_image"] == "vrnetlab/vr-c8000v:17.16.01a"

    def test_cleans_up_copied_file(self, tmp_path):
        """Should clean up the copied qcow2 file after build."""
        from app.tasks.vrnetlab_build import build_vrnetlab_image

        # Create qcow2 file and vrnetlab directory
        qcow2_file = tmp_path / "test.qcow2"
        qcow2_file.write_bytes(b"fake qcow2 content")
        vrnetlab_dir = tmp_path / "vrnetlab" / "cisco" / "c8000v"
        vrnetlab_dir.mkdir(parents=True)

        with patch("app.tasks.vrnetlab_build._get_vrnetlab_path") as mock_path:
            mock_path.return_value = str(tmp_path / "vrnetlab")
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="naming to docker.io/vrnetlab/vr-c8000v:17.16.01a",
                    stderr="",
                )
                with patch("app.tasks.vrnetlab_build._update_manifest_with_docker_image"):
                    build_vrnetlab_image(
                        qcow2_path=str(qcow2_file),
                        device_id="c8000v",
                        vrnetlab_subdir="cisco/c8000v",
                    )

                    # Copied file in vrnetlab dir should be cleaned up
                    copied_path = vrnetlab_dir / "test.qcow2"
                    assert not copied_path.exists()


class TestParseDockerImageFromOutput:
    """Tests for the _parse_docker_image_from_output function."""

    def test_parses_naming_to_format(self):
        """Should parse 'naming to docker.io/...' format."""
        from app.tasks.vrnetlab_build import _parse_docker_image_from_output

        output = """
Step 10/10 : RUN echo "done"
naming to docker.io/vrnetlab/vr-c8000v:17.16.01a
"""
        result = _parse_docker_image_from_output(output, "c8000v", "cisco/c8000v", None)

        assert result == "vrnetlab/vr-c8000v:17.16.01a"

    def test_parses_successfully_tagged_format(self):
        """Should parse 'Successfully tagged ...' format."""
        from app.tasks.vrnetlab_build import _parse_docker_image_from_output

        output = """
Build complete
Successfully tagged vrnetlab/vr-csr1000v:17.03.06
"""
        result = _parse_docker_image_from_output(output, "csr1000v", "cisco/csr1000v", None)

        assert result == "vrnetlab/vr-csr1000v:17.03.06"

    def test_parses_vrnetlab_pattern(self):
        """Should find vrnetlab image pattern in output."""
        from app.tasks.vrnetlab_build import _parse_docker_image_from_output

        output = """
Building vrnetlab/vr-xrv9k:7.5.1
Build finished
"""
        result = _parse_docker_image_from_output(output, "xrv9k", "cisco/xrv9k", None)

        assert result == "vrnetlab/vr-xrv9k:7.5.1"

    def test_fallback_with_version(self):
        """Should construct expected name from version when not parsed."""
        from app.tasks.vrnetlab_build import _parse_docker_image_from_output

        output = "Build completed"

        result = _parse_docker_image_from_output(output, "c8000v", "cisco/c8000v", "17.16.01a")

        assert result == "vrnetlab/vr-c8000v:17.16.01a"

    def test_returns_none_when_nothing_matches(self):
        """Should return None when no pattern matches."""
        from app.tasks.vrnetlab_build import _parse_docker_image_from_output

        output = "Build completed"

        result = _parse_docker_image_from_output(output, "c8000v", "cisco/c8000v", None)

        assert result is None


class TestUpdateManifestWithDockerImage:
    """Tests for the _update_manifest_with_docker_image function."""

    def test_creates_new_image_entry(self, tmp_path):
        """Should create new Docker image entry in manifest."""
        from app.tasks.vrnetlab_build import _update_manifest_with_docker_image

        manifest = {"images": []}

        with patch("app.tasks.vrnetlab_build.load_manifest") as mock_load:
            mock_load.return_value = manifest
            with patch("app.tasks.vrnetlab_build.find_image_by_id") as mock_find:
                mock_find.return_value = None
                with patch("app.tasks.vrnetlab_build.save_manifest") as mock_save:
                    with patch("app.tasks.vrnetlab_build.create_image_entry") as mock_create:
                        mock_create.return_value = {
                            "id": "docker:vrnetlab/vr-c8000v:17.16.01a",
                            "kind": "docker",
                            "reference": "vrnetlab/vr-c8000v:17.16.01a",
                        }

                        _update_manifest_with_docker_image(
                            qcow2_path="/path/to/image.qcow2",
                            docker_image="vrnetlab/vr-c8000v:17.16.01a",
                            device_id="c8000v",
                            version="17.16.01a",
                            qcow2_image_id="qcow2:c8000v:17.16.01a",
                        )

                        mock_save.assert_called_once()

    def test_updates_existing_entry(self):
        """Should update existing entry if found."""
        from app.tasks.vrnetlab_build import _update_manifest_with_docker_image

        existing = {
            "id": "docker:vrnetlab/vr-c8000v:17.16.01a",
            "kind": "docker",
            "reference": "vrnetlab/vr-c8000v:17.16.01a",
            "is_default": False,
        }
        manifest = {"images": [existing]}

        with patch("app.tasks.vrnetlab_build.load_manifest") as mock_load:
            mock_load.return_value = manifest
            with patch("app.tasks.vrnetlab_build.find_image_by_id") as mock_find:
                mock_find.return_value = existing
                with patch("app.tasks.vrnetlab_build.save_manifest") as mock_save:
                    _update_manifest_with_docker_image(
                        qcow2_path="/path/to/image.qcow2",
                        docker_image="vrnetlab/vr-c8000v:17.16.01a",
                        device_id="c8000v",
                        version="17.16.01a",
                    )

                    # Should mark as default
                    assert existing["is_default"] is True
                    mock_save.assert_called_once()


class TestGetBuildStatus:
    """Tests for the get_build_status function."""

    def test_returns_none_when_no_build_found(self):
        """Should return None when no build found for qcow2."""
        from app.tasks.vrnetlab_build import get_build_status

        with patch("app.tasks.vrnetlab_build.load_manifest") as mock_load:
            mock_load.return_value = {"images": []}

            result = get_build_status("qcow2:c8000v:17.16.01a")

            assert result is None

    def test_returns_build_info_when_found(self):
        """Should return build info when Docker image was built from qcow2."""
        from app.tasks.vrnetlab_build import get_build_status

        docker_image = {
            "id": "docker:vrnetlab/vr-c8000v:17.16.01a",
            "reference": "vrnetlab/vr-c8000v:17.16.01a",
            "built_from": "qcow2:c8000v:17.16.01a",
        }

        with patch("app.tasks.vrnetlab_build.load_manifest") as mock_load:
            mock_load.return_value = {"images": [docker_image]}

            result = get_build_status("qcow2:c8000v:17.16.01a")

            assert result is not None
            assert result["built"] is True
            assert result["docker_image_id"] == "docker:vrnetlab/vr-c8000v:17.16.01a"
            assert result["docker_reference"] == "vrnetlab/vr-c8000v:17.16.01a"
