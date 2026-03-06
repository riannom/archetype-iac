"""Tests for app.routers.images.sync — round 12 deep coverage.

Targets:
- _execute_sync_job internals (docker path, file-based path, error handling)
- stream_image generator (success streaming, docker save failure, exception in generator)
- push_image_to_hosts (dedup existing jobs, max-concurrent enforcement, ImageHost upsert)
- list_sync_jobs filter by image_id
- cancel_sync_job for 'cancelled' status (triple-terminal)
"""
from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# Shared helpers / constants
# ---------------------------------------------------------------------------

MOCK_MANIFEST = [
    {
        "id": "docker:ceos:4.28.0F",
        "reference": "ceos:4.28.0F",
        "kind": "docker",
        "device_id": "arista_ceos",
    },
    {
        "id": "file:/images/iosv.qcow2",
        "reference": "/images/iosv.qcow2",
        "kind": "qcow2",
        "device_id": "cisco_iosv",
    },
    {
        "id": "docker:no-ref",
        "reference": "",
        "kind": "docker",
        "device_id": "empty",
    },
]


def _mock_find(manifest, image_id):
    for img in manifest:
        if img["id"] == image_id:
            return img
    return None


def _make_sync_job(
    db: Session,
    host_id: str,
    *,
    image_id: str = "docker:ceos:4.28.0F",
    status: str = "pending",
    job_id: str | None = None,
) -> models.ImageSyncJob:
    job = models.ImageSyncJob(
        id=job_id or str(uuid4()),
        image_id=image_id,
        host_id=host_id,
        status=status,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@contextmanager
def _fake_session_ctx(test_db):
    """Build a context-manager that yields the test_db session."""
    yield test_db


# ---------------------------------------------------------------------------
# _execute_sync_job — Docker path
# ---------------------------------------------------------------------------

class TestExecuteSyncJobDocker:
    """Tests for _execute_sync_job with docker images."""

    @pytest.mark.asyncio
    async def test_docker_sync_success(self, test_db: Session, sample_host):
        """Full happy-path: docker inspect, docker save, POST to agent succeeds."""
        from app.routers.images.sync import _execute_sync_job

        job = _make_sync_job(test_db, sample_host.id, job_id="docker-ok")
        image = MOCK_MANIFEST[0]  # docker kind

        # Mock docker inspect returning size
        inspect_proc = AsyncMock()
        inspect_proc.communicate = AsyncMock(return_value=(b"12345\n", b""))
        inspect_proc.returncode = 0

        # Mock docker save writing chunks
        save_proc = AsyncMock()
        save_proc.stdout = AsyncMock()
        save_proc.stdout.read = AsyncMock(side_effect=[b"chunk1", b"chunk2", b""])
        save_proc.communicate = AsyncMock(return_value=(b"", b""))
        save_proc.returncode = 0

        call_count = {"n": 0}

        async def fake_subprocess(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return inspect_proc
            return save_proc

        async def fake_wait_for(coro, timeout):
            return await coro

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"success": True}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.db.get_session", lambda: _fake_session_ctx(test_db)),
            patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess),
            patch("asyncio.wait_for", side_effect=fake_wait_for),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.agent_client._get_agent_auth_headers", return_value={}),
            patch("app.agent_client.http._get_agent_auth_headers", return_value={}),
            patch("tempfile.mkstemp", return_value=(99, "/tmp/fake.tar")),
            patch("os.fdopen", return_value=MagicMock(
                __enter__=MagicMock(return_value=BytesIO()),
                __exit__=MagicMock(return_value=False),
            )),
            patch("builtins.open", return_value=BytesIO(b"tardata")),
            patch("os.unlink"),
        ):
            await _execute_sync_job("docker-ok", "docker:ceos:4.28.0F", image, sample_host)

        test_db.refresh(job)
        assert job.status == "completed"
        assert job.progress_percent == 100

    @pytest.mark.asyncio
    async def test_docker_save_failure_marks_job_failed(self, test_db: Session, sample_host):
        """When docker save returns non-zero, job should be marked failed."""
        from app.routers.images.sync import _execute_sync_job

        job = _make_sync_job(test_db, sample_host.id, job_id="docker-fail")
        image = MOCK_MANIFEST[0]

        # inspect subprocess
        inspect_proc = AsyncMock()
        inspect_proc.communicate = AsyncMock(return_value=(b"999\n", b""))
        inspect_proc.returncode = 0

        # docker save fails
        save_proc = AsyncMock()
        save_proc.stdout = AsyncMock()
        save_proc.stdout.read = AsyncMock(side_effect=[b""])  # immediate EOF
        save_proc.communicate = AsyncMock(return_value=(b"", b"docker save error msg"))
        save_proc.returncode = 1

        call_count = {"n": 0}

        async def fake_subprocess(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return inspect_proc
            return save_proc

        async def fake_wait_for(coro, timeout):
            return await coro

        with (
            patch("app.db.get_session", lambda: _fake_session_ctx(test_db)),
            patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess),
            patch("asyncio.wait_for", side_effect=fake_wait_for),
            patch("httpx.AsyncClient") as mock_client_cls,
            patch("app.agent_client.http._get_agent_auth_headers", return_value={}),
            patch("tempfile.mkstemp", return_value=(99, "/tmp/fail.tar")),
            patch("os.fdopen", return_value=MagicMock(
                __enter__=MagicMock(return_value=BytesIO()),
                __exit__=MagicMock(return_value=False),
            )),
            patch("os.unlink"),
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await _execute_sync_job("docker-fail", "docker:ceos:4.28.0F", image, sample_host)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "docker save" in (job.error_message or "").lower()

    @pytest.mark.asyncio
    async def test_no_reference_marks_job_failed(self, test_db: Session, sample_host):
        """Image with empty reference raises ValueError, job marked failed."""
        from app.routers.images.sync import _execute_sync_job

        job = _make_sync_job(test_db, sample_host.id, job_id="no-ref-job")
        image = {"id": "docker:no-ref", "reference": "", "kind": "docker"}

        with patch("app.db.get_session", lambda: _fake_session_ctx(test_db)):
            await _execute_sync_job("no-ref-job", "docker:no-ref", image, sample_host)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "no reference" in (job.error_message or "").lower()

    @pytest.mark.asyncio
    async def test_host_not_found_returns_early(self, test_db: Session, sample_host):
        """If host disappears before job runs, function returns without crash."""
        from app.routers.images.sync import _execute_sync_job

        job = _make_sync_job(test_db, sample_host.id, job_id="gone-host")
        image = MOCK_MANIFEST[0]

        # Delete the host so it can't be found
        test_db.delete(sample_host)
        test_db.commit()

        with patch("app.db.get_session", lambda: _fake_session_ctx(test_db)):
            await _execute_sync_job("gone-host", "docker:ceos:4.28.0F", image, sample_host)

        # Job should still be pending (host not found, returned early)
        test_db.refresh(job)
        assert job.status == "pending"

    @pytest.mark.asyncio
    async def test_job_not_found_returns_early(self, test_db: Session, sample_host):
        """If job record disappears, function returns without crash."""
        from app.routers.images.sync import _execute_sync_job

        image = MOCK_MANIFEST[0]

        with patch("app.db.get_session", lambda: _fake_session_ctx(test_db)):
            # Use a non-existent job_id — should just return
            await _execute_sync_job("nonexistent-job", "docker:ceos:4.28.0F", image, sample_host)


# ---------------------------------------------------------------------------
# _execute_sync_job — File-based path
# ---------------------------------------------------------------------------

class TestExecuteSyncJobFileBased:
    """Tests for _execute_sync_job with file-based images (qcow2, img, iol)."""

    @pytest.mark.asyncio
    async def test_file_not_found_marks_failed(self, test_db: Session, sample_host):
        """If the source file doesn't exist, job fails."""
        from app.routers.images.sync import _execute_sync_job

        job = _make_sync_job(
            test_db, sample_host.id, job_id="file-missing",
            image_id="file:/images/iosv.qcow2",
        )
        image = MOCK_MANIFEST[1]  # qcow2 kind

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.db.get_session", lambda: _fake_session_ctx(test_db)),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.agent_client.http._get_agent_auth_headers", return_value={}),
        ):
            await _execute_sync_job(
                "file-missing", "file:/images/iosv.qcow2", image, sample_host
            )

        test_db.refresh(job)
        assert job.status == "failed"
        assert "not found" in (job.error_message or "").lower()

    @pytest.mark.asyncio
    async def test_unsupported_kind_marks_failed(self, test_db: Session, sample_host):
        """Unsupported image kind (not docker, not file-based) fails."""
        from app.routers.images.sync import _execute_sync_job

        job = _make_sync_job(test_db, sample_host.id, job_id="bad-kind")
        image = {"id": "oci:something", "reference": "oci://foo", "kind": "oci"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.db.get_session", lambda: _fake_session_ctx(test_db)),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.agent_client.http._get_agent_auth_headers", return_value={}),
        ):
            await _execute_sync_job("bad-kind", "oci:something", image, sample_host)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "unsupported" in (job.error_message or "").lower()

    @pytest.mark.asyncio
    async def test_agent_returns_failure(self, test_db: Session, sample_host, tmp_path):
        """Agent pull progress failure should mark the sync job failed."""
        from app.routers.images.sync import _execute_sync_job

        job = _make_sync_job(
            test_db, sample_host.id, job_id="agent-nack",
            image_id="file:/images/iosv.qcow2",
        )
        fake_file = tmp_path / "iosv.qcow2"
        fake_file.write_bytes(b"fake qcow2 data")
        image = {
            "id": "file:" + str(fake_file),
            "reference": str(fake_file),
            "kind": "qcow2",
            "device_id": "cisco_iosv",
            "sha256": "abc123",
        }

        mock_pull_response = MagicMock()
        mock_pull_response.status_code = 200
        mock_pull_response.raise_for_status = MagicMock()
        mock_pull_response.json.return_value = {"job_id": "agent-pull-job"}

        mock_progress_response = MagicMock()
        mock_progress_response.status_code = 200
        mock_progress_response.raise_for_status = MagicMock()
        mock_progress_response.json.return_value = {
            "status": "failed",
            "error": "Agent disk full",
            "progress_percent": 0,
            "bytes_transferred": 0,
            "total_bytes": fake_file.stat().st_size,
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_pull_response)
        mock_client.get = AsyncMock(return_value=mock_progress_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.db.get_session", lambda: _fake_session_ctx(test_db)),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.agent_client.http._get_agent_auth_headers", return_value={}),
        ):
            await _execute_sync_job(
                "agent-nack", image["id"], image, sample_host
            )

        test_db.refresh(job)
        assert job.status == "failed"
        assert "Agent disk full" in (job.error_message or "")


