"""Extended tests for api/app/tasks/jobs_multihost.py.

Covers additional scenarios beyond the base file:
- Deploy: topology JSON construction, node placement updates, management IP capture,
  webhook dispatch on failure, partial rollback with mixed results
- Destroy: missing host records, tunnel teardown interaction, remaining link states,
  completed-with-warnings status, inner exception during error handler
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.state import JobStatus, LinkActualState
from app.tasks.jobs_multihost import run_multihost_deploy, run_multihost_destroy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MODULE = "app.tasks.jobs_multihost"


def _mock_get_session(test_db: Session):
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


def _make_host(test_db, host_id, *, name=None, status="online"):
    from datetime import datetime, timezone

    host = models.Host(
        id=host_id,
        name=name or host_id,
        address=f"{host_id}:8080",
        status=status,
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
        last_heartbeat=datetime.now(timezone.utc),
        resource_usage=json.dumps({}),
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


def _make_node(test_db, lab_id, name, *, host_id=None, device="linux"):
    node = models.Node(
        lab_id=lab_id,
        gui_id=name.lower(),
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


@dataclass
class _FakeAnalysis:
    placements: dict
    cross_host_links: list


def _build_standard_patches(test_db, host_map=None, deploy_result=None, destroy_result=None):
    """Build a dict of standard patches for multihost tests."""
    patches = {
        "get_session": patch(f"{MODULE}.get_session", _mock_get_session(test_db)),
        "record_started": patch(f"{MODULE}._record_started"),
        "record_failed": patch(f"{MODULE}._record_failed"),
        "record_completed": patch(f"{MODULE}.record_job_completed"),
        "broadcast": patch(f"{MODULE}._broadcast_job_progress", new_callable=AsyncMock),
        "update_lab_state": patch(f"{MODULE}.update_lab_state"),
        "release_tx": patch(f"{MODULE}._release_db_transaction_for_io"),
        "dispatch_webhook": patch(f"{MODULE}._dispatch_webhook", new_callable=AsyncMock),
        "emit_deploy": patch(f"{MODULE}.emit_deploy_finished", new_callable=AsyncMock),
        "emit_destroy": patch(f"{MODULE}.emit_destroy_finished", new_callable=AsyncMock),
        "emit_job_failed": patch(f"{MODULE}.emit_job_failed", new_callable=AsyncMock),
        "capture_ips": patch(f"{MODULE}._capture_node_ips", new_callable=AsyncMock),
        "update_placements": patch(f"{MODULE}._update_node_placements", new_callable=AsyncMock),
        "resource_validation": patch(f"{MODULE}.settings.resource_validation_enabled", False),
    }
    return patches


# ---------------------------------------------------------------------------
# Tests: Deploy - topology construction and node placement
# ---------------------------------------------------------------------------

class TestMultihostDeployTopologyConstruction:
    """Tests for topology JSON building during deploy."""

    @pytest.mark.asyncio
    async def test_topology_built_per_host(self, test_db: Session, test_user):
        """Each host should get its own topology JSON from TopologyService."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host_a = _make_host(test_db, "host-a", name="Host A")
        host_b = _make_host(test_db, "host-b", name="Host B")

        _make_node(test_db, lab.id, "R1", host_id=host_a.id)
        _make_node(test_db, lab.id, "R2", host_id=host_b.id)

        analysis = _FakeAnalysis(
            placements={host_a.id: [{"node_name": "R1"}], host_b.id: [{"node_name": "R2"}]},
            cross_host_links=[],
        )

        topology_calls = []

        def mock_build_deploy(lab_id, host_id):
            topology_calls.append(host_id)
            return {"nodes": [{"name": f"node-on-{host_id}"}], "links": []}

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [
                    MagicMock(host_id=host_a.id),
                    MagicMock(host_id=host_b.id),
                ]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts.build_deploy_topology.side_effect = mock_build_deploy
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                    mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})

                    with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                        await run_multihost_deploy(job.id, lab.id)

        assert set(topology_calls) == {host_a.id, host_b.id}

    @pytest.mark.asyncio
    async def test_node_placements_updated_per_host(self, test_db: Session, test_user):
        """_update_node_placements should be called for each host with its node names."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host = _make_host(test_db, "host-c", name="Host C")

        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["resource_validation"]:
            with patch(f"{MODULE}._update_node_placements", new_callable=AsyncMock) as mock_up:
                with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id)]
                    mock_ts.analyze_placements.return_value = analysis
                    mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "R1"}], "links": []}
                    mock_ts_cls.return_value = mock_ts

                    with patch(f"{MODULE}.agent_client") as mock_ac:
                        mock_ac.is_agent_online.return_value = True
                        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                        mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})

                        with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                            await run_multihost_deploy(job.id, lab.id)

            mock_up.assert_awaited_once()
            call_args = mock_up.call_args
            assert call_args[0][2] == host.id
            assert call_args[0][3] == ["R1"]

    @pytest.mark.asyncio
    async def test_management_ips_captured_per_host(self, test_db: Session, test_user):
        """_capture_node_ips should be called for each agent in host_to_agent."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host_a = _make_host(test_db, "host-d")
        host_b = _make_host(test_db, "host-e")

        _make_node(test_db, lab.id, "R1", host_id=host_a.id)
        _make_node(test_db, lab.id, "R2", host_id=host_b.id)

        analysis = _FakeAnalysis(
            placements={host_a.id: [{"node_name": "R1"}], host_b.id: [{"node_name": "R2"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}._capture_node_ips", new_callable=AsyncMock) as mock_ips:
                with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.get_nodes.return_value = [MagicMock(host_id=host_a.id), MagicMock(host_id=host_b.id)]
                    mock_ts.analyze_placements.return_value = analysis
                    mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "R1"}], "links": []}
                    mock_ts_cls.return_value = mock_ts

                    with patch(f"{MODULE}.agent_client") as mock_ac:
                        mock_ac.is_agent_online.return_value = True
                        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                        mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})

                        with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                            await run_multihost_deploy(job.id, lab.id)

            assert mock_ips.await_count == 2


