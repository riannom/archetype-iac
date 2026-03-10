"""Round 12 deep-path tests for app/tasks/image_sync.py.

Targets callback chains, timeout/retry logic, and error handling paths
NOT covered by existing test_tasks_image_sync.py or test_tasks_image_sync_extended.py.

Focus areas:
- ensure_images_for_deployment: timeout, partial failure, exception in gather
- _run_sync_and_callback: host-not-found path
- _wait_for_sync_and_callback: error status branch, poll iteration
- check_and_start_image_sync: outer exception handler, missing image_id
- check_agent_has_image: checksum mismatch, non-200 for file images
- reconcile_agent_images: mark previously-synced as missing, exception path
- _sync_image_to_agent_impl: job disappears, updates existing ImageHost
"""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models
from tests.factories import make_host


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lab_with_nodes(test_db: Session, user: models.User, node_names: list[str],
                         *, desired: str = "running") -> tuple[models.Lab, list[models.NodeState]]:
    lab = models.Lab(
        name="Round12 Lab",
        owner_id=user.id,
        provider="docker",
        state="starting",
        workspace_path="/tmp/r12-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    nodes = []
    for name in node_names:
        ns = models.NodeState(
            lab_id=lab.id,
            node_id=name,
            node_name=name,
            desired_state=desired,
            actual_state="undeployed",
        )
        test_db.add(ns)
        nodes.append(ns)
    test_db.commit()
    for ns in nodes:
        test_db.refresh(ns)
    return lab, nodes


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnsureImagesTimeoutPath:
    """ensure_images_for_deployment: asyncio.TimeoutError branch."""

    @pytest.mark.asyncio
    async def test_timeout_marks_syncing_refs_as_failed(
        self, test_db: Session, test_user: models.User, monkeypatch,
    ):
        """When sync tasks exceed the timeout, all syncing refs should be
        marked failed and the function returns (False, missing, logs)."""
        from app.config import settings
        from app.tasks.image_sync import ensure_images_for_deployment

        monkeypatch.setattr(settings, "image_sync_pre_deploy_check", True)

        host = make_host(test_db, host_id="timeout-host")
        lab, nodes = _make_lab_with_nodes(test_db, test_user, ["n1", "n2"])
        image_to_nodes = {"slow:img": ["n1", "n2"]}
        manifest = {"images": [{"id": "docker:slow:img", "kind": "docker", "reference": "slow:img"}]}

        async def never_finish(*a, **kw):
            await asyncio.sleep(9999)
            return True, None

        with patch("app.tasks.image_sync.check_agent_has_image", new=AsyncMock(return_value=False)), \
             patch("app.tasks.image_sync.load_manifest", return_value=manifest), \
             patch("app.tasks.image_sync.sync_image_to_agent", side_effect=never_finish):
            all_ready, missing, logs = await ensure_images_for_deployment(
                host_id=host.id,
                image_references=["slow:img"],
                timeout=0,  # immediate timeout
                database=test_db,
                lab_id=lab.id,
                image_to_nodes=image_to_nodes,
            )

        assert all_ready is False
        assert "slow:img" in missing
        assert any("timed out" in line.lower() for line in logs)

        # Nodes should have been marked failed
        for ns in nodes:
            test_db.refresh(ns)
            assert ns.image_sync_status == "failed"


class TestEnsureImagesPartialFailure:
    """ensure_images_for_deployment: one sync succeeds, one raises exception."""

    @pytest.mark.asyncio
    async def test_partial_failure_reports_both_outcomes(
        self, test_db: Session, test_user: models.User, monkeypatch,
    ):
        """When gather returns a mix of success tuples and exceptions,
        the function should correctly partition synced vs still_missing."""
        from app.config import settings
        from app.tasks.image_sync import ensure_images_for_deployment

        monkeypatch.setattr(settings, "image_sync_pre_deploy_check", True)
        monkeypatch.setattr(settings, "image_sync_timeout", 30)

        host = make_host(test_db, host_id="partial-host")
        lab, nodes = _make_lab_with_nodes(test_db, test_user, ["good-node", "bad-node"])
        image_to_nodes = {"good:img": ["good-node"], "bad:img": ["bad-node"]}
        manifest = {"images": [
            {"id": "docker:good:img", "kind": "docker", "reference": "good:img"},
            {"id": "docker:bad:img", "kind": "docker", "reference": "bad:img"},
        ]}

        call_count = 0

        async def selective_sync(image_id, host_id, database=None):
            nonlocal call_count
            call_count += 1
            if "good" in image_id:
                return True, None
            raise ConnectionError("agent unreachable")

        with patch("app.tasks.image_sync.check_agent_has_image", new=AsyncMock(return_value=False)), \
             patch("app.tasks.image_sync.load_manifest", return_value=manifest), \
             patch("app.tasks.image_sync.sync_image_to_agent", side_effect=selective_sync):
            all_ready, missing, logs = await ensure_images_for_deployment(
                host_id=host.id,
                image_references=["good:img", "bad:img"],
                database=test_db,
                lab_id=lab.id,
                image_to_nodes=image_to_nodes,
            )

        assert all_ready is False
        assert "bad:img" in missing
        assert "good:img" not in missing
        assert any("synced successfully" in line for line in logs)
        assert any("FAILED" in line for line in logs)


class TestEnsureImagesMissingFromLibrary:
    """ensure_images_for_deployment: missing images have no library entry."""

    @pytest.mark.asyncio
    async def test_missing_not_in_library_returns_failure(
        self, test_db: Session, test_user: models.User, monkeypatch,
    ):
        """When missing images have no matching library entry, no sync tasks
        are created and the function reports failure."""
        from app.config import settings
        from app.tasks.image_sync import ensure_images_for_deployment

        monkeypatch.setattr(settings, "image_sync_pre_deploy_check", True)
        monkeypatch.setattr(settings, "image_sync_timeout", 30)

        host = make_host(test_db, host_id="nolib-host")
        lab, _ = _make_lab_with_nodes(test_db, test_user, ["orphan"])
        # Empty manifest -- image not found
        manifest = {"images": []}

        with patch("app.tasks.image_sync.check_agent_has_image", new=AsyncMock(return_value=False)), \
             patch("app.tasks.image_sync.load_manifest", return_value=manifest):
            all_ready, missing, logs = await ensure_images_for_deployment(
                host_id=host.id,
                image_references=["phantom:img"],
                database=test_db,
                lab_id=lab.id,
                image_to_nodes={"phantom:img": ["orphan"]},
            )

        assert all_ready is False
        assert "phantom:img" in missing
        assert any("not found in library" in line.lower() for line in logs)


class TestEnsureImagesOuterException:
    """ensure_images_for_deployment: unhandled exception in implementation."""

    @pytest.mark.asyncio
    async def test_outer_exception_returns_all_refs_as_missing(
        self, test_db: Session, test_user: models.User, monkeypatch,
    ):
        """An unexpected exception should be caught and all refs returned as missing."""
        from app.config import settings
        from app.tasks.image_sync import ensure_images_for_deployment

        monkeypatch.setattr(settings, "image_sync_pre_deploy_check", True)

        host = make_host(test_db, host_id="exc-host")

        with patch("app.tasks.image_sync.load_manifest", side_effect=RuntimeError("manifest boom")):
            all_ready, missing, logs = await ensure_images_for_deployment(
                host_id=host.id,
                image_references=["any:ref"],
                database=test_db,
            )

        assert all_ready is False
        assert "any:ref" in missing
        assert any("error" in line.lower() for line in logs)


class TestRunSyncAndCallbackHostNotFound:
    """_run_sync_and_callback: host disappears between schedule and execution."""

    @pytest.mark.asyncio
    async def test_host_not_found_returns_early(self, test_db: Session, test_user: models.User):
        """When the host record is gone, the callback should log and return
        without attempting sync or marking nodes."""
        from app.tasks.image_sync import _run_sync_and_callback

        lab, nodes = _make_lab_with_nodes(test_db, test_user, ["n1"])

        @contextmanager
        def fake_session():
            yield test_db

        with patch("app.tasks.image_sync.get_session", fake_session), \
             patch("app.tasks.image_sync._broadcast_nodes_sync_cleared") as mock_clear, \
             patch("app.tasks.image_sync._mark_nodes_sync_failed") as mock_fail:
            await _run_sync_and_callback(
                sync_job_id="job-gone",
                image_id="docker:test:1",
                image={"reference": "test:1"},
                host_id="nonexistent-host-xyz",
                lab_id=lab.id,
                node_ids=["n1"],
                image_to_nodes={"test:1": ["n1"]},
                provider="docker",
            )

        # Neither callback should fire -- early return
        mock_clear.assert_not_called()
        mock_fail.assert_not_called()


class TestRunSyncAndCallbackSyncJobFailed:
    """_run_sync_and_callback: sync job completes with failed status."""

    @pytest.mark.asyncio
    async def test_failed_sync_marks_nodes(self, test_db: Session, test_user: models.User):
        """When the sync job ends in non-completed status, nodes are marked failed."""
        from app.tasks.image_sync import _run_sync_and_callback

        host = make_host(test_db, host_id="fail-cb-host")
        lab, nodes = _make_lab_with_nodes(test_db, test_user, ["n1"])

        # Create a failed sync job
        sync_job = models.ImageSyncJob(
            id=str(uuid4()),
            image_id="docker:fail:1",
            host_id=host.id,
            status="failed",
            error_message="disk full",
        )
        test_db.add(sync_job)
        test_db.commit()

        @contextmanager
        def fake_session():
            yield test_db

        with patch("app.tasks.image_sync.get_session", fake_session), \
             patch("app.routers.images._execute_sync_job", new=AsyncMock()), \
             patch("app.tasks.image_sync._broadcast_nodes_sync_cleared") as mock_clear, \
             patch("app.tasks.image_sync._mark_nodes_sync_failed") as mock_fail:
            await _run_sync_and_callback(
                sync_job_id=sync_job.id,
                image_id="docker:fail:1",
                image={"reference": "fail:1"},
                host_id=host.id,
                lab_id=lab.id,
                node_ids=["n1"],
                image_to_nodes={"fail:1": ["n1"]},
                provider="docker",
            )

        mock_clear.assert_not_called()
        mock_fail.assert_called_once()
        call_args = mock_fail.call_args
        assert "disk full" in call_args[1].get("error_msg", call_args[0][3] if len(call_args[0]) > 3 else "")


class TestWaitForSyncAndCallbackErrorStatus:
    """_wait_for_sync_and_callback: job reaches 'error' status (not 'failed')."""

    @pytest.mark.asyncio
    async def test_error_status_marks_nodes_failed(self, test_db: Session, test_user: models.User):
        """The error branch (status in completed/failed/error) for 'error'
        should mark nodes as failed."""
        from app.tasks.image_sync import _wait_for_sync_and_callback

        host = make_host(test_db, host_id="wait-err-host")
        lab, nodes = _make_lab_with_nodes(test_db, test_user, ["w1"])

        sync_job = models.ImageSyncJob(
            id=str(uuid4()),
            image_id="docker:err:1",
            host_id=host.id,
            status="error",
            error_message="internal error",
        )
        test_db.add(sync_job)
        test_db.commit()

        @contextmanager
        def fake_session():
            yield test_db

        with patch("app.tasks.image_sync.get_session", fake_session), \
             patch("app.tasks.image_sync.asyncio.sleep", new=AsyncMock()), \
             patch("app.tasks.image_sync._broadcast_nodes_sync_cleared") as mock_clear, \
             patch("app.tasks.image_sync._mark_nodes_sync_failed") as mock_fail, \
             patch("app.tasks.image_sync._trigger_re_reconcile") as mock_recon:
            await _wait_for_sync_and_callback(
                sync_job_id=sync_job.id,
                image={"reference": "err:1"},
                host_id=host.id,
                lab_id=lab.id,
                node_ids=["w1"],
                image_to_nodes={"err:1": ["w1"]},
                provider="docker",
                poll_interval=0.01,
                max_wait=1.0,
            )

        mock_fail.assert_called_once()
        mock_clear.assert_not_called()
        mock_recon.assert_not_called()


class TestWaitForSyncAndCallbackTimeout:
    """_wait_for_sync_and_callback: polling exceeds max_wait."""

    @pytest.mark.asyncio
    async def test_timeout_marks_nodes_failed(self, test_db: Session, test_user: models.User):
        """When polling exceeds max_wait without terminal status, nodes
        should be marked failed with a timeout message."""
        from app.tasks.image_sync import _wait_for_sync_and_callback

        host = make_host(test_db, host_id="wait-to-host")
        lab, nodes = _make_lab_with_nodes(test_db, test_user, ["wt1"])

        # Job stays in 'transferring' forever
        sync_job = models.ImageSyncJob(
            id=str(uuid4()),
            image_id="docker:stuck:1",
            host_id=host.id,
            status="transferring",
        )
        test_db.add(sync_job)
        test_db.commit()

        @contextmanager
        def fake_session():
            yield test_db

        with patch("app.tasks.image_sync.get_session", fake_session), \
             patch("app.tasks.image_sync.asyncio.sleep", new=AsyncMock()), \
             patch("app.tasks.image_sync._mark_nodes_sync_failed") as mock_fail:
            await _wait_for_sync_and_callback(
                sync_job_id=sync_job.id,
                image={"reference": "stuck:1"},
                host_id=host.id,
                lab_id=lab.id,
                node_ids=["wt1"],
                image_to_nodes={"stuck:1": ["wt1"]},
                provider="docker",
                poll_interval=0.5,
                max_wait=1.0,
            )

        mock_fail.assert_called_once()
        args = mock_fail.call_args
        error_msg = args[1].get("error_msg", args[0][3] if len(args[0]) > 3 else "")
        assert "timed out" in error_msg.lower()


class TestCheckAgentHasImageChecksumMismatch:
    """check_agent_has_image: SHA256 mismatch returns False."""

    @pytest.mark.asyncio
    async def test_checksum_mismatch_returns_false(self, test_db: Session):
        """When agent reports a different sha256 from expected, should return False."""
        from app.tasks.image_sync import check_agent_has_image

        host = make_host(test_db, host_id="sha-host", providers=["docker", "libvirt"])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "exists": True,
            "sha256": "aaaa1111bbbb2222cccc3333dddd4444eeee5555ffff6666",
        }

        with patch("app.tasks.image_sync.httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            result = await check_agent_has_image(
                host,
                "/images/nexus.qcow2",
                expected_sha256="0000111122223333444455556666777788889999",
            )

        assert result is False


