"""Round 12 multi-host deployment tests for api/app/tasks/jobs_multihost.py.

Targets mid-deploy failure handling, partial recovery when one agent fails,
rollback logic, node placement across hosts, destroy with mixed agent states,
and unexpected exception propagation.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.state import JobStatus, LinkActualState
from app.tasks.jobs_multihost import run_multihost_deploy, run_multihost_destroy
from tests.factories import make_host, make_job, make_lab, make_link_state, make_node


# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

MODULE = "app.tasks.jobs_multihost"


def _mock_get_session(test_db: Session):
    @contextmanager
    def mock_session():
        yield test_db
    return mock_session


@dataclass
class _FakeAnalysis:
    placements: dict
    cross_host_links: list = field(default_factory=list)
    single_host: bool = False


def _standard_patches(test_db):
    """Return dict of common patches for multihost deploy/destroy tests."""
    return {
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


def _enter_all(patches):
    """Enter all patches in the dict, returning mocks keyed by name."""
    mocks = {}
    for k, p in patches.items():
        mocks[k] = p.__enter__()
    return mocks


# ---------------------------------------------------------------------------
# Deploy: mid-deploy failure and rollback
# ---------------------------------------------------------------------------


class TestDeployMidFailureRollback:
    """Verify rollback behavior when one agent fails mid-deploy."""

    @pytest.mark.asyncio
    async def test_one_agent_fails_triggers_rollback_of_successful_agents(
        self, test_db: Session, test_user,
    ):
        """When deploy to agent-B fails, agent-A (success) should be rolled back."""
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        host_a = make_host(test_db, "host-a", name="Agent-A")
        host_b = make_host(test_db, "host-b", name="Agent-B")
        make_node(test_db, lab, "r1", host_id=host_a.id)
        make_node(test_db, lab, "r2", host_id=host_b.id)

        analysis = _FakeAnalysis(
            placements={
                host_a.id: [{"node_name": "r1"}],
                host_b.id: [{"node_name": "r2"}],
            },
        )

        patches = _standard_patches(test_db)
        with ExitStack() as stack:
            for p in patches.values():
                stack.enter_context(p)

            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [
                    MagicMock(host_id=host_a.id, device="linux"),
                    MagicMock(host_id=host_b.id, device="linux"),
                ]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts.build_deploy_topology.side_effect = lambda lid, hid: {
                    "nodes": [{"name": "r1" if hid == host_a.id else "r2"}],
                    "links": [],
                }
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac, \
                     patch(f"{MODULE}.get_node_provider", return_value="docker"):
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})

                    # Agent-A succeeds, Agent-B raises
                    async def _deploy_side_effect(agent, jid, lid, **kwargs):
                        if agent.id == host_a.id:
                            return {"status": "completed"}
                        raise RuntimeError("Agent-B crashed")

                    mock_ac.deploy_to_agent = AsyncMock(side_effect=_deploy_side_effect)
                    mock_ac.destroy_on_agent = AsyncMock(
                        return_value={"status": "completed"}
                    )

                    await run_multihost_deploy(job.id, lab.id)

                    # Verify rollback was called for agent-A only
                    mock_ac.destroy_on_agent.assert_called_once()
                    rollback_agent = mock_ac.destroy_on_agent.call_args[0][0]
                    assert rollback_agent.id == host_a.id

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "Rollback" in job.log_path
        assert "Agent-B crashed" in job.log_path

    @pytest.mark.asyncio
    async def test_all_agents_fail_no_rollback_needed(
        self, test_db: Session, test_user,
    ):
        """When all agents fail deploy, no rollback is attempted."""
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        host_a = make_host(test_db, "host-all-fail-a", name="AgentA")
        host_b = make_host(test_db, "host-all-fail-b", name="AgentB")
        make_node(test_db, lab, "n1", host_id=host_a.id)
        make_node(test_db, lab, "n2", host_id=host_b.id)

        analysis = _FakeAnalysis(
            placements={
                host_a.id: [{"node_name": "n1"}],
                host_b.id: [{"node_name": "n2"}],
            },
        )

        patches = _standard_patches(test_db)
        with ExitStack() as stack:
            for p in patches.values():
                stack.enter_context(p)
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [
                    MagicMock(host_id=host_a.id, device="linux"),
                    MagicMock(host_id=host_b.id, device="linux"),
                ]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "n1"}], "links": []}
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac, \
                     patch(f"{MODULE}.get_node_provider", return_value="docker"):
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                    mock_ac.deploy_to_agent = AsyncMock(
                        side_effect=RuntimeError("total failure")
                    )
                    mock_ac.destroy_on_agent = AsyncMock()

                    await run_multihost_deploy(job.id, lab.id)

                    # No rollback because nothing succeeded
                    mock_ac.destroy_on_agent.assert_not_called()

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "No hosts to rollback" in job.log_path

    @pytest.mark.asyncio
    async def test_rollback_failure_is_logged_but_job_still_fails(
        self, test_db: Session, test_user,
    ):
        """When rollback itself fails, the error is logged in job output."""
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        host_a = make_host(test_db, "host-rb-fail-a", name="AgentA")
        host_b = make_host(test_db, "host-rb-fail-b", name="AgentB")
        make_node(test_db, lab, "n1", host_id=host_a.id)
        make_node(test_db, lab, "n2", host_id=host_b.id)

        analysis = _FakeAnalysis(
            placements={
                host_a.id: [{"node_name": "n1"}],
                host_b.id: [{"node_name": "n2"}],
            },
        )

        patches = _standard_patches(test_db)
        with ExitStack() as stack:
            for p in patches.values():
                stack.enter_context(p)
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [
                    MagicMock(host_id=host_a.id, device="linux"),
                    MagicMock(host_id=host_b.id, device="linux"),
                ]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts.build_deploy_topology.side_effect = lambda lid, hid: {
                    "nodes": [{"name": "n1" if hid == host_a.id else "n2"}],
                    "links": [],
                }
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac, \
                     patch(f"{MODULE}.get_node_provider", return_value="docker"):
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})

                    async def _deploy(agent, jid, lid, **kwargs):
                        if agent.id == host_a.id:
                            return {"status": "completed"}
                        return {"status": "error", "stderr": "deploy error on B"}

                    mock_ac.deploy_to_agent = AsyncMock(side_effect=_deploy)
                    # Rollback also fails
                    mock_ac.destroy_on_agent = AsyncMock(
                        side_effect=RuntimeError("rollback explosion")
                    )

                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "rollback FAILED" in job.log_path
        assert "rollback explosion" in job.log_path


# ---------------------------------------------------------------------------
# Deploy: link creation failure after successful container deploy
# ---------------------------------------------------------------------------


class TestDeployLinkFailure:
    """Containers deploy OK, but link orchestration fails."""

    @pytest.mark.asyncio
    async def test_link_failure_marks_job_failed_with_error_state(
        self, test_db: Session, test_user,
    ):
        """If create_deployment_links reports failures, job is FAILED."""
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        host = make_host(test_db, "host-link-fail")
        make_node(test_db, lab, "r1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "r1"}]},
        )

        patches = _standard_patches(test_db)
        with ExitStack() as stack:
            for p in patches.values():
                stack.enter_context(p)
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id, device="linux")]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "r1"}], "links": []}
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                    mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})

                    with patch(
                        "app.tasks.link_orchestration.create_deployment_links",
                        new_callable=AsyncMock,
                        return_value=(2, 3),  # 2 OK, 3 failed
                    ):
                        await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "3 failed" in job.log_path
        assert "some links failed" in job.log_path.lower()


# ---------------------------------------------------------------------------
# Deploy: unplaced nodes get assigned default agent
# ---------------------------------------------------------------------------


class TestDeployUnplacedNodeAssignment:
    """Nodes without host_id get assigned to default agent."""

    @pytest.mark.asyncio
    async def test_unplaced_nodes_assigned_default_agent(
        self, test_db: Session, test_user,
    ):
        """Unplaced nodes should be assigned to a default agent before deploy."""
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        host = make_host(test_db, "default-host")
        node = make_node(test_db, lab, "r1", host_id=None)  # No placement

        # After assignment, analysis will show node on default host
        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "r1"}]},
        )

        patches = _standard_patches(test_db)
        with ExitStack() as stack:
            for p in patches.values():
                stack.enter_context(p)
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                # First call returns node without host_id
                mock_ts.get_nodes.return_value = [node]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts.build_deploy_topology.return_value = {"nodes": [{"name": "r1"}], "links": []}
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.get_agent_for_lab = AsyncMock(return_value=host)
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.get_lab_status_from_agent = AsyncMock(return_value={})
                    mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})

                    with patch(
                        "app.tasks.link_orchestration.create_deployment_links",
                        new_callable=AsyncMock,
                        return_value=(0, 0),
                    ):
                        await run_multihost_deploy(job.id, lab.id)

                    mock_ac.get_agent_for_lab.assert_called_once()

        test_db.refresh(job)
        assert job.status == JobStatus.COMPLETED.value
        # Node should now have host_id set
        test_db.refresh(node)
        assert node.host_id == host.id

    @pytest.mark.asyncio
    async def test_no_default_agent_fails_job(
        self, test_db: Session, test_user,
    ):
        """When unplaced nodes exist and no default agent, job fails."""
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        node = make_node(test_db, lab, "r1", host_id=None)

        patches = _standard_patches(test_db)
        with ExitStack() as stack:
            for p in patches.values():
                stack.enter_context(p)
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [node]
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.get_agent_for_lab = AsyncMock(return_value=None)

                    await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "no default agent" in job.log_path.lower()


# ---------------------------------------------------------------------------
# Deploy: agent preflight failure
# ---------------------------------------------------------------------------


class TestDeployPreflightFailure:
    """Agent connectivity checks before deploy."""

    @pytest.mark.asyncio
    async def test_agent_preflight_connectivity_failure_blocks_deploy(
        self, test_db: Session, test_user,
    ):
        """If an agent fails preflight, deploy aborts before dispatching."""
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        host = make_host(test_db, "unreachable-host")
        make_node(test_db, lab, "r1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "r1"}]},
        )

        patches = _standard_patches(test_db)
        with ExitStack() as stack:
            for p in patches.values():
                stack.enter_context(p)
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.get_nodes.return_value = [MagicMock(host_id=host.id, device="linux")]
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    # Preflight fails
                    mock_ac.get_lab_status_from_agent = AsyncMock(
                        side_effect=ConnectionError("connection refused")
                    )
                    mock_ac.deploy_to_agent = AsyncMock()

                    await run_multihost_deploy(job.id, lab.id)

                    # Deploy should NOT have been called
                    mock_ac.deploy_to_agent.assert_not_called()

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "Missing or unhealthy" in job.log_path


# ---------------------------------------------------------------------------
# Deploy: unexpected exception wraps in error handler
# ---------------------------------------------------------------------------


class TestDeployUnexpectedException:
    """Top-level exception handler in run_multihost_deploy."""

    @pytest.mark.asyncio
    async def test_unexpected_exception_sets_job_failed_and_lab_error(
        self, test_db: Session, test_user,
    ):
        """Unhandled exception triggers the outer except block."""
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)

        patches = _standard_patches(test_db)
        with ExitStack() as stack:
            for p in patches.values():
                stack.enter_context(p)
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts_cls.side_effect = ValueError("topology init boom")

                await run_multihost_deploy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "topology init boom" in job.log_path


# ---------------------------------------------------------------------------
# Destroy: partial agent availability
# ---------------------------------------------------------------------------


class TestDestroyPartialAgentAvailability:
    """Destroy with some agents offline or missing."""

    @pytest.mark.asyncio
    async def test_destroy_with_one_agent_offline_completes_with_warnings(
        self, test_db: Session, test_user,
    ):
        """When one agent is offline during destroy, job completes with warnings."""
        lab = make_lab(test_db, test_user, state="running")
        job = make_job(test_db, lab, test_user, action="down")
        host_a = make_host(test_db, "dest-ok-host", name="OnlineAgent")
        host_b = make_host(test_db, "dest-off-host", name="OfflineAgent", status="offline")
        # Remove last_heartbeat so is_agent_online returns False
        host_b.last_heartbeat = None
        test_db.commit()

        make_node(test_db, lab, "r1", host_id=host_a.id)
        make_node(test_db, lab, "r2", host_id=host_b.id)

        analysis = _FakeAnalysis(
            placements={
                host_a.id: [{"node_name": "r1"}],
                host_b.id: [{"node_name": "r2"}],
            },
        )

        patches = _standard_patches(test_db)
        with ExitStack() as stack:
            for p in patches.values():
                stack.enter_context(p)
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.side_effect = lambda a: a.id == host_a.id
                    mock_ac.destroy_on_agent = AsyncMock(
                        return_value={"status": "completed"}
                    )

                    with patch(
                        "app.tasks.link_orchestration.teardown_deployment_links",
                        new_callable=AsyncMock,
                        return_value=(0, 0),
                    ):
                        await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.COMPLETED_WITH_WARNINGS.value
        assert "offline" in job.log_path.lower() or "unreachable" in job.log_path.lower()

    @pytest.mark.asyncio
    async def test_destroy_no_reachable_agents_fails(
        self, test_db: Session, test_user,
    ):
        """When no agents are reachable during destroy, job fails."""
        lab = make_lab(test_db, test_user, state="running")
        job = make_job(test_db, lab, test_user, action="down")
        host = make_host(test_db, "all-off-host", name="DeadAgent", status="offline")
        host.last_heartbeat = None
        test_db.commit()
        make_node(test_db, lab, "r1", host_id=host.id)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "r1"}]},
        )

        patches = _standard_patches(test_db)
        with ExitStack() as stack:
            for p in patches.values():
                stack.enter_context(p)
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = False

                    await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "No online agents" in job.log_path


# ---------------------------------------------------------------------------
# Destroy: link state cleanup
# ---------------------------------------------------------------------------


class TestDestroyLinkStateCleanup:
    """Verify LinkState records are properly handled after destroy."""

    @pytest.mark.asyncio
    async def test_successful_destroy_deletes_remaining_link_states(
        self, test_db: Session, test_user,
    ):
        """After a fully successful destroy, lingering LinkState rows are deleted."""
        lab = make_lab(test_db, test_user, state="running")
        job = make_job(test_db, lab, test_user, action="down")
        host = make_host(test_db, "host-ls-clean")
        make_node(test_db, lab, "r1", host_id=host.id)
        make_node(test_db, lab, "r2", host_id=host.id)
        make_link_state(test_db, lab)

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "r1"}, {"node_name": "r2"}]},
        )

        patches = _standard_patches(test_db)
        with ExitStack() as stack:
            for p in patches.values():
                stack.enter_context(p)
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    mock_ac.destroy_on_agent = AsyncMock(
                        return_value={"status": "completed"}
                    )

                    with patch(
                        "app.tasks.link_orchestration.teardown_deployment_links",
                        new_callable=AsyncMock,
                        return_value=(1, 0),
                    ):
                        await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.COMPLETED.value
        # Link state should be deleted
        remaining = (
            test_db.query(models.LinkState)
            .filter(models.LinkState.lab_id == lab.id)
            .all()
        )
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_partial_destroy_marks_link_states_error(
        self, test_db: Session, test_user,
    ):
        """After partial destroy failure, LinkStates are marked error + desired=deleted."""
        lab = make_lab(test_db, test_user, state="running")
        job = make_job(test_db, lab, test_user, action="down")
        host = make_host(test_db, "host-ls-partial")
        make_node(test_db, lab, "r1", host_id=host.id)
        make_node(test_db, lab, "r2", host_id=host.id)
        ls = make_link_state(test_db, lab, desired="up", actual="up")

        analysis = _FakeAnalysis(
            placements={host.id: [{"node_name": "r1"}, {"node_name": "r2"}]},
        )

        patches = _standard_patches(test_db)
        with ExitStack() as stack:
            for p in patches.values():
                stack.enter_context(p)
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts = MagicMock()
                mock_ts.analyze_placements.return_value = analysis
                mock_ts_cls.return_value = mock_ts

                with patch(f"{MODULE}.agent_client") as mock_ac:
                    mock_ac.is_agent_online.return_value = True
                    # Destroy fails
                    mock_ac.destroy_on_agent = AsyncMock(
                        side_effect=RuntimeError("destroy error")
                    )

                    with patch(
                        "app.tasks.link_orchestration.teardown_deployment_links",
                        new_callable=AsyncMock,
                        return_value=(0, 0),
                    ):
                        await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.COMPLETED_WITH_WARNINGS.value

        test_db.refresh(ls)
        assert ls.desired_state == "deleted"
        assert ls.actual_state == LinkActualState.ERROR.value
        assert "pending retry" in ls.error_message


# ---------------------------------------------------------------------------
# Destroy: unexpected exception handler
# ---------------------------------------------------------------------------


class TestDestroyUnexpectedException:
    """Outer exception handler in run_multihost_destroy."""

    @pytest.mark.asyncio
    async def test_unexpected_destroy_exception_marks_job_failed(
        self, test_db: Session, test_user,
    ):
        """Unhandled exception in destroy sets job to FAILED."""
        lab = make_lab(test_db, test_user, state="running")
        job = make_job(test_db, lab, test_user, action="down")

        patches = _standard_patches(test_db)
        with ExitStack() as stack:
            for p in patches.values():
                stack.enter_context(p)
            with patch(f"{MODULE}.TopologyService") as mock_ts_cls:
                mock_ts_cls.side_effect = TypeError("bad analysis")

                await run_multihost_destroy(job.id, lab.id)

        test_db.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "bad analysis" in job.log_path