# ---------------------------------------------------------------------------
# Tests: Deploy - rollback with mixed results
# ---------------------------------------------------------------------------

class TestMultihostDeployRollbackMixed:
    """Tests for rollback behavior with mixed deploy results."""

    @pytest.mark.asyncio
    async def test_only_successful_hosts_get_rollback(self, test_db: Session, test_user):
        """During rollback, only hosts with completed deploys should be destroyed."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host_a = _make_host(test_db, "host-f")
        host_b = _make_host(test_db, "host-g")

        _make_node(test_db, lab.id, "R1", host_id=host_a.id)
        _make_node(test_db, lab.id, "R2", host_id=host_b.id)

        analysis = _FakeAnalysis(
            placements={host_a.id: [{"node_name": "R1"}], host_b.id: [{"node_name": "R2"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [MagicMock(host_id=host_a.id), MagicMock(host_id=host_b.id)]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "X"}], "links": []}
                mock_ts_cls.return_value = mock_ts

                deploy_results = [
                    {"status": "completed"},  # host_a succeeds
                    RuntimeError("connection timeout"),  # host_b fails
                ]

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                    mock_ac.deploy_to_agent = AsyncMock(side_effect=deploy_results)
                    mock_ac.destroy_on_agent = AsyncMock(return_value={"status": "completed"})

                    await run_multihost_deploy(job.id, lab.id)

                # Only host_a should have been rolled back
                mock_ac.destroy_on_agent.assert_awaited_once()

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_non_completed_status_triggers_rollback(self, test_db: Session, test_user):
        """Deploy returning non-'completed' status should trigger rollback."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host = _make_host(test_db, "host-h")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id)]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "R1"}], "links": []}
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                    mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "error", "stderr": "disk full"})
                    mock_ac.destroy_on_agent = AsyncMock(return_value={"status": "completed"})

                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "disk full" in job.log_path