class TestCheckAgentHasImageNon200ForFile:
    """check_agent_has_image: non-200 response for file-based image."""

    @pytest.mark.asyncio
    async def test_non_200_for_file_returns_false(self, test_db: Session):
        """A 404 from the agent for a file image should return False."""
        from app.tasks.image_sync import check_agent_has_image

        host = make_host(test_db, host_id="404-host", providers=["docker", "libvirt"])

        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("app.tasks.image_sync.httpx.AsyncClient") as mock_client:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock()
            mock_instance.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_instance

            result = await check_agent_has_image(host, "/images/disk.qcow2")

        assert result is False


class TestCheckAndStartImageSyncOuterException:
    """check_and_start_image_sync: exception in outer try/except."""

    @pytest.mark.asyncio
    async def test_outer_exception_returns_gracefully(
        self, test_db: Session, test_user: models.User,
    ):
        """An unexpected exception should be caught and returned via log_entries."""
        from app.tasks.image_sync import check_and_start_image_sync

        host = make_host(test_db, host_id="exc-cas-host")
        lab, nodes = _make_lab_with_nodes(test_db, test_user, ["ex1"])

        # Force an exception during image check
        with patch("app.tasks.image_sync.check_agent_has_image",
                    new=AsyncMock(side_effect=RuntimeError("unexpected boom"))):
            syncing, failed, logs = await check_and_start_image_sync(
                host_id=host.id,
                image_references=["boom:ref"],
                database=test_db,
                lab_id=lab.id,
                job_id="job-exc",
                node_ids=["ex1"],
                image_to_nodes={"boom:ref": ["ex1"]},
                provider="docker",
            )

        assert any("error" in line.lower() for line in logs)


