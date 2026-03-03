"""Tests for app/tasks/jobs_node_reconcile.py - Node reconciliation job and cross-host links."""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models
from app.state import JobStatus


def _fake_get_session(session):
    """Create a fake get_session context manager that yields the test session."""
    @contextmanager
    def _get_session():
        yield session
    return _get_session


def _make_job(
    test_db: Session,
    lab_id: str,
    user_id: str,
    *,
    status: str = "queued",
    action: str = "sync:lab",
) -> models.Job:
    """Helper to create a Job."""
    job = models.Job(
        id=str(uuid4()),
        lab_id=lab_id,
        user_id=user_id,
        action=action,
        status=status,
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


def _make_node_state(
    test_db: Session,
    lab_id: str,
    node_id: str,
    node_name: str,
    *,
    desired_state: str = "running",
    actual_state: str = "running",
) -> models.NodeState:
    """Helper to create a NodeState."""
    ns = models.NodeState(
        id=str(uuid4()),
        lab_id=lab_id,
        node_id=node_id,
        node_name=node_name,
        desired_state=desired_state,
        actual_state=actual_state,
    )
    test_db.add(ns)
    test_db.commit()
    test_db.refresh(ns)
    return ns


class TestRunNodeReconcile:
    """Tests for the run_node_reconcile async function."""

    @pytest.mark.asyncio
    async def test_job_not_found(self, test_db: Session):
        """Should log error and return early when job ID does not exist."""
        from app.tasks.jobs_node_reconcile import run_node_reconcile

        with patch("app.tasks.jobs_node_reconcile.get_session", _fake_get_session(test_db)):
            await run_node_reconcile(
                job_id="nonexistent-job",
                lab_id="nonexistent-lab",
                node_ids=["n1"],
            )
            # Should not raise; just logs and returns

    @pytest.mark.asyncio
    async def test_lab_not_found_marks_job_failed(
        self, test_db: Session, test_user: models.User
    ):
        """When lab is not found, job should be marked failed."""
        from app.tasks.jobs_node_reconcile import run_node_reconcile

        # Create a job referencing a nonexistent lab
        fake_lab_id = str(uuid4())
        job = models.Job(
            id=str(uuid4()),
            lab_id=fake_lab_id,
            user_id=test_user.id,
            action="sync:lab",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with patch("app.tasks.jobs_node_reconcile.get_session", _fake_get_session(test_db)):
            await run_node_reconcile(
                job_id=job.id,
                lab_id=fake_lab_id,
                node_ids=["n1"],
            )

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert job.completed_at is not None
        assert "not found" in job.log_path

    @pytest.mark.asyncio
    async def test_delegates_to_node_lifecycle_manager(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should instantiate NodeLifecycleManager and call execute()."""
        from app.tasks.jobs_node_reconcile import run_node_reconcile

        job = _make_job(test_db, sample_lab.id, test_user.id, status="running")
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock()

        with patch("app.tasks.jobs_node_reconcile.get_session", _fake_get_session(test_db)):
            with patch(
                "app.tasks.node_lifecycle.NodeLifecycleManager",
                return_value=mock_manager,
            ) as mock_cls:
                await run_node_reconcile(
                    job_id=job.id,
                    lab_id=sample_lab.id,
                    node_ids=["n1", "n2"],
                    provider="docker",
                )

        mock_cls.assert_called_once()
        call_args = mock_cls.call_args
        assert call_args[0][1] == sample_lab  # lab arg
        assert call_args[0][2] == job  # job arg
        assert call_args[0][3] == ["n1", "n2"]  # node_ids
        assert call_args[0][4] == "docker"  # provider
        mock_manager.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_manager_exception_marks_job_failed(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """When NodeLifecycleManager.execute() raises, job should be marked failed."""
        from app.tasks.jobs_node_reconcile import run_node_reconcile

        job = _make_job(test_db, sample_lab.id, test_user.id, status="running")
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock(side_effect=RuntimeError("Agent timeout"))

        with patch("app.tasks.jobs_node_reconcile.get_session", _fake_get_session(test_db)):
            with patch(
                "app.tasks.node_lifecycle.NodeLifecycleManager",
                return_value=mock_manager,
            ):
                await run_node_reconcile(
                    job_id=job.id,
                    lab_id=sample_lab.id,
                    node_ids=["n1"],
                )

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert job.completed_at is not None
        assert "Agent timeout" in job.log_path

    @pytest.mark.asyncio
    async def test_inner_exception_during_failure_handling(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """If both execute() and error-handler DB update fail, should not raise."""
        from app.tasks.jobs_node_reconcile import run_node_reconcile

        job = _make_job(test_db, sample_lab.id, test_user.id, status="running")

        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock(side_effect=RuntimeError("Agent down"))

        # Make the error-handler's session.get() also fail by patching rollback
        # to raise on second invocation
        call_count = {"rollback": 0}
        original_rollback = test_db.rollback

        def failing_rollback():
            call_count["rollback"] += 1
            if call_count["rollback"] >= 2:
                raise RuntimeError("DB connection lost")
            return original_rollback()

        with patch("app.tasks.jobs_node_reconcile.get_session", _fake_get_session(test_db)):
            with patch(
                "app.tasks.node_lifecycle.NodeLifecycleManager",
                return_value=mock_manager,
            ):
                with patch.object(test_db, "rollback", side_effect=failing_rollback):
                    # Should not raise despite double failure
                    await run_node_reconcile(
                        job_id=job.id,
                        lab_id=sample_lab.id,
                        node_ids=["n1"],
                    )

    @pytest.mark.asyncio
    async def test_default_provider_is_docker(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Provider parameter should default to 'docker'."""
        from app.tasks.jobs_node_reconcile import run_node_reconcile

        job = _make_job(test_db, sample_lab.id, test_user.id, status="running")
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock()

        with patch("app.tasks.jobs_node_reconcile.get_session", _fake_get_session(test_db)):
            with patch(
                "app.tasks.node_lifecycle.NodeLifecycleManager",
                return_value=mock_manager,
            ) as mock_cls:
                await run_node_reconcile(
                    job_id=job.id,
                    lab_id=sample_lab.id,
                    node_ids=["n1"],
                )

        call_args = mock_cls.call_args
        assert call_args[0][4] == "docker"

    @pytest.mark.asyncio
    async def test_reconcile_with_multiple_node_ids(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should pass all node IDs through to NodeLifecycleManager."""
        from app.tasks.jobs_node_reconcile import run_node_reconcile

        job = _make_job(test_db, sample_lab.id, test_user.id, status="running")
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock()

        node_ids = ["n1", "n2", "n3", "n4"]

        with patch("app.tasks.jobs_node_reconcile.get_session", _fake_get_session(test_db)):
            with patch(
                "app.tasks.node_lifecycle.NodeLifecycleManager",
                return_value=mock_manager,
            ) as mock_cls:
                await run_node_reconcile(
                    job_id=job.id,
                    lab_id=sample_lab.id,
                    node_ids=node_ids,
                    provider="docker",
                )

        assert mock_cls.call_args[0][3] == node_ids


class TestCreateCrossHostLinksIfReady:
    """Tests for _create_cross_host_links_if_ready."""

    @pytest.mark.asyncio
    async def test_no_pending_links_returns_early(self, test_db: Session, sample_lab: models.Lab):
        """Should return early when no cross-host links need creation."""
        from app.tasks.jobs_node_reconcile import _create_cross_host_links_if_ready

        log_parts = []
        with patch("app.tasks.jobs_node_reconcile.agent_client"):
            await _create_cross_host_links_if_ready(test_db, sample_lab.id, log_parts)

        # Should not add any log entries since it returned early
        assert not any("Cross-Host Links" in part for part in log_parts)

    @pytest.mark.asyncio
    async def test_pending_cross_host_triggers_creation(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        sample_cross_host_link_state: models.LinkState,
        multiple_hosts: list[models.Host],
    ):
        """Pending cross-host links should trigger create_deployment_links."""
        from app.tasks.jobs_node_reconcile import _create_cross_host_links_if_ready

        mock_create = AsyncMock(return_value=(1, 0))
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=True)
        mock_lock.__exit__ = MagicMock(return_value=False)

        log_parts = []

        with patch("app.tasks.jobs_node_reconcile.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            with patch(
                "app.tasks.link_orchestration.create_deployment_links",
                mock_create,
            ):
                with patch(
                    "app.utils.locks.link_ops_lock",
                    return_value=mock_lock,
                ):
                    with patch(
                        "app.tasks.jobs_node_reconcile._release_db_transaction_for_io"
                    ):
                        await _create_cross_host_links_if_ready(
                            test_db, sample_lab.id, log_parts
                        )

        mock_create.assert_awaited_once()
        assert any("Cross-Host Links" in part for part in log_parts)

    @pytest.mark.asyncio
    async def test_no_online_agents_returns_early(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        sample_cross_host_link_state: models.LinkState,
        multiple_hosts: list[models.Host],
    ):
        """Should return early when no agents are online."""
        from app.tasks.jobs_node_reconcile import _create_cross_host_links_if_ready

        log_parts = []

        with patch("app.tasks.jobs_node_reconcile.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = False
            await _create_cross_host_links_if_ready(
                test_db, sample_lab.id, log_parts
            )

        # Should not have attempted creation
        assert not any("Cross-Host Links" in part for part in log_parts)

    @pytest.mark.asyncio
    async def test_lock_not_acquired_skips(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        sample_cross_host_link_state: models.LinkState,
        multiple_hosts: list[models.Host],
    ):
        """When link_ops_lock cannot be acquired, creation should be skipped."""
        from app.tasks.jobs_node_reconcile import _create_cross_host_links_if_ready

        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=False)
        mock_lock.__exit__ = MagicMock(return_value=False)

        log_parts = []

        with patch("app.tasks.jobs_node_reconcile.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            with patch(
                "app.utils.locks.link_ops_lock",
                return_value=mock_lock,
            ):
                await _create_cross_host_links_if_ready(
                    test_db, sample_lab.id, log_parts
                )

        # Should not have attempted to create links (returned after lock failure)
        assert not any("link creation:" in part.lower() for part in log_parts)

    @pytest.mark.asyncio
    async def test_uncategorized_links_trigger_creation(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """Links without host IDs (uncategorized) should trigger creation."""
        from app.tasks.jobs_node_reconcile import _create_cross_host_links_if_ready

        # Create a link_state with source_host_id=None (uncategorized)
        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R2",
            target_interface="eth1",
            desired_state="up",
            actual_state="pending",
            source_host_id=None,
        )
        test_db.add(link)
        test_db.commit()

        mock_create = AsyncMock(return_value=(1, 0))
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=True)
        mock_lock.__exit__ = MagicMock(return_value=False)

        log_parts = []

        with patch("app.tasks.jobs_node_reconcile.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            with patch(
                "app.tasks.link_orchestration.create_deployment_links",
                mock_create,
            ):
                with patch(
                    "app.utils.locks.link_ops_lock",
                    return_value=mock_lock,
                ):
                    with patch(
                        "app.tasks.jobs_node_reconcile._release_db_transaction_for_io"
                    ):
                        await _create_cross_host_links_if_ready(
                            test_db, sample_lab.id, log_parts
                        )

        mock_create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_creation_exception_logged(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        sample_cross_host_link_state: models.LinkState,
        multiple_hosts: list[models.Host],
    ):
        """Exceptions during link creation should be caught and appended to log_parts."""
        from app.tasks.jobs_node_reconcile import _create_cross_host_links_if_ready

        mock_create = AsyncMock(side_effect=RuntimeError("VXLAN tunnel error"))
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=True)
        mock_lock.__exit__ = MagicMock(return_value=False)

        log_parts = []

        with patch("app.tasks.jobs_node_reconcile.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            with patch(
                "app.tasks.link_orchestration.create_deployment_links",
                mock_create,
            ):
                with patch(
                    "app.utils.locks.link_ops_lock",
                    return_value=mock_lock,
                ):
                    with patch(
                        "app.tasks.jobs_node_reconcile._release_db_transaction_for_io"
                    ):
                        await _create_cross_host_links_if_ready(
                            test_db, sample_lab.id, log_parts
                        )

        # Error message should be recorded in log_parts
        assert any("failed" in part.lower() for part in log_parts)

    @pytest.mark.asyncio
    async def test_force_recreate_when_tunnels_missing(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        multiple_hosts: list[models.Host],
    ):
        """Cross-host links that are UP but have no tunnels on agent should trigger force recreate."""
        from app.tasks.jobs_node_reconcile import _create_cross_host_links_if_ready

        # Create a cross-host link that appears UP
        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R3:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R3",
            target_interface="eth1",
            desired_state="up",
            actual_state="up",
            is_cross_host=True,
            source_host_id=multiple_hosts[0].id,
            target_host_id=multiple_hosts[1].id,
        )
        test_db.add(link)
        # Create a node placement so the host lookup works
        placement = models.NodePlacement(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_name="R1",
            host_id=multiple_hosts[0].id,
        )
        test_db.add(placement)
        test_db.commit()

        mock_create = AsyncMock(return_value=(1, 0))
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=True)
        mock_lock.__exit__ = MagicMock(return_value=False)

        log_parts = []

        with patch("app.tasks.jobs_node_reconcile.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            # Agent reports no tunnels for this lab -> force_recreate
            mock_ac.get_overlay_status_from_agent = AsyncMock(
                return_value={"tunnels": [], "link_tunnels": []}
            )
            with patch(
                "app.tasks.link_orchestration.create_deployment_links",
                mock_create,
            ):
                with patch(
                    "app.utils.locks.link_ops_lock",
                    return_value=mock_lock,
                ):
                    with patch(
                        "app.tasks.jobs_node_reconcile._release_db_transaction_for_io"
                    ):
                        await _create_cross_host_links_if_ready(
                            test_db, sample_lab.id, log_parts
                        )

        mock_create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_links_up_and_tunnels_present_skips(
        self,
        test_db: Session,
        sample_lab: models.Lab,
        multiple_hosts: list[models.Host],
    ):
        """Cross-host links all UP with tunnels present should skip creation."""
        from app.tasks.jobs_node_reconcile import _create_cross_host_links_if_ready

        # Create a cross-host link that is UP
        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R3:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R3",
            target_interface="eth1",
            desired_state="up",
            actual_state="up",
            is_cross_host=True,
            source_host_id=multiple_hosts[0].id,
            target_host_id=multiple_hosts[1].id,
        )
        test_db.add(link)
        placement = models.NodePlacement(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_name="R1",
            host_id=multiple_hosts[0].id,
        )
        test_db.add(placement)
        test_db.commit()

        log_parts = []

        with patch("app.tasks.jobs_node_reconcile.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            # Agent reports tunnels ARE present for this lab
            mock_ac.get_overlay_status_from_agent = AsyncMock(
                return_value={
                    "tunnels": [{"lab_id": sample_lab.id, "vni": 100}],
                    "link_tunnels": [],
                }
            )
            with patch(
                "app.tasks.jobs_node_reconcile._release_db_transaction_for_io"
            ):
                await _create_cross_host_links_if_ready(
                    test_db, sample_lab.id, log_parts
                )

        # Should have returned without attempting creation
        assert not any("Cross-Host Links" in part for part in log_parts)