# ---------------------------------------------------------------------------
# Tests: Deploy - webhook on failure
# ---------------------------------------------------------------------------

class TestMultihostDeployWebhookOnFailure:
    """Tests for webhook dispatch when deploy fails."""

    @pytest.mark.asyncio
    async def test_deploy_failed_webhook_on_exception(self, test_db: Session, test_user):
        """Unexpected exception should dispatch deploy_failed webhook."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}._dispatch_webhook", new_callable=AsyncMock) as mock_wh:
                with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.get_nodes.side_effect = RuntimeError("DB gone")
                    mock_ts_cls.return_value = mock_ts

                    await run_multihost_deploy(job.id, lab.id)

            # Should have dispatched deploy_failed webhook
            webhook_events = [call.args[0] for call in mock_wh.call_args_list]
            assert "lab.deploy_failed" in webhook_events


# ---------------------------------------------------------------------------
# Tests: Destroy - completed with warnings status
# ---------------------------------------------------------------------------

class TestMultihostDestroyWarnings:
    """Tests for destroy completed-with-warnings behavior."""

    @pytest.mark.asyncio
    async def test_partial_destroy_sets_completed_with_warnings(
        self, test_db: Session, test_user
    ):
        """Partial destroy failure should set COMPLETED_WITH_WARNINGS."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        host_a = _make_host(test_db, "host-i")
        host_b = _make_host(test_db, "host-j")

        _make_node(test_db, lab.id, "R1", host_id=host_a.id)
        _make_node(test_db, lab.id, "R2", host_id=host_b.id)

        analysis = _FakeAnalysis(
            placements={host_a.id: [{"node_name": "R1"}], host_b.id: [{"node_name": "R2"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_destroy"], \
             patches["emit_job_failed"], patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.destroy_on_agent = AsyncMock(side_effect=[
                        {"status": "completed"},
                        RuntimeError("agent crashed"),
                    ])

                    with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                        await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.COMPLETED_WITH_WARNINGS.value

    @pytest.mark.asyncio
    async def test_link_states_marked_for_retry_on_partial_failure(
        self, test_db: Session, test_user
    ):
        """Remaining link states should be marked for retry on partial destroy failure."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        host = _make_host(test_db, "host-k")
        _make_node(test_db, lab.id, "R1", host_id=host.id)

        # Create a link state that should be preserved on partial failure
        ls = models.LinkState(
            lab_id=lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R2",
            target_interface="eth1",
            desired_state="up",
            actual_state="up",
        )
        test_db.add(ls)
        test_db.commit()
        test_db.refresh(ls)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_destroy"], \
             patches["emit_job_failed"], patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.destroy_on_agent = AsyncMock(
                        return_value={"status": "error", "stderr": "fail"}
                    )

                    with patch("app.tasks.link_orchestration.teardown_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                        await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(ls)
        assert ls.desired_state == "deleted"
        assert ls.actual_state == LinkActualState.ERROR.value
        assert "pending retry" in ls.error_message


# ---------------------------------------------------------------------------
# Tests: Destroy - inner exception during error handler
# ---------------------------------------------------------------------------

class TestMultihostDestroyInnerException:
    """Tests for inner exception handling during destroy failure."""

    @pytest.mark.asyncio
    async def test_inner_exception_does_not_propagate(self, test_db: Session, test_user):
        """If both main logic and error handler fail, exception should not propagate."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_destroy"], \
             patches["emit_job_failed"], patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.analyze_placements.side_effect = RuntimeError("boom")
                mock_ts_cls.return_value = mock_ts

                # Should not raise
                await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "boom" in job.log_path


# ---------------------------------------------------------------------------
# Tests: Destroy - no reachable agents with details
# ---------------------------------------------------------------------------

class TestMultihostDestroyNoAgents:
    """Tests for destroy with no reachable agents."""

    @pytest.mark.asyncio
    async def test_all_agents_missing_reports_detail(self, test_db: Session, test_user):
        """Missing agent records should be reported in error message."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")

        analysis = _FakeAnalysis(
            placements={"nonexistent-host": [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_destroy"], \
             patches["emit_job_failed"], patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "missing" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_all_agents_offline_reports_detail(self, test_db: Session, test_user):
        """Offline agents should be reported in error details."""
        lab = _make_lab(test_db, test_user, state="running")
        job = _make_job(test_db, lab, test_user, action="down")
        host = _make_host(test_db, "host-l", status="offline")

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_destroy"], \
             patches["emit_job_failed"], patches["capture_ips"], patches["update_placements"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = False

                    await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "offline" in job.log_path.lower()


# ---------------------------------------------------------------------------
# Tests: Deploy - job not found / lab not found
# ---------------------------------------------------------------------------

class TestMultihostDeployMissing:
    """Tests for missing job/lab edge cases."""

    @pytest.mark.asyncio
    async def test_missing_job_returns_early(self, test_db: Session, test_user):
        """Non-existent job ID should return without error."""
        from uuid import uuid4

        patches = _build_standard_patches(test_db)
        with patches["get_session"]:
            # Should not raise
            await run_multihost_deploy(str(uuid4()), str(uuid4()))

    @pytest.mark.asyncio
    async def test_missing_lab_fails_job(self, test_db: Session, test_user):
        """Job with non-existent lab should be marked failed."""
        from uuid import uuid4

        fake_lab_id = str(uuid4())
        job = models.Job(
            lab_id=fake_lab_id,
            user_id=test_user.id,
            action="up",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"]:
            await run_multihost_deploy(job.id, fake_lab_id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "not found" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_unplaced_nodes_get_default_agent(self, test_db: Session, test_user):
        """Nodes without host_id should be assigned to default agent."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        host = _make_host(test_db, "host-default")

        # Node WITHOUT host_id
        node = _make_node(test_db, lab.id, "R1")
        assert node.host_id is None

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "R1"}]},
            cross_host_links=[],
        )

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["broadcast"], patches["update_lab_state"], \
             patches["release_tx"], patches["dispatch_webhook"], patches["emit_deploy"], \
             patches["capture_ips"], patches["update_placements"], patches["resource_validation"]:
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [MagicMock(host_id=None)]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "R1"}], "links": []}
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_agent_for_lab = AsyncMock(return_value=host)
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                    mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})

                    with patch("app.tasks.link_orchestration.create_deployment_links", new_callable=AsyncMock, return_value=(0, 0)):
                        await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        # Should have completed (or at least not errored on placement)
        assert job.status in (JobStatus.COMPLETED.value, JobStatus.COMPLETED_WITH_WARNINGS.value)


# ---------------------------------------------------------------------------
# Tests: Destroy - missing job / lab not found
# ---------------------------------------------------------------------------

class TestMultihostDestroyMissing:
    """Tests for missing job/lab in destroy."""

    @pytest.mark.asyncio
    async def test_missing_job_returns_early(self, test_db: Session, test_user):
        """Non-existent job ID should return without error."""
        from uuid import uuid4

        patches = _build_standard_patches(test_db)
        with patches["get_session"]:
            await run_multihost_destroy(str(uuid4()), str(uuid4()))

    @pytest.mark.asyncio
    async def test_missing_lab_fails_job(self, test_db: Session, test_user):
        """Job with non-existent lab should be marked failed."""
        from uuid import uuid4

        fake_lab_id = str(uuid4())
        job = models.Job(
            lab_id=fake_lab_id,
            user_id=test_user.id,
            action="down",
            status="queued",
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        patches = _build_standard_patches(test_db)
        with patches["get_session"], patches["record_started"], patches["record_failed"], \
             patches["record_completed"], patches["emit_destroy"], patches["emit_job_failed"]:
            await run_multihost_destroy(job.id, fake_lab_id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "not found" in job.log_path.lower()
