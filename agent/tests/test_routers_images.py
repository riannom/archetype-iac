"""Tests for agent image management endpoints in agent/routers/images.py.

Covers list_images, receive_image (docker + file-based), pull progress,
active transfers, and persisted transfer state recovery.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
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


# ---------------------------------------------------------------------------
# TestListImages
# ---------------------------------------------------------------------------


class TestListImages:
    """Tests for GET /images."""

    def test_returns_inventory(self, client: TestClient) -> None:
        """Returns a list of Docker images on the agent."""
        fake_images = [
            DockerImageInfo(id="sha256:aaa", tags=["ceos:4.28"], size_bytes=1024),
            DockerImageInfo(id="sha256:bbb", tags=["srlinux:latest"], size_bytes=2048),
        ]

        with patch("agent.routers.images._get_docker_images", return_value=fake_images):
            resp = client.get("/images")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["images"]) == 2
        assert body["images"][0]["id"] == "sha256:aaa"
        assert "ceos:4.28" in body["images"][0]["tags"]

    def test_empty(self, client: TestClient) -> None:
        """Empty image list returns successfully."""
        with patch("agent.routers.images._get_docker_images", return_value=[]):
            resp = client.get("/images")

        assert resp.status_code == 200
        body = resp.json()
        assert body["images"] == []


# ---------------------------------------------------------------------------
# TestReceiveDockerImage
# ---------------------------------------------------------------------------


class TestReceiveDockerImage:
    """Tests for POST /images/receive (Docker tar images)."""

    def test_success(self, client: TestClient) -> None:
        """Successfully receives and loads a Docker image tar."""
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "Loaded image: ceos:4.28.0F"
        fake_result.stderr = ""

        with patch("agent.routers.images.subprocess.run", return_value=fake_result) as mock_run:
            resp = client.post(
                "/images/receive",
                data={
                    "reference": "ceos:4.28.0F",
                    "total_bytes": "100",
                    "job_id": "j1",
                    "image_id": "test",
                    "sha256": "",
                },
                files={"file": ("image.tar", b"fake-tar-content", "application/octet-stream")},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "ceos:4.28.0F" in body["loaded_images"]

    def test_docker_load_failure(self, client: TestClient) -> None:
        """docker load failure surfaces error."""
        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stdout = ""
        fake_result.stderr = "invalid tar"

        with patch("agent.routers.images.subprocess.run", return_value=fake_result):
            resp = client.post(
                "/images/receive",
                data={
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

    def test_checksum_mismatch(self, client: TestClient, tmp_path: Path, monkeypatch) -> None:
        """File-based image with wrong checksum is rejected and deleted."""
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path / "workspace"))
        monkeypatch.setattr(settings, "enable_libvirt", True)
        dest = tmp_path / "workspace" / "images"
        dest.mkdir(parents=True, exist_ok=True)

        image_path = f"{dest}/test.qcow2"

        resp = client.post(
            "/images/receive",
            data={
                "reference": image_path,
                "total_bytes": "10",
                "job_id": "j3",
                "image_id": "test",
                "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
            },
            files={"file": ("test.qcow2", b"qcow2-bytes", "application/octet-stream")},
        )

        body = resp.json()
        assert body["success"] is False
        assert "Checksum mismatch" in body["error"]
        # File should be removed on mismatch
        assert not os.path.exists(image_path)


# ---------------------------------------------------------------------------
# TestReceiveFileBasedImage
# ---------------------------------------------------------------------------


class TestReceiveFileBasedImage:
    """Tests for POST /images/receive (file-based qcow2/img images)."""

    def test_success(self, client: TestClient, tmp_path: Path, monkeypatch) -> None:
        """File-based image stored to correct destination."""
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path / "workspace"))
        monkeypatch.setattr(settings, "enable_libvirt", True)
        dest_dir = tmp_path / "workspace" / "images"
        dest_dir.mkdir(parents=True, exist_ok=True)

        image_path = f"{dest_dir}/router.qcow2"

        resp = client.post(
            "/images/receive",
            data={
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

    def test_path_traversal_rejected(self, client: TestClient, tmp_path: Path, monkeypatch) -> None:
        """Destination outside allowed bases is rejected."""
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path / "workspace"))
        monkeypatch.setattr(settings, "enable_libvirt", True)

        resp = client.post(
            "/images/receive",
            data={
                "reference": "/tmp/evil.qcow2",
                "total_bytes": "10",
                "job_id": "j5",
                "image_id": "test",
                "sha256": "",
            },
            files={"file": ("evil.qcow2", b"evil", "application/octet-stream")},
        )

        assert resp.status_code == 400

    def test_libvirt_disabled(self, client: TestClient, monkeypatch) -> None:
        """qcow2 upload when libvirt is disabled returns error."""
        monkeypatch.setattr(settings, "enable_libvirt", False)

        resp = client.post(
            "/images/receive",
            data={
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


# ---------------------------------------------------------------------------
# TestPullProgress
# ---------------------------------------------------------------------------


class TestPullProgress:
    """Tests for GET /images/pull/{job_id}/progress."""

    def test_returns_status(self, client: TestClient) -> None:
        """Known job returns its current progress."""
        _image_pull_jobs["abc"] = ImagePullProgress(
            job_id="abc",
            status="transferring",
            progress_percent=50,
            bytes_transferred=1000,
            total_bytes=2000,
        )

        resp = client.get("/images/pull/abc/progress")
        body = resp.json()
        assert body["status"] == "transferring"
        assert body["progress_percent"] == 50

    def test_unknown_job_returns_unknown(self, client: TestClient) -> None:
        """Missing job returns status=unknown instead of 404."""
        resp = client.get("/images/pull/nonexistent/progress")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "unknown"
        assert "not found" in body["error"].lower()


# ---------------------------------------------------------------------------
# TestActiveTransfers
# ---------------------------------------------------------------------------


class TestActiveTransfers:
    """Tests for GET /images/active-transfers."""

    def test_only_non_terminal_jobs(self, client: TestClient) -> None:
        """Only pending/transferring/loading jobs appear in active list."""
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


# ---------------------------------------------------------------------------
# TestTransferStatePersistence
# ---------------------------------------------------------------------------


class TestTransferStatePersistence:
    """Tests for transfer state persistence and crash recovery."""

    def test_persist_and_load_marks_interrupted_failed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Persisted in-progress jobs are marked failed on reload."""
        state_file = tmp_path / ".active_transfers.json"
        monkeypatch.setattr(
            "agent.routers.images._TRANSFER_STATE_FILE", state_file,
        )

        # Simulate an active job that was persisted before crash
        _image_pull_jobs["crash-job"] = ImagePullProgress(
            job_id="crash-job",
            status="transferring",
            progress_percent=40,
            bytes_transferred=500,
            total_bytes=1000,
            started_at=time.time(),
        )
        _persist_transfer_state()
        assert state_file.exists()

        # Clear in-memory state to simulate restart
        _image_pull_jobs.clear()

        # Reload
        _load_persisted_transfer_state()

        assert "crash-job" in _image_pull_jobs
        recovered = _image_pull_jobs["crash-job"]
        assert recovered.status == "failed"
        assert "restarted" in recovered.error.lower()
        # State file should be cleaned up after load
        assert not state_file.exists()
