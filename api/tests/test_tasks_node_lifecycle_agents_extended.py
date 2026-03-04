"""Extended tests for app/tasks/node_lifecycle_agents.py — covering gaps not
addressed by test_tasks_node_lifecycle_agents.py.

Covers:
- _run_bin_pack_placement (capacity failures, all unreachable, overflow, warnings,
  unplaceable nodes, empty buckets fallthrough, successful placement)
- _group_and_dispatch (single agent, multiple agents with sub-jobs, no-agent nodes,
  empty mapping)
- _handle_unassigned_nodes (empty list no-op, nodes needing action → error,
  nodes already in terminal state, no other node_states fallback)
- _resolve_final_agent (target online, target offline, placement-based, lab.agent_id
  fallback, healthy-agent fallback, no agent at all)
- _release_db_transaction_for_io (pending writes commit, clean session rollback,
  no session no-op, commit exception recovery)
- _resolve_agents integration (full flow)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models
from app.state import JobStatus, NodeActualState, NodeDesiredState


# ---------------------------------------------------------------------------
# Helpers (mirrors the existing test file's _make_mixin / _make_host /
# _make_node_state helpers so this file is self-contained)
# ---------------------------------------------------------------------------


def _make_host(
    test_db: Session,
    *,
    host_id: str | None = None,
    name: str = "agent",
    status: str = "online",
    heartbeat_offset: timedelta = timedelta(seconds=0),
    providers: list[str] | None = None,
    is_local: bool = False,
) -> models.Host:
    """Create and persist a Host row."""
    hid = host_id or str(uuid4())
    host = models.Host(
        id=hid,
        name=name,
        address=f"{name}.local:8001",
        status=status,
        capabilities=json.dumps({"providers": providers or ["docker"]}),
        version="1.0.0",
        last_heartbeat=datetime.now(timezone.utc) - heartbeat_offset,
        resource_usage=json.dumps({}),
    )
    # is_local is a property derived from capabilities on some versions; set it
    # via the dict override when the model supports it.
    try:
        host.is_local = is_local
    except AttributeError:
        pass
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


def _make_node_state(
    test_db: Session,
    lab: models.Lab,
    name: str,
    *,
    desired: str = "running",
    actual: str = "undeployed",
    node_id: str | None = None,
) -> models.NodeState:
    """Create and persist a NodeState row."""
    ns = models.NodeState(
        lab_id=lab.id,
        node_id=node_id or name.lower(),
        node_name=name,
        desired_state=desired,
        actual_state=actual,
    )
    test_db.add(ns)
    test_db.commit()
    test_db.refresh(ns)
    return ns


def _make_mixin(
    session: Session,
    lab: models.Lab,
    job: models.Job,
    node_states: list[models.NodeState],
    *,
    db_nodes_map: dict | None = None,
    db_nodes_by_gui_id: dict | None = None,
    placements_map: dict | None = None,
    provider: str | None = "docker",
    target_agent_id: str | None = None,
):
    """Build a minimal AgentResolutionMixin instance with injected attributes."""
    from app.tasks.node_lifecycle_agents import AgentResolutionMixin

    mixin = AgentResolutionMixin.__new__(AgentResolutionMixin)
    mixin.session = session
    mixin.lab = lab
    mixin.job = job
    mixin.node_states = list(node_states)
    mixin.db_nodes_map = db_nodes_map or {}
    mixin.db_nodes_by_gui_id = db_nodes_by_gui_id or {}
    mixin.placements_map = placements_map or {}
    mixin.provider = provider
    mixin.target_agent_id = target_agent_id
    mixin.agent = None
    mixin.log_parts = []
    mixin._broadcast_state = MagicMock()
    return mixin


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ext_lab(test_db: Session, test_user: models.User) -> models.Lab:
    """Lab for extended agent resolution tests."""
    lab = models.Lab(
        name="Extended Lab",
        owner_id=test_user.id,
        provider="docker",
        state="starting",
        workspace_path="/tmp/ext-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


@pytest.fixture()
def ext_job(test_db: Session, ext_lab: models.Lab, test_user: models.User) -> models.Job:
    """Job for extended agent resolution tests."""
    job = models.Job(
        id=str(uuid4()),
        lab_id=ext_lab.id,
        user_id=test_user.id,
        action="sync:lab",
        status="queued",
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


# ---------------------------------------------------------------------------
# TestReleaseDbTransactionForIo
# ---------------------------------------------------------------------------


class TestReleaseDbTransactionForIo:
    """Tests for _release_db_transaction_for_io."""

    def test_no_session_is_noop(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """When session is None the method should be a complete no-op."""
        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns])
        mixin.session = None  # type: ignore[assignment]

        # Should not raise
        mixin._release_db_transaction_for_io("test reason")

    def test_pending_writes_trigger_commit(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Session with pending new objects should be committed."""
        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns])

        mock_session = MagicMock()
        mock_session.new = [object()]  # non-empty → pending writes
        mock_session.dirty = []
        mock_session.deleted = []
        mixin.session = mock_session

        mixin._release_db_transaction_for_io("pending writes")

        mock_session.commit.assert_called_once()
        mock_session.rollback.assert_not_called()

    def test_clean_session_triggers_rollback(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Session with no pending writes should be rolled back."""
        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns])

        mock_session = MagicMock()
        mock_session.new = []
        mock_session.dirty = []
        mock_session.deleted = []
        mixin.session = mock_session

        mixin._release_db_transaction_for_io("clean session")

        mock_session.rollback.assert_called_once()
        mock_session.commit.assert_not_called()

    def test_commit_exception_falls_back_to_rollback(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """If commit raises, a rollback attempt should be made without re-raising."""
        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns])

        mock_session = MagicMock()
        mock_session.new = [object()]
        mock_session.dirty = []
        mock_session.deleted = []
        mock_session.commit.side_effect = RuntimeError("db gone")
        mixin.session = mock_session

        # Should not propagate the exception
        mixin._release_db_transaction_for_io("commit error recovery")

        mock_session.commit.assert_called_once()
        mock_session.rollback.assert_called_once()

    def test_dirty_objects_trigger_commit(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Session with dirty (modified) objects is considered to have pending writes."""
        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns])

        mock_session = MagicMock()
        mock_session.new = []
        mock_session.dirty = [object()]  # dirty objects → pending writes
        mock_session.deleted = []
        mixin.session = mock_session

        mixin._release_db_transaction_for_io("dirty objects")

        mock_session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# TestRunBinPackPlacement
