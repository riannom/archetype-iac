"""Tests for image pull/progress/transfer/backfill/check functions in agent/routers/images.py.

Covers: _persist_transfer_state, _clear_persisted_transfer_state,
_load_persisted_transfer_state, get_pull_progress, get_active_transfers,
backfill_metadata, list_images, check_image.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = REPO_ROOT / "agent"
for p in [str(REPO_ROOT), str(AGENT_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.routers.images import (  # noqa: E402
    _clear_persisted_transfer_state,
    _image_pull_jobs,
    _load_persisted_transfer_state,
    _persist_transfer_state,
)
from agent.schemas import DockerImageInfo, ImagePullProgress  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state(tmp_path):
    """Reset module-level state and redirect state file to tmp_path between tests."""
    _image_pull_jobs.clear()
    # Redirect persisted state file to a temp location
    import agent.routers.images as mod

    original = mod._TRANSFER_STATE_FILE
    mod._TRANSFER_STATE_FILE = tmp_path / ".active_transfers.json"
    yield
    _image_pull_jobs.clear()
    mod._TRANSFER_STATE_FILE = original


@pytest.fixture()
def state_file():
    """Return the current (redirected) transfer state file path."""
    import agent.routers.images as mod

    return mod._TRANSFER_STATE_FILE


# ---------------------------------------------------------------------------
# _persist_transfer_state
# ---------------------------------------------------------------------------


class TestPersistTransferState:
    """Tests for _persist_transfer_state()."""

    def test_writes_active_jobs_to_disk(self, state_file):
        """Active (non-terminal) jobs are persisted to disk."""
        _image_pull_jobs["j1"] = ImagePullProgress(
            job_id="j1", status="transferring", progress_percent=50,
            bytes_transferred=500, total_bytes=1000,
        )
        _persist_transfer_state()
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert "j1" in data
        assert data["j1"]["status"] == "transferring"

    def test_skips_terminal_jobs(self, state_file):
        """Completed/failed jobs are not persisted."""
        _image_pull_jobs["done"] = ImagePullProgress(
            job_id="done", status="completed", progress_percent=100,
        )
        _image_pull_jobs["err"] = ImagePullProgress(
            job_id="err", status="failed", error="boom",
        )
        _persist_transfer_state()
        # No active jobs -> file should be removed (via _clear)
        assert not state_file.exists()

    def test_clears_file_when_no_active_jobs(self, state_file):
        """When all jobs are terminal, the state file is removed."""
        # Create file first
        state_file.write_text("{}")
        _persist_transfer_state()
        assert not state_file.exists()

    def test_persists_pending_and_loading(self, state_file):
        """Pending and loading statuses are considered active."""
        _image_pull_jobs["p"] = ImagePullProgress(job_id="p", status="pending")
        _image_pull_jobs["l"] = ImagePullProgress(job_id="l", status="loading", progress_percent=90)
        _persist_transfer_state()
        data = json.loads(state_file.read_text())
        assert "p" in data
        assert "l" in data

    def test_handles_write_error_gracefully(self, state_file):
        """OSError during write is caught and logged."""
        _image_pull_jobs["j1"] = ImagePullProgress(job_id="j1", status="transferring")
        with patch.object(type(state_file), "write_text", side_effect=OSError("disk full")):
            # Should not raise
            _persist_transfer_state()


# ---------------------------------------------------------------------------
# _clear_persisted_transfer_state
# ---------------------------------------------------------------------------


class TestClearPersistedTransferState:
    """Tests for _clear_persisted_transfer_state()."""

    def test_removes_existing_file(self, state_file):
        """Removes the state file if it exists."""
        state_file.write_text("{}")
        _clear_persisted_transfer_state()
        assert not state_file.exists()

    def test_noop_when_no_file(self, state_file):
        """No error when file does not exist."""
        assert not state_file.exists()
        _clear_persisted_transfer_state()  # Should not raise


# ---------------------------------------------------------------------------
# _load_persisted_transfer_state
# ---------------------------------------------------------------------------


class TestLoadPersistedTransferState:
    """Tests for _load_persisted_transfer_state()."""

    def test_loads_and_marks_interrupted_as_failed(self, state_file):
        """Interrupted jobs are loaded with status='failed'."""
        persisted = {
            "j1": {
                "job_id": "j1",
                "status": "transferring",
                "progress_percent": 50,
                "bytes_transferred": 500,
                "total_bytes": 1000,
                "started_at": 1700000000.0,
            }
        }
        state_file.write_text(json.dumps(persisted))
        _load_persisted_transfer_state()
        assert "j1" in _image_pull_jobs
        assert _image_pull_jobs["j1"].status == "failed"
        assert _image_pull_jobs["j1"].error == "Agent restarted during transfer"
        assert _image_pull_jobs["j1"].progress_percent == 50

    def test_removes_state_file_after_loading(self, state_file):
        """State file is cleaned up after successful load."""
        state_file.write_text(json.dumps({"j1": {"job_id": "j1", "status": "pending"}}))
        _load_persisted_transfer_state()
        assert not state_file.exists()

    def test_noop_when_no_file(self, state_file):
        """No action when state file does not exist."""
        _load_persisted_transfer_state()
        assert len(_image_pull_jobs) == 0

    def test_handles_corrupt_json(self, state_file):
        """Corrupt JSON is handled gracefully without crash."""
        state_file.write_text("NOT VALID JSON {{{")
        _load_persisted_transfer_state()  # Should not raise
        assert len(_image_pull_jobs) == 0


# ---------------------------------------------------------------------------
# get_pull_progress
# ---------------------------------------------------------------------------


class TestGetPullProgress:
    """Tests for GET /images/pull/{job_id}/progress."""

    def test_returns_known_job(self):
        from agent.routers.images import get_pull_progress

        _image_pull_jobs["abc"] = ImagePullProgress(
            job_id="abc", status="transferring", progress_percent=42,
        )
        result = get_pull_progress("abc")
        assert result.status == "transferring"
        assert result.progress_percent == 42

    def test_returns_unknown_for_missing_job(self):
        from agent.routers.images import get_pull_progress

        result = get_pull_progress("nonexistent")
        assert result.status == "unknown"
        assert "not found" in (result.error or "").lower()

    def test_returns_completed_job(self):
        from agent.routers.images import get_pull_progress

        _image_pull_jobs["done"] = ImagePullProgress(
            job_id="done", status="completed", progress_percent=100,
            bytes_transferred=2048, total_bytes=2048,
        )
        result = get_pull_progress("done")
        assert result.status == "completed"
        assert result.progress_percent == 100


# ---------------------------------------------------------------------------
# get_active_transfers
# ---------------------------------------------------------------------------


class TestGetActiveTransfers:
    """Tests for GET /images/active-transfers."""

    def test_returns_only_active_jobs(self):
        from agent.routers.images import get_active_transfers

        _image_pull_jobs["active"] = ImagePullProgress(
            job_id="active", status="transferring", progress_percent=30,
        )
        _image_pull_jobs["done"] = ImagePullProgress(
            job_id="done", status="completed", progress_percent=100,
        )
        _image_pull_jobs["err"] = ImagePullProgress(
            job_id="err", status="failed", error="oops",
        )

        with patch("glob.glob", return_value=[]):
            result = get_active_transfers()

        assert "active" in result["active_jobs"]
        assert "done" not in result["active_jobs"]
        assert "err" not in result["active_jobs"]

    def test_includes_agent_uptime(self):
        from agent.routers.images import get_active_transfers

        with patch("glob.glob", return_value=[]):
            result = get_active_transfers()

        assert "agent_uptime_seconds" in result
        assert isinstance(result["agent_uptime_seconds"], int)

    def test_reports_stale_temp_files(self):
        from agent.routers.images import get_active_transfers

        mock_stat = MagicMock()
        mock_stat.st_size = 999
        mock_stat.st_mtime = time.time() - 120

        with patch("glob.glob", return_value=["/tmp/tmpXYZ.tar"]):
            with patch("agent.routers.images.os.stat", return_value=mock_stat):
                result = get_active_transfers()

        assert len(result["temp_files"]) == 1
        assert result["temp_files"][0]["size_bytes"] == 999


# ---------------------------------------------------------------------------
# backfill_metadata
# ---------------------------------------------------------------------------


class TestBackfillMetadata:
    """Tests for POST /images/backfill-metadata."""

    def test_updates_known_images(self):
        from agent.routers.images import backfill_metadata

        mock_img = MagicMock()
        mock_img.id = "sha256:abc"
        mock_img.tags = ["ceos:4.28"]

        mock_client = MagicMock()
        mock_client.images.get.return_value = mock_img

        with patch("agent.routers.images.get_docker_client", return_value=mock_client):
            with patch("agent.image_metadata.set_docker_image_metadata") as mock_set:
                result = backfill_metadata({"ceos:4.28": "arista_ceos"})

        assert result["updated"] == 1
        mock_set.assert_called_once_with(
            image_id="sha256:abc",
            tags=["ceos:4.28"],
            device_id="arista_ceos",
            source="api-backfill",
        )

    def test_skips_image_not_found(self):
        from agent.routers.images import backfill_metadata
        import docker.errors

        mock_client = MagicMock()
        mock_client.images.get.side_effect = docker.errors.ImageNotFound("nope")

        with patch("agent.routers.images.get_docker_client", return_value=mock_client):
            with patch("agent.image_metadata.set_docker_image_metadata") as mock_set:
                result = backfill_metadata({"missing:latest": "some_device"})

        assert result["updated"] == 0
        mock_set.assert_not_called()

    def test_handles_generic_exception(self):
        from agent.routers.images import backfill_metadata

        mock_client = MagicMock()
        mock_client.images.get.side_effect = RuntimeError("Docker gone")

        with patch("agent.routers.images.get_docker_client", return_value=mock_client):
            with patch("agent.image_metadata.set_docker_image_metadata"):
                result = backfill_metadata({"broken:latest": "dev"})

        assert result["updated"] == 0

    def test_uses_reference_as_tag_fallback(self):
        """When image has no tags, reference is used as fallback."""
        from agent.routers.images import backfill_metadata

        mock_img = MagicMock()
        mock_img.id = "sha256:def"
        mock_img.tags = []  # No tags

        mock_client = MagicMock()
        mock_client.images.get.return_value = mock_img

        with patch("agent.routers.images.get_docker_client", return_value=mock_client):
            with patch("agent.image_metadata.set_docker_image_metadata") as mock_set:
                backfill_metadata({"myimg:v1": "dev_type"})

        mock_set.assert_called_once()
        call_kwargs = mock_set.call_args[1]
        assert call_kwargs["tags"] == ["myimg:v1"]


# ---------------------------------------------------------------------------
# list_images
# ---------------------------------------------------------------------------


class TestListImages:
    """Tests for GET /images."""

    def test_returns_docker_images(self):
        from agent.routers.images import list_images

        fake = [
            DockerImageInfo(id="sha256:aaa", tags=["img1:latest"], size_bytes=100),
        ]
        with patch("agent.routers.images._get_docker_images", return_value=fake), \
             patch("agent.routers.images._get_file_images", return_value=[]):
            result = list_images()

        assert len(result.images) == 1
        assert result.images[0].id == "sha256:aaa"

    def test_returns_empty_list(self):
        from agent.routers.images import list_images

        with patch("agent.routers.images._get_docker_images", return_value=[]), \
             patch("agent.routers.images._get_file_images", return_value=[]):
            result = list_images()

        assert result.images == []


# ---------------------------------------------------------------------------
# check_image
# ---------------------------------------------------------------------------


class TestCheckImage:
    """Tests for GET /images/{reference:path}."""

    def test_docker_image_found(self):
        from agent.routers.images import check_image

        mock_img = MagicMock()
        mock_img.id = "sha256:abc123"
        mock_img.tags = ["ceos:4.28"]
        mock_img.attrs = {"Size": 512, "Created": "2024-01-01T00:00:00Z"}

        mock_client = MagicMock()
        mock_client.images.get.return_value = mock_img

        with patch("agent.routers.images.get_docker_client", return_value=mock_client):
            result = check_image("ceos:4.28")

        assert result.exists is True
        assert result.image is not None
        assert result.image.id == "sha256:abc123"

    def test_docker_image_not_found(self):
        from agent.routers.images import check_image
        import docker.errors

        mock_client = MagicMock()
        mock_client.images.get.side_effect = docker.errors.ImageNotFound("nope")

        with patch("agent.routers.images.get_docker_client", return_value=mock_client):
            result = check_image("nonexistent:latest")

        assert result.exists is False

    def test_file_based_qcow2_exists(self, tmp_path):
        from agent.routers.images import check_image

        img_path = tmp_path / "test.qcow2"
        img_path.write_bytes(b"\x00" * 64)
        sha_path = Path(str(img_path) + ".sha256")
        sha_path.write_text("deadbeef1234")

        with patch("agent.routers.images.settings") as mock_settings:
            mock_settings.enable_libvirt = True
            with patch("agent.routers.images.os.path.exists", side_effect=lambda p: p in (str(img_path), str(sha_path))):
                with patch("builtins.open", MagicMock(return_value=MagicMock(
                    read=MagicMock(return_value="deadbeef1234"),
                    __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value="deadbeef1234"), strip=MagicMock(return_value="deadbeef1234"))),
                    __exit__=MagicMock(return_value=False),
                ))):
                    result = check_image(str(img_path))

        assert result.exists is True
        assert result.sha256 == "deadbeef1234"

    def test_file_based_qcow2_libvirt_disabled(self):
        from agent.routers.images import check_image

        with patch("agent.routers.images.settings") as mock_settings:
            mock_settings.enable_libvirt = False
            result = check_image("/var/lib/archetype/images/test.qcow2")

        assert result.exists is False

    def test_file_based_iol_check(self):
        from agent.routers.images import check_image

        with patch("agent.routers.images.settings") as mock_settings:
            mock_settings.enable_libvirt = False
            mock_settings.enable_docker = True
            with patch("agent.routers.images.os.path.exists", return_value=True):
                result = check_image("/var/lib/archetype/images/test.iol")

        assert result.exists is True

    def test_generic_exception_returns_not_exists(self):
        from agent.routers.images import check_image

        with patch("agent.routers.images.get_docker_client", side_effect=RuntimeError("docker down")):
            result = check_image("some:image")

        assert result.exists is False