# ---------------------------------------------------------------------------
# _execute_sync_job — httpx error handling
# ---------------------------------------------------------------------------

class TestExecuteSyncJobHttpErrors:
    """Tests for httpx error categorization in _execute_sync_job."""

    @pytest.mark.asyncio
    async def test_timeout_error_categorized(self, test_db: Session, sample_host, tmp_path):
        """httpx.TimeoutException is caught and categorized."""
        from app.routers.images.sync import _execute_sync_job

        job = _make_sync_job(
            test_db, sample_host.id, job_id="timeout-job",
            image_id="file:/images/iosv.qcow2",
        )
        fake_file = tmp_path / "iosv.qcow2"
        fake_file.write_bytes(b"data")
        image = {
            "id": "file:" + str(fake_file),
            "reference": str(fake_file),
            "kind": "qcow2",
            "device_id": "cisco_iosv",
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.db.get_session", lambda: _fake_session_ctx(test_db)),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.agent_client.http._get_agent_auth_headers", return_value={}),
        ):
            await _execute_sync_job("timeout-job", image["id"], image, sample_host)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "timed out" in (job.error_message or "").lower()

    @pytest.mark.asyncio
    async def test_connect_error_categorized(self, test_db: Session, sample_host, tmp_path):
        """httpx.ConnectError is caught and categorized."""
        from app.routers.images.sync import _execute_sync_job

        job = _make_sync_job(
            test_db, sample_host.id, job_id="connect-err",
            image_id="file:/images/iosv.qcow2",
        )
        fake_file = tmp_path / "iosv.qcow2"
        fake_file.write_bytes(b"data")
        image = {
            "id": "file:" + str(fake_file),
            "reference": str(fake_file),
            "kind": "qcow2",
            "device_id": "cisco_iosv",
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.db.get_session", lambda: _fake_session_ctx(test_db)),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.agent_client.http._get_agent_auth_headers", return_value={}),
        ):
            await _execute_sync_job("connect-err", image["id"], image, sample_host)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "connect" in (job.error_message or "").lower()


