"""Tests for agent image management endpoints.

Source: agent/routers/images.py
Covers: list_images, receive_image (docker + file-based), pull progress,
        active transfers, backfill checksums, check image, transfer state
        persistence and crash recovery.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent.config import settings
from agent.main import app
from agent.routers.images import (
    _image_pull_jobs,
    _load_persisted_transfer_state,
    _persist_transfer_state,
    _clear_persisted_transfer_state,
    _TRANSFER_STATE_FILE,
)
from agent.schemas import DockerImageInfo, ImagePullProgress


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_pull_jobs():
    """Reset module-level pull job state between tests."""
    _image_pull_jobs.clear()
    yield
    _image_pull_jobs.clear()


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(settings, "controller_secret", "")


# ---------------------------------------------------------------------------
# TestListImages
# ---------------------------------------------------------------------------


class TestListImages:
    """Tests for GET /images."""

    def test_returns_inventory(self, client):
        """Returns a list of Docker images on the agent."""
        fake_images = [
            DockerImageInfo(id="sha256:aaa", tags=["ceos:4.28"], size_bytes=1024),
            DockerImageInfo(id="sha256:bbb", tags=["srlinux:latest"], size_bytes=2048),
        ]
        with patch("agent.routers.images._get_docker_images", return_value=fake_images):
            resp = client.get("/images")

        body = resp.json()
        assert len(body["images"]) == 2
        assert body["images"][0]["id"] == "sha256:aaa"

    def test_empty_inventory(self, client):
        """Empty image list returns successfully."""
        with patch("agent.routers.images._get_docker_images", return_value=[]):
            resp = client.get("/images")

        body = resp.json()
        assert body["images"] == []

    def test_image_tags_present(self, client):
        """Image tags are included in response."""
        fake_images = [
            DockerImageInfo(id="sha256:ccc", tags=["alpine:3.18", "alpine:latest"], size_bytes=512),
        ]
        with patch("agent.routers.images._get_docker_images", return_value=fake_images):
            resp = client.get("/images")

        body = resp.json()
        assert "alpine:3.18" in body["images"][0]["tags"]
        assert "alpine:latest" in body["images"][0]["tags"]


# ---------------------------------------------------------------------------
# TestReceiveDockerImage
# ---------------------------------------------------------------------------


class TestReceiveDockerImage:
    """Tests for POST /images/receive with Docker tar images."""

    def test_success(self, client):
        """Successfully receives and loads a Docker image tar."""
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "Loaded image: ceos:4.28.0F"
        fake_result.stderr = ""

        with patch("subprocess.run", return_value=fake_result):
            resp = client.post(
                "/images/receive",
                params={
                    "reference": "ceos:4.28.0F",
                    "total_bytes": "100",
                    "job_id": "j1",
                    "image_id": "test",
                    "sha256": "",
                },
                files={"file": ("image.tar", b"fake-tar-content", "application/octet-stream")},
            )

        body = resp.json()
        assert body["success"] is True
        assert "ceos:4.28.0F" in body["loaded_images"]

    def test_docker_load_failure(self, client):
        """docker load failure surfaces error."""
        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stdout = ""
        fake_result.stderr = "invalid tar"

        with patch("subprocess.run", return_value=fake_result):
            resp = client.post(
                "/images/receive",
                params={
                    "reference": "bad:image",
                    "total_bytes": "50",
                    "job_id": "j2",
                    "image_id": "test",
                    "sha256": "",
                },
                files={"file": ("image.tar", b"bad", "application/octet-stream")},
            )

        body = resp.json()
        assert body["success"] is False
        assert "invalid tar" in body["error"]

    def test_progress_tracking(self, client):
        """Progress is tracked during image receive."""
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "Loaded image: test:latest"
        fake_result.stderr = ""

        with patch("subprocess.run", return_value=fake_result):
            resp = client.post(
                "/images/receive",
                params={
                    "reference": "test:latest",
                    "total_bytes": "100",
                    "job_id": "track-1",
                    "image_id": "test",
                    "sha256": "",
                },
                files={"file": ("image.tar", b"data", "application/octet-stream")},
            )

        assert resp.json()["success"] is True
        # After completion, job should be completed
        assert _image_pull_jobs["track-1"].status == "completed"


# ---------------------------------------------------------------------------
# TestReceiveFileBasedImage
# ---------------------------------------------------------------------------


class TestReceiveFileBasedImage:
    """Tests for POST /images/receive with file-based images."""

    def test_qcow2_success(self, client, tmp_path, monkeypatch):
        """File-based qcow2 image stored to correct destination."""
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path / "workspace"))
        monkeypatch.setattr(settings, "enable_libvirt", True)
        dest_dir = tmp_path / "workspace" / "images"
        dest_dir.mkdir(parents=True, exist_ok=True)

        image_path = f"{dest_dir}/router.qcow2"

        resp = client.post(
            "/images/receive",
            params={
                "reference": image_path,
                "total_bytes": "10",
                "job_id": "j4",
                "image_id": "test",
                "sha256": "",
            },
            files={"file": ("router.qcow2", b"fake-qcow2", "application/octet-stream")},
        )

        body = resp.json()
        assert body["success"] is True
        assert os.path.exists(image_path)
        # Sidecar checksum should also exist
        assert os.path.exists(image_path + ".sha256")

    def test_checksum_mismatch_deletes_file(self, client, tmp_path, monkeypatch):
        """File with wrong checksum is rejected and deleted."""
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path / "workspace"))
        monkeypatch.setattr(settings, "enable_libvirt", True)
        dest = tmp_path / "workspace" / "images"
        dest.mkdir(parents=True, exist_ok=True)

        image_path = f"{dest}/test.qcow2"

        resp = client.post(
            "/images/receive",
            params={
                "reference": image_path,
                "total_bytes": "10",
                "job_id": "j3",
                "image_id": "test",
                "sha256": "0" * 64,
            },
            files={"file": ("test.qcow2", b"qcow2-bytes", "application/octet-stream")},
        )

        body = resp.json()
        assert body["success"] is False
        assert "Checksum mismatch" in body["error"]
        assert not os.path.exists(image_path)

    def test_path_traversal_rejected(self, client, tmp_path, monkeypatch):
        """Destination outside allowed bases is rejected."""
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path / "workspace"))
        monkeypatch.setattr(settings, "enable_libvirt", True)

        resp = client.post(
            "/images/receive",
            params={
                "reference": "/tmp/evil.qcow2",
                "total_bytes": "10",
                "job_id": "j5",
                "image_id": "test",
                "sha256": "",
            },
            files={"file": ("evil.qcow2", b"evil", "application/octet-stream")},
        )

        body = resp.json()
        assert body["success"] is False
        assert "Invalid destination" in body["error"]

    def test_libvirt_disabled_rejects_qcow2(self, client, monkeypatch):
        """qcow2 upload when libvirt is disabled returns error."""
        monkeypatch.setattr(settings, "enable_libvirt", False)

        resp = client.post(
            "/images/receive",
            params={
                "reference": "/var/lib/archetype/images/test.qcow2",
                "total_bytes": "10",
                "job_id": "j6",
                "image_id": "test",
                "sha256": "",
            },
            files={"file": ("test.qcow2", b"data", "application/octet-stream")},
        )

        body = resp.json()
        assert body["success"] is False
        assert "libvirt" in body["error"].lower()

    def test_non_absolute_path_rejected(self, client, monkeypatch):
        """Non-absolute path for file-based image is rejected."""
        monkeypatch.setattr(settings, "enable_libvirt", True)

        resp = client.post(
            "/images/receive",
            params={
                "reference": "relative/path.qcow2",
                "total_bytes": "10",
                "job_id": "j7",
                "image_id": "test",
                "sha256": "",
            },
            files={"file": ("path.qcow2", b"data", "application/octet-stream")},
        )

        body = resp.json()
        assert body["success"] is False
        assert "absolute" in body["error"].lower()


# ---------------------------------------------------------------------------
# TestPullProgress
# ---------------------------------------------------------------------------


class TestPullProgress:
    """Tests for GET /images/pull/{job_id}/progress."""

    def test_known_job(self, client):
        """Known job returns its current progress."""
        _image_pull_jobs["abc"] = ImagePullProgress(
            job_id="abc", status="transferring",
            progress_percent=50, bytes_transferred=1000, total_bytes=2000,
        )

        resp = client.get("/images/pull/abc/progress")
        body = resp.json()
        assert body["status"] == "transferring"
        assert body["progress_percent"] == 50

    def test_unknown_job(self, client):
        """Missing job returns status=unknown."""
        resp = client.get("/images/pull/nonexistent/progress")
        body = resp.json()
        assert body["status"] == "unknown"
        assert "not found" in body["error"].lower()

    def test_completed_job(self, client):
        """Completed job returns 100% progress."""
        _image_pull_jobs["done"] = ImagePullProgress(
            job_id="done", status="completed",
            progress_percent=100, bytes_transferred=5000, total_bytes=5000,
        )

        resp = client.get("/images/pull/done/progress")
        body = resp.json()
        assert body["status"] == "completed"
        assert body["progress_percent"] == 100

    def test_failed_job(self, client):
        """Failed job returns error details."""
        _image_pull_jobs["fail"] = ImagePullProgress(
            job_id="fail", status="failed", error="connection reset",
        )

        resp = client.get("/images/pull/fail/progress")
        body = resp.json()
        assert body["status"] == "failed"
        assert "connection reset" in body["error"]


# ---------------------------------------------------------------------------
# TestActiveTransfers
# ---------------------------------------------------------------------------


class TestActiveTransfers:
    """Tests for GET /images/active-transfers."""

    def test_only_non_terminal_jobs(self, client):
        """Only pending/transferring/loading jobs appear."""
        _image_pull_jobs["active1"] = ImagePullProgress(
            job_id="active1", status="transferring", progress_percent=30,
        )
        _image_pull_jobs["done1"] = ImagePullProgress(
            job_id="done1", status="completed", progress_percent=100,
        )
        _image_pull_jobs["fail1"] = ImagePullProgress(
            job_id="fail1", status="failed", error="boom",
        )

        resp = client.get("/images/active-transfers")
        body = resp.json()
        assert "active1" in body["active_jobs"]
        assert "done1" not in body["active_jobs"]
        assert "fail1" not in body["active_jobs"]

    def test_includes_uptime(self, client):
        """Response includes agent uptime."""
        resp = client.get("/images/active-transfers")
        body = resp.json()
        assert "agent_uptime_seconds" in body
        assert body["agent_uptime_seconds"] >= 0


# ---------------------------------------------------------------------------
# TestCheckImage
# ---------------------------------------------------------------------------


class TestCheckImage:
    """Tests for GET /images/{reference:path}."""

    def test_docker_image_exists(self, client):
        """Docker image found returns exists=True."""
        mock_img = MagicMock()
        mock_img.id = "sha256:aaa"
        mock_img.tags = ["ceos:4.28"]
        mock_img.attrs = {"Size": 500000, "Created": "2024-01-01T00:00:00Z"}

        mock_docker = MagicMock()
        mock_docker.images.get.return_value = mock_img

        with patch("agent.routers.images.get_docker_client", return_value=mock_docker):
            resp = client.get("/images/ceos:4.28")

        body = resp.json()
        assert body["exists"] is True
        assert body["image"]["id"] == "sha256:aaa"

    def test_docker_image_not_found(self, client):
        """Docker image not found returns exists=False."""
        import docker
        mock_docker = MagicMock()
        mock_docker.images.get.side_effect = docker.errors.ImageNotFound("not found")

        with patch("agent.routers.images.get_docker_client", return_value=mock_docker):
            resp = client.get("/images/nonexistent:latest")

        body = resp.json()
        assert body["exists"] is False

    def test_file_based_image_exists(self, client, tmp_path, monkeypatch):
        """File-based image check returns exists=True when file present."""
        monkeypatch.setattr(settings, "enable_libvirt", True)

        image_path = tmp_path / "router.qcow2"
        image_path.write_bytes(b"fake")
        sidecar = tmp_path / "router.qcow2.sha256"
        sidecar.write_text("abcdef123456")

        resp = client.get(f"/images/{image_path}")

        body = resp.json()
        assert body["exists"] is True
        assert body["sha256"] == "abcdef123456"

    def test_file_based_image_missing(self, client, monkeypatch):
        """File-based image check returns exists=False when file absent."""
        monkeypatch.setattr(settings, "enable_libvirt", True)

        resp = client.get("/images//var/lib/archetype/images/nonexistent.qcow2")

        body = resp.json()
        assert body["exists"] is False

    def test_qcow2_libvirt_disabled(self, client, monkeypatch):
        """qcow2 check with libvirt disabled returns exists=False."""
        monkeypatch.setattr(settings, "enable_libvirt", False)

        resp = client.get("/images//var/lib/archetype/images/test.qcow2")

        body = resp.json()
        assert body["exists"] is False


# ---------------------------------------------------------------------------
# TestBackfillChecksums
# ---------------------------------------------------------------------------


class TestBackfillChecksums:
    """Tests for POST /images/backfill-checksums."""

    def test_no_images_dir(self, client):
        """Returns zero updates when image dir does not exist."""
        with patch("agent.routers.images.os.path.isdir", return_value=False):
            resp = client.post("/images/backfill-checksums")

        body = resp.json()
        assert body["updated"] == 0


# ---------------------------------------------------------------------------
# TestTransferStatePersistence
# ---------------------------------------------------------------------------


class TestTransferStatePersistence:
    """Tests for transfer state persistence and crash recovery."""

    def test_persist_and_load_marks_interrupted_failed(self, tmp_path, monkeypatch):
        """Persisted in-progress jobs are marked failed on reload."""
        state_file = tmp_path / ".active_transfers.json"
        monkeypatch.setattr("agent.routers.images._TRANSFER_STATE_FILE", state_file)

        _image_pull_jobs["crash-job"] = ImagePullProgress(
            job_id="crash-job", status="transferring",
            progress_percent=40, bytes_transferred=500,
            total_bytes=1000, started_at=time.time(),
        )
        _persist_transfer_state()
        assert state_file.exists()

        _image_pull_jobs.clear()
        _load_persisted_transfer_state()

        assert "crash-job" in _image_pull_jobs
        assert _image_pull_jobs["crash-job"].status == "failed"
        assert "restarted" in _image_pull_jobs["crash-job"].error.lower()

    def test_clear_removes_file(self, tmp_path, monkeypatch):
        """Clear removes the persisted state file."""
        state_file = tmp_path / ".active_transfers.json"
        state_file.write_text("{}")
        monkeypatch.setattr("agent.routers.images._TRANSFER_STATE_FILE", state_file)

        _clear_persisted_transfer_state()
        assert not state_file.exists()

    def test_load_missing_file_is_noop(self, tmp_path, monkeypatch):
        """Loading when no state file exists is a no-op."""
        state_file = tmp_path / ".active_transfers.json"
        monkeypatch.setattr("agent.routers.images._TRANSFER_STATE_FILE", state_file)

        _load_persisted_transfer_state()
        assert len(_image_pull_jobs) == 0