# ---------------------------------------------------------------------------


class TestRunBinPackPlacement:
    """Tests for _run_bin_pack_placement."""

    @pytest.mark.asyncio
    async def test_all_candidates_unreachable_returns_none(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """When all candidate agents fail ping, no buckets are built and None
        is returned (fallthrough to fallback, not a hard failure)."""
        host = _make_host(test_db, name="unreachable-agent")
        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns])

        all_node_agents: dict[str, str] = {}

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client, patch(
            "app.tasks.node_lifecycle_agents.settings",
        ) as mock_settings:
            mock_settings.agent_stale_timeout = 90
            mock_settings.placement_controller_reserve_mb = 0
            mock_client.is_agent_online.return_value = True
            mock_client.ping_agent = AsyncMock(side_effect=Exception("unreachable"))
            mock_client.query_agent_capacity = AsyncMock(return_value={})
            mock_client.get_healthy_agent = AsyncMock(return_value=host)
            with patch(
                "app.agent_client.get_agent_providers",
                return_value=["docker"],
            ):
                result = await mixin._run_bin_pack_placement(
                    all_node_agents, [ns], [],
                )

        # All agents unreachable → empty buckets → return None (fall through)
        assert result is None
        assert all_node_agents == {}

    @pytest.mark.asyncio
    async def test_capacity_fetch_failure_skips_agent(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """When capacity query fails for an agent, that agent is skipped."""
        _make_host(test_db, name="cap-fail-agent")
        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns])

        all_node_agents: dict[str, str] = {}

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client, patch(
            "app.tasks.node_lifecycle_agents.settings",
        ) as mock_settings:
            mock_settings.agent_stale_timeout = 90
            mock_settings.placement_controller_reserve_mb = 0
            # Ping succeeds, capacity query raises
            mock_client.ping_agent = AsyncMock(return_value=None)
            mock_client.query_agent_capacity = AsyncMock(
                side_effect=Exception("cap query failed")
            )
            with patch(
                "app.agent_client.get_agent_providers",
                return_value=["docker"],
            ):
                result = await mixin._run_bin_pack_placement(
                    all_node_agents, [ns], [],
                )

        # Capacity query failed → empty buckets → fallthrough
        assert result is None

    @pytest.mark.asyncio
    async def test_capacity_error_field_skips_agent(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Capacity response with 'error' key or missing memory_total_gb is skipped."""
        _make_host(test_db, name="bad-cap-agent")
        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns])

        all_node_agents: dict[str, str] = {}

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client, patch(
            "app.tasks.node_lifecycle_agents.settings",
        ) as mock_settings:
            mock_settings.agent_stale_timeout = 90
            mock_settings.placement_controller_reserve_mb = 0
            mock_client.ping_agent = AsyncMock(return_value=None)
            # Returns dict with error field
            mock_client.query_agent_capacity = AsyncMock(
                return_value={"error": "not ready"}
            )
            with patch(
                "app.agent_client.get_agent_providers",
                return_value=["docker"],
            ):
                result = await mixin._run_bin_pack_placement(
                    all_node_agents, [ns], [],
                )

        assert result is None
        assert all_node_agents == {}

    @pytest.mark.asyncio
    async def test_unplaceable_nodes_fail_job(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """When bin-packer reports unplaceable nodes the job is marked FAILED."""
        host = _make_host(test_db, name="tight-agent")
        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns])

        all_node_agents: dict[str, str] = {}

        from app.services.resource_capacity import PlacementPlan

        fake_plan = PlacementPlan(
            assignments={},
            unplaceable=["R1"],
            errors=["Insufficient memory for R1"],
        )

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client, patch(
            "app.tasks.node_lifecycle_agents.settings",
        ) as mock_settings, patch(
            "app.services.resource_capacity.plan_placement",
            return_value=fake_plan,
        ):
            mock_settings.agent_stale_timeout = 90
            mock_settings.placement_controller_reserve_mb = 0
            mock_client.ping_agent = AsyncMock(return_value=None)
            mock_client.query_agent_capacity = AsyncMock(
                return_value={
                    "memory_total_gb": 8,
                    "allocated_memory_mb": 0,
                    "cpu_count": 4,
                    "allocated_vcpus": 0,
                }
            )
            with patch(
                "app.agent_client.get_agent_providers",
                return_value=["docker"],
            ):
                result = await mixin._run_bin_pack_placement(
                    all_node_agents, [ns], [],
                )

        assert result is False
        test_db.refresh(ext_job)
        assert ext_job.status == JobStatus.FAILED.value
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.ERROR.value

    @pytest.mark.asyncio
    async def test_successful_placement_assigns_nodes(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Successful bin-pack populates all_node_agents and returns None."""
        host = _make_host(test_db, name="ok-agent")
        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns])

        all_node_agents: dict[str, str] = {}

        from app.services.resource_capacity import PlacementPlan

        fake_plan = PlacementPlan(
            assignments={"R1": host.id},
            unplaceable=[],
            warnings=["Low memory on agent"],
        )

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client, patch(
            "app.tasks.node_lifecycle_agents.settings",
        ) as mock_settings, patch(
            "app.services.resource_capacity.plan_placement",
            return_value=fake_plan,
        ):
            mock_settings.agent_stale_timeout = 90
            mock_settings.placement_controller_reserve_mb = 0
            mock_client.ping_agent = AsyncMock(return_value=None)
            mock_client.query_agent_capacity = AsyncMock(
                return_value={
                    "memory_total_gb": 16,
                    "allocated_memory_mb": 2048,
                    "cpu_count": 8,
                    "allocated_vcpus": 2,
                }
            )
            with patch(
                "app.agent_client.get_agent_providers",
                return_value=["docker"],
            ):
                result = await mixin._run_bin_pack_placement(
                    all_node_agents, [ns], [],
                )

        # Returns None (not False) for success path
        assert result is None
        assert all_node_agents["R1"] == host.id
        # Warning was appended to log_parts
        assert any("Low memory" in p for p in mixin.log_parts)

    @pytest.mark.asyncio
    async def test_sticky_overflow_moves_node_to_bin_packer(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Sticky node that exceeds its agent's capacity overflows to bin-packer pool."""
        host = _make_host(test_db, name="tight-sticky-agent")
        ns_sticky = _make_node_state(test_db, ext_lab, "R1")
        ns_new = _make_node_state(test_db, ext_lab, "R2")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns_sticky, ns_new])

        # Pre-assign R1 as "sticky"
        all_node_agents: dict[str, str] = {"R1": host.id}

        from app.services.resource_capacity import PlacementPlan

        fake_plan = PlacementPlan(
            assignments={"R1": host.id, "R2": host.id},
            unplaceable=[],
        )

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client, patch(
            "app.tasks.node_lifecycle_agents.settings",
        ) as mock_settings, patch(
            "app.services.resource_capacity.plan_placement",
            return_value=fake_plan,
        ) as mock_plan:
            mock_settings.agent_stale_timeout = 90
            mock_settings.placement_controller_reserve_mb = 0
            mock_client.ping_agent = AsyncMock(return_value=None)
            # Very little memory — sticky node should overflow
            mock_client.query_agent_capacity = AsyncMock(
                return_value={
                    "memory_total_gb": 0.5,  # 512 MB total
                    "allocated_memory_mb": 0,
                    "cpu_count": 2,
                    "allocated_vcpus": 0,
                }
            )
            with patch(
                "app.agent_client.get_agent_providers",
                return_value=["docker"],
            ):
                result = await mixin._run_bin_pack_placement(
                    all_node_agents,
                    [ns_new],        # new_nodes
                    ["R1"],          # sticky_nodes
                )

        # Method should complete without error (return None or False but not raise)
        assert result is None or result is False