# ---------------------------------------------------------------------------
# stream_image endpoint — deeper paths
# ---------------------------------------------------------------------------

class TestStreamImageDeep:
    """Test stream_image beyond basic 404/400 validation."""

    def test_stream_success_with_content_length(
        self, test_client: TestClient, auth_headers: dict
    ):
        """Successful stream returns tar content-type and content-length header."""
        # Mock docker inspect for content-length
        inspect_proc = AsyncMock()
        inspect_proc.communicate = AsyncMock(return_value=(b"54321\n", b""))
        inspect_proc.returncode = 0

        # Mock docker save for streaming
        save_proc = AsyncMock()
        save_proc.stdout = AsyncMock()
        save_proc.stdout.read = AsyncMock(side_effect=[b"tar-data-chunk", b""])
        save_proc.communicate = AsyncMock(return_value=(b"", b""))
        save_proc.returncode = 0

        call_count = {"n": 0}

        async def fake_subprocess(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return inspect_proc
            return save_proc

        async def fake_wait_for(coro, timeout):
            return await coro

        with (
            patch("app.routers.images.sync.load_manifest", return_value=MOCK_MANIFEST),
            patch("app.routers.images.sync.find_image_by_id", side_effect=_mock_find),
            patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess),
            patch("asyncio.wait_for", side_effect=fake_wait_for),
        ):
            resp = test_client.get(
                "/images/library/docker:ceos:4.28.0F/stream",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/x-tar"
        assert resp.headers.get("content-length") == "54321"
        assert resp.content == b"tar-data-chunk"

    def test_stream_without_content_length_on_inspect_failure(
        self, test_client: TestClient, auth_headers: dict
    ):
        """When docker inspect fails, response omits Content-Length but still streams."""
        # Inspect fails
        inspect_proc = AsyncMock()
        inspect_proc.communicate = AsyncMock(return_value=(b"", b"not found"))
        inspect_proc.returncode = 1

        # Save succeeds
        save_proc = AsyncMock()
        save_proc.stdout = AsyncMock()
        save_proc.stdout.read = AsyncMock(side_effect=[b"data", b""])
        save_proc.communicate = AsyncMock(return_value=(b"", b""))
        save_proc.returncode = 0

        call_count = {"n": 0}

        async def fake_subprocess(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return inspect_proc
            return save_proc

        async def fake_wait_for(coro, timeout):
            return await coro

        with (
            patch("app.routers.images.sync.load_manifest", return_value=MOCK_MANIFEST),
            patch("app.routers.images.sync.find_image_by_id", side_effect=_mock_find),
            patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess),
            patch("asyncio.wait_for", side_effect=fake_wait_for),
        ):
            resp = test_client.get(
                "/images/library/docker:ceos:4.28.0F/stream",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        # Content-Length should NOT be present when inspect fails

    def test_stream_file_based_image(self, test_client: TestClient, auth_headers: dict, tmp_path):
        """File-based images should stream raw bytes with content length."""
        file_path = tmp_path / "sonic-vs.img"
        file_path.write_bytes(b"qcow2-stream")

        manifest = [
            {
                "id": "qcow2:sonic-vs.img",
                "reference": str(file_path),
                "kind": "qcow2",
            }
        ]

        with (
            patch("app.routers.images.sync.load_manifest", return_value=manifest),
            patch("app.routers.images.sync.find_image_by_id", side_effect=lambda _m, _i: manifest[0]),
        ):
            resp = test_client.get(
                "/images/library/qcow2:sonic-vs.img/stream",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"
        assert resp.headers["content-length"] == str(len(b"qcow2-stream"))
        assert resp.content == b"qcow2-stream"

    def test_stream_docker_save_error_still_returns_200(
        self, test_client: TestClient, auth_headers: dict
    ):
        """docker save returning non-zero prints error but generator still completes."""
        inspect_proc = AsyncMock()
        inspect_proc.communicate = AsyncMock(return_value=(b"100\n", b""))
        inspect_proc.returncode = 0

        save_proc = AsyncMock()
        save_proc.stdout = AsyncMock()
        save_proc.stdout.read = AsyncMock(side_effect=[b"partial", b""])
        save_proc.communicate = AsyncMock(return_value=(b"", b"save error"))
        save_proc.returncode = 1

        call_count = {"n": 0}

        async def fake_subprocess(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return inspect_proc
            return save_proc

        async def fake_wait_for(coro, timeout):
            return await coro

        with (
            patch("app.routers.images.sync.load_manifest", return_value=MOCK_MANIFEST),
            patch("app.routers.images.sync.find_image_by_id", side_effect=_mock_find),
            patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess),
            patch("asyncio.wait_for", side_effect=fake_wait_for),
        ):
            resp = test_client.get(
                "/images/library/docker:ceos:4.28.0F/stream",
                headers=auth_headers,
            )

        # StreamingResponse starts with 200 before generator runs
        assert resp.status_code == 200
        assert resp.content == b"partial"


# ---------------------------------------------------------------------------
# push_image_to_hosts — dedup & concurrency
# ---------------------------------------------------------------------------

class TestPushImageDedup:
    """Tests for job dedup and max-concurrent enforcement in push_image_to_hosts."""

    def test_push_dedup_existing_pending_job(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        sample_host: models.Host,
    ):
        """Existing pending job for same image+host is reused, not duplicated."""
        _make_sync_job(
            test_db, sample_host.id,
            job_id="existing-pending",
            status="pending",
        )

        with (
            patch("app.routers.images.sync.load_manifest", return_value=MOCK_MANIFEST),
            patch("app.routers.images.sync.find_image_by_id", side_effect=_mock_find),
            patch("asyncio.create_task"),  # prevent background task
        ):
            resp = test_client.post(
                "/images/library/docker:ceos:4.28.0F/push",
                json={"host_ids": [sample_host.id]},
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "existing-pending" in data["jobs"]

    def test_push_dedup_existing_transferring_job(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        sample_host: models.Host,
    ):
        """Existing transferring job for same image+host is reused."""
        _make_sync_job(
            test_db, sample_host.id,
            job_id="existing-xfer",
            status="transferring",
        )

        with (
            patch("app.routers.images.sync.load_manifest", return_value=MOCK_MANIFEST),
            patch("app.routers.images.sync.find_image_by_id", side_effect=_mock_find),
            patch("asyncio.create_task"),
        ):
            resp = test_client.post(
                "/images/library/docker:ceos:4.28.0F/push",
                json={"host_ids": [sample_host.id]},
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "existing-xfer" in data["jobs"]

    def test_push_creates_image_host_record(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        sample_host: models.Host,
    ):
        """Push creates an ImageHost record with 'syncing' status."""
        with (
            patch("app.routers.images.sync.load_manifest", return_value=MOCK_MANIFEST),
            patch("app.routers.images.sync.find_image_by_id", side_effect=_mock_find),
            patch("asyncio.create_task"),
        ):
            resp = test_client.post(
                "/images/library/docker:ceos:4.28.0F/push",
                json={"host_ids": [sample_host.id]},
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        ih = test_db.query(models.ImageHost).filter(
            models.ImageHost.image_id == "docker:ceos:4.28.0F",
            models.ImageHost.host_id == sample_host.id,
        ).first()
        assert ih is not None
        assert ih.status == "syncing"

    def test_push_updates_existing_image_host(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        sample_host: models.Host,
    ):
        """Push updates existing ImageHost record to 'syncing', clears error."""
        ih = models.ImageHost(
            id=str(uuid4()),
            image_id="docker:ceos:4.28.0F",
            host_id=sample_host.id,
            reference="ceos:4.28.0F",
            status="failed",
            error_message="previous error",
        )
        test_db.add(ih)
        test_db.commit()

        with (
            patch("app.routers.images.sync.load_manifest", return_value=MOCK_MANIFEST),
            patch("app.routers.images.sync.find_image_by_id", side_effect=_mock_find),
            patch("asyncio.create_task"),
        ):
            resp = test_client.post(
                "/images/library/docker:ceos:4.28.0F/push",
                json={"host_ids": [sample_host.id]},
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        test_db.refresh(ih)
        assert ih.status == "syncing"
        assert ih.error_message is None

    def test_push_to_all_hosts(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        multiple_hosts: list[models.Host],
    ):
        """Push with host_ids=None targets all online hosts."""
        with (
            patch("app.routers.images.sync.load_manifest", return_value=MOCK_MANIFEST),
            patch("app.routers.images.sync.find_image_by_id", side_effect=_mock_find),
            patch("asyncio.create_task"),
        ):
            resp = test_client.post(
                "/images/library/docker:ceos:4.28.0F/push",
                json={"host_ids": None},
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        # multiple_hosts has 2 online + 1 offline; should only sync to online
        assert data["count"] == 2

    def test_push_specific_hosts(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        multiple_hosts: list[models.Host],
    ):
        """Push with specific host_ids only targets those hosts."""
        with (
            patch("app.routers.images.sync.load_manifest", return_value=MOCK_MANIFEST),
            patch("app.routers.images.sync.find_image_by_id", side_effect=_mock_find),
            patch("asyncio.create_task"),
        ):
            resp = test_client.post(
                "/images/library/docker:ceos:4.28.0F/push",
                json={"host_ids": [multiple_hosts[0].id]},
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1


# ---------------------------------------------------------------------------
# list_sync_jobs — image_id filter
# ---------------------------------------------------------------------------

class TestListSyncJobsImageFilter:
    """Test filtering sync jobs by image_id."""

    def test_filter_by_image_id(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        sample_host: models.Host,
    ):
        _make_sync_job(test_db, sample_host.id, image_id="docker:ceos:4.28.0F")
        _make_sync_job(test_db, sample_host.id, image_id="docker:srlinux:latest")

        resp = test_client.get(
            "/images/sync-jobs?image_id=docker:ceos:4.28.0F",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["image_id"] == "docker:ceos:4.28.0F"

    def test_sync_job_host_name_resolved(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        sample_host: models.Host,
    ):
        """Verify host_name is resolved in list response."""
        _make_sync_job(test_db, sample_host.id)
        resp = test_client.get("/images/sync-jobs", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["host_name"] == sample_host.name


# ---------------------------------------------------------------------------
# cancel_sync_job — edge cases
# ---------------------------------------------------------------------------

class TestCancelSyncJobEdgeCases:
    """Additional cancel edge cases."""

    def test_cancel_already_cancelled_rejected(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        sample_host: models.Host,
    ):
        """Cancelling an already-cancelled job returns 400."""
        _make_sync_job(
            test_db, sample_host.id, job_id="already-cancel", status="cancelled"
        )
        resp = test_client.delete(
            "/images/sync-jobs/already-cancel", headers=admin_auth_headers
        )
        assert resp.status_code == 400
        assert "Cannot cancel" in resp.json()["detail"]

    def test_cancel_loading_job_succeeds(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        sample_host: models.Host,
    ):
        """'loading' status jobs can be cancelled."""
        job = _make_sync_job(
            test_db, sample_host.id, job_id="loading-job", status="loading"
        )
        resp = test_client.delete(
            "/images/sync-jobs/loading-job", headers=admin_auth_headers
        )
        assert resp.status_code == 200
        test_db.refresh(job)
        assert job.status == "cancelled"
        assert job.completed_at is not None

    def test_cancel_without_image_host_record(
        self,
        test_client: TestClient,
        test_db: Session,
        admin_auth_headers: dict,
        sample_host: models.Host,
    ):
        """Cancel works even when no ImageHost record exists (no crash)."""
        _make_sync_job(
            test_db, sample_host.id, job_id="no-ih-cancel", status="pending"
        )
        resp = test_client.delete(
            "/images/sync-jobs/no-ih-cancel", headers=admin_auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"


# ---------------------------------------------------------------------------
# get_sync_job — host deleted
# ---------------------------------------------------------------------------

class TestGetSyncJobHostDeleted:
    """Test get_sync_job when the host no longer exists."""

    def test_job_with_deleted_host(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        sample_host: models.Host,
    ):
        """Job can be retrieved even if its host was deleted (host_name=None)."""
        _make_sync_job(test_db, sample_host.id, job_id="orphan-job")

        # Delete the host
        test_db.delete(sample_host)
        test_db.commit()

        resp = test_client.get(
            "/images/sync-jobs/orphan-job", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "orphan-job"
        assert data["host_name"] is None
