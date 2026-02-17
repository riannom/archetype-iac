"""Tests for app/tasks/iol_build.py - IOL Docker image build tasks."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestBuildIolImage:
    """Tests for the build_iol_image function."""

    def test_returns_error_when_binary_not_found(self):
        """Should return error when IOL binary doesn't exist."""
        from app.tasks.iol_build import build_iol_image

        result = build_iol_image(
            iol_path="/nonexistent/path/iol.bin",
            device_id="iol-xe",
        )

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_returns_error_when_assets_missing(self, tmp_path):
        """Should return error when Dockerfile/entrypoint.sh are missing."""
        from app.tasks.iol_build import build_iol_image

        # Create IOL binary
        iol_file = tmp_path / "iol.bin"
        iol_file.write_bytes(b"\x7fELF fake binary")

        with patch("app.tasks.iol_build._IOL_ASSETS_DIR", tmp_path / "missing"):
            result = build_iol_image(
                iol_path=str(iol_file),
                device_id="iol-xe",
            )

            assert result["success"] is False
            assert "assets missing" in result["error"].lower()

    def test_returns_error_on_build_failure(self, tmp_path):
        """Should return error when docker build fails."""
        from app.tasks.iol_build import build_iol_image

        iol_file = tmp_path / "iol.bin"
        iol_file.write_bytes(b"\x7fELF fake binary")

        # Create fake assets directory
        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        (assets_dir / "Dockerfile").write_text("FROM scratch")
        (assets_dir / "entrypoint.sh").write_text("#!/bin/bash")

        with patch("app.tasks.iol_build._IOL_ASSETS_DIR", assets_dir):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1,
                    stdout="",
                    stderr="Error: build failed",
                )

                result = build_iol_image(
                    iol_path=str(iol_file),
                    device_id="iol-xe",
                )

                assert result["success"] is False
                assert "failed" in result["error"].lower()

    def test_returns_error_on_timeout(self, tmp_path):
        """Should return error when build times out."""
        from app.tasks.iol_build import build_iol_image

        iol_file = tmp_path / "iol.bin"
        iol_file.write_bytes(b"\x7fELF fake binary")

        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        (assets_dir / "Dockerfile").write_text("FROM scratch")
        (assets_dir / "entrypoint.sh").write_text("#!/bin/bash")

        with patch("app.tasks.iol_build._IOL_ASSETS_DIR", assets_dir):
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = subprocess.TimeoutExpired("docker", 600)

                result = build_iol_image(
                    iol_path=str(iol_file),
                    device_id="iol-xe",
                )

                assert result["success"] is False
                assert "timed out" in result["error"].lower()

    def test_success_builds_correct_tag(self, tmp_path):
        """Should build with correct image tag on success."""
        from app.tasks.iol_build import build_iol_image

        iol_file = tmp_path / "i86bi-linux-l3-adventerprisek9-15.6.1T.bin"
        iol_file.write_bytes(b"\x7fELF fake binary")

        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        (assets_dir / "Dockerfile").write_text("FROM scratch")
        (assets_dir / "entrypoint.sh").write_text("#!/bin/bash")

        with patch("app.tasks.iol_build._IOL_ASSETS_DIR", assets_dir):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="Successfully built abc123",
                    stderr="",
                )
                with patch("app.tasks.iol_build._update_manifest_with_iol_image"):
                    result = build_iol_image(
                        iol_path=str(iol_file),
                        device_id="iol-xe",
                        version="15.6.1T",
                    )

                    assert result["success"] is True
                    assert result["docker_image"] == "archetype/iol-xe:15.6.1T"
                    assert result["device_id"] == "iol-xe"

    def test_success_uses_latest_when_no_version(self, tmp_path):
        """Should use 'latest' tag when no version provided."""
        from app.tasks.iol_build import build_iol_image

        iol_file = tmp_path / "iol.bin"
        iol_file.write_bytes(b"\x7fELF fake binary")

        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        (assets_dir / "Dockerfile").write_text("FROM scratch")
        (assets_dir / "entrypoint.sh").write_text("#!/bin/bash")

        with patch("app.tasks.iol_build._IOL_ASSETS_DIR", assets_dir):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="", stderr=""
                )
                with patch("app.tasks.iol_build._update_manifest_with_iol_image"):
                    result = build_iol_image(
                        iol_path=str(iol_file),
                        device_id="iol-l2",
                    )

                    assert result["success"] is True
                    assert result["docker_image"] == "archetype/iol-l2:latest"

    def test_cleans_up_build_directory(self, tmp_path):
        """Should clean up temp build directory after build."""
        from app.tasks.iol_build import build_iol_image

        iol_file = tmp_path / "iol.bin"
        iol_file.write_bytes(b"\x7fELF fake binary")

        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        (assets_dir / "Dockerfile").write_text("FROM scratch")
        (assets_dir / "entrypoint.sh").write_text("#!/bin/bash")

        build_dirs = []
        orig_mkdtemp = __import__("tempfile").mkdtemp

        def tracking_mkdtemp(**kwargs):
            d = orig_mkdtemp(**kwargs)
            build_dirs.append(d)
            return d

        with patch("app.tasks.iol_build._IOL_ASSETS_DIR", assets_dir):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="", stderr=""
                )
                with patch("app.tasks.iol_build._update_manifest_with_iol_image"):
                    with patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp):
                        build_iol_image(
                            iol_path=str(iol_file),
                            device_id="iol-xe",
                        )

        # Build directory should have been cleaned up
        for d in build_dirs:
            assert not Path(d).exists(), f"Build dir {d} was not cleaned up"

    def test_copies_binary_as_iol_bin(self, tmp_path):
        """Should copy IOL binary as iol.bin in build directory."""
        from app.tasks.iol_build import build_iol_image

        iol_file = tmp_path / "i86bi-linux-l3-adventerprisek9-15.6.1T.bin"
        iol_file.write_bytes(b"\x7fELF fake binary content")

        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        (assets_dir / "Dockerfile").write_text("FROM scratch")
        (assets_dir / "entrypoint.sh").write_text("#!/bin/bash")

        captured_cwd = []

        def capture_run(cmd, **kwargs):
            captured_cwd.append(kwargs.get("cwd"))
            # Check that iol.bin exists in build dir
            build_dir = Path(kwargs.get("cwd", ""))
            assert (build_dir / "iol.bin").exists()
            assert (build_dir / "iol.bin").read_bytes() == b"\x7fELF fake binary content"
            assert (build_dir / "Dockerfile").exists()
            assert (build_dir / "entrypoint.sh").exists()
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("app.tasks.iol_build._IOL_ASSETS_DIR", assets_dir):
            with patch("subprocess.run", side_effect=capture_run):
                with patch("app.tasks.iol_build._update_manifest_with_iol_image"):
                    result = build_iol_image(
                        iol_path=str(iol_file),
                        device_id="iol-xe",
                    )

        assert result["success"] is True


