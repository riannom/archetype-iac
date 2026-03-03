"""Comprehensive tests for api/app/tasks/jobs_multihost.py.

Covers:
- Multi-host dispatch logic (distributing nodes across agents)
- Capacity checks and overflow handling
- Partial failure rollback (what happens when one agent fails)
- Single-host fallback behavior
- Agent communication failure handling
- Destroy-side dispatch and link cleanup
- Unexpected exception handling in both deploy and destroy
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.tasks.jobs_multihost import run_multihost_deploy, run_multihost_destroy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MODULE = "app.tasks.jobs_multihost"


def _mock_get_session(test_db: Session):
    """Create a mock get_session context manager that yields the test DB."""

    @contextmanager
    def mock_session():
        yield test_db

    return mock_session


def _make_lab(test_db, test_user, *, state="stopped", name="Test Lab"):
    lab = models.Lab(
        name=name,
        owner_id=test_user.id,
        provider="docker",
        state=state,
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


def _make_job(test_db, lab, test_user, *, action="up", status="queued"):
    job = models.Job(
        lab_id=lab.id,
        user_id=test_user.id,
        action=action,
        status=status,
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


def _make_node(test_db, lab, *, gui_id, name, device="linux", host_id=None):
    node = models.Node(
        lab_id=lab.id,
        gui_id=gui_id,
        display_name=name,
        container_name=name,
        node_type="device",
        device=device,
        host_id=host_id,
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    return node


def _make_link_state(test_db, lab, *, link_name="r1:eth1-r2:eth1",
                     desired="up", actual="up"):
    ls = models.LinkState(
        lab_id=lab.id,
        link_name=link_name,
        source_node=link_name.split("-")[0].split(":")[0],
        source_interface=link_name.split("-")[0].split(":")[1],
        target_node=link_name.split("-")[1].split(":")[0],
        target_interface=link_name.split("-")[1].split(":")[1],
        desired_state=desired,
        actual_state=actual,
    )
    test_db.add(ls)
    test_db.commit()
    test_db.refresh(ls)
    return ls


@dataclass
class _FakeAnalysis:
    """Minimal stand-in for TopologyAnalysisResult."""
    placements: dict
    cross_host_links: list
    single_host: bool = False


def _standard_deploy_patches(test_db):
    """Return a dict of patch targets common to most deploy tests."""
    return {
        f"{MODULE}.get_session": _mock_get_session(test_db),
        f"{MODULE}._dispatch_webhook": AsyncMock(),
        f"{MODULE}._capture_node_ips": AsyncMock(),
        f"{MODULE}.emit_deploy_finished": AsyncMock(),
        f"{MODULE}._broadcast_job_progress": AsyncMock(),
        f"{MODULE}._update_node_placements": AsyncMock(),
    }


def _standard_destroy_patches(test_db):
    """Return a dict of patch targets common to most destroy tests."""
    return {
        f"{MODULE}.get_session": _mock_get_session(test_db),
        f"{MODULE}._dispatch_webhook": AsyncMock(),
        f"{MODULE}.emit_destroy_finished": AsyncMock(),
        f"{MODULE}.emit_job_failed": AsyncMock(),
        f"{MODULE}._broadcast_job_progress": AsyncMock(),
    }


# ---------------------------------------------------------------------------
# Deploy Tests
# ---------------------------------------------------------------------------


class TestMultihostDeployDispatch:
    """Tests for multi-host dispatch logic in run_multihost_deploy."""

    @pytest.mark.asyncio
    async def test_job_not_found_returns_early(self, test_db: Session, test_user):
        """If the job ID doesn't exist in DB, the function returns early."""
        with patch(f"{MODULE}.get_session", _mock_get_session(test_db)):
            await run_multihost_deploy("nonexistent-job", "some-lab")

        # No crash, no state mutation — function just logged and returned.

    @pytest.mark.asyncio
    async def test_lab_not_found_fails_job(self, test_db: Session, test_user):
        """If the lab doesn't exist, the job is marked failed."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        # Delete the lab so the lookup fails
        test_db.delete(lab)
        test_db.commit()

        with patch(f"{MODULE}.get_session", _mock_get_session(test_db)):
            await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "not found" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_single_host_deploy_success(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Deploying to a single host with one node succeeds end-to-end."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        patches = _standard_deploy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}._capture_node_ips", patches[f"{MODULE}._capture_node_ips"]):
                    with patch(f"{MODULE}.emit_deploy_finished", patches[f"{MODULE}.emit_deploy_finished"]):
                        with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                            with patch(f"{MODULE}._update_node_placements", patches[f"{MODULE}._update_node_placements"]):
                                with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                                    with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                                        with patch(f"{MODULE}.agent_client.deploy_to_agent", new_callable=AsyncMock, return_value={"status": "completed", "stdout": "OK"}):
                                            with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(1, 0)):
                                                await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        test_db.refresh(lab)
        assert job.status == "completed"
        assert lab.state == "running"

    @pytest.mark.asyncio
    async def test_two_host_deploy_dispatches_to_both(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Nodes on two different hosts result in two deploy_to_agent calls."""
        host1, host2 = multiple_hosts[0], multiple_hosts[1]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        _make_node(test_db, lab, gui_id="n2", name="r2", host_id=host2.id)

        mock_deploy = AsyncMock(return_value={"status": "completed", "stdout": "OK"})

        patches = _standard_deploy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}._capture_node_ips", patches[f"{MODULE}._capture_node_ips"]):
                    with patch(f"{MODULE}.emit_deploy_finished", patches[f"{MODULE}.emit_deploy_finished"]):
                        with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                            with patch(f"{MODULE}._update_node_placements", patches[f"{MODULE}._update_node_placements"]):
                                with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                                    with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                                        with patch(f"{MODULE}.agent_client.deploy_to_agent", mock_deploy):
                                            with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                                await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "completed"
        assert mock_deploy.await_count == 2

    @pytest.mark.asyncio
    async def test_three_nodes_on_two_hosts(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Three nodes split across two hosts; deploy to each host once."""
        host1, host2 = multiple_hosts[0], multiple_hosts[1]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        _make_node(test_db, lab, gui_id="n2", name="r2", host_id=host1.id)
        _make_node(test_db, lab, gui_id="n3", name="r3", host_id=host2.id)

        mock_deploy = AsyncMock(return_value={"status": "completed", "stdout": "OK"})

        patches = _standard_deploy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}._capture_node_ips", patches[f"{MODULE}._capture_node_ips"]):
                    with patch(f"{MODULE}.emit_deploy_finished", patches[f"{MODULE}.emit_deploy_finished"]):
                        with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                            with patch(f"{MODULE}._update_node_placements", patches[f"{MODULE}._update_node_placements"]):
                                with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                                    with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                                        with patch(f"{MODULE}.agent_client.deploy_to_agent", mock_deploy):
                                            with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                                await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "completed"
        # Two hosts => two deploy calls
        assert mock_deploy.await_count == 2


class TestMultihostDeployUnplacedNodes:
    """Tests for unplaced node assignment during deploy."""

    @pytest.mark.asyncio
    async def test_unplaced_nodes_assigned_to_default_agent(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Nodes without host_id get assigned to the default agent."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        node = _make_node(test_db, lab, gui_id="n1", name="r1", host_id=None)

        patches = _standard_deploy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}._capture_node_ips", patches[f"{MODULE}._capture_node_ips"]):
                    with patch(f"{MODULE}.emit_deploy_finished", patches[f"{MODULE}.emit_deploy_finished"]):
                        with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                            with patch(f"{MODULE}._update_node_placements", patches[f"{MODULE}._update_node_placements"]):
                                with patch(f"{MODULE}.agent_client.get_agent_for_lab", new_callable=AsyncMock, return_value=host1):
                                    with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                                        with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                                            with patch(f"{MODULE}.agent_client.deploy_to_agent", new_callable=AsyncMock, return_value={"status": "completed", "stdout": "OK"}):
                                                with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(node)
        assert node.host_id == host1.id
        test_db.refresh(job)
        assert job.status == "completed"

    @pytest.mark.asyncio
    async def test_no_default_agent_fails_job(self, test_db: Session, test_user):
        """Unplaced nodes with no available default agent fails the job."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=None)

        with patch(f"{MODULE}.get_session", _mock_get_session(test_db)):
            with patch(f"{MODULE}.agent_client.get_agent_for_lab", new_callable=AsyncMock, return_value=None):
                await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "no host assignment" in job.log_path.lower() or "no default agent" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_mixed_placed_and_unplaced(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Mix of placed and unplaced nodes: unplaced ones get assigned."""
        host1, host2 = multiple_hosts[0], multiple_hosts[1]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        unplaced = _make_node(test_db, lab, gui_id="n2", name="r2", host_id=None)

        patches = _standard_deploy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}._capture_node_ips", patches[f"{MODULE}._capture_node_ips"]):
                    with patch(f"{MODULE}.emit_deploy_finished", patches[f"{MODULE}.emit_deploy_finished"]):
                        with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                            with patch(f"{MODULE}._update_node_placements", patches[f"{MODULE}._update_node_placements"]):
                                with patch(f"{MODULE}.agent_client.get_agent_for_lab", new_callable=AsyncMock, return_value=host2):
                                    with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                                        with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                                            with patch(f"{MODULE}.agent_client.deploy_to_agent", new_callable=AsyncMock, return_value={"status": "completed", "stdout": "OK"}):
                                                with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(unplaced)
        assert unplaced.host_id == host2.id
        test_db.refresh(job)
        assert job.status == "completed"


class TestMultihostDeployAgentFailures:
    """Tests for agent communication failures during deploy."""

    @pytest.mark.asyncio
    async def test_missing_host_record_fails(self, test_db: Session, test_user):
        """Node assigned to non-existent host fails the job."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id="ghost-host-id")

        with patch(f"{MODULE}.get_session", _mock_get_session(test_db)):
            with patch(f"{MODULE}._dispatch_webhook", AsyncMock()):
                with patch(f"{MODULE}._broadcast_job_progress", AsyncMock()):
                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"

    @pytest.mark.asyncio
    async def test_offline_agent_fails_job(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Node on an offline agent causes failure (all hosts must be online)."""
        host3 = multiple_hosts[2]  # offline host
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host3.id)

        with patch(f"{MODULE}.get_session", _mock_get_session(test_db)):
            with patch(f"{MODULE}._dispatch_webhook", AsyncMock()):
                with patch(f"{MODULE}._broadcast_job_progress", AsyncMock()):
                    with patch(f"{MODULE}.agent_client.is_agent_online", return_value=False):
                        await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "missing" in job.log_path.lower() or "unhealthy" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_preflight_connectivity_failure(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Agent appears online but preflight connectivity check fails."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        with patch(f"{MODULE}.get_session", _mock_get_session(test_db)):
            with patch(f"{MODULE}._dispatch_webhook", AsyncMock()):
                with patch(f"{MODULE}._broadcast_job_progress", AsyncMock()):
                    with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                        with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, side_effect=ConnectionError("refused")):
                            await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "preflight" in job.log_path.lower() or "connectivity" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_deploy_exception_triggers_rollback(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """If deploy_to_agent raises, successful hosts are rolled back."""
        host1, host2 = multiple_hosts[0], multiple_hosts[1]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        _make_node(test_db, lab, gui_id="n2", name="r2", host_id=host2.id)

        call_count = 0

        async def mock_deploy(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"status": "completed", "stdout": "OK"}
            raise RuntimeError("agent crashed")

        mock_rollback = AsyncMock(return_value={"status": "completed"})

        patches = _standard_deploy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                    with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                        with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                            with patch(f"{MODULE}.agent_client.deploy_to_agent", new_callable=AsyncMock, side_effect=mock_deploy):
                                with patch(f"{MODULE}.agent_client.destroy_on_agent", mock_rollback):
                                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        # Rollback should be called on the host that succeeded
        assert mock_rollback.await_count >= 1

    @pytest.mark.asyncio
    async def test_deploy_non_completed_status_triggers_rollback(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Deploy returning status != 'completed' triggers rollback of successful hosts."""
        host1, host2 = multiple_hosts[0], multiple_hosts[1]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        _make_node(test_db, lab, gui_id="n2", name="r2", host_id=host2.id)

        results = iter([
            {"status": "completed", "stdout": "OK"},
            {"status": "error", "stderr": "something went wrong"},
        ])

        async def mock_deploy(*args, **kwargs):
            return next(results)

        mock_rollback = AsyncMock(return_value={"status": "completed"})

        patches = _standard_deploy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                    with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                        with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                            with patch(f"{MODULE}.agent_client.deploy_to_agent", new_callable=AsyncMock, side_effect=mock_deploy):
                                with patch(f"{MODULE}.agent_client.destroy_on_agent", mock_rollback):
                                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert mock_rollback.await_count >= 1

    @pytest.mark.asyncio
    async def test_rollback_failure_does_not_crash(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """If rollback itself fails, the job still gets marked failed (no crash)."""
        host1, host2 = multiple_hosts[0], multiple_hosts[1]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        _make_node(test_db, lab, gui_id="n2", name="r2", host_id=host2.id)

        results = iter([
            {"status": "completed", "stdout": "OK"},
            RuntimeError("deploy failed"),
        ])

        async def mock_deploy(*args, **kwargs):
            r = next(results)
            if isinstance(r, Exception):
                raise r
            return r

        mock_rollback = AsyncMock(side_effect=RuntimeError("rollback also failed"))

        patches = _standard_deploy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                    with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                        with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                            with patch(f"{MODULE}.agent_client.deploy_to_agent", new_callable=AsyncMock, side_effect=mock_deploy):
                                with patch(f"{MODULE}.agent_client.destroy_on_agent", mock_rollback):
                                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "rollback" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_all_deploys_fail_no_rollback_needed(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """If all deploy calls fail, no rollback is dispatched."""
        host1, host2 = multiple_hosts[0], multiple_hosts[1]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        _make_node(test_db, lab, gui_id="n2", name="r2", host_id=host2.id)

        mock_deploy = AsyncMock(side_effect=RuntimeError("total failure"))
        mock_rollback = AsyncMock()

        patches = _standard_deploy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                    with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                        with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                            with patch(f"{MODULE}.agent_client.deploy_to_agent", mock_deploy):
                                with patch(f"{MODULE}.agent_client.destroy_on_agent", mock_rollback):
                                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        # No hosts succeeded, so no rollback calls
        mock_rollback.assert_not_awaited()
        assert "no hosts to rollback" in job.log_path.lower()


class TestMultihostDeployCapacity:
    """Tests for resource capacity checks during deploy."""

    @pytest.mark.asyncio
    async def test_capacity_failure_blocks_deploy(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """When check_multihost_capacity returns fits=False, job fails."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        cap_result = MagicMock()
        cap_result.fits = False

        with patch(f"{MODULE}.get_session", _mock_get_session(test_db)):
            with patch(f"{MODULE}.settings") as mock_settings:
                mock_settings.resource_validation_enabled = True
                mock_settings.image_sync_enabled = False
                with patch(f"{MODULE}._dispatch_webhook", AsyncMock()):
                    with patch(f"{MODULE}._broadcast_job_progress", AsyncMock()):
                        with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                            with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                                with patch("app.services.resource_capacity.check_multihost_capacity", return_value={host1.id: cap_result}):
                                    with patch("app.services.resource_capacity.format_capacity_error", return_value="Not enough RAM"):
                                        await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "Not enough RAM" in job.log_path

    @pytest.mark.asyncio
    async def test_capacity_warnings_logged_but_deploy_continues(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Capacity warnings are appended to log without blocking deploy."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        cap_result = MagicMock()
        cap_result.fits = True

        patches = _standard_deploy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}.settings") as mock_settings:
                mock_settings.resource_validation_enabled = True
                mock_settings.image_sync_enabled = False
                with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                    with patch(f"{MODULE}._capture_node_ips", patches[f"{MODULE}._capture_node_ips"]):
                        with patch(f"{MODULE}.emit_deploy_finished", patches[f"{MODULE}.emit_deploy_finished"]):
                            with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                                with patch(f"{MODULE}._update_node_placements", patches[f"{MODULE}._update_node_placements"]):
                                    with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                                        with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                                            with patch(f"{MODULE}.agent_client.deploy_to_agent", new_callable=AsyncMock, return_value={"status": "completed", "stdout": "OK"}):
                                                with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                                    with patch("app.services.resource_capacity.check_multihost_capacity", return_value={host1.id: cap_result}):
                                                        with patch("app.services.resource_capacity.format_capacity_warnings", return_value=["CPU near limit on agent-1"]):
                                                            await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "completed"
        assert "WARNING" in job.log_path
        assert "CPU near limit" in job.log_path

    @pytest.mark.asyncio
    async def test_capacity_check_skipped_when_disabled(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """When resource_validation_enabled=False, no capacity check runs."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        patches = _standard_deploy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}.settings") as mock_settings:
                mock_settings.resource_validation_enabled = False
                with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                    with patch(f"{MODULE}._capture_node_ips", patches[f"{MODULE}._capture_node_ips"]):
                        with patch(f"{MODULE}.emit_deploy_finished", patches[f"{MODULE}.emit_deploy_finished"]):
                            with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                                with patch(f"{MODULE}._update_node_placements", patches[f"{MODULE}._update_node_placements"]):
                                    with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                                        with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                                            with patch(f"{MODULE}.agent_client.deploy_to_agent", new_callable=AsyncMock, return_value={"status": "completed", "stdout": "OK"}):
                                                with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                                    with patch("app.services.resource_capacity.check_multihost_capacity") as mock_cap:
                                                        await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "completed"
        mock_cap.assert_not_called()


class TestMultihostDeployLinks:
    """Tests for link creation during deploy."""

    @pytest.mark.asyncio
    async def test_link_failures_fail_job(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """If create_deployment_links reports failures, job is marked failed."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        patches = _standard_deploy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                    with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                        with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                            with patch(f"{MODULE}.agent_client.deploy_to_agent", new_callable=AsyncMock, return_value={"status": "completed", "stdout": "OK"}):
                                with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(3, 2)):
                                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "link" in job.log_path.lower()
        test_db.refresh(lab)
        assert lab.state == "error"

    @pytest.mark.asyncio
    async def test_zero_links_no_issue(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Lab with no links (0 ok, 0 failed) still succeeds."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        patches = _standard_deploy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}._capture_node_ips", patches[f"{MODULE}._capture_node_ips"]):
                    with patch(f"{MODULE}.emit_deploy_finished", patches[f"{MODULE}.emit_deploy_finished"]):
                        with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                            with patch(f"{MODULE}._update_node_placements", patches[f"{MODULE}._update_node_placements"]):
                                with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                                    with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                                        with patch(f"{MODULE}.agent_client.deploy_to_agent", new_callable=AsyncMock, return_value={"status": "completed", "stdout": "OK"}):
                                            with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                                await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "completed"


class TestMultihostDeployExceptions:
    """Tests for unexpected exception handling during deploy."""

    @pytest.mark.asyncio
    async def test_unexpected_exception_marks_job_failed(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """An unexpected exception in the try block marks the job failed."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        with patch(f"{MODULE}.get_session", _mock_get_session(test_db)):
            with patch(f"{MODULE}._dispatch_webhook", AsyncMock()):
                with patch(f"{MODULE}._broadcast_job_progress", AsyncMock()):
                    with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                        with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                            # Force an unexpected error by making deploy_to_agent raise inside gather
                            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                                mock_ts = MagicMock()
                                mock_ts.get_nodes.return_value = []
                                mock_ts.analyze_placements.side_effect = ValueError("unexpected boom")
                                mock_ts_cls.return_value = mock_ts
                                await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "unexpected" in job.log_path.lower() or "boom" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_deploy_webhook_dispatched_on_success(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """On successful deploy, lab.deploy_complete webhook is dispatched."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        mock_webhook = AsyncMock()
        patches = _standard_deploy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", mock_webhook):
                with patch(f"{MODULE}._capture_node_ips", patches[f"{MODULE}._capture_node_ips"]):
                    with patch(f"{MODULE}.emit_deploy_finished", patches[f"{MODULE}.emit_deploy_finished"]):
                        with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                            with patch(f"{MODULE}._update_node_placements", patches[f"{MODULE}._update_node_placements"]):
                                with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                                    with patch(f"{MODULE}.agent_client.get_lab_status_from_agent", new_callable=AsyncMock, return_value={"nodes": []}):
                                        with patch(f"{MODULE}.agent_client.deploy_to_agent", new_callable=AsyncMock, return_value={"status": "completed", "stdout": "OK"}):
                                            with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                                await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "completed"
        mock_webhook.assert_awaited()
        event_types = [c.args[0] for c in mock_webhook.call_args_list]
        assert "lab.deploy_complete" in event_types


# ---------------------------------------------------------------------------
# Destroy Tests
# ---------------------------------------------------------------------------


class TestMultihostDestroyDispatch:
    """Tests for multi-host dispatch logic in run_multihost_destroy."""

    @pytest.mark.asyncio
    async def test_job_not_found_returns_early(self, test_db: Session, test_user):
        """If the job ID doesn't exist, the function returns early."""
        with patch(f"{MODULE}.get_session", _mock_get_session(test_db)):
            await run_multihost_destroy("nonexistent-job", "some-lab")

    @pytest.mark.asyncio
    async def test_lab_not_found_fails_job(self, test_db: Session, test_user):
        """If the lab doesn't exist, the job is marked failed."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        test_db.delete(lab)
        test_db.commit()

        with patch(f"{MODULE}.get_session", _mock_get_session(test_db)):
            await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "not found" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_successful_destroy_sets_stopped(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Successful destroy on all hosts sets lab to stopped."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        patches = _standard_destroy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}.emit_destroy_finished", patches[f"{MODULE}.emit_destroy_finished"]):
                    with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                        with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                            with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                with patch(f"{MODULE}.agent_client.destroy_on_agent", new_callable=AsyncMock, return_value={"status": "completed"}):
                                    await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        test_db.refresh(lab)
        assert job.status == "completed"
        assert lab.state == "stopped"

    @pytest.mark.asyncio
    async def test_destroy_dispatches_to_multiple_hosts(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Destroy calls destroy_on_agent for each host with nodes."""
        host1, host2 = multiple_hosts[0], multiple_hosts[1]
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        _make_node(test_db, lab, gui_id="n2", name="r2", host_id=host2.id)

        mock_destroy = AsyncMock(return_value={"status": "completed"})

        patches = _standard_destroy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}.emit_destroy_finished", patches[f"{MODULE}.emit_destroy_finished"]):
                    with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                        with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                            with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                with patch(f"{MODULE}.agent_client.destroy_on_agent", mock_destroy):
                                    await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "completed"
        assert mock_destroy.await_count == 2


class TestMultihostDestroyPartialFailure:
    """Tests for partial failure scenarios during destroy."""

    @pytest.mark.asyncio
    async def test_offline_agent_produces_warnings(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Offline agent during destroy results in completed_with_warnings."""
        host1, _host2, host3 = multiple_hosts[0], multiple_hosts[1], multiple_hosts[2]
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        _make_node(test_db, lab, gui_id="n2", name="r2", host_id=host3.id)

        patches = _standard_destroy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}.emit_job_failed", patches[f"{MODULE}.emit_job_failed"]):
                    with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                        with patch(f"{MODULE}.agent_client.is_agent_online", side_effect=lambda h: h.id == host1.id):
                            with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                with patch(f"{MODULE}.agent_client.destroy_on_agent", new_callable=AsyncMock, return_value={"status": "completed"}):
                                    await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "completed_with_warnings"
        assert "offline" in job.log_path.lower() or "unreachable" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_no_reachable_agents_fails_job(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """If no agents are reachable at all, destroy fails."""
        host3 = multiple_hosts[2]  # offline
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host3.id)

        with patch(f"{MODULE}.get_session", _mock_get_session(test_db)):
            with patch(f"{MODULE}._broadcast_job_progress", AsyncMock()):
                with patch(f"{MODULE}.agent_client.is_agent_online", return_value=False):
                    await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "no online agents" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_destroy_exception_on_agent_produces_warnings(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Exception from destroy_on_agent results in completed_with_warnings."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        patches = _standard_destroy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}.emit_job_failed", patches[f"{MODULE}.emit_job_failed"]):
                    with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                        with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                            with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                with patch(f"{MODULE}.agent_client.destroy_on_agent", new_callable=AsyncMock, side_effect=RuntimeError("agent down")):
                                    await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "completed_with_warnings"
        assert "failed" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_tunnel_teardown_failure_produces_warnings(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Tunnel teardown failures result in completed_with_warnings."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        patches = _standard_destroy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}.emit_job_failed", patches[f"{MODULE}.emit_job_failed"]):
                    with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                        with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                            # 2 tunnel teardown failures
                            with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(1, 2)):
                                with patch(f"{MODULE}.agent_client.destroy_on_agent", new_callable=AsyncMock, return_value={"status": "completed"}):
                                    await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "completed_with_warnings"
        assert "overlay" in job.log_path.lower() or "tunnel" in job.log_path.lower()


class TestMultihostDestroyLinkStateCleanup:
    """Tests for LinkState cleanup behavior during destroy."""

    @pytest.mark.asyncio
    async def test_link_states_deleted_on_full_success(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """On full success, remaining LinkState rows are deleted."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        _make_link_state(test_db, lab)

        patches = _standard_destroy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}.emit_destroy_finished", patches[f"{MODULE}.emit_destroy_finished"]):
                    with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                        with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                            with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                with patch(f"{MODULE}.agent_client.destroy_on_agent", new_callable=AsyncMock, return_value={"status": "completed"}):
                                    await run_multihost_destroy(job.id, lab.id)

        remaining = (
            test_db.query(models.LinkState)
            .filter(models.LinkState.lab_id == lab.id)
            .first()
        )
        assert remaining is None

    @pytest.mark.asyncio
    async def test_link_states_preserved_on_partial_failure(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """On partial failure, LinkState rows are updated (not deleted)."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        ls = _make_link_state(test_db, lab, desired="up", actual="up")
        ls_id = ls.id

        patches = _standard_destroy_patches(test_db)
        with patch(f"{MODULE}.get_session", patches[f"{MODULE}.get_session"]):
            with patch(f"{MODULE}._dispatch_webhook", patches[f"{MODULE}._dispatch_webhook"]):
                with patch(f"{MODULE}.emit_job_failed", patches[f"{MODULE}.emit_job_failed"]):
                    with patch(f"{MODULE}._broadcast_job_progress", patches[f"{MODULE}._broadcast_job_progress"]):
                        with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                            # Tunnel failure triggers partial
                            with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(0, 1)):
                                with patch(f"{MODULE}.agent_client.destroy_on_agent", new_callable=AsyncMock, return_value={"status": "completed"}):
                                    await run_multihost_destroy(job.id, lab.id)

        remaining = test_db.get(models.LinkState, ls_id)
        assert remaining is not None
        assert remaining.desired_state == "deleted"
        assert remaining.actual_state == "error"
        assert remaining.error_message is not None


class TestMultihostDestroyWebhooks:
    """Tests for webhook dispatching during destroy."""

    @pytest.mark.asyncio
    async def test_success_dispatches_destroy_complete(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Full success dispatches lab.destroy_complete webhook."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        mock_webhook = AsyncMock()
        with patch(f"{MODULE}.get_session", _mock_get_session(test_db)):
            with patch(f"{MODULE}._dispatch_webhook", mock_webhook):
                with patch(f"{MODULE}.emit_destroy_finished", AsyncMock()):
                    with patch(f"{MODULE}._broadcast_job_progress", AsyncMock()):
                        with patch(f"{MODULE}.agent_client.is_agent_online", return_value=True):
                            with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                with patch(f"{MODULE}.agent_client.destroy_on_agent", new_callable=AsyncMock, return_value={"status": "completed"}):
                                    await run_multihost_destroy(job.id, lab.id)

        event_types = [c.args[0] for c in mock_webhook.call_args_list]
        assert "lab.destroy_complete" in event_types

    @pytest.mark.asyncio
    async def test_partial_failure_dispatches_job_failed(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """Partial failure dispatches job.failed webhook."""
        host1, _host2, host3 = multiple_hosts[0], multiple_hosts[1], multiple_hosts[2]
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)
        _make_node(test_db, lab, gui_id="n2", name="r2", host_id=host3.id)

        mock_webhook = AsyncMock()
        with patch(f"{MODULE}.get_session", _mock_get_session(test_db)):
            with patch(f"{MODULE}._dispatch_webhook", mock_webhook):
                with patch(f"{MODULE}.emit_job_failed", AsyncMock()):
                    with patch(f"{MODULE}._broadcast_job_progress", AsyncMock()):
                        with patch(f"{MODULE}.agent_client.is_agent_online", side_effect=lambda h: h.id == host1.id):
                            with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                                with patch(f"{MODULE}.agent_client.destroy_on_agent", new_callable=AsyncMock, return_value={"status": "completed"}):
                                    await run_multihost_destroy(job.id, lab.id)

        event_types = [c.args[0] for c in mock_webhook.call_args_list]
        assert "job.failed" in event_types


class TestMultihostDestroyExceptions:
    """Tests for unexpected exception handling during destroy."""

    @pytest.mark.asyncio
    async def test_unexpected_exception_marks_job_failed(
        self, test_db: Session, test_user, multiple_hosts
    ):
        """An unexpected exception marks the destroy job as failed."""
        host1 = multiple_hosts[0]
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id=host1.id)

        with patch(f"{MODULE}.get_session", _mock_get_session(test_db)):
            with patch(f"{MODULE}._broadcast_job_progress", AsyncMock()):
                with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.analyze_placements.side_effect = ValueError("kaboom")
                    mock_ts_cls.return_value = mock_ts
                    await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "kaboom" in job.log_path.lower() or "unexpected" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_missing_host_record_during_destroy(
        self, test_db: Session, test_user
    ):
        """Node assigned to a non-existent host record during destroy."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        _make_node(test_db, lab, gui_id="n1", name="r1", host_id="ghost-agent")

        with patch(f"{MODULE}.get_session", _mock_get_session(test_db)):
            with patch(f"{MODULE}._broadcast_job_progress", AsyncMock()):
                await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        # No reachable agents => fails
        assert job.status == "failed"
        assert "no online agents" in job.log_path.lower()