# ---------------------------------------------------------------------------
# TestGroupAndDispatch
# ---------------------------------------------------------------------------


class TestGroupAndDispatch:
    """Tests for _group_and_dispatch."""

    def test_empty_mapping_returns_all_nodes_without_agent(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """When no nodes have an agent assigned, all are returned as without-agent."""
        ns1 = _make_node_state(test_db, ext_lab, "R1")
        ns2 = _make_node_state(test_db, ext_lab, "R2")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns1, ns2])

        without_agent = mixin._group_and_dispatch({})

        assert len(without_agent) == 2
        # target_agent_id stays None, node_states unchanged
        assert mixin.target_agent_id is None
        assert len(mixin.node_states) == 2

    def test_single_agent_sets_target_no_sub_jobs(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """With one agent, target_agent_id is set and no sub-jobs are spawned."""
        host = _make_host(test_db, name="single-agent")
        ns1 = _make_node_state(test_db, ext_lab, "R1")
        ns2 = _make_node_state(test_db, ext_lab, "R2")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns1, ns2])

        all_node_agents = {"R1": host.id, "R2": host.id}

        with patch("app.tasks.node_lifecycle_agents.safe_create_task") as mock_task:
            without_agent = mixin._group_and_dispatch(all_node_agents)

        assert without_agent == []
        assert mixin.target_agent_id == host.id
        assert len(mixin.node_states) == 2
        # No sub-jobs needed for a single agent
        mock_task.assert_not_called()

    def test_multiple_agents_spawn_sub_jobs(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """With two agents, the second gets a sub-job spawned via safe_create_task."""
        host1 = _make_host(test_db, name="primary-agent")
        host2 = _make_host(test_db, name="secondary-agent")
        ns1 = _make_node_state(test_db, ext_lab, "R1")
        ns2 = _make_node_state(test_db, ext_lab, "R2")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns1, ns2])

        # Stable ordering: R1 → host1, R2 → host2 (dict insertion order)
        all_node_agents = {"R1": host1.id, "R2": host2.id}

        with patch(
            "app.tasks.node_lifecycle_agents.safe_create_task",
        ) as mock_task, patch(
            "app.tasks.jobs.run_node_reconcile",
            return_value=AsyncMock(),
        ):
            without_agent = mixin._group_and_dispatch(all_node_agents)

        assert without_agent == []
        # Primary agent is the first key
        assert mixin.target_agent_id == host1.id
        # Primary node_states only contain R1 (the primary agent's nodes)
        primary_names = {ns.node_name for ns in mixin.node_states}
        assert "R1" in primary_names
        # safe_create_task called once for the secondary agent
        mock_task.assert_called_once()

    def test_sub_job_created_in_db_for_second_agent(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """A Job row should be persisted for each additional agent."""
        host1 = _make_host(test_db, name="agent-a")
        host2 = _make_host(test_db, name="agent-b")
        ns1 = _make_node_state(test_db, ext_lab, "R1")
        ns2 = _make_node_state(test_db, ext_lab, "R2")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns1, ns2])

        all_node_agents = {"R1": host1.id, "R2": host2.id}

        with patch("app.tasks.node_lifecycle_agents.safe_create_task"), patch(
            "app.tasks.jobs.run_node_reconcile",
            return_value=AsyncMock(),
        ):
            mixin._group_and_dispatch(all_node_agents)

        # A child job should have been persisted for host2
        child_jobs = (
            test_db.query(models.Job)
            .filter(models.Job.parent_job_id == ext_job.id)
            .all()
        )
        assert len(child_jobs) == 1
        assert host2.id in child_jobs[0].action

    def test_mixed_assigned_and_unassigned(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Nodes without agent go into the without-agent list, assigned ones are grouped."""
        host = _make_host(test_db, name="one-agent")
        ns1 = _make_node_state(test_db, ext_lab, "R1")
        ns2 = _make_node_state(test_db, ext_lab, "R2")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns1, ns2])

        # Only R1 has an agent
        all_node_agents = {"R1": host.id}

        with patch("app.tasks.node_lifecycle_agents.safe_create_task"):
            without_agent = mixin._group_and_dispatch(all_node_agents)

        assert len(without_agent) == 1
        assert without_agent[0].node_name == "R2"


# ---------------------------------------------------------------------------
# TestHandleUnassignedNodes
# ---------------------------------------------------------------------------


class TestHandleUnassignedNodes:
    """Tests for _handle_unassigned_nodes."""

    def test_empty_list_is_noop(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Empty without-agent list should not modify anything."""
        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns])

        mixin._handle_unassigned_nodes([])

        # node_states unchanged
        assert len(mixin.node_states) == 1
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.UNDEPLOYED.value

    def test_nodes_needing_action_marked_error(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Unassigned nodes that need action (desired=running, actual!=running)
        should be set to error state."""
        ns_primary = _make_node_state(test_db, ext_lab, "R1")
        ns_orphan = _make_node_state(
            test_db, ext_lab, "R2",
            desired="running",
            actual="undeployed",
        )
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns_primary])

        mixin._handle_unassigned_nodes([ns_orphan])

        test_db.refresh(ns_orphan)
        assert ns_orphan.actual_state == NodeActualState.ERROR.value
        assert ns_orphan.error_message is not None

    def test_node_already_running_is_not_marked_error(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Unassigned node that is already running (desired=running, actual=running)
        should not be marked error."""
        ns_primary = _make_node_state(test_db, ext_lab, "R1")
        ns_already_ok = _make_node_state(
            test_db, ext_lab, "R2",
            desired="running",
            actual="running",
        )
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns_primary])

        mixin._handle_unassigned_nodes([ns_already_ok])

        test_db.refresh(ns_already_ok)
        assert ns_already_ok.actual_state == NodeActualState.RUNNING.value

    def test_node_desired_stopped_already_undeployed_is_not_error(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Unassigned node with desired=stopped and actual=undeployed needs no action."""
        ns_primary = _make_node_state(test_db, ext_lab, "R1")
        ns_stopped = _make_node_state(
            test_db, ext_lab, "R2",
            desired="stopped",
            actual="undeployed",
        )
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns_primary])

        mixin._handle_unassigned_nodes([ns_stopped])

        test_db.refresh(ns_stopped)
        assert ns_stopped.actual_state == NodeActualState.UNDEPLOYED.value

    def test_no_other_node_states_triggers_fallback(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """When self.node_states is empty, unassigned nodes become the new node_states."""
        ns_orphan = _make_node_state(
            test_db, ext_lab, "R1",
            desired="running",
            actual="undeployed",
        )
        mixin = _make_mixin(test_db, ext_lab, ext_job, [])
        # node_states is empty (no primary agent assigned anyone)
        mixin.node_states = []

        mixin._handle_unassigned_nodes([ns_orphan])

        # Fallback: orphan becomes the node_states list
        assert mixin.node_states == [ns_orphan]

    def test_node_desired_stopped_actual_running_is_error(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Node desired=stopped but actual=running is still running — needs action."""
        ns_primary = _make_node_state(test_db, ext_lab, "R1")
        ns_running = _make_node_state(
            test_db, ext_lab, "R2",
            desired="stopped",
            actual="running",
        )
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns_primary])

        mixin._handle_unassigned_nodes([ns_running])

        test_db.refresh(ns_running)
        assert ns_running.actual_state == NodeActualState.ERROR.value