class TestUpdateManifestWithIolImage:
    """Tests for _update_manifest_with_iol_image."""

    def test_creates_new_entry(self):
        """Should create new Docker image entry in manifest."""
        from app.tasks.iol_build import _update_manifest_with_iol_image

        manifest = {"images": []}

        with patch("app.tasks.iol_build.load_manifest", return_value=manifest):
            with patch("app.tasks.iol_build.find_image_by_id", return_value=None):
                with patch("app.tasks.iol_build.save_manifest") as mock_save:
                    with patch("app.tasks.iol_build.create_image_entry") as mock_create:
                        mock_create.return_value = {
                            "id": "docker:archetype/iol-xe:15.6.1T",
                            "kind": "docker",
                            "reference": "archetype/iol-xe:15.6.1T",
                        }

                        _update_manifest_with_iol_image(
                            iol_path="/path/to/iol.bin",
                            docker_image="archetype/iol-xe:15.6.1T",
                            device_id="iol-xe",
                            version="15.6.1T",
                            iol_image_id="iol:iol.bin",
                        )

                        mock_save.assert_called_once()
                        assert len(manifest["images"]) == 1

    def test_updates_existing_entry(self):
        """Should update existing entry if found."""
        from app.tasks.iol_build import _update_manifest_with_iol_image

        existing = {
            "id": "docker:archetype/iol-xe:15.6.1T",
            "kind": "docker",
            "is_default": False,
        }
        manifest = {"images": [existing]}

        with patch("app.tasks.iol_build.load_manifest", return_value=manifest):
            with patch("app.tasks.iol_build.find_image_by_id", return_value=existing):
                with patch("app.tasks.iol_build.save_manifest"):
                    _update_manifest_with_iol_image(
                        iol_path="/path/to/iol.bin",
                        docker_image="archetype/iol-xe:15.6.1T",
                        device_id="iol-xe",
                        version="15.6.1T",
                    )

                    assert existing["is_default"] is True


class TestGetIolBuildStatus:
    """Tests for get_iol_build_status."""

    def test_returns_none_when_no_build(self):
        """Should return None when no build found."""
        from app.tasks.iol_build import get_iol_build_status

        with patch("app.tasks.iol_build.load_manifest") as mock_load:
            mock_load.return_value = {"images": []}

            result = get_iol_build_status("iol:iol.bin")

            assert result is None

    def test_returns_build_info(self):
        """Should return build info when Docker image was built from IOL."""
        from app.tasks.iol_build import get_iol_build_status

        docker_image = {
            "id": "docker:archetype/iol-xe:15.6.1T",
            "reference": "archetype/iol-xe:15.6.1T",
            "built_from": "iol:iol.bin",
        }

        with patch("app.tasks.iol_build.load_manifest") as mock_load:
            mock_load.return_value = {"images": [docker_image]}

            result = get_iol_build_status("iol:iol.bin")

            assert result is not None
            assert result["built"] is True
            assert result["docker_reference"] == "archetype/iol-xe:15.6.1T"
