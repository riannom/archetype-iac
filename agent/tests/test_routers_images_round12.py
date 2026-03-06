"""Round-12 deep-path tests for agent/routers/images.py.

Targets uncovered internal paths:
- _execute_pull_from_controller: streaming, non-200, docker load fail, exception, cleanup
- receive_image: TimeoutExpired, Loaded image ID parsing, .iol handling, device_id metadata
- backfill_image_checksums: actual hash computation
- Transfer state: OSError on persist, corrupt JSON on load, stale temp OSError
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent.config import settings
from agent.main import app
from agent.routers.images import (
    _execute_pull_from_controller,
    _image_pull_jobs,
    _load_persisted_transfer_state,
    _persist_transfer_state,
)
from agent.schemas import ImagePullProgress


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_pull_jobs():
    """Reset module-level pull job state between tests."""
    _image_pull_jobs.clear()
    yield
    _image_pull_jobs.clear()


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(settings, "controller_secret", "")


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# _execute_pull_from_controller — streaming internals
# ---------------------------------------------------------------------------


class TestExecutePullFromController:
    """Deep paths through the background pull task."""

    @pytest.mark.asyncio
    async def test_non_200_marks_failed(self, monkeypatch):
        """Controller returning non-200 marks the pull job as failed."""
        monkeypatch.setattr(settings, "controller_url", "http://fake-controller:8000")

        mock_response = AsyncMock()
        mock_response.status_code = 502
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_response)

        with patch("agent.routers.images.get_http_client", return_value=mock_client):
            with patch("agent.routers.images.get_controller_auth_headers", return_value={}):
                await _execute_pull_from_controller(
                    job_id="pull-502", image_id="img1", reference="test:latest",
                )

        assert "pull-502" in _image_pull_jobs
        progress = _image_pull_jobs["pull-502"]
        assert progress.status == "failed"
        assert "502" in (progress.error or "")

    @pytest.mark.asyncio
    async def test_docker_load_failure_marks_failed(self, monkeypatch, tmp_path):
        """docker load returning non-zero marks the pull job as failed."""
        monkeypatch.setattr(settings, "controller_url", "http://fake-controller:8000")

        # Build a mock streaming response that yields one chunk then stops
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": "100"}

        async def _aiter_bytes(chunk_size=1024 * 1024):
            yield b"fake-image-data"

        mock_response.aiter_bytes = _aiter_bytes
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_response)

        docker_result = MagicMock()
        docker_result.returncode = 1
        docker_result.stdout = ""
        docker_result.stderr = "Error: invalid tar header"

        with patch("agent.routers.images.get_http_client", return_value=mock_client):
            with patch("agent.routers.images.get_controller_auth_headers", return_value={}):
                with patch("agent.routers.images.subprocess.run", return_value=docker_result):
                    await _execute_pull_from_controller(
                        job_id="pull-loadfail", image_id="img2", reference="bad:image",
                    )

        progress = _image_pull_jobs["pull-loadfail"]
        assert progress.status == "failed"
        assert "invalid tar header" in (progress.error or "")

    @pytest.mark.asyncio
    async def test_successful_pull_completes(self, monkeypatch, tmp_path):
        """Successful pull sets status to completed with 100% progress."""
        monkeypatch.setattr(settings, "controller_url", "http://fake-controller:8000")

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": "15"}

        async def _aiter_bytes(chunk_size=1024 * 1024):
            yield b"fake-image-data"

        mock_response.aiter_bytes = _aiter_bytes
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_response)

        docker_result = MagicMock()
        docker_result.returncode = 0
        docker_result.stdout = "Loaded image: test:latest"
        docker_result.stderr = ""

        with patch("agent.routers.images.get_http_client", return_value=mock_client):
            with patch("agent.routers.images.get_controller_auth_headers", return_value={}):
                with patch("agent.routers.images.subprocess.run", return_value=docker_result):
                    await _execute_pull_from_controller(
                        job_id="pull-ok", image_id="img3", reference="test:latest",
                    )

        progress = _image_pull_jobs["pull-ok"]
        assert progress.status == "completed"
        assert progress.progress_percent == 100
        assert progress.bytes_transferred == 15

    @pytest.mark.asyncio
    async def test_exception_during_stream_marks_failed(self, monkeypatch):
        """Network exception during streaming marks the pull job as failed."""
        monkeypatch.setattr(settings, "controller_url", "http://fake-controller:8000")

        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(side_effect=ConnectionError("connection reset"))
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_response)

        with patch("agent.routers.images.get_http_client", return_value=mock_client):
            with patch("agent.routers.images.get_controller_auth_headers", return_value={}):
                await _execute_pull_from_controller(
                    job_id="pull-ex", image_id="img4", reference="test:latest",
                )

        progress = _image_pull_jobs["pull-ex"]
        assert progress.status == "failed"
        assert "connection reset" in (progress.error or "").lower()

    @pytest.mark.asyncio
    async def test_progress_without_content_length(self, monkeypatch):
        """Progress tracks MB-based percentage when content-length is absent."""
        monkeypatch.setattr(settings, "controller_url", "http://fake-controller:8000")

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.headers = {}  # no content-length

        data = b"x" * (2 * 1024 * 1024)  # 2MB
        chunks = [data[:1024 * 1024], data[1024 * 1024:]]

        async def _aiter_bytes(chunk_size=1024 * 1024):
            for c in chunks:
                yield c

        mock_response.aiter_bytes = _aiter_bytes
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_response)

        docker_result = MagicMock()
        docker_result.returncode = 0
        docker_result.stdout = "Loaded image: big:latest"
        docker_result.stderr = ""

        with patch("agent.routers.images.get_http_client", return_value=mock_client):
            with patch("agent.routers.images.get_controller_auth_headers", return_value={}):
                with patch("agent.routers.images.subprocess.run", return_value=docker_result):
                    await _execute_pull_from_controller(
                        job_id="pull-nolen", image_id="img5", reference="big:latest",
                    )

        progress = _image_pull_jobs["pull-nolen"]
        assert progress.status == "completed"
        assert progress.bytes_transferred == 2 * 1024 * 1024

    @pytest.mark.asyncio
    async def test_file_based_pull_writes_image_and_checksum(self, monkeypatch, tmp_path):
        """File-based pulls should store the image directly instead of docker loading it."""
        monkeypatch.setattr(settings, "controller_url", "http://fake-controller:8000")
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path))

        target = tmp_path / "sonic-vs.img"
        content = b"qcow2-bytes"
        expected_hash = hashlib.sha256(content).hexdigest()

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": str(len(content))}

        async def _aiter_bytes(chunk_size=1024 * 1024):
            yield content

        mock_response.aiter_bytes = _aiter_bytes
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_response)

        with patch("agent.routers.images.get_http_client", return_value=mock_client):
            with patch("agent.routers.images.get_controller_auth_headers", return_value={}):
                await _execute_pull_from_controller(
                    job_id="pull-file",
                    image_id="qcow2:sonic-vs.img",
                    reference=str(target),
                    sha256=expected_hash,
                    device_id="sonic-vs",
                )

        progress = _image_pull_jobs["pull-file"]
        assert progress.status == "completed"
        assert target.read_bytes() == content
        assert target.with_suffix(".img.sha256").read_text() == expected_hash


# ---------------------------------------------------------------------------
# receive_image — Docker tar edge cases
# ---------------------------------------------------------------------------


class TestReceiveDockerEdgeCases:
    """Deep paths through Docker tar receive."""

    def test_timeout_expired(self, client, monkeypatch):
        """subprocess.TimeoutExpired during docker load returns error."""
        with patch(
            "agent.routers.images.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="docker load", timeout=600),
        ):
            resp = client.post(
                "/images/receive",
                data={
                    "reference": "slow:image",
                    "total_bytes": "10",
                    "job_id": "j-timeout",
                    "image_id": "test",
                    "sha256": "",
                },
                files={"file": ("image.tar", b"data", "application/octet-stream")},
            )

        body = resp.json()
        assert body["success"] is False
        assert "timed out" in body["error"]
        assert _image_pull_jobs["j-timeout"].status == "failed"

    def test_loaded_image_id_parsing(self, client):
        """'Loaded image ID:' lines are parsed from docker load output."""
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "Loaded image ID: sha256:abcdef1234567890"
        fake_result.stderr = ""

        with patch("agent.routers.images.subprocess.run", return_value=fake_result):
            resp = client.post(
                "/images/receive",
                data={
                    "reference": "untagged:image",
                    "total_bytes": "10",
                    "job_id": "j-imgid",
                    "image_id": "test",
                    "sha256": "",
                },
                files={"file": ("image.tar", b"data", "application/octet-stream")},
            )

        body = resp.json()
        assert body["success"] is True
        assert "sha256:abcdef1234567890" in body["loaded_images"]

    def test_generic_exception_during_receive(self, client):
        """Unexpected exception during Docker receive returns failure with error."""
        with patch(
            "agent.routers.images.subprocess.run",
            side_effect=RuntimeError("unexpected failure"),
        ):
            resp = client.post(
                "/images/receive",
                data={
                    "reference": "crash:image",
                    "total_bytes": "10",
                    "job_id": "j-crash",
                    "image_id": "test",
                    "sha256": "",
                },
                files={"file": ("image.tar", b"data", "application/octet-stream")},
            )

        body = resp.json()
        assert body["success"] is False
        assert "unexpected failure" in body["error"]
        assert _image_pull_jobs["j-crash"].status == "failed"

    def test_device_id_metadata_persisted_on_docker_receive(self, client):
        """device_id triggers metadata persistence after successful Docker load."""
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "Loaded image: ceos:4.28"
        fake_result.stderr = ""

        mock_img = MagicMock()
        mock_img.id = "sha256:abc"
        mock_img.tags = ["ceos:4.28"]

        mock_docker = MagicMock()
        mock_docker.images.get.return_value = mock_img

        with patch("agent.routers.images.subprocess.run", return_value=fake_result):
            with patch("agent.routers.images.get_docker_client", return_value=mock_docker):
                with patch("agent.image_metadata.set_docker_image_metadata") as mock_set:
                    resp = client.post(
                        "/images/receive",
                        data={
                            "reference": "ceos:4.28",
                            "total_bytes": "10",
                            "job_id": "j-meta",
                            "image_id": "test",
                            "sha256": "",
                            "device_id": "arista_ceos",
                        },
                        files={"file": ("image.tar", b"data", "application/octet-stream")},
                    )

        body = resp.json()
        assert body["success"] is True
        mock_set.assert_called_once_with(
            image_id="sha256:abc",
            tags=["ceos:4.28"],
            device_id="arista_ceos",
            source="api-sync",
        )


# ---------------------------------------------------------------------------
# receive_image — File-based edge cases
# ---------------------------------------------------------------------------


class TestReceiveFileEdgeCases:
    """Deep paths through file-based image receive."""

    def test_iol_docker_disabled_rejected(self, client, monkeypatch):
        """.iol upload when docker is disabled returns error."""
        monkeypatch.setattr(settings, "enable_docker", False)
        monkeypatch.setattr(settings, "enable_libvirt", True)

        resp = client.post(
            "/images/receive",
            data={
                "reference": "/var/lib/archetype/images/test.iol",
                "total_bytes": "10",
                "job_id": "j-iol",
                "image_id": "test",
                "sha256": "",
            },
            files={"file": ("test.iol", b"iol-data", "application/octet-stream")},
        )

        body = resp.json()
        assert body["success"] is False
        assert "docker" in body["error"].lower()

    def test_file_receive_with_device_id_metadata(self, client, tmp_path, monkeypatch):
        """device_id persists metadata for file-based images."""
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path / "workspace"))
        monkeypatch.setattr(settings, "enable_libvirt", True)
        dest_dir = tmp_path / "workspace" / "images"
        dest_dir.mkdir(parents=True, exist_ok=True)

        image_path = f"{dest_dir}/router.qcow2"

        with patch("agent.image_metadata.set_file_image_metadata") as mock_set:
            resp = client.post(
                "/images/receive",
                data={
                    "reference": image_path,
                    "total_bytes": "10",
                    "job_id": "j-fmeta",
                    "image_id": "test",
                    "sha256": "",
                    "device_id": "cisco_iosv",
                },
                files={"file": ("router.qcow2", b"qcow2-data", "application/octet-stream")},
            )

        body = resp.json()
        assert body["success"] is True
        mock_set.assert_called_once_with(
            path=image_path,
            device_id="cisco_iosv",
            source="api-sync",
        )

    def test_file_receive_checksum_match_writes_sidecar(self, client, tmp_path, monkeypatch):
        """Correct checksum writes sidecar .sha256 file."""
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path / "workspace"))
        monkeypatch.setattr(settings, "enable_libvirt", True)
        dest_dir = tmp_path / "workspace" / "images"
        dest_dir.mkdir(parents=True, exist_ok=True)

        content = b"known-content-for-hash"
        expected_hash = hashlib.sha256(content).hexdigest()
        image_path = f"{dest_dir}/verified.qcow2"

        resp = client.post(
            "/images/receive",
            data={
                "reference": image_path,
                "total_bytes": str(len(content)),
                "job_id": "j-sha-ok",
                "image_id": "test",
                "sha256": expected_hash,
            },
            files={"file": ("verified.qcow2", content, "application/octet-stream")},
        )

        body = resp.json()
        assert body["success"] is True
        sidecar = Path(image_path + ".sha256")
        assert sidecar.exists()
        assert sidecar.read_text() == expected_hash

    def test_file_receive_temp_cleanup_on_write_error(self, client, tmp_path, monkeypatch):
        """Temp file is cleaned up when write to destination fails."""
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path / "workspace"))
        monkeypatch.setattr(settings, "enable_libvirt", True)
        dest_dir = tmp_path / "workspace" / "images"
        dest_dir.mkdir(parents=True, exist_ok=True)

        image_path = f"{dest_dir}/fail-write.qcow2"

        # Make os.replace raise to simulate atomic rename failure
        with patch("agent.routers.images.os.replace", side_effect=OSError("disk full")):
            resp = client.post(
                "/images/receive",
                data={
                    "reference": image_path,
                    "total_bytes": "10",
                    "job_id": "j-writefail",
                    "image_id": "test",
                    "sha256": "",
                },
                files={"file": ("fail-write.qcow2", b"data", "application/octet-stream")},
            )

        body = resp.json()
        assert body["success"] is False
        assert "disk full" in body["error"]
        # Verify no .part temp files linger
        part_files = list(dest_dir.glob("*.part-*"))
        assert len(part_files) == 0


# ---------------------------------------------------------------------------
# backfill_image_checksums — actual computation
# ---------------------------------------------------------------------------


class TestBackfillImageChecksums:
    """Tests for POST /images/backfill-checksums with real files."""

    def test_computes_sha256_for_missing_sidecars(self, client, tmp_path):
        """Creates .sha256 sidecar files for images that lack them."""
        image_dir = tmp_path / "images"
        image_dir.mkdir()

        # Create a qcow2 file without sidecar
        content = b"test-qcow2-content"
        (image_dir / "router.qcow2").write_bytes(content)
        with patch("agent.routers.images.os.path.isdir", return_value=True):
            with patch(
                "agent.routers.images.glob.glob" if hasattr(__builtins__, '__name__') else "glob.glob",
                side_effect=lambda pattern: (
                    [str(image_dir / "router.qcow2")]
                    if "qcow2" in pattern
                    else []
                ),
            ):
                with patch("agent.routers.images.os.path.exists", side_effect=lambda p: not p.endswith(".sha256") and os.path.exists(p)):
                    with patch("builtins.open", side_effect=lambda p, *a, **kw: open(p, *a, **kw) if not p.endswith(".sha256") else MagicMock(__enter__=MagicMock(return_value=MagicMock(write=MagicMock())), __exit__=MagicMock(return_value=False))):
                        # This is getting too convoluted; test the function directly
                        pass

        # Simpler: test via the actual backfill function with real I/O
        img_file = image_dir / "test.qcow2"
        img_file.write_bytes(content)

        with patch("agent.routers.images.os.path.isdir", return_value=True):
            # Patch glob inside the function (it imports glob as globmod)
            import glob as globmod

            def fake_glob(pattern):
                if "qcow2" in pattern:
                    return [str(img_file)]
                return []

            with patch.object(globmod, "glob", fake_glob):
                with patch("agent.routers.images.os.path.exists", side_effect=lambda p: (
                    False if p.endswith(".sha256") else os.path.exists(p)
                )):
                    resp = client.post("/images/backfill-checksums")

        body = resp.json()
        assert body["updated"] == 1

    def test_skips_existing_sidecars(self, client, tmp_path):
        """Images with existing .sha256 sidecars are skipped."""
        image_dir = tmp_path / "images"
        image_dir.mkdir()

        img_file = image_dir / "existing.qcow2"
        img_file.write_bytes(b"content")
        sidecar = image_dir / "existing.qcow2.sha256"
        sidecar.write_text("already-hashed")

        import glob as globmod

        with patch("agent.routers.images.os.path.isdir", return_value=True):
            with patch.object(globmod, "glob", side_effect=lambda p: (
                [str(img_file)] if "qcow2" in p else []
            )):
                with patch("agent.routers.images.os.path.exists", return_value=True):
                    resp = client.post("/images/backfill-checksums")

        body = resp.json()
        assert body["updated"] == 0


# ---------------------------------------------------------------------------
# Transfer state persistence — edge cases
# ---------------------------------------------------------------------------


class TestTransferStatePersistenceEdgeCases:
    """Edge cases for transfer state file I/O."""

    def test_persist_oserror_is_swallowed(self, tmp_path, monkeypatch):
        """OSError during state file write does not crash."""
        import agent.routers.images as mod

        bad_path = tmp_path / "readonly" / "state.json"
        monkeypatch.setattr(mod, "_TRANSFER_STATE_FILE", bad_path)
        # Parent dir doesn't exist so write_text will fail

        _image_pull_jobs["j1"] = ImagePullProgress(
            job_id="j1", status="transferring", progress_percent=50,
        )
        # Should not raise
        _persist_transfer_state()

    def test_load_corrupt_json_is_handled(self, tmp_path, monkeypatch):
        """Corrupt JSON in state file does not crash, loads zero jobs."""
        import agent.routers.images as mod

        state_file = tmp_path / ".active_transfers.json"
        state_file.write_text("{{{not valid json")
        monkeypatch.setattr(mod, "_TRANSFER_STATE_FILE", state_file)

        _load_persisted_transfer_state()
        assert len(_image_pull_jobs) == 0

    def test_load_preserves_bytes_transferred(self, tmp_path, monkeypatch):
        """Loaded interrupted jobs preserve bytes_transferred from persisted state."""
        import agent.routers.images as mod

        state_file = tmp_path / ".active_transfers.json"
        persisted = {
            "j-bytes": {
                "job_id": "j-bytes",
                "status": "transferring",
                "progress_percent": 60,
                "bytes_transferred": 12345,
                "total_bytes": 20000,
                "started_at": time.time() - 300,
            }
        }
        state_file.write_text(json.dumps(persisted))
        monkeypatch.setattr(mod, "_TRANSFER_STATE_FILE", state_file)

        _load_persisted_transfer_state()

        recovered = _image_pull_jobs["j-bytes"]
        assert recovered.status == "failed"
        assert recovered.bytes_transferred == 12345
        assert recovered.total_bytes == 20000


# ---------------------------------------------------------------------------
# Active transfers — stale temp file edge cases
# ---------------------------------------------------------------------------


class TestActiveTransfersEdgeCases:
    """Edge cases for GET /images/active-transfers."""

    def test_stale_temp_file_oserror_skipped(self, client):
        """OSError reading a stale temp file is silently skipped."""
        import glob as glob_mod

        with patch.object(glob_mod, "glob", return_value=["/tmp/tmpXYZ.tar"]):
            with patch("agent.routers.images.os.stat", side_effect=OSError("gone")):
                resp = client.get("/images/active-transfers")

        body = resp.json()
        assert body["temp_files"] == []
        assert "agent_uptime_seconds" in body

    def test_multiple_active_statuses(self, client):
        """All three non-terminal statuses (pending, transferring, loading) appear."""
        _image_pull_jobs["p1"] = ImagePullProgress(job_id="p1", status="pending")
        _image_pull_jobs["t1"] = ImagePullProgress(job_id="t1", status="transferring", progress_percent=50)
        _image_pull_jobs["l1"] = ImagePullProgress(job_id="l1", status="loading", progress_percent=90)
        _image_pull_jobs["c1"] = ImagePullProgress(job_id="c1", status="completed", progress_percent=100)

        import glob as glob_mod
        with patch.object(glob_mod, "glob", return_value=[]):
            resp = client.get("/images/active-transfers")

        body = resp.json()
        active = body["active_jobs"]
        assert "p1" in active
        assert "t1" in active
        assert "l1" in active
        assert "c1" not in active