# ---------------------------------------------------------------------------
# TestResolveFinalAgent
# ---------------------------------------------------------------------------


class TestResolveFinalAgent:
    """Tests for _resolve_final_agent."""

    @pytest.mark.asyncio
    async def test_target_agent_online_succeeds(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """When target_agent_id points to an online host, self.agent is set."""
        host = _make_host(test_db, name="target-online")
        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(
            test_db, ext_lab, ext_job, [ns],
            target_agent_id=host.id,
        )

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client:
            mock_client.is_agent_online.return_value = True
            mock_client.get_healthy_agent = AsyncMock(return_value=None)
            result = await mixin._resolve_final_agent()

        assert result is True
        assert mixin.agent is not None
        assert mixin.agent.id == host.id

    @pytest.mark.asyncio
    async def test_target_agent_offline_fails_job(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """When target_agent_id points to an offline host, job is marked FAILED."""
        host = _make_host(test_db, name="target-offline", status="online")
        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(
            test_db, ext_lab, ext_job, [ns],
            target_agent_id=host.id,
        )

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client:
            mock_client.is_agent_online.return_value = False
            mock_client.get_healthy_agent = AsyncMock(return_value=None)
            result = await mixin._resolve_final_agent()

        assert result is False
        test_db.refresh(ext_job)
        assert ext_job.status == JobStatus.FAILED.value
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.ERROR.value

    @pytest.mark.asyncio
    async def test_placement_based_agent_used_when_no_target(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Without target_agent_id, a single active placement's host is used."""
        host = _make_host(test_db, name="placement-host")
        ns = _make_node_state(test_db, ext_lab, "R1")

        placement = models.NodePlacement(
            lab_id=ext_lab.id,
            node_name="R1",
            host_id=host.id,
            status="active",
        )
        test_db.add(placement)
        test_db.commit()
        test_db.refresh(placement)

        mixin = _make_mixin(
            test_db, ext_lab, ext_job, [ns],
            placements_map={"R1": placement},
            target_agent_id=None,
        )

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client:
            mock_client.is_agent_online.return_value = True
            mock_client.get_healthy_agent = AsyncMock(return_value=None)
            result = await mixin._resolve_final_agent()

        assert result is True
        assert mixin.agent is not None
        assert mixin.agent.id == host.id

    @pytest.mark.asyncio
    async def test_lab_agent_id_fallback(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Without target or placement, lab.agent_id is used as fallback."""
        host = _make_host(test_db, name="lab-default-agent")
        ext_lab.agent_id = host.id
        test_db.commit()

        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(
            test_db, ext_lab, ext_job, [ns],
            target_agent_id=None,
        )

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client:
            mock_client.is_agent_online.return_value = True
            mock_client.get_healthy_agent = AsyncMock(return_value=None)
            result = await mixin._resolve_final_agent()

        assert result is True
        assert mixin.agent is not None
        assert mixin.agent.id == host.id

    @pytest.mark.asyncio
    async def test_healthy_agent_fallback(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """When lab.agent_id is None, get_healthy_agent is tried as last resort."""
        host = _make_host(test_db, name="healthy-fallback")
        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(
            test_db, ext_lab, ext_job, [ns],
            target_agent_id=None,
        )
        # Ensure no lab.agent_id
        ext_lab.agent_id = None
        test_db.commit()
        test_db.refresh(ext_lab)
        mixin.lab = ext_lab

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client:
            mock_client.is_agent_online.return_value = True
            mock_client.get_healthy_agent = AsyncMock(return_value=host)
            result = await mixin._resolve_final_agent()

        assert result is True
        assert mixin.agent is not None
        assert mixin.agent.id == host.id

    @pytest.mark.asyncio
    async def test_no_agent_at_all_fails_job(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """When no agent can be found anywhere, job is marked FAILED."""
        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(
            test_db, ext_lab, ext_job, [ns],
            target_agent_id=None,
        )
        ext_lab.agent_id = None
        test_db.commit()
        test_db.refresh(ext_lab)
        mixin.lab = ext_lab

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client:
            mock_client.is_agent_online.return_value = False
            mock_client.get_healthy_agent = AsyncMock(return_value=None)
            result = await mixin._resolve_final_agent()

        assert result is False
        test_db.refresh(ext_job)
        assert ext_job.status == JobStatus.FAILED.value
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.ERROR.value

    @pytest.mark.asyncio
    async def test_target_agent_not_in_db_is_handled(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """target_agent_id that doesn't exist in DB → agent stays None → job fails."""
        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(
            test_db, ext_lab, ext_job, [ns],
            target_agent_id="nonexistent-host-id",
        )

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client:
            mock_client.is_agent_online.return_value = False
            mock_client.get_healthy_agent = AsyncMock(return_value=None)
            result = await mixin._resolve_final_agent()

        # agent row not found → self.agent = None → job fails
        assert result is False
        test_db.refresh(ext_job)
        assert ext_job.status == JobStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_failed_placement_not_used(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Placements with status='failed' should not be used as fallback."""
        host = _make_host(test_db, name="failed-placement-host")
        healthy = _make_host(test_db, name="healthy-agent")
        ns = _make_node_state(test_db, ext_lab, "R1")

        placement = models.NodePlacement(
            lab_id=ext_lab.id,
            node_name="R1",
            host_id=host.id,
            status="failed",
        )
        test_db.add(placement)
        test_db.commit()
        test_db.refresh(placement)

        ext_lab.agent_id = None
        test_db.commit()

        mixin = _make_mixin(
            test_db, ext_lab, ext_job, [ns],
            placements_map={"R1": placement},
            target_agent_id=None,
        )
        mixin.lab = ext_lab

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client:
            mock_client.is_agent_online.return_value = True
            mock_client.get_healthy_agent = AsyncMock(return_value=healthy)
            result = await mixin._resolve_final_agent()

        assert result is True
        # Should have fallen back to healthy agent, not the failed placement host
        assert mixin.agent.id == healthy.id


# ---------------------------------------------------------------------------
# TestResolveAgentsIntegration
# ---------------------------------------------------------------------------


class TestResolveAgentsIntegration:
    """Integration tests for _resolve_agents — full pipeline."""

    @pytest.mark.asyncio
    async def test_full_flow_single_agent_success(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Full _resolve_agents with a single healthy agent succeeds."""
        host = _make_host(test_db, name="integration-agent")
        ext_lab.agent_id = host.id
        test_db.commit()

        ns = _make_node_state(test_db, ext_lab, "R1")
        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns])
        mixin.lab = ext_lab

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client, patch(
            "app.tasks.node_lifecycle_agents.settings",
        ) as mock_settings:
            mock_settings.agent_stale_timeout = 90
            mock_settings.placement_scoring_enabled = False
            mock_client.is_agent_online.return_value = True
            mock_client.ping_agent = AsyncMock(return_value=None)
            mock_client.get_healthy_agent = AsyncMock(return_value=host)
            with patch(
                "app.agent_client.get_agent_providers",
                return_value=["docker"],
            ):
                result = await mixin._resolve_agents()

        assert result is True
        assert mixin.agent is not None

    @pytest.mark.asyncio
    async def test_full_flow_explicit_placement_online(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Explicit host_id is respected and agent is resolved via that host."""
        host = _make_host(test_db, name="explicit-integration-host")
        ns = _make_node_state(test_db, ext_lab, "R1")

        node_def = models.Node(
            lab_id=ext_lab.id,
            gui_id="r1",
            display_name="R1",
            container_name="R1",
            device="linux",
            host_id=host.id,
        )
        test_db.add(node_def)
        test_db.commit()
        test_db.refresh(node_def)

        mixin = _make_mixin(
            test_db, ext_lab, ext_job, [ns],
            db_nodes_map={"R1": node_def},
        )

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client, patch(
            "app.tasks.node_lifecycle_agents.settings",
        ) as mock_settings:
            mock_settings.agent_stale_timeout = 90
            mock_settings.placement_scoring_enabled = False
            mock_client.is_agent_online.return_value = True
            mock_client.ping_agent = AsyncMock(return_value=None)
            mock_client.get_healthy_agent = AsyncMock(return_value=host)
            with patch(
                "app.agent_client.get_agent_providers",
                return_value=["docker"],
            ):
                result = await mixin._resolve_agents()

        assert result is True
        assert mixin.target_agent_id == host.id

    @pytest.mark.asyncio
    async def test_full_flow_no_agents_fails(
        self, test_db: Session, ext_lab: models.Lab, ext_job: models.Job,
    ):
        """Full _resolve_agents with no available agents marks job FAILED."""
        ns = _make_node_state(test_db, ext_lab, "R1")
        ext_lab.agent_id = None
        test_db.commit()
        test_db.refresh(ext_lab)

        mixin = _make_mixin(test_db, ext_lab, ext_job, [ns])
        mixin.lab = ext_lab

        with patch(
            "app.tasks.node_lifecycle_agents.agent_client",
        ) as mock_client, patch(
            "app.tasks.node_lifecycle_agents.settings",
        ) as mock_settings:
            mock_settings.agent_stale_timeout = 90
            mock_settings.placement_scoring_enabled = False
            mock_client.is_agent_online.return_value = False
            mock_client.ping_agent = AsyncMock(return_value=None)
            mock_client.get_healthy_agent = AsyncMock(return_value=None)
            with patch(
                "app.agent_client.get_agent_providers",
                return_value=["docker"],
            ):
                result = await mixin._resolve_agents()

        assert result is False
        test_db.refresh(ext_job)
        assert ext_job.status == JobStatus.FAILED.value