class TestReconcileAgentImagesMarksMissing:
    """reconcile_agent_images: previously synced image no longer on agent."""

    @pytest.mark.asyncio
    async def test_synced_image_removed_from_agent_marked_missing(
        self, test_db: Session,
    ):
        """An ImageHost that was 'synced' should flip to 'missing' when the
        agent inventory no longer contains the image."""
        from app.tasks.image_sync import reconcile_agent_images

        host = make_host(test_db, host_id="recon-host")

        # Pre-existing synced record
        from datetime import datetime, timezone
        ih = models.ImageHost(
            id=str(uuid4()),
            image_id="docker:vanished:1.0",
            host_id=host.id,
            reference="vanished:1.0",
            status="synced",
            synced_at=datetime.now(timezone.utc),
        )
        test_db.add(ih)
        test_db.commit()

        manifest = {"images": [
            {"id": "docker:vanished:1.0", "kind": "docker", "reference": "vanished:1.0"},
        ]}

        # Agent inventory returns empty -- image was removed
        with patch("app.tasks.image_sync.get_agent_image_inventory", new=AsyncMock(return_value=[])), \
             patch("app.tasks.image_sync.load_manifest", return_value=manifest):
            await reconcile_agent_images(host.id, test_db)

        test_db.refresh(ih)
        assert ih.status == "missing"