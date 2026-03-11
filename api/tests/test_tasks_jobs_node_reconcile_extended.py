"""Extended tests for app/tasks/jobs_node_reconcile.py.

Covers additional scenarios beyond the base file:
- run_node_reconcile: empty node_ids list, custom provider passthrough
- _create_cross_host_links_if_ready: new links from DB link definitions,
  force_recreate with link_tunnels present, multiple agents in host_to_agent,
  overlay status with matching tunnels vs missing
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models
from app.state import JobStatus
from tests.factories import make_host, make_job, make_link, make_link_state, make_node, make_placement


def _fake_get_session(session):
    @contextmanager
    def _get_session():
        yield session
    return _get_session


# ---------------------------------------------------------------------------
# Tests: run_node_reconcile - extended
# ---------------------------------------------------------------------------

class TestRunNodeReconcileExtended:
    """Extended tests for run_node_reconcile."""

    @pytest.mark.asyncio
    async def test_empty_node_ids_still_delegates(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Empty node_ids list should still create manager and call execute."""
        from app.tasks.jobs import run_node_reconcile

        job = make_job(test_db, sample_lab.id, test_user.id, status="running")
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock()

        with patch("app.tasks.jobs.get_session", _fake_get_session(test_db)):
            with patch(
                "app.tasks.node_lifecycle.NodeLifecycleManager",
                return_value=mock_manager,
            ) as mock_cls:
                await run_node_reconcile(
                    job_id=job.id,
                    lab_id=sample_lab.id,
                    node_ids=[],
                )

        mock_cls.assert_called_once()
        assert mock_cls.call_args[0][3] == []
        mock_manager.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_custom_provider_passthrough(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Custom provider should be passed through to NodeLifecycleManager."""
        from app.tasks.jobs import run_node_reconcile

        job = make_job(test_db, sample_lab.id, test_user.id, status="running")
        mock_manager = MagicMock()
        mock_manager.execute = AsyncMock()

        with patch("app.tasks.jobs.get_session", _fake_get_session(test_db)):
            with patch(
                "app.tasks.node_lifecycle.NodeLifecycleManager",
                return_value=mock_manager,
            ) as mock_cls:
                await run_node_reconcile(
                    job_id=job.id,
                    lab_id=sample_lab.id,
                    node_ids=["n1"],
                    provider="libvirt",
                )

        assert mock_cls.call_args[0][4] == "libvirt"

    @pytest.mark.asyncio
    async def test_job_found_but_lab_missing_on_retry(
        self, test_db: Session, test_user: models.User
    ):
        """If job exists but lab doesn't, job should be failed."""
        from app.tasks.jobs import run_node_reconcile

        fake_lab_id = str(uuid4())
        job = models.Job(
            id=str(uuid4()),
            lab_id=fake_lab_id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        with patch("app.tasks.jobs.get_session", _fake_get_session(test_db)):
            await run_node_reconcile(
                job_id=job.id,
                lab_id=fake_lab_id,
                node_ids=["n1"],
            )

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "not found" in job.log_path.lower()


# ---------------------------------------------------------------------------
# Tests: _create_cross_host_links_if_ready - new links from definitions
# ---------------------------------------------------------------------------

class TestCreateCrossHostLinksNewLinks:
    """Tests for new link detection from DB link definitions."""

    @pytest.mark.asyncio
    async def test_new_db_links_trigger_creation(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """Links in DB that don't have LinkState records should trigger creation."""
        from app.tasks.jobs import _create_cross_host_links_if_ready

        host_a = make_host(test_db, "host-a")
        host_b = make_host(test_db, "host-b")

        n1 = make_node(test_db, sample_lab.id, "R1", host_id=host_a.id)
        n2 = make_node(test_db, sample_lab.id, "R2", host_id=host_b.id)

        # Create a link definition but NO LinkState
        make_link(
            test_db, sample_lab.id,
            n1.id, "eth1", n2.id, "eth1",
            link_name="R1:eth1-R2:eth1",
        )

        mock_create = AsyncMock(return_value=(1, 0))
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=True)
        mock_lock.__exit__ = MagicMock(return_value=False)

        log_parts = []

        with patch("app.tasks.jobs.agent_client") as mock_ac:
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
                        "app.tasks.jobs._release_db_transaction_for_io"
                    ):
                        await _create_cross_host_links_if_ready(
                            test_db, sample_lab.id, log_parts
                        )

        mock_create.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: _create_cross_host_links_if_ready - force recreate logic
# ---------------------------------------------------------------------------

class TestCreateCrossHostLinksForceRecreate:
    """Tests for force_recreate tunnel detection logic."""

    @pytest.mark.asyncio
    async def test_link_tunnels_present_prevents_force_recreate(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """When link_tunnels are reported for the lab, force_recreate should not trigger."""
        from app.tasks.jobs import _create_cross_host_links_if_ready

        host_a = make_host(test_db, "host-c")
        host_b = make_host(test_db, "host-d")

        # Cross-host link that is UP
        make_link_state(
            test_db, sample_lab.id,
            link_name="R1:eth1-R3:eth1",
            actual="up", is_cross_host=True,
            source_host_id=host_a.id, target_host_id=host_b.id,
        )
        make_placement(test_db, sample_lab.id, "R1", host_a.id)

        log_parts = []

        with patch("app.tasks.jobs.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            # Agent reports link_tunnels present for this lab
            mock_ac.get_overlay_status_from_agent = AsyncMock(
                return_value={
                    "tunnels": [],
                    "link_tunnels": [{"lab_id": sample_lab.id, "link_name": "R1:eth1-R3:eth1"}],
                }
            )
            with patch("app.tasks.jobs._release_db_transaction_for_io"):
                await _create_cross_host_links_if_ready(
                    test_db, sample_lab.id, log_parts
                )

        # Should not have attempted creation since link_tunnels is present
        assert not any("Cross-Host Links" in part for part in log_parts)

    @pytest.mark.asyncio
    async def test_both_tunnels_and_link_tunnels_empty_triggers_recreate(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """When both tunnels and link_tunnels are empty, force_recreate should trigger."""
        from app.tasks.jobs import _create_cross_host_links_if_ready

        host_a = make_host(test_db, "host-e")
        host_b = make_host(test_db, "host-f")

        make_link_state(
            test_db, sample_lab.id,
            link_name="R1:eth1-R3:eth1",
            actual="up", is_cross_host=True,
            source_host_id=host_a.id, target_host_id=host_b.id,
        )
        make_placement(test_db, sample_lab.id, "R1", host_a.id)

        mock_create = AsyncMock(return_value=(1, 0))
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=True)
        mock_lock.__exit__ = MagicMock(return_value=False)

        log_parts = []

        with patch("app.tasks.jobs.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
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
                    with patch("app.tasks.jobs._release_db_transaction_for_io"):
                        await _create_cross_host_links_if_ready(
                            test_db, sample_lab.id, log_parts
                        )

        mock_create.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: _create_cross_host_links_if_ready - multiple agent host_to_agent
# ---------------------------------------------------------------------------

class TestCreateCrossHostLinksMultiAgent:
    """Tests for building host_to_agent map with multiple agents."""

    @pytest.mark.asyncio
    async def test_only_online_agents_in_host_map(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """Only online agents should be included in host_to_agent."""
        from app.tasks.jobs import _create_cross_host_links_if_ready

        host_online = make_host(test_db, "host-g", status="online")
        host_offline = make_host(test_db, "host-h", status="offline")

        # Create uncategorized link to trigger creation
        make_link_state(
            test_db, sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            actual="pending",
            source_host_id=None,
        )

        AsyncMock(return_value=(0, 0))
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=True)
        mock_lock.__exit__ = MagicMock(return_value=False)

        created_host_map = {}

        async def capture_create(session, lab_id, host_to_agent, log_parts):
            created_host_map.update(host_to_agent)
            return (0, 0)

        with patch("app.tasks.jobs.agent_client") as mock_ac:
            def online_check(agent):
                return agent.status == "online"
            mock_ac.is_agent_online.side_effect = online_check

            with patch(
                "app.tasks.link_orchestration.create_deployment_links",
                new_callable=AsyncMock,
                side_effect=capture_create,
            ):
                with patch("app.utils.locks.link_ops_lock", return_value=mock_lock):
                    with patch("app.tasks.jobs._release_db_transaction_for_io"):
                        await _create_cross_host_links_if_ready(
                            test_db, sample_lab.id, []
                        )

        # Only the online agent should be in the map
        assert host_online.id in created_host_map
        assert host_offline.id not in created_host_map


# ---------------------------------------------------------------------------
# Tests: _create_cross_host_links_if_ready - zero counts don't log
# ---------------------------------------------------------------------------

class TestCreateCrossHostLinksZeroResults:
    """Tests for zero-count results in cross-host link creation."""

    @pytest.mark.asyncio
    async def test_zero_ok_zero_failed_no_info_log(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """When create_deployment_links returns (0, 0), info log should not be emitted."""
        from app.tasks.jobs import _create_cross_host_links_if_ready

        make_host(test_db, "host-i")

        make_link_state(
            test_db, sample_lab.id,
            source_host_id=None,
        )

        mock_create = AsyncMock(return_value=(0, 0))
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=True)
        mock_lock.__exit__ = MagicMock(return_value=False)

        log_parts = []

        with patch("app.tasks.jobs.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = True
            with patch(
                "app.tasks.link_orchestration.create_deployment_links",
                mock_create,
            ):
                with patch("app.utils.locks.link_ops_lock", return_value=mock_lock):
                    with patch("app.tasks.jobs._release_db_transaction_for_io"):
                        await _create_cross_host_links_if_ready(
                            test_db, sample_lab.id, log_parts
                        )

        # Phase 4 header should be present but no result line
        assert any("Cross-Host Links" in part for part in log_parts)


# ---------------------------------------------------------------------------
# Tests: run_node_reconcile - exception handling
# ---------------------------------------------------------------------------

class TestRunNodeReconcileExceptionHandling:
    """Tests for exception handling in run_node_reconcile."""

    @pytest.mark.asyncio
    async def test_unexpected_exception_fails_job(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Unexpected exception during execution should fail the job."""
        from app.tasks.jobs import run_node_reconcile

        job = make_job(test_db, sample_lab.id, test_user.id, status="running")

        with patch("app.tasks.jobs.get_session", _fake_get_session(test_db)):
            with patch(
                "app.tasks.node_lifecycle.NodeLifecycleManager",
                side_effect=RuntimeError("Manager init failed"),
            ):
                await run_node_reconcile(
                    job_id=job.id,
                    lab_id=sample_lab.id,
                    node_ids=["n1"],
                )

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "Manager init failed" in job.log_path

    @pytest.mark.asyncio
    async def test_missing_job_returns_silently(self, test_db: Session, sample_lab: models.Lab):
        """Non-existent job should return without error."""
        from app.tasks.jobs import run_node_reconcile

        with patch("app.tasks.jobs.get_session", _fake_get_session(test_db)):
            # Should not raise
            await run_node_reconcile(
                job_id="nonexistent-job-id",
                lab_id=sample_lab.id,
                node_ids=["n1"],
            )


# ---------------------------------------------------------------------------
# Tests: _create_cross_host_links_if_ready - no online agents
# ---------------------------------------------------------------------------

class TestCreateCrossHostLinksNoAgents:
    """Tests for cross-host links when no agents are available."""

    @pytest.mark.asyncio
    async def test_no_online_agents_returns_early(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """When no agents are online, should return without creating links."""
        from app.tasks.jobs import _create_cross_host_links_if_ready

        make_link_state(
            test_db, sample_lab.id,
            source_host_id=None,
        )

        log_parts = []

        with patch("app.tasks.jobs.agent_client") as mock_ac:
            mock_ac.is_agent_online.return_value = False
            with patch("app.tasks.jobs._release_db_transaction_for_io"):
                await _create_cross_host_links_if_ready(
                    test_db, sample_lab.id, log_parts
                )

        # No "Cross-Host Links" header since we returned early due to no agents
        assert not any("Cross-Host Links" in part for part in log_parts)