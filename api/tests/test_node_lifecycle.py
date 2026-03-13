"""Tests for NodeLifecycleManager (Phase 2.4).

Tests each phase method independently with mocked dependencies,
plus full execute() orchestration tests for mixed states, multi-agent,
and error isolation.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import models
from app.agent_client import AgentUnavailableError
from app.schemas.lab import CrossHostLink
from app.state import JobStatus, NodeActualState
from app.tasks.node_lifecycle import LifecycleResult, NodeLifecycleManager, _get_container_name
from tests.factories import make_host, make_job, make_lab, make_node, make_node_state, make_placement


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(session, lab, job, node_ids, agent=None, monkeypatch=None):
    """Create a NodeLifecycleManager with common mocks applied."""
    manager = NodeLifecycleManager(session, lab, job, node_ids)
    if agent:
        manager.agent = agent
        manager.target_agent_id = agent.id
    # Disable broadcasts by default in tests
    manager._broadcast_state = MagicMock()
    # broadcast_job_progress is a module-level function, not an instance method
    return manager


# ---------------------------------------------------------------------------
# _get_container_name
# ---------------------------------------------------------------------------


class TestGetContainerName:
    def test_basic(self):
        assert _get_container_name("lab1", "R1") == "archetype-lab1-R1"

    def test_sanitizes_special_chars(self):
        result = _get_container_name("lab/1!@#", "R.1")
        assert "/" not in result
        assert "!" not in result
        assert "@" not in result

    def test_truncates_long_lab_id(self):
        long_id = "a" * 50
        result = _get_container_name(long_id, "R1")
        # Lab ID portion should be at most 20 chars
        assert len(result.split("-")[1]) <= 20


# ---------------------------------------------------------------------------
# LifecycleResult
# ---------------------------------------------------------------------------


class TestLifecycleResult:
    def test_noop(self):
        r = LifecycleResult.noop()
        assert r.success is True
        assert r.error_count == 0
        assert "No action needed" in r.log

    def test_custom(self):
        r = LifecycleResult(success=False, error_count=2, log=["failed"])
        assert r.success is False
        assert r.error_count == 2


# ---------------------------------------------------------------------------
# _get_startup_config
# ---------------------------------------------------------------------------


class TestGetStartupConfig:
    def test_n9kv_prefers_saved_workspace_config(
        self, test_db, test_user, tmp_path
    ):
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        node_def = make_node(
            test_db, lab, "n1", "R1", "R1", device="cisco_n9kv", host_id=host.id
        )
        node_def.active_config_snapshot_id = "snap-1"
        test_db.commit()

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.explicit_snapshots_map = {"snap-1": MagicMock(content="from-active")}
        manager.latest_snapshots_map = {"R1": MagicMock(content="from-latest")}

        ws = tmp_path / lab.id
        cfg = ws / "configs" / "R1" / "startup-config"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("from-workspace", encoding="utf-8")

        with patch("app.tasks.node_lifecycle_deploy.lab_workspace", return_value=ws):
            assert manager._get_startup_config("R1", node_def) == "from-workspace"

    def test_non_n9kv_prefers_active_then_json_then_latest(
        self, test_db, test_user, tmp_path
    ):
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        node_def = make_node(
            test_db, lab, "n1", "R1", "R1", device="linux", host_id=host.id
        )
        node_def.active_config_snapshot_id = "snap-1"
        node_def.config_json = json.dumps({"startup-config": "from-json"})
        test_db.commit()

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.explicit_snapshots_map = {"snap-1": MagicMock(content="from-active")}
        manager.latest_snapshots_map = {"R1": MagicMock(content="from-latest")}

        ws = tmp_path / lab.id
        cfg = ws / "configs" / "R1" / "startup-config"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("from-workspace", encoding="utf-8")

        with patch("app.tasks.node_lifecycle_deploy.lab_workspace", return_value=ws):
            assert manager._get_startup_config("R1", node_def) == "from-active"

            node_def.active_config_snapshot_id = None
            assert manager._get_startup_config("R1", node_def) == "from-json"

            node_def.config_json = None
            assert manager._get_startup_config("R1", node_def) == "from-latest"

    def test_non_n9kv_falls_back_to_saved_workspace_config(
        self, test_db, test_user, tmp_path
    ):
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        node_def = make_node(
            test_db, lab, "n1", "R1", "R1", device="linux", host_id=host.id
        )
        node_def.active_config_snapshot_id = None
        node_def.config_json = None
        test_db.commit()

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.explicit_snapshots_map = {}
        manager.latest_snapshots_map = {}

        ws = tmp_path / lab.id
        cfg = ws / "configs" / "R1" / "startup-config"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("from-workspace", encoding="utf-8")

        with patch("app.tasks.node_lifecycle_deploy.lab_workspace", return_value=ws):
            assert manager._get_startup_config("R1", node_def) == "from-workspace"


# ---------------------------------------------------------------------------
# _load_and_validate
# ---------------------------------------------------------------------------


class TestLoadAndValidate:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_node_states(self, test_db, test_user):
        """If no NodeState rows exist for the given node_ids, returns False."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        manager = _make_manager(test_db, lab, job, ["nonexistent-id"])

        with patch.object(manager.topo_service, "get_nodes", return_value=[]):
            result = await manager._load_and_validate()

        assert result is False
        assert job.status == JobStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_returns_false_when_all_in_desired_state(self, test_db, test_user):
        """If all nodes are already in desired state, returns False."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        # Node wants running and IS running
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        with patch.object(manager.topo_service, "get_nodes", return_value=[]):
            result = await manager._load_and_validate()

        assert result is False
        assert job.status == JobStatus.COMPLETED.value
        assert "already in desired state" in job.log_path

    @pytest.mark.asyncio
    async def test_returns_true_when_nodes_need_action(self, test_db, test_user):
        """Nodes needing action cause _load_and_validate to return True."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        # Node wants running but is undeployed
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        with patch.object(manager.topo_service, "get_nodes", return_value=[node_def]):
            result = await manager._load_and_validate()

        assert result is True
        assert len(manager.node_states) == 1
        assert "R1" in manager.db_nodes_map

    @pytest.mark.asyncio
    async def test_stopped_desired_already_stopped(self, test_db, test_user):
        """Stopped nodes with desired=stopped need no action."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="stopped")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        with patch.object(manager.topo_service, "get_nodes", return_value=[]):
            result = await manager._load_and_validate()

        assert result is False

    @pytest.mark.asyncio
    async def test_fixes_placeholder_node_name(self, test_db, test_user):
        """Placeholder node_name (equals node_id) gets fixed to container_name."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        # node_name == node_id indicates a placeholder
        ns = make_node_state(test_db, lab, "gui-id-1", "gui-id-1", desired="running", actual="undeployed")
        node_def = make_node(test_db, lab, "gui-id-1", "R1", "archetype-test-R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        with patch.object(manager.topo_service, "get_nodes", return_value=[node_def]):
            result = await manager._load_and_validate()

        assert result is True
        assert ns.node_name == "archetype-test-R1"

    @pytest.mark.asyncio
    async def test_batch_loads_maps(self, test_db, test_user):
        """Batch-loaded maps are populated correctly."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)
        make_placement(test_db, lab, "R1", host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        with patch.object(manager.topo_service, "get_nodes", return_value=[node_def]):
            await manager._load_and_validate()

        assert "R1" in manager.db_nodes_map
        assert "n1" in manager.db_nodes_by_gui_id
        assert "R1" in manager.placements_map
        assert "R1" in manager.all_lab_states


# ---------------------------------------------------------------------------
# _set_transitional_states
# ---------------------------------------------------------------------------


class TestSetTransitionalStates:
    @pytest.mark.asyncio
    async def test_undeployed_to_pending(self, test_db, test_user):
        """Undeployed node wanting running → pending."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]

        await manager._set_transitional_states()
        assert ns.actual_state == NodeActualState.PENDING.value
        assert ns.error_message is None

    @pytest.mark.asyncio
    async def test_stopped_to_starting(self, test_db, test_user):
        """Stopped node wanting running → starting."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]

        await manager._set_transitional_states()
        assert ns.actual_state == NodeActualState.STARTING.value
        assert ns.starting_started_at is not None

    @pytest.mark.asyncio
    async def test_running_to_stopping(self, test_db, test_user):
        """Running node wanting stopped → stopping."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]

        await manager._set_transitional_states()
        assert ns.actual_state == NodeActualState.STOPPING.value
        assert ns.stopping_started_at is not None

    @pytest.mark.asyncio
    async def test_error_to_pending(self, test_db, test_user):
        """Error node wanting running → pending (retry via state machine)."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="error")
        ns.error_message = "Previous failure"
        test_db.commit()

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]

        await manager._set_transitional_states()
        assert ns.actual_state == NodeActualState.PENDING.value
        assert ns.error_message is None

    @pytest.mark.asyncio
    async def test_broadcasts_state_change(self, test_db, test_user):
        """State changes are broadcast via WebSocket."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]

        await manager._set_transitional_states()
        manager._broadcast_state.assert_called_once_with(ns)

    @pytest.mark.asyncio
    async def test_no_broadcast_when_no_change(self, test_db, test_user):
        """No broadcast if state doesn't change."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        # Already running, wants running — no transition
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]

        await manager._set_transitional_states()
        manager._broadcast_state.assert_not_called()


# ---------------------------------------------------------------------------
# _resolve_agents
# ---------------------------------------------------------------------------


class TestResolveAgents:
    @pytest.mark.asyncio
    async def test_explicit_host_honored(self, test_db, test_user):
        """Node with explicit host_id → that agent is used."""
        host = make_host(test_db, "host-a", "Host A")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": test_db.query(models.Node).filter_by(container_name="R1").first()}
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_agents.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.ping_agent = AsyncMock(return_value=True)
            mock_ac.get_healthy_agent = AsyncMock(return_value=None)
            result = await manager._resolve_agents()

        assert result is True
        assert manager.agent.id == host.id

    @pytest.mark.asyncio
    async def test_explicit_host_offline_fails(self, test_db, test_user):
        """Explicit host that is offline → job fails."""
        host = make_host(test_db, "host-a", "Host A", status="offline")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": test_db.query(models.Node).filter_by(container_name="R1").first()}
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_agents.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=False)
            result = await manager._resolve_agents()

        assert result is False
        assert job.status == JobStatus.FAILED.value
        assert ns.actual_state == NodeActualState.ERROR.value

    @pytest.mark.asyncio
    async def test_no_agent_available_fails(self, test_db, test_user):
        """No agents available → job fails."""
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {}
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_agents.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=False)
            mock_ac.get_healthy_agent = AsyncMock(return_value=None)
            mock_ac.get_agent_for_node = AsyncMock(return_value=None)
            result = await manager._resolve_agents()

        assert result is False
        assert job.status == JobStatus.FAILED.value
        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "No agent available"

    @pytest.mark.asyncio
    async def test_placement_affinity(self, test_db, test_user):
        """Node with existing placement → uses that agent."""
        host = make_host(test_db, "host-a", "Host A")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        make_placement(test_db, lab, "R1", host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {}
        manager.placements_map = {"R1": test_db.query(models.NodePlacement).first()}

        with patch("app.tasks.node_lifecycle_agents.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.ping_agent = AsyncMock(return_value=True)
            mock_ac.get_healthy_agent = AsyncMock(return_value=None)
            result = await manager._resolve_agents()

        assert result is True
        assert manager.agent.id == host.id

    @pytest.mark.asyncio
    async def test_stale_sticky_placement_is_evicted_and_reassigned(self, test_db, test_user):
        """Unreachable sticky placements should be marked failed and re-homed."""
        stale_host = make_host(test_db, "host-a", "Host A")
        fallback_host = make_host(test_db, "host-b", "Host B")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        placement = make_placement(test_db, lab, "R1", stale_host.id, status="deployed")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {}
        manager.placements_map = {"R1": placement}

        async def _ping(agent):
            if agent.id == stale_host.id:
                raise AgentUnavailableError("unreachable")
            return True

        with patch("app.tasks.node_lifecycle_agents.settings.placement_scoring_enabled", False):
            with patch("app.tasks.node_lifecycle_agents.agent_client") as mock_ac:
                mock_ac.is_agent_online = MagicMock(return_value=True)
                mock_ac.ping_agent = AsyncMock(side_effect=_ping)
                mock_ac.get_healthy_agent = AsyncMock(return_value=fallback_host)
                mock_ac.get_agent_for_node = AsyncMock(return_value=fallback_host)
                result = await manager._resolve_agents()

        test_db.refresh(placement)
        assert result is True
        assert manager.agent.id == fallback_host.id
        assert placement.status == "failed"

    @pytest.mark.asyncio
    async def test_resolve_final_agent_skips_failed_placement_affinity(self, test_db, test_user):
        """Failed sticky placements must not be reused as affinity."""
        stale_host = make_host(test_db, "host-a", "Host A")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        placement = make_placement(test_db, lab, "R1", stale_host.id, status="failed")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.placements_map = {"R1": placement}
        manager.target_agent_id = None

        with patch("app.tasks.node_lifecycle_agents.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.get_healthy_agent = AsyncMock(return_value=None)
            result = await manager._resolve_final_agent()

        assert result is False
        assert job.status == JobStatus.FAILED.value
        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "No agent available"

    @pytest.mark.asyncio
    async def test_multi_agent_spawns_sub_jobs(self, test_db, test_user):
        """Nodes on different agents → spawns sub-jobs for other agents."""
        host_a = make_host(test_db, "host-a", "Host A")
        host_b = make_host(test_db, "host-b", "Host B")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)

        ns1 = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        ns2 = make_node_state(test_db, lab, "n2", "R2", desired="running", actual="undeployed")
        make_node(test_db, lab, "n1", "R1", "R1", host_id=host_a.id)
        make_node(test_db, lab, "n2", "R2", "R2", host_id=host_b.id)

        manager = _make_manager(test_db, lab, job, [ns1.node_id, ns2.node_id])
        manager.node_states = [ns1, ns2]
        manager.db_nodes_map = {
            "R1": test_db.query(models.Node).filter_by(container_name="R1").first(),
            "R2": test_db.query(models.Node).filter_by(container_name="R2").first(),
        }
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_agents.agent_client") as mock_ac, \
             patch("app.tasks.node_lifecycle_agents.safe_create_task"):
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.ping_agent = AsyncMock(return_value=True)
            mock_ac.get_healthy_agent = AsyncMock(return_value=None)
            result = await manager._resolve_agents()

        assert result is True
        # Manager handles one agent, spawns sub-job for the other
        assert len(manager.node_states) == 1
        # A sub-job was created in the database
        sub_jobs = test_db.query(models.Job).filter(
            models.Job.parent_job_id == job.id
        ).all()
        assert len(sub_jobs) == 1

    @pytest.mark.asyncio
    async def test_explicit_host_honored_when_node_state_name_stale(self, test_db, test_user):
        """Explicit host_id must be honored even if NodeState.node_name is stale."""
        host = make_host(test_db, "host-a", "Host A")
        fallback_host = make_host(test_db, "host-b", "Host B")
        lab = make_lab(test_db, test_user, agent_id=fallback_host.id)
        job = make_job(test_db, lab, test_user)

        # NodeState has stale node_name, but node_id still points to the correct node.
        ns = make_node_state(test_db, lab, "gui-n9kv", "stale-name", desired="running", actual="undeployed")
        make_node(test_db, lab, "gui-n9kv", "N9KV", "n9kv", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {}
        manager.db_nodes_by_gui_id = {
            "gui-n9kv": test_db.query(models.Node).filter_by(gui_id="gui-n9kv").first()
        }
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_agents.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.ping_agent = AsyncMock(return_value=True)
            mock_ac.get_healthy_agent = AsyncMock(return_value=None)
            result = await manager._resolve_agents()

        assert result is True
        assert manager.agent.id == host.id


# ---------------------------------------------------------------------------
# Hard preflight gates
# ---------------------------------------------------------------------------


class TestHardPreflightGates:
    def test_assigned_host_health_gate_fails_before_transitional_changes(self, test_db, test_user):
        host = make_host(test_db, "host-a", "Host A", status="offline")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node}
        manager.db_nodes_by_gui_id = {"n1": node}

        result = manager._check_assigned_host_health_gate()

        assert result is False
        assert job.status == JobStatus.FAILED.value
        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Assigned host unavailable: Host A"
        assert "Assigned host availability check failed" in manager.log_parts[1]

    @pytest.mark.asyncio
    async def test_execute_stops_before_transitional_states_when_preflight_fails(self, test_db, test_user):
        host = make_host(test_db, "host-a", "Host A", status="offline")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id])

        with patch.object(manager, "_set_transitional_states", new_callable=AsyncMock) as mock_set_transitional:
            result = await manager.execute()

        test_db.refresh(ns)
        assert result.success is False
        mock_set_transitional.assert_not_awaited()
        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Assigned host unavailable: Host A"

    @pytest.mark.asyncio
    async def test_assigned_host_image_gate_fails_before_transitional_changes(self, test_db, test_user):
        host = make_host(test_db, "host-a", "Host A", status="online")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id, device="linux")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node}
        manager.db_nodes_by_gui_id = {"n1": node}

        with patch("app.tasks.image_sync.ensure_images_for_deployment", new_callable=AsyncMock) as mock_ensure, \
             patch("app.tasks.node_lifecycle.resolve_node_image", return_value="linux:latest"):
            mock_ensure.return_value = (False, ["linux:latest"], ["Checking 1 image(s) on agent Host A..."])
            result = await manager._check_assigned_host_image_gate()

        assert result is False
        assert job.status == JobStatus.FAILED.value
        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Required image not available on assigned host: Host A"

    @pytest.mark.asyncio
    async def test_execute_stops_before_transitional_states_when_image_preflight_fails(self, test_db, test_user):
        host = make_host(test_db, "host-a", "Host A", status="online")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id, device="linux")

        manager = _make_manager(test_db, lab, job, [ns.node_id])

        with patch.object(manager, "_set_transitional_states", new_callable=AsyncMock) as mock_set_transitional, \
             patch("app.tasks.image_sync.ensure_images_for_deployment", new_callable=AsyncMock) as mock_ensure, \
             patch("app.tasks.node_lifecycle.resolve_node_image", return_value="linux:latest"):
            mock_ensure.return_value = (False, ["linux:latest"], ["Checking 1 image(s) on agent Host A..."])
            result = await manager.execute()

        test_db.refresh(ns)
        assert result.success is False
        mock_set_transitional.assert_not_awaited()
        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Required image not available on assigned host: Host A"

    @pytest.mark.asyncio
    async def test_runtime_conflict_gate_fails_before_transitional_changes(self, test_db, test_user):
        host = make_host(test_db, "host-a", "Host A", status="online")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id, device="linux")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.agent = host
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node}
        manager.db_nodes_by_gui_id = {"n1": node}

        with patch("app.tasks.node_lifecycle.agent_client.probe_runtime_conflict_on_agent", new_callable=AsyncMock) as mock_probe:
            mock_probe.return_value = {
                "available": False,
                "classification": "foreign",
                "error": "Container archetype-test-r1 is not managed by Archetype",
            }
            result = await manager._check_runtime_namespace_gate()

        assert result is False
        assert job.status == JobStatus.FAILED.value
        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Container archetype-test-r1 is not managed by Archetype"

    @pytest.mark.asyncio
    async def test_execute_stops_before_transitional_states_when_runtime_conflict_preflight_fails(self, test_db, test_user):
        host = make_host(test_db, "host-a", "Host A", status="online")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id, device="linux")

        manager = _make_manager(test_db, lab, job, [ns.node_id])

        with patch.object(manager, "_check_assigned_host_image_gate", new_callable=AsyncMock, return_value=True), \
             patch.object(manager, "_set_transitional_states", new_callable=AsyncMock) as mock_set_transitional, \
             patch("app.tasks.node_lifecycle.agent_client.probe_runtime_conflict_on_agent", new_callable=AsyncMock) as mock_probe:
            mock_probe.return_value = {
                "available": False,
                "classification": "stale_managed",
                "error": "Container archetype-test-r1 belongs to a different managed node identity",
            }
            result = await manager.execute()

        test_db.refresh(ns)
        assert result.success is False
        mock_set_transitional.assert_not_awaited()
        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Container archetype-test-r1 belongs to a different managed node identity"

    @pytest.mark.asyncio
    async def test_cross_host_capacity_gate_fails_before_transitional_changes(self, test_db, test_user):
        host_a = make_host(test_db, "host-a", "Host A", status="online")
        host_b = make_host(test_db, "host-b", "Host B", status="online")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        peer_ns = make_node_state(test_db, lab, "n2", "R2", desired="running", actual="running")
        node = make_node(test_db, lab, "n1", "R1", "R1", host_id=host_a.id, device="linux")
        make_node(test_db, lab, "n2", "R2", "R2", host_id=host_b.id, device="linux")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node}
        manager.db_nodes_by_gui_id = {"n1": node}
        manager.all_lab_states = {"R1": ns, "R2": peer_ns}

        manager.topo_service.get_cross_host_links = MagicMock(return_value=[
            CrossHostLink(
                link_id="R1:eth1-R2:eth1",
                node_a="R1",
                interface_a="eth1",
                host_a=host_a.id,
                node_b="R2",
                interface_b="eth1",
                host_b=host_b.id,
            )
        ])

        with patch("app.tasks.node_lifecycle.agent_client.get_ovs_status_from_agent", new_callable=AsyncMock) as mock_status:
            mock_status.return_value = {"initialized": True, "vlan_allocations": 3901}
            result = await manager._check_cross_host_link_capacity_gate()

        assert result is False
        assert job.status == JobStatus.FAILED.value
        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Cross-host link capacity unavailable"

    @pytest.mark.asyncio
    async def test_execute_stops_before_transitional_states_when_cross_host_capacity_preflight_fails(self, test_db, test_user):
        host_a = make_host(test_db, "host-a", "Host A", status="online")
        host_b = make_host(test_db, "host-b", "Host B", status="online")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        make_node_state(test_db, lab, "n2", "R2", desired="running", actual="running")
        make_node(test_db, lab, "n1", "R1", "R1", host_id=host_a.id, device="linux")
        make_node(test_db, lab, "n2", "R2", "R2", host_id=host_b.id, device="linux")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.topo_service.get_cross_host_links = MagicMock(return_value=[
            CrossHostLink(
                link_id="R1:eth1-R2:eth1",
                node_a="R1",
                interface_a="eth1",
                host_a=host_a.id,
                node_b="R2",
                interface_b="eth1",
                host_b=host_b.id,
            )
        ])

        with patch.object(manager, "_check_assigned_host_image_gate", new_callable=AsyncMock, return_value=True), \
             patch("app.tasks.node_lifecycle.agent_client.probe_runtime_conflict_on_agent", new_callable=AsyncMock) as mock_probe, \
             patch("app.tasks.node_lifecycle.agent_client.get_ovs_status_from_agent", new_callable=AsyncMock) as mock_status, \
             patch.object(manager, "_set_transitional_states", new_callable=AsyncMock) as mock_set_transitional:
            mock_probe.return_value = {"available": True, "classification": "absent"}
            mock_status.return_value = {"initialized": True, "vlan_allocations": 3901}
            result = await manager.execute()

        test_db.refresh(ns)
        assert result.success is False
        mock_set_transitional.assert_not_awaited()
        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Cross-host link capacity unavailable"

    @pytest.mark.asyncio
    async def test_assigned_host_image_gate_reports_sync_disabled(self, test_db, test_user):
        host = make_host(test_db, "host-a", "Host A", status="online")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id, device="linux")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node}
        manager.db_nodes_by_gui_id = {"n1": node}

        with patch("app.tasks.image_sync.ensure_images_for_deployment", new_callable=AsyncMock) as mock_ensure, \
             patch("app.tasks.node_lifecycle.resolve_node_image", return_value="linux:latest"):
            mock_ensure.return_value = (
                False,
                ["linux:latest"],
                ["Image sync is disabled for this agent"],
            )
            result = await manager._check_assigned_host_image_gate()

        assert result is False
        assert ns.error_message == "Image sync disabled on assigned host: Host A"

    @pytest.mark.asyncio
    async def test_assigned_host_image_gate_reports_missing_library_entry(self, test_db, test_user):
        host = make_host(test_db, "host-a", "Host A", status="online")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id, device="linux")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node}
        manager.db_nodes_by_gui_id = {"n1": node}

        with patch("app.tasks.image_sync.ensure_images_for_deployment", new_callable=AsyncMock) as mock_ensure, \
             patch("app.tasks.node_lifecycle.resolve_node_image", return_value="linux:latest"):
            mock_ensure.return_value = (
                False,
                ["linux:latest"],
                ["Missing images not found in library - cannot sync"],
            )
            result = await manager._check_assigned_host_image_gate()

        assert result is False
        assert ns.error_message == "Required image not present in library for assigned host: Host A"

    def test_assigned_host_capacity_gate_fails_before_transitional_changes(self, test_db, test_user, monkeypatch):
        host = make_host(test_db, "host-a", "Host A", status="online")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id, device="linux")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node}
        manager.db_nodes_by_gui_id = {"n1": node}

        from app.tasks import node_lifecycle
        monkeypatch.setattr(node_lifecycle.settings, "resource_validation_enabled", True)

        mock_cap_result = MagicMock(
            fits=False,
            required_memory_mb=4096,
            available_memory_mb=1024,
            has_warnings=False,
        )
        with patch("app.services.resource_capacity.check_capacity", return_value=mock_cap_result), \
             patch("app.services.resource_capacity.format_capacity_error", return_value="Host A: requires 4096MB RAM, 1024MB available"):
            result = manager._check_assigned_host_capacity_gate()

        assert result is False
        assert job.status == JobStatus.FAILED.value
        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Insufficient resources on assigned host"


# ---------------------------------------------------------------------------
# _check_resources
# ---------------------------------------------------------------------------


class TestCheckResources:
    @pytest.mark.asyncio
    async def test_skips_when_disabled(self, test_db, test_user, monkeypatch):
        """Resource validation disabled → returns True immediately."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {}

        from app.tasks import node_lifecycle
        monkeypatch.setattr(node_lifecycle.settings, "resource_validation_enabled", False)

        result = await manager._check_resources()
        assert result is True

    @pytest.mark.asyncio
    async def test_skips_when_no_deploy_candidates(self, test_db, test_user, monkeypatch):
        """No nodes needing deploy → returns True."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        # Node is stopping, not deploying
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        from app.tasks import node_lifecycle
        monkeypatch.setattr(node_lifecycle.settings, "resource_validation_enabled", True)

        result = await manager._check_resources()
        assert result is True

    @pytest.mark.asyncio
    async def test_insufficient_resources_fails(self, test_db, test_user, monkeypatch):
        """Insufficient resources → error state, job failed."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node_def = make_node(test_db, lab, "n1", "R1", "R1")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}

        from app.tasks import node_lifecycle
        monkeypatch.setattr(node_lifecycle.settings, "resource_validation_enabled", True)

        # Mock capacity check to fail (catastrophic: available < 50% of required)
        mock_cap_result = MagicMock()
        mock_cap_result.fits = False
        mock_cap_result.required_memory_mb = 4096
        mock_cap_result.available_memory_mb = 1024
        with patch("app.services.resource_capacity.check_capacity", return_value=mock_cap_result), \
             patch("app.services.resource_capacity.format_capacity_error", return_value="Not enough RAM"):
            result = await manager._check_resources()

        assert result is False
        assert job.status == JobStatus.FAILED.value
        assert ns.actual_state == NodeActualState.ERROR.value
        assert "Insufficient resources" in ns.error_message

    @pytest.mark.asyncio
    async def test_explicit_host_at_capacity_fails_no_fallback(self, test_db, test_user, monkeypatch):
        """Explicit host_id + host at capacity → ERROR with resource info, no fallback."""
        host = make_host(test_db, "host-a", "Host A")
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}

        from app.tasks import node_lifecycle
        monkeypatch.setattr(node_lifecycle.settings, "resource_validation_enabled", True)

        # Mock capacity check to fail (catastrophic: available < 50% of required)
        mock_cap_result = MagicMock()
        mock_cap_result.fits = False
        mock_cap_result.required_memory_mb = 4096
        mock_cap_result.available_memory_mb = 1024
        with patch("app.services.resource_capacity.check_capacity", return_value=mock_cap_result), \
             patch("app.services.resource_capacity.format_capacity_error", return_value="Host A: requires 4096MB RAM, 2048MB available"):
            result = await manager._check_resources()

        assert result is False
        assert ns.actual_state == NodeActualState.ERROR.value
        assert "Insufficient resources" in ns.error_message
        # Verify no fallback to another host was attempted
        assert manager.agent.id == host.id


# ---------------------------------------------------------------------------
# _categorize_nodes
# ---------------------------------------------------------------------------


class TestCategorizeNodes:
    def test_deploy_start_stop_groups(self, test_db, test_user):
        """Nodes are correctly categorized into deploy/start/stop groups."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)

        ns_deploy = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        ns_start = make_node_state(test_db, lab, "n2", "R2", desired="running", actual="stopped")
        ns_stop = make_node_state(test_db, lab, "n3", "R3", desired="stopped", actual="running")

        manager = _make_manager(test_db, lab, job, ["n1", "n2", "n3"], agent=host)
        manager.node_states = [ns_deploy, ns_start, ns_stop]

        deploy, start, stop = manager._categorize_nodes()

        assert len(deploy) == 1
        assert deploy[0].node_name == "R1"
        assert len(start) == 1
        assert start[0].node_name == "R2"
        assert len(stop) == 1
        assert stop[0].node_name == "R3"

    def test_pending_categorized_as_deploy(self, test_db, test_user):
        """Pending nodes wanting running are categorized as deploy."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)

        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]

        deploy, start, stop = manager._categorize_nodes()
        assert len(deploy) == 1
        assert len(start) == 0

    def test_error_categorized_as_start(self, test_db, test_user):
        """Error nodes wanting running are categorized as start."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)

        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="error")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]

        deploy, start, stop = manager._categorize_nodes()
        assert len(deploy) == 0
        assert len(start) == 1

    def test_stopping_categorized_as_stop(self, test_db, test_user):
        """Stopping nodes wanting stopped are categorized as stop."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)

        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="stopping")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]

        deploy, start, stop = manager._categorize_nodes()
        assert len(stop) == 1


# ---------------------------------------------------------------------------
# _handle_migration
# ---------------------------------------------------------------------------


class TestHandleMigration:
    @pytest.mark.asyncio
    async def test_no_migration_when_same_agent(self, test_db, test_user):
        """No migration needed when node is already on target agent."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        make_placement(test_db, lab, "R1", host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {}
        manager._refresh_placements()

        with patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock) as mock_update:
            await manager._handle_migration([ns])
            mock_update.assert_called_once()

        # No migration log entries
        assert not any("Migration" in p for p in manager.log_parts)

    @pytest.mark.asyncio
    async def test_migration_stops_old_container(self, test_db, test_user):
        """Migration destroys node on old agent before deploying to new."""
        old_host = make_host(test_db, "old-host", "Old Host")
        new_host = make_host(test_db, "new-host", "New Host")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        make_placement(test_db, lab, "R1", old_host.id)
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=new_host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=new_host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager._refresh_placements()

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock):
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.destroy_node_on_agent = AsyncMock(return_value={"success": True})
            await manager._handle_migration([ns])

        mock_ac.destroy_node_on_agent.assert_called()
        call_args = mock_ac.destroy_node_on_agent.call_args
        assert call_args[0][0].id == old_host.id  # old agent
        assert call_args[0][2] == "R1"  # node_name

    @pytest.mark.asyncio
    async def test_migration_deletes_old_placement(self, test_db, test_user):
        """Migration removes old placement records."""
        old_host = make_host(test_db, "old-host", "Old Host")
        new_host = make_host(test_db, "new-host", "New Host")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        make_placement(test_db, lab, "R1", old_host.id)
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=new_host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=new_host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager._refresh_placements()

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock):
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.destroy_node_on_agent = AsyncMock(return_value={"success": True})
            await manager._handle_migration([ns])

        # Old placement should be deleted
        old_placements = test_db.query(models.NodePlacement).filter_by(
            host_id=old_host.id
        ).all()
        assert len(old_placements) == 0

    @pytest.mark.asyncio
    async def test_migration_offline_old_agent_queues_cleanup(self, test_db, test_user):
        """Offline old agent queues deferred cleanup while moving placement."""
        old_host = make_host(test_db, "old-host", "Old Host", status="offline")
        new_host = make_host(test_db, "new-host", "New Host")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        make_placement(test_db, lab, "R1", old_host.id)
        make_node(test_db, lab, "n1", "R1", "R1", host_id=new_host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=new_host)
        manager.node_states = [ns]
        manager._refresh_placements()

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock):
            mock_ac.is_agent_online = MagicMock(return_value=False)
            await manager._handle_migration([ns])

        pending = (
            test_db.query(models.NodeMigrationCleanup)
            .filter_by(lab_id=lab.id, node_name="R1", old_host_id=old_host.id)
            .first()
        )
        assert pending is not None
        assert pending.status == "pending"

        old_placements = test_db.query(models.NodePlacement).filter_by(
            host_id=old_host.id
        ).all()
        assert len(old_placements) == 0


# ---------------------------------------------------------------------------
# _stop_nodes
# ---------------------------------------------------------------------------


class TestStopNodes:
    @pytest.mark.asyncio
    async def test_successful_stop(self, test_db, test_user):
        """Successful stop sets stopped state."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        container_name = _get_container_name(lab.id, "R1")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.reconcile_nodes_on_agent = AsyncMock(return_value={
                "results": [{"container_name": container_name, "success": True}]
            })
            await manager._stop_nodes([ns])

        assert ns.actual_state == NodeActualState.STOPPED.value
        assert ns.error_message is None
        assert ns.is_ready is False

    @pytest.mark.asyncio
    async def test_stop_failure_sets_error(self, test_db, test_user):
        """Failed stop sets error state with error message."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        container_name = _get_container_name(lab.id, "R1")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.reconcile_nodes_on_agent = AsyncMock(return_value={
                "results": [{"container_name": container_name, "success": False, "error": "Container busy"}]
            })
            await manager._stop_nodes([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Container busy"

    @pytest.mark.asyncio
    async def test_stop_uses_placement_agent(self, test_db, test_user):
        """Stop uses actual container location from placements."""
        main_host = make_host(test_db, "main-host", "Main Host")
        actual_host = make_host(test_db, "actual-host", "Actual Host")
        lab = make_lab(test_db, test_user, agent_id=main_host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        make_placement(test_db, lab, "R1", actual_host.id)
        container_name = _get_container_name(lab.id, "R1")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=main_host)
        manager.node_states = [ns]
        manager._refresh_placements()

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.reconcile_nodes_on_agent = AsyncMock(return_value={
                "results": [{"container_name": container_name, "success": True}]
            })
            await manager._stop_nodes([ns])

        # Should have called reconcile_nodes_on_agent on actual_host, not main_host
        call_args = mock_ac.reconcile_nodes_on_agent.call_args
        assert call_args[0][0].id == actual_host.id

    @pytest.mark.asyncio
    async def test_transient_error_preserves_state(self, test_db, test_user):
        """AgentUnavailableError keeps current state (transient)."""
        from app.agent_client import AgentUnavailableError

        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.reconcile_nodes_on_agent = AsyncMock(
                side_effect=AgentUnavailableError("Agent timeout")
            )
            await manager._stop_nodes([ns])

        # State preserved (not set to error) — transient
        assert ns.actual_state == "running"
        assert "transient" in ns.error_message


# ---------------------------------------------------------------------------
# _deploy_nodes
# ---------------------------------------------------------------------------


class TestDeployNodes:
    @pytest.mark.asyncio
    async def test_successful_deploy(self, test_db, test_user):
        """Successful deploy marks nodes as running."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager.old_agent_ids = set()

        # Create mock graph
        mock_node = MagicMock()
        mock_node.container_name = "R1"
        mock_node.name = "R1"
        mock_node.id = "n1"
        mock_node.vars = {}
        mock_graph = MagicMock()
        mock_graph.nodes = [mock_node]
        mock_graph.links = []
        mock_graph.defaults = None

        with patch.object(manager.topo_service, "has_nodes", return_value=True), \
             patch.object(manager, "_filter_topology_for_agent", return_value=(mock_graph, {"R1"})), \
             patch.object(manager, "_validate_topology_placement", return_value=[]), \
             patch("app.tasks.node_lifecycle_deploy.graph_to_deploy_topology", return_value={}), \
             patch("app.tasks.jobs.acquire_deploy_lock", return_value=(True, [])), \
             patch("app.tasks.jobs.release_deploy_lock"), \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock), \
             patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock), \
             patch("app.tasks.jobs._cleanup_orphan_containers", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch("app.tasks.node_lifecycle_deploy.settings") as mock_settings:
            mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
                "nodes": [{"name": "R1", "node_definition_id": node_def.id, "runtime_id": "runtime-r1"}]
            })
            await manager._deploy_nodes([ns])

        assert ns.actual_state == NodeActualState.RUNNING.value

    @pytest.mark.asyncio
    async def test_deploy_failure_sets_error(self, test_db, test_user):
        """Deploy failure sets error state on affected nodes."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager.old_agent_ids = set()

        mock_node = MagicMock()
        mock_node.container_name = "R1"
        mock_node.name = "R1"
        mock_node.id = "n1"
        mock_node.vars = {}
        mock_graph = MagicMock()
        mock_graph.nodes = [mock_node]
        mock_graph.links = []

        with patch.object(manager.topo_service, "has_nodes", return_value=True), \
             patch.object(manager, "_filter_topology_for_agent", return_value=(mock_graph, {"R1"})), \
             patch.object(manager, "_validate_topology_placement", return_value=[]), \
             patch("app.tasks.node_lifecycle_deploy.graph_to_deploy_topology", return_value={}), \
             patch("app.tasks.jobs.acquire_deploy_lock", return_value=(True, [])), \
             patch("app.tasks.jobs.release_deploy_lock"), \
             patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch("app.tasks.node_lifecycle_deploy.settings") as mock_settings:
            mock_ac.deploy_to_agent = AsyncMock(
                return_value={"status": "failed", "error_message": "Timeout"}
            )
            await manager._deploy_nodes([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Timeout"

    @pytest.mark.asyncio
    async def test_deploy_lock_conflict_sets_error(self, test_db, test_user):
        """Lock conflict → error state."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager.old_agent_ids = set()

        mock_node = MagicMock()
        mock_node.container_name = "R1"
        mock_node.name = "R1"
        mock_node.id = "n1"
        mock_node.vars = {}
        mock_graph = MagicMock()
        mock_graph.nodes = [mock_node]
        mock_graph.links = []

        with patch.object(manager.topo_service, "has_nodes", return_value=True), \
             patch.object(manager, "_filter_topology_for_agent", return_value=(mock_graph, {"R1"})), \
             patch.object(manager, "_validate_topology_placement", return_value=[]), \
             patch("app.tasks.node_lifecycle_deploy.graph_to_deploy_topology", return_value={}), \
             patch("app.tasks.jobs.acquire_deploy_lock", return_value=(False, ["R1"])), \
             patch("app.tasks.node_lifecycle_deploy.settings") as mock_settings:
            await manager._deploy_nodes([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "Deploy lock conflict" in ns.error_message

    @pytest.mark.asyncio
    async def test_no_topology_sets_error(self, test_db, test_user):
        """No topology defined → error state."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        with patch.object(manager.topo_service, "has_nodes", return_value=False), \
             patch("app.tasks.node_lifecycle_deploy.settings") as mock_settings:
            await manager._deploy_nodes([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "No topology" in ns.error_message

    @pytest.mark.asyncio
    async def test_transient_error_keeps_pending(self, test_db, test_user):
        """AgentUnavailableError → pending (not error), preserves retryability."""
        from app.agent_client import AgentUnavailableError

        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager.old_agent_ids = set()

        mock_node = MagicMock()
        mock_node.container_name = "R1"
        mock_node.name = "R1"
        mock_node.id = "n1"
        mock_node.vars = {}
        mock_graph = MagicMock()
        mock_graph.nodes = [mock_node]
        mock_graph.links = []

        with patch.object(manager.topo_service, "has_nodes", return_value=True), \
             patch.object(manager, "_filter_topology_for_agent", return_value=(mock_graph, {"R1"})), \
             patch.object(manager, "_validate_topology_placement", return_value=[]), \
             patch("app.tasks.node_lifecycle_deploy.graph_to_deploy_topology", return_value={}), \
             patch("app.tasks.jobs.acquire_deploy_lock", return_value=(True, [])), \
             patch("app.tasks.jobs.release_deploy_lock"), \
             patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch("app.tasks.node_lifecycle_deploy.settings") as mock_settings:
            mock_ac.deploy_to_agent = AsyncMock(
                side_effect=AgentUnavailableError("Connection refused")
            )
            await manager._deploy_nodes([ns])

        # Transient error keeps pending, not error
        assert ns.actual_state == NodeActualState.PENDING.value


# ---------------------------------------------------------------------------
# _finalize
# ---------------------------------------------------------------------------


class TestFinalize:
    @pytest.mark.asyncio
    async def test_all_success(self, test_db, test_user):
        """All nodes OK → completed status."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        result = await manager._finalize()

        assert result.success is True
        assert result.error_count == 0
        assert job.status == JobStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_some_errors(self, test_db, test_user):
        """Some nodes in error → failed status with error count."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns_ok = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")
        ns_err = make_node_state(test_db, lab, "n2", "R2", desired="running", actual="error")

        manager = _make_manager(test_db, lab, job, [ns_ok.node_id, ns_err.node_id], agent=host)
        manager.node_states = [ns_ok, ns_err]

        result = await manager._finalize()

        assert result.success is False
        assert result.error_count == 1
        assert job.status == JobStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_sets_completed_at(self, test_db, test_user):
        """Finalize always sets completed_at."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        await manager._finalize()

        assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_stopped_desired_error_converges_to_stopped(self, test_db, test_user):
        """Guard: desired=stopped must not remain stuck in error at finalize."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="error")
        ns.error_message = "sync failed"
        ns.enforcement_attempts = 3
        test_db.commit()

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        result = await manager._finalize()

        assert result.success is True
        assert result.error_count == 0
        assert job.status == JobStatus.COMPLETED.value
        assert ns.actual_state == NodeActualState.STOPPED.value
        assert ns.error_message is None
        assert ns.enforcement_attempts == 0

    @pytest.mark.asyncio
    async def test_finalize_recomputes_lab_state_from_current_nodes(self, test_db, test_user):
        """Successful finalize clears stale lab-level error residue."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        lab.state = "error"
        lab.state_error = "Old job failed"
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")
        test_db.commit()

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        result = await manager._finalize()
        test_db.refresh(lab)

        assert result.success is True
        assert lab.state == "running"
        assert lab.state_error is None


# ---------------------------------------------------------------------------
# Full execute() orchestration
# ---------------------------------------------------------------------------


class TestExecuteOrchestration:
    @pytest.mark.asyncio
    async def test_noop_when_all_in_desired_state(self, test_db, test_user):
        """Execute returns noop when all nodes already in desired state."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")

        manager = _make_manager(test_db, lab, job, ["n1"])
        with patch.object(manager.topo_service, "get_nodes", return_value=[]):
            result = await manager.execute()

        assert result.success is True
        assert job.status == JobStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_deploy_flow(self, test_db, test_user):
        """Full deploy flow: undeployed → pending → running."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1"])

        mock_node = MagicMock()
        mock_node.container_name = "R1"
        mock_node.name = "R1"
        mock_node.id = "n1"
        mock_node.vars = {}
        mock_graph = MagicMock()
        mock_graph.nodes = [mock_node]
        mock_graph.links = []
        mock_graph.defaults = None

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.ping_agent = AsyncMock(return_value=True)
        mock_ac.get_healthy_agent = AsyncMock(return_value=None)
        mock_ac.probe_runtime_conflict_on_agent = AsyncMock(
            return_value={"available": True, "classification": "absent"}
        )
        mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})
        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
            "nodes": [{"name": "R1", "node_definition_id": node_def.id, "runtime_id": "runtime-r1"}]
        })
        mock_ac.check_node_readiness = AsyncMock(return_value={"is_ready": True})
        mock_settings = MagicMock()
        mock_settings.resource_validation_enabled = False
        mock_settings.image_sync_pre_deploy_check = False

        with patch.object(manager.topo_service, "get_nodes", return_value=[node_def]), \
             patch("app.tasks.node_lifecycle.agent_client", mock_ac), \
             patch("app.tasks.node_lifecycle_agents.agent_client", mock_ac), \
             patch("app.tasks.node_lifecycle_deploy.agent_client", mock_ac), \
             patch("app.tasks.node_lifecycle_stop.agent_client", mock_ac), \
             patch.object(manager.topo_service, "has_nodes", return_value=True), \
             patch.object(manager, "_filter_topology_for_agent", return_value=(mock_graph, {"R1"})), \
             patch.object(manager, "_validate_topology_placement", return_value=[]), \
             patch("app.tasks.node_lifecycle_deploy.graph_to_deploy_topology", return_value={}), \
             patch("app.tasks.jobs.acquire_deploy_lock", return_value=(True, [])), \
             patch("app.tasks.jobs.release_deploy_lock"), \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock), \
             patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock), \
             patch("app.tasks.jobs._cleanup_orphan_containers", new_callable=AsyncMock), \
             patch("app.tasks.jobs._create_cross_host_links_if_ready", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle.settings", mock_settings), \
             patch("app.tasks.node_lifecycle_deploy.settings", mock_settings), \
             patch("app.tasks.node_lifecycle_agents.settings", mock_settings):

            result = await manager.execute()

        assert result.success is True
        assert job.status == JobStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_stop_flow(self, test_db, test_user):
        """Full stop flow: running → stopping → stopped."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)
        container_name = _get_container_name(lab.id, "R1")

        manager = _make_manager(test_db, lab, job, ["n1"])

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.ping_agent = AsyncMock(return_value=True)
        mock_ac.get_healthy_agent = AsyncMock(return_value=None)
        mock_ac.reconcile_nodes_on_agent = AsyncMock(return_value={
            "results": [{"container_name": container_name, "success": True}]
        })
        mock_settings = MagicMock()
        mock_settings.resource_validation_enabled = False
        mock_settings.image_sync_pre_deploy_check = False

        with patch.object(manager.topo_service, "get_nodes", return_value=[node_def]), \
             patch("app.tasks.node_lifecycle.agent_client", mock_ac), \
             patch("app.tasks.node_lifecycle_agents.agent_client", mock_ac), \
             patch("app.tasks.node_lifecycle_deploy.agent_client", mock_ac), \
             patch("app.tasks.node_lifecycle_stop.agent_client", mock_ac), \
             patch("app.tasks.jobs._create_cross_host_links_if_ready", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle.settings", mock_settings), \
             patch("app.tasks.node_lifecycle_deploy.settings", mock_settings), \
             patch("app.tasks.node_lifecycle_agents.settings", mock_settings):

            result = await manager.execute()

        assert result.success is True
        assert ns.actual_state == NodeActualState.STOPPED.value
        assert job.status == JobStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_mixed_deploy_and_stop(self, test_db, test_user):
        """Mixed: one node deploys, another stops — both succeed."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        make_node_state(test_db, lab, "n2", "R2", desired="stopped", actual="running")
        node_def1 = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)
        node_def2 = make_node(test_db, lab, "n2", "R2", "R2", host_id=host.id)
        container_name_r2 = _get_container_name(lab.id, "R2")

        manager = _make_manager(test_db, lab, job, ["n1", "n2"])

        mock_node1 = MagicMock()
        mock_node1.container_name = "R1"
        mock_node1.name = "R1"
        mock_node1.id = "n1"
        mock_node1.vars = {}
        mock_node2 = MagicMock()
        mock_node2.container_name = "R2"
        mock_node2.name = "R2"
        mock_node2.id = "n2"
        mock_node2.vars = {}
        mock_graph = MagicMock()
        mock_graph.nodes = [mock_node1, mock_node2]
        mock_graph.links = []
        mock_graph.defaults = None

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.ping_agent = AsyncMock(return_value=True)
        mock_ac.get_healthy_agent = AsyncMock(return_value=None)
        mock_ac.probe_runtime_conflict_on_agent = AsyncMock(
            return_value={"available": True, "classification": "absent"}
        )
        mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})
        mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
            "nodes": [{"name": "R1", "node_definition_id": node_def1.id, "runtime_id": "runtime-r1"}]
        })
        mock_ac.check_node_readiness = AsyncMock(return_value={"is_ready": True})
        mock_ac.reconcile_nodes_on_agent = AsyncMock(return_value={
            "results": [{"container_name": container_name_r2, "success": True}]
        })
        mock_settings = MagicMock()
        mock_settings.resource_validation_enabled = False
        mock_settings.image_sync_pre_deploy_check = False

        with patch.object(manager.topo_service, "get_nodes", return_value=[node_def1, node_def2]), \
             patch("app.tasks.node_lifecycle.agent_client", mock_ac), \
             patch("app.tasks.node_lifecycle_agents.agent_client", mock_ac), \
             patch("app.tasks.node_lifecycle_deploy.agent_client", mock_ac), \
             patch("app.tasks.node_lifecycle_stop.agent_client", mock_ac), \
             patch.object(manager.topo_service, "has_nodes", return_value=True), \
             patch.object(manager, "_filter_topology_for_agent", return_value=(mock_graph, {"R1"})), \
             patch.object(manager, "_validate_topology_placement", return_value=[]), \
             patch("app.tasks.node_lifecycle_deploy.graph_to_deploy_topology", return_value={}), \
             patch("app.tasks.jobs.acquire_deploy_lock", return_value=(True, [])), \
             patch("app.tasks.jobs.release_deploy_lock"), \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock), \
             patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock), \
             patch("app.tasks.jobs._cleanup_orphan_containers", new_callable=AsyncMock), \
             patch("app.tasks.jobs._create_cross_host_links_if_ready", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle.settings", mock_settings), \
             patch("app.tasks.node_lifecycle_deploy.settings", mock_settings), \
             patch("app.tasks.node_lifecycle_agents.settings", mock_settings):

            result = await manager.execute()

        assert result.success is True
        assert job.status == JobStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_resource_check_before_migration(self, test_db, test_user, monkeypatch):
        """Phase 2.2: Resource check runs BEFORE migration."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1"])

        call_order = []

        async def mock_check_resources():
            call_order.append("check_resources")
            return False  # Fail resources

        async def mock_handle_migration(nodes):
            call_order.append("handle_migration")

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.ping_agent = AsyncMock(return_value=True)
        mock_ac.get_healthy_agent = AsyncMock(return_value=None)
        mock_ac.probe_runtime_conflict_on_agent = AsyncMock(
            return_value={"available": True, "classification": "absent"}
        )
        mock_settings = MagicMock()
        mock_settings.resource_validation_enabled = True
        mock_settings.image_sync_pre_deploy_check = False

        with patch.object(manager.topo_service, "get_nodes", return_value=[node_def]), \
             patch("app.tasks.node_lifecycle.agent_client", mock_ac), \
             patch("app.tasks.node_lifecycle_agents.agent_client", mock_ac), \
             patch("app.tasks.node_lifecycle_deploy.agent_client", mock_ac), \
             patch("app.tasks.node_lifecycle_stop.agent_client", mock_ac), \
             patch("app.tasks.node_lifecycle.settings", mock_settings), \
             patch("app.tasks.node_lifecycle_deploy.settings", mock_settings), \
             patch("app.tasks.node_lifecycle_agents.settings", mock_settings):

            # Override phase methods to track call order
            manager._check_resources = mock_check_resources
            manager._handle_migration = mock_handle_migration

            result = await manager.execute()

        # Resource check was called but migration was NOT
        assert "check_resources" in call_order
        assert "handle_migration" not in call_order
        assert result.success is False

    @pytest.mark.asyncio
    async def test_error_isolation(self, test_db, test_user):
        """Deploy failure for one node doesn't prevent stopping another."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        # R1 will fail to deploy, R2 should still stop
        ns_deploy = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        ns_stop = make_node_state(test_db, lab, "n2", "R2", desired="stopped", actual="running")
        node_def1 = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)
        node_def2 = make_node(test_db, lab, "n2", "R2", "R2", host_id=host.id)
        container_name_r2 = _get_container_name(lab.id, "R2")

        manager = _make_manager(test_db, lab, job, ["n1", "n2"])

        mock_node = MagicMock()
        mock_node.container_name = "R1"
        mock_node.name = "R1"
        mock_node.id = "n1"
        mock_node.vars = {}
        mock_graph = MagicMock()
        mock_graph.nodes = [mock_node]
        mock_graph.links = []
        mock_graph.defaults = None

        mock_ac = MagicMock()
        mock_ac.is_agent_online = MagicMock(return_value=True)
        mock_ac.ping_agent = AsyncMock(return_value=True)
        mock_ac.get_healthy_agent = AsyncMock(return_value=None)
        mock_ac.probe_runtime_conflict_on_agent = AsyncMock(
            return_value={"available": True, "classification": "absent"}
        )
        # Deploy fails
        mock_ac.deploy_to_agent = AsyncMock(
            return_value={"status": "failed", "error_message": "Deploy error"}
        )
        # Stop succeeds (via batch reconcile)
        mock_ac.reconcile_nodes_on_agent = AsyncMock(return_value={
            "results": [{"container_name": container_name_r2, "success": True}]
        })
        mock_settings = MagicMock()
        mock_settings.resource_validation_enabled = False
        mock_settings.image_sync_pre_deploy_check = False

        with patch.object(manager.topo_service, "get_nodes", return_value=[node_def1, node_def2]), \
             patch("app.tasks.node_lifecycle.agent_client", mock_ac), \
             patch("app.tasks.node_lifecycle_agents.agent_client", mock_ac), \
             patch("app.tasks.node_lifecycle_deploy.agent_client", mock_ac), \
             patch("app.tasks.node_lifecycle_stop.agent_client", mock_ac), \
             patch.object(manager.topo_service, "has_nodes", return_value=True), \
             patch.object(manager, "_filter_topology_for_agent", return_value=(mock_graph, {"R1"})), \
             patch.object(manager, "_validate_topology_placement", return_value=[]), \
             patch("app.tasks.node_lifecycle_deploy.graph_to_deploy_topology", return_value={}), \
             patch("app.tasks.jobs.acquire_deploy_lock", return_value=(True, [])), \
             patch("app.tasks.jobs.release_deploy_lock"), \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock), \
             patch("app.tasks.jobs._create_cross_host_links_if_ready", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle.settings", mock_settings), \
             patch("app.tasks.node_lifecycle_deploy.settings", mock_settings), \
             patch("app.tasks.node_lifecycle_agents.settings", mock_settings):

            result = await manager.execute()

        # R1 failed deploy, R2 stopped successfully — overall failed due to R1
        assert result.success is False
        assert ns_deploy.actual_state == NodeActualState.ERROR.value
        assert ns_stop.actual_state == NodeActualState.STOPPED.value


# ---------------------------------------------------------------------------
# Per-node lifecycle (Phase 3)
# ---------------------------------------------------------------------------


class TestDeployNodesPerNode:
    """Test _deploy_nodes_per_node — per-node container create+start."""

    @pytest.mark.asyncio
    async def test_deploy_per_node_success(self, test_db, test_user):
        """Per-node deploy: creates and starts each node individually."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", device="linux", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager._manifest = None
        manager.latest_snapshots_map = {}
        manager.explicit_snapshots_map = {}

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock), \
             patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock), \
             patch.object(manager.topo_service, "get_interface_count_map", return_value={"R1": 4}), \
             patch.object(manager, "_connect_same_host_links", new_callable=AsyncMock):
            mock_ac.create_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.start_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
                "nodes": [{"name": "R1", "node_definition_id": node_def.id, "runtime_id": "runtime-r1"}]
            })
            await manager._deploy_nodes_per_node([ns])

        assert ns.actual_state == NodeActualState.RUNNING.value
        assert ns.error_message is None
        assert ns.boot_started_at is not None
        mock_ac.create_node_on_agent.assert_called_once()
        mock_ac.start_node_on_agent.assert_called_once()

    @pytest.mark.asyncio
    async def test_deploy_per_node_create_failure(self, test_db, test_user):
        """Per-node deploy: create failure sets error state."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager._manifest = None
        manager.latest_snapshots_map = {}
        manager.explicit_snapshots_map = {}

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch.object(manager.topo_service, "get_interface_count_map", return_value={}):
            mock_ac.create_node_on_agent = AsyncMock(
                return_value={"success": False, "error": "Image not found"}
            )
            await manager._deploy_nodes_per_node([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "Image not found" in ns.error_message
        # start_node_on_agent should not be called after create fails
        mock_ac.start_node_on_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_deploy_per_node_start_failure(self, test_db, test_user):
        """Per-node deploy: start failure sets error despite successful create."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager._manifest = None
        manager.latest_snapshots_map = {}
        manager.explicit_snapshots_map = {}

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch.object(manager.topo_service, "get_interface_count_map", return_value={}):
            mock_ac.create_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.start_node_on_agent = AsyncMock(
                return_value={"success": False, "error": "Container start timeout"}
            )
            await manager._deploy_nodes_per_node([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "start timeout" in ns.error_message.lower()

    @pytest.mark.asyncio
    async def test_deploy_per_node_no_node_def(self, test_db, test_user):
        """Per-node deploy: missing node definition sets error."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {}  # No node definitions
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}

        await manager._deploy_nodes_per_node([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "not found" in ns.error_message.lower()

    @pytest.mark.asyncio
    async def test_deploy_per_node_connects_links(self, test_db, test_user):
        """Per-node deploy: calls _connect_same_host_links after deploy."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager._manifest = None
        manager.latest_snapshots_map = {}
        manager.explicit_snapshots_map = {}

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock), \
             patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock), \
             patch.object(manager.topo_service, "get_interface_count_map", return_value={}), \
             patch.object(manager, "_connect_same_host_links", new_callable=AsyncMock) as mock_links:
            mock_ac.create_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.start_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
                "nodes": [{"name": "R1", "node_definition_id": node_def.id, "runtime_id": "runtime-r1"}]
            })
            await manager._deploy_nodes_per_node([ns])

        # Links connected incrementally during deploy and at the end
        assert mock_links.call_count >= 1
        mock_links.assert_any_call({"R1"})


class TestStartNodesPerNode:
    """Test _start_nodes_per_node — per-node start with veth repair."""

    @pytest.mark.asyncio
    async def test_start_per_node_success(self, test_db, test_user):
        """Per-node start calls start_node_on_agent (not deploy_to_agent)."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager._manifest = None
        manager.latest_snapshots_map = {}
        manager.explicit_snapshots_map = {}

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock), \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock), \
             patch.object(manager, "_connect_same_host_links", new_callable=AsyncMock):
            mock_ac.start_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.create_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
                "nodes": [{"name": "R1", "node_definition_id": node_def.id, "runtime_id": "runtime-r1"}]
            })
            # deploy_to_agent should NOT be called
            mock_ac.deploy_to_agent = AsyncMock()
            await manager._start_nodes_per_node([ns])

        assert ns.actual_state == NodeActualState.RUNNING.value
        assert ns.boot_started_at is not None
        mock_ac.start_node_on_agent.assert_called_once()
        mock_ac.deploy_to_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_per_node_failure(self, test_db, test_user):
        """Per-node start failure sets error state."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager._manifest = None
        manager.latest_snapshots_map = {}
        manager.explicit_snapshots_map = {}

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch.object(manager.topo_service, "get_interface_count_map", return_value={}):
            mock_ac.start_node_on_agent = AsyncMock(
                return_value={"success": False, "error": "Network error"}
            )
            mock_ac.create_node_on_agent = AsyncMock(return_value={"success": True})
            await manager._start_nodes_per_node([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "Network error" in ns.error_message

    @pytest.mark.asyncio
    async def test_start_per_node_reconnects_links(self, test_db, test_user):
        """Per-node start reconnects same-host links."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager._manifest = None
        manager.latest_snapshots_map = {}
        manager.explicit_snapshots_map = {}

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock), \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock), \
             patch.object(manager, "_connect_same_host_links", new_callable=AsyncMock) as mock_links:
            mock_ac.start_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.create_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
                "nodes": [{"name": "R1", "node_definition_id": node_def.id, "runtime_id": "runtime-r1"}]
            })
            await manager._start_nodes_per_node([ns])

        # Links connected incrementally during deploy and at the end
        assert mock_links.call_count >= 1
        mock_links.assert_any_call({"R1"})

    @pytest.mark.asyncio
    async def test_start_per_node_falls_back_to_redeploy_on_domain_missing(
        self, test_db, test_user
    ):
        """If start target is missing, per-node start should redeploy."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(
            test_db, lab, "n1", "R1", desired="running", actual="stopped"
        )
        node_def = make_node(
            test_db, lab, "n1", "R1", "R1", host_id=host.id
        )

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}

        async def _fake_deploy(single_ns):
            single_ns.actual_state = NodeActualState.RUNNING.value
            single_ns.error_message = None
            single_ns.starting_started_at = None
            single_ns.boot_started_at = datetime.now(timezone.utc)
            return single_ns.node_name

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock), \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock), \
             patch.object(manager, "_connect_same_host_links", new_callable=AsyncMock), \
             patch.object(manager, "_deploy_single_node", new_callable=AsyncMock) as mock_deploy:
            mock_ac.start_node_on_agent = AsyncMock(return_value={
                "success": False,
                "error": "Libvirt error: Domain not found: no domain with matching name 'R1'",
            })
            mock_deploy.side_effect = _fake_deploy

            await manager._start_nodes_per_node([ns])

        mock_deploy.assert_called_once_with(ns)
        assert ns.actual_state == NodeActualState.RUNNING.value
        assert ns.error_message is None


class TestDeployDispatch:
    """Test _deploy_nodes dispatches to per-node path."""

    @pytest.mark.asyncio
    async def test_dispatches_to_per_node(self, test_db, test_user):
        """_deploy_nodes uses per-node path."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        manager._deploy_nodes_per_node = AsyncMock()

        await manager._deploy_nodes([ns])

        manager._deploy_nodes_per_node.assert_called_once_with([ns])


class TestIsCeosKind:
    """Test _is_ceos_kind helper."""

    def test_ceos_matches(self):
        from app.tasks.node_lifecycle import _is_ceos_kind
        assert _is_ceos_kind("ceos") is True
        assert _is_ceos_kind("cEOS") is True
        assert _is_ceos_kind("arista_ceos") is True

    def test_non_ceos(self):
        from app.tasks.node_lifecycle import _is_ceos_kind
        assert _is_ceos_kind("linux") is False
        assert _is_ceos_kind("srl") is False
        assert _is_ceos_kind("") is False
        assert _is_ceos_kind(None) is False


# ---------------------------------------------------------------------------
# Unified lifecycle: auto-extract before stop
# ---------------------------------------------------------------------------


class TestAutoExtractBeforeStop:
    """Tests for _auto_extract_before_stop (unified lifecycle)."""

    @pytest.mark.asyncio
    async def test_extracts_configs_and_creates_autosave_snapshots(
        self, test_db, test_user
    ):
        """Auto-extract creates autosave snapshots with set_as_active=True."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        make_node(test_db, lab, "n1", "R1", "R1", device="ceos")
        ns = make_node_state(
            test_db, lab, "n1", "R1", desired="stopped", actual="running"
        )

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        mock_save = MagicMock(return_value=MagicMock(id="snap-1"))

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={
                "success": True,
                "configs": [
                    {"node_name": "R1", "content": "hostname R1\n!"},
                ],
            })
            with patch(
                "app.services.config_service.ConfigService.save_extracted_config",
                mock_save,
            ):
                await manager._auto_extract_before_stop([ns])

        # Extract called on agent
        mock_ac.extract_configs_on_agent.assert_awaited_once_with(host, lab.id)

        # Snapshot saved with autosave type and set_as_active=True
        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args[1]
        assert call_kwargs["snapshot_type"] == "autosave"
        assert call_kwargs["set_as_active"] is True
        assert call_kwargs["node_name"] == "R1"
        assert call_kwargs["content"] == "hostname R1\n!"

    @pytest.mark.asyncio
    async def test_extracts_from_stopping_nodes(self, test_db, test_user):
        """Auto-extract also works on nodes in 'stopping' transitional state.

        The NLM sets transitional states before calling auto-extract,
        so nodes will be in 'stopping' rather than 'running'.
        """
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        make_node(test_db, lab, "n1", "R1", "R1", device="ceos")
        ns = make_node_state(
            test_db, lab, "n1", "R1", desired="stopped", actual="stopping"
        )

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        mock_save = MagicMock(return_value=MagicMock(id="snap-1"))

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={
                "success": True,
                "configs": [
                    {"node_name": "R1", "content": "hostname R1\n!"},
                ],
            })
            with patch(
                "app.services.config_service.ConfigService.save_extracted_config",
                mock_save,
            ):
                await manager._auto_extract_before_stop([ns])

        # Extract called even though state is 'stopping'
        mock_ac.extract_configs_on_agent.assert_awaited_once()
        mock_save.assert_called_once()
        assert mock_save.call_args[1]["snapshot_type"] == "autosave"

    @pytest.mark.asyncio
    async def test_skips_non_extractable_nodes(self, test_db, test_user):
        """Auto-extract skips nodes that are already stopped/undeployed."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(
            test_db, lab, "n1", "R1", desired="stopped", actual="stopped"
        )

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.extract_configs_on_agent = AsyncMock()
            await manager._auto_extract_before_stop([ns])

        # No running nodes → no extraction
        mock_ac.extract_configs_on_agent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_extraction_failure_does_not_block_stop(self, test_db, test_user):
        """If auto-extract raises, stop continues (failure-tolerant)."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(
            test_db, lab, "n1", "R1", desired="stopped", actual="running"
        )

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.extract_configs_on_agent = AsyncMock(
                side_effect=Exception("Agent crashed")
            )

            # Should NOT raise — failure-tolerant
            await manager._auto_extract_before_stop([ns])

        # Verify it was called (and failed)
        mock_ac.extract_configs_on_agent.assert_awaited_once()


# ---------------------------------------------------------------------------
# Unified lifecycle: stop calls auto-extract
# ---------------------------------------------------------------------------


class TestStopNodesCallsAutoExtract:
    """Verify _stop_nodes integrates auto-extract before reconcile."""

    @pytest.mark.asyncio
    async def test_stop_calls_auto_extract_then_reconcile(self, test_db, test_user):
        """_stop_nodes calls _auto_extract_before_stop before reconcile."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(
            test_db, lab, "n1", "R1", desired="stopped", actual="running"
        )
        container_name = _get_container_name(lab.id, "R1")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        call_order = []

        async def mock_extract(nodes):
            call_order.append("extract")

        manager._auto_extract_before_stop = mock_extract

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)

            async def mock_reconcile(*args, **kwargs):
                call_order.append("reconcile")
                return {
                    "results": [
                        {"container_name": container_name, "success": True}
                    ]
                }

            mock_ac.reconcile_nodes_on_agent = mock_reconcile
            await manager._stop_nodes([ns])

        assert call_order == ["extract", "reconcile"]


# ---------------------------------------------------------------------------
# Unified lifecycle: start uses deploy (fresh create)
# ---------------------------------------------------------------------------


class TestStartUsesDeployPath:
    """Verify _start_nodes_per_node uses _deploy_single_node."""

    @pytest.mark.asyncio
    async def test_start_calls_deploy_single_node(self, test_db, test_user):
        """_start_nodes_per_node calls _deploy_single_node, not _start_single_node."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        node_def = make_node(test_db, lab, "n1", "R1", "R1", device="linux")
        ns = make_node_state(
            test_db, lab, "n1", "R1", desired="running", actual="stopped"
        )

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}

        deploy_called = []

        async def mock_deploy(node_state):
            deploy_called.append(node_state.node_name)
            node_state.actual_state = NodeActualState.RUNNING.value
            return node_state.node_name

        manager._deploy_single_node = mock_deploy
        manager._connect_same_host_links = AsyncMock()

        with patch("app.tasks.node_lifecycle_deploy.agent_client"):
            with patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock):
                with patch(
                    "app.tasks.jobs._update_node_placements", new_callable=AsyncMock
                ):
                    await manager._start_nodes_per_node([ns])

        assert deploy_called == ["R1"]

    @pytest.mark.asyncio
    async def test_start_updates_placements_for_fresh_containers(
        self, test_db, test_user
    ):
        """_start_nodes_per_node calls _update_node_placements after deploy."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        node_def = make_node(test_db, lab, "n1", "R1", "R1", device="linux")
        ns = make_node_state(
            test_db, lab, "n1", "R1", desired="running", actual="stopped"
        )

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}

        async def mock_deploy(node_state):
            node_state.actual_state = NodeActualState.RUNNING.value
            return node_state.node_name

        manager._deploy_single_node = mock_deploy
        manager._connect_same_host_links = AsyncMock()

        with patch("app.tasks.node_lifecycle_deploy.agent_client"):
            with patch(
                "app.tasks.jobs._capture_node_ips", new_callable=AsyncMock
            ):
                with patch(
                    "app.tasks.jobs._update_node_placements",
                    new_callable=AsyncMock,
                ) as mock_update_placements:
                    await manager._start_nodes_per_node([ns])

        mock_update_placements.assert_awaited_once()
        call_args = mock_update_placements.call_args[0]
        assert call_args[2] == host.id  # agent_id
        assert "R1" in call_args[3]  # deployed node names

    @pytest.mark.asyncio
    async def test_start_ceos_stagger_still_applies(self, test_db, test_user):
        """cEOS nodes still deploy sequentially with stagger delay."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        node1 = make_node(test_db, lab, "n1", "ceos1", "ceos1", device="ceos")
        node2 = make_node(test_db, lab, "n2", "ceos2", "ceos2", device="ceos")
        ns1 = make_node_state(
            test_db, lab, "n1", "ceos1", desired="running", actual="stopped"
        )
        ns2 = make_node_state(
            test_db, lab, "n2", "ceos2", desired="running", actual="stopped"
        )

        manager = _make_manager(test_db, lab, job, [ns1.node_id, ns2.node_id], agent=host)
        manager.node_states = [ns1, ns2]
        manager.db_nodes_map = {"ceos1": node1, "ceos2": node2}
        manager.placements_map = {}
        manager.all_lab_states = {"ceos1": ns1, "ceos2": ns2}

        deploy_order = []

        async def mock_deploy(node_state):
            deploy_order.append(node_state.node_name)
            node_state.actual_state = NodeActualState.RUNNING.value
            return node_state.node_name

        manager._deploy_single_node = mock_deploy
        manager._connect_same_host_links = AsyncMock()

        with patch("app.tasks.node_lifecycle_deploy.agent_client"):
            with patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock):
                with patch(
                    "app.tasks.jobs._update_node_placements",
                    new_callable=AsyncMock,
                ):
                    with patch(
                        "app.tasks.node_lifecycle_deploy.asyncio.sleep",
                        new_callable=AsyncMock,
                    ) as mock_sleep:
                        await manager._start_nodes_per_node([ns1, ns2])

        # Both cEOS nodes deployed sequentially
        assert deploy_order == ["ceos1", "ceos2"]
        # Stagger delay applied between cEOS nodes
        mock_sleep.assert_awaited_once()


# ---------------------------------------------------------------------------
# Phase 5: New test classes for reliability improvements
# ---------------------------------------------------------------------------


class TestAgentOfflineDuringDeploy:
    """Tests for agent going offline mid-deployment."""

    @pytest.mark.asyncio
    async def test_partial_success_some_nodes_deployed(self, test_db, test_user):
        """Agent fails after deploying 2 of 4 nodes."""
        from app.agent_client import AgentUnavailableError

        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns1 = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        ns2 = make_node_state(test_db, lab, "n2", "R2", desired="running", actual="pending")
        node_def1 = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)
        node_def2 = make_node(test_db, lab, "n2", "R2", "R2", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns1.node_id, ns2.node_id], agent=host)
        manager.node_states = [ns1, ns2]
        manager.db_nodes_map = {"R1": node_def1, "R2": node_def2}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns1, "R2": ns2}
        manager._manifest = None
        manager.latest_snapshots_map = {}
        manager.explicit_snapshots_map = {}

        call_count = 0

        async def mock_create(agent, lab_id, node_name, kind, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:  # First node succeeds
                return {"success": True}
            raise AgentUnavailableError("Connection refused")

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock), \
             patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock), \
             patch.object(manager.topo_service, "get_interface_count_map", return_value={}), \
            patch.object(manager, "_connect_same_host_links", new_callable=AsyncMock), \
            patch("app.tasks.node_lifecycle_deploy.asyncio.sleep", new_callable=AsyncMock):
            mock_ac.create_node_on_agent = mock_create
            mock_ac.start_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
                "nodes": [{"name": "R1", "node_definition_id": node_def1.id, "runtime_id": "runtime-r1"}]
            })
            await manager._deploy_nodes_per_node([ns1, ns2])

        # First node deployed successfully, second failed
        assert ns1.actual_state == "running"
        assert ns2.actual_state == "pending"

    @pytest.mark.asyncio
    async def test_failed_placement_skipped_in_resolve(self, test_db, test_user):
        """Node with failed placement goes to resource scoring."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        make_node(test_db, lab, "n1", "R1", "R1")
        # Placement with failed status
        make_placement(test_db, lab, "R1", host.id, status="failed")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        await manager._load_and_validate()

        # Verify failed placement is in the map
        placement = manager.placements_map.get("R1")
        assert placement is not None
        assert placement.status == "failed"

    @pytest.mark.asyncio
    async def test_all_nodes_on_offline_agent(self, test_db, test_user):
        """All nodes assigned to offline agent should fail gracefully."""
        from app.agent_client import AgentUnavailableError

        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns1 = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        ns2 = make_node_state(test_db, lab, "n2", "R2", desired="running", actual="pending")
        node_def1 = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)
        node_def2 = make_node(test_db, lab, "n2", "R2", "R2", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns1.node_id, ns2.node_id], agent=host)
        manager.node_states = [ns1, ns2]
        manager.db_nodes_map = {"R1": node_def1, "R2": node_def2}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns1, "R2": ns2}
        manager._manifest = None
        manager.latest_snapshots_map = {}
        manager.explicit_snapshots_map = {}

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch.object(manager.topo_service, "get_interface_count_map", return_value={}), \
             patch.object(manager, "_connect_same_host_links", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle_deploy.asyncio.sleep", new_callable=AsyncMock):
            mock_ac.create_node_on_agent = AsyncMock(
                side_effect=AgentUnavailableError("Connection refused")
            )
            await manager._deploy_nodes_per_node([ns1, ns2])

        # Both nodes in pending (transient failure, not error)
        assert ns1.actual_state == "pending"
        assert ns2.actual_state == "pending"


class TestDeployRetry:
    """Tests for in-job retry on transient failures."""

    @pytest.mark.asyncio
    async def test_retry_succeeds_after_transient_failure(self, test_db, test_user):
        """First attempt fails, second succeeds."""
        from app.agent_client import AgentUnavailableError

        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager._manifest = None
        manager.latest_snapshots_map = {}
        manager.explicit_snapshots_map = {}

        attempt_count = 0

        async def mock_create(agent, lab_id, node_name, kind, **kwargs):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 1:
                raise AgentUnavailableError("Timeout")
            return {"success": True}

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch.object(manager.topo_service, "get_interface_count_map", return_value={}), \
             patch("app.tasks.node_lifecycle_deploy.asyncio.sleep", new_callable=AsyncMock):
            mock_ac.create_node_on_agent = mock_create
            mock_ac.start_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.get_lab_status_from_agent = AsyncMock(return_value={
                "nodes": [{"name": "R1", "node_definition_id": node_def.id, "runtime_id": "runtime-r1"}]
            })
            result = await manager._deploy_single_node_with_retry(ns)

        assert result == "R1"
        assert ns.actual_state == "running"
        assert attempt_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted(self, test_db, test_user):
        """All retry attempts fail → node in pending state."""
        from app.agent_client import AgentUnavailableError

        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager._manifest = None
        manager.latest_snapshots_map = {}
        manager.explicit_snapshots_map = {}

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch.object(manager.topo_service, "get_interface_count_map", return_value={}), \
             patch("app.tasks.node_lifecycle_deploy.asyncio.sleep", new_callable=AsyncMock):
            mock_ac.create_node_on_agent = AsyncMock(
                side_effect=AgentUnavailableError("Timeout")
            )
            result = await manager._deploy_single_node_with_retry(ns)

        assert result is None
        assert ns.actual_state == "pending"
        assert ns.error_message is not None

    @pytest.mark.asyncio
    async def test_no_retry_on_permanent_error(self, test_db, test_user):
        """Non-transient errors (error state) should not retry."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager._manifest = None
        manager.latest_snapshots_map = {}
        manager.explicit_snapshots_map = {}

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch.object(manager.topo_service, "get_interface_count_map", return_value={}), \
             patch("app.tasks.node_lifecycle_deploy.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            # Return a non-transient failure (sets error state, not pending)
            mock_ac.create_node_on_agent = AsyncMock(
                return_value={"success": False, "error": "Image not found"}
            )
            result = await manager._deploy_single_node_with_retry(ns)

        assert result is None
        assert ns.actual_state == "error"
        # Should NOT have retried (no sleep called)
        mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_retry_respects_backoff(self, test_db, test_user):
        """Verify backoff delay between retry attempts."""
        from app.agent_client import AgentUnavailableError
        from app.tasks.node_lifecycle import DEPLOY_RETRY_BACKOFF_SECONDS

        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager._manifest = None
        manager.latest_snapshots_map = {}
        manager.explicit_snapshots_map = {}

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac, \
             patch.object(manager.topo_service, "get_interface_count_map", return_value={}), \
             patch("app.tasks.node_lifecycle_deploy.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_ac.create_node_on_agent = AsyncMock(
                side_effect=AgentUnavailableError("Timeout")
            )
            await manager._deploy_single_node_with_retry(ns)

        # Verify sleep was called with the correct backoff
        mock_sleep.assert_awaited_once_with(DEPLOY_RETRY_BACKOFF_SECONDS)


class TestActiveReadinessPolling:
    """Tests for in-job readiness polling after deploy."""

    @pytest.mark.asyncio
    async def test_readiness_detected_within_poll_interval(self, test_db, test_user):
        """Node becomes ready → detected on next poll cycle."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}

        poll_count = 0

        async def mock_readiness(*args, **kwargs):
            nonlocal poll_count
            poll_count += 1
            if poll_count >= 2:
                return {"is_ready": True}
            return {"is_ready": False}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.node_lifecycle.asyncio.sleep", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle.asyncio.get_running_loop") as mock_loop:
            # Simulate time progression
            times = iter([0, 6, 12])
            mock_loop.return_value.time = lambda: next(times, 120)
            mock_ac.check_node_readiness = mock_readiness
            await manager._wait_for_readiness(["R1"])

        assert ns.is_ready is True
        assert poll_count == 2

    @pytest.mark.asyncio
    async def test_readiness_timeout_reached(self, test_db, test_user):
        """Timeout → stops polling, nodes left for reconciliation."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.node_lifecycle.asyncio.sleep", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle.asyncio.get_running_loop") as mock_loop:
            # Start at 0, jump past timeout on second call
            times = iter([0, 200])
            mock_loop.return_value.time = lambda: next(times, 200)
            mock_ac.check_node_readiness = AsyncMock(return_value={"is_ready": False})
            await manager._wait_for_readiness(["R1"])

        # Node should NOT be ready (timed out)
        assert ns.is_ready is not True
        assert any("timeout" in line.lower() for line in manager.log_parts)

    @pytest.mark.asyncio
    async def test_readiness_uses_agent_timeout_override(self, test_db, test_user):
        """Per-node timeout from agent readiness should override default 120s."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}

        responses = iter([
            {"is_ready": False, "timeout": 600, "message": "Boot in progress"},
            {"is_ready": True, "timeout": 600},
        ])

        async def mock_readiness(*args, **kwargs):
            return next(responses)

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.node_lifecycle.asyncio.sleep", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle.asyncio.get_running_loop") as mock_loop:
            # Elapsed passes 120s before first probe result is processed.
            times = iter([0, 130, 136])
            mock_loop.return_value.time = lambda: next(times, 136)
            mock_ac.check_node_readiness = mock_readiness
            await manager._wait_for_readiness(["R1"])

        assert ns.is_ready is True
        assert not any("Readiness timeout (120s)" in line for line in manager.log_parts)

    @pytest.mark.asyncio
    async def test_multiple_nodes_different_boot_times(self, test_db, test_user):
        """Nodes boot at different times, each detected independently."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns1 = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")
        ns2 = make_node_state(test_db, lab, "n2", "R2", desired="running", actual="running")
        node_def1 = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)
        node_def2 = make_node(test_db, lab, "n2", "R2", "R2", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns1.node_id, ns2.node_id], agent=host)
        manager.node_states = [ns1, ns2]
        manager.db_nodes_map = {"R1": node_def1, "R2": node_def2}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns1, "R2": ns2}

        poll_count = 0

        async def mock_readiness(agent, lab_id, node_name, **kwargs):
            nonlocal poll_count
            poll_count += 1
            # R1 becomes ready on poll 2, R2 on poll 4
            if node_name == "R1" and poll_count >= 2:
                return {"is_ready": True}
            if node_name == "R2" and poll_count >= 5:
                return {"is_ready": True}
            return {"is_ready": False}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.node_lifecycle.asyncio.sleep", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle.asyncio.get_running_loop") as mock_loop:
            times = iter([0, 6, 12, 18, 24, 30])
            mock_loop.return_value.time = lambda: next(times, 0)
            mock_ac.check_node_readiness = mock_readiness
            await manager._wait_for_readiness(["R1", "R2"])

        assert ns1.is_ready is True
        assert ns2.is_ready is True

    @pytest.mark.asyncio
    async def test_agent_unreachable_during_readiness(self, test_db, test_user):
        """Agent error during readiness poll → doesn't crash job."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.node_lifecycle.asyncio.sleep", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle.asyncio.get_running_loop") as mock_loop:
            # Simulate timeout after one poll
            times = iter([0, 6, 200])
            mock_loop.return_value.time = lambda: next(times, 200)
            mock_ac.check_node_readiness = AsyncMock(
                side_effect=Exception("Connection reset")
            )
            # Should not raise
            await manager._wait_for_readiness(["R1"])

        # Node not ready but no crash
        assert ns.is_ready is not True

    @pytest.mark.asyncio
    async def test_readiness_logs_probe_details_when_waiting(self, test_db, test_user):
        """Unready poll cycles should include readiness message/details in job log."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}

        responses = iter([
            {
                "is_ready": False,
                "message": "Boot in progress (POAP failure observed)",
                "progress_percent": 30,
                "details": "markers=poap_failure,startup_config_ref",
            },
            {"is_ready": True},
        ])

        async def mock_readiness(*args, **kwargs):
            return next(responses)

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.node_lifecycle.asyncio.sleep", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle.asyncio.get_running_loop") as mock_loop:
            times = iter([0, 6, 12])
            mock_loop.return_value.time = lambda: next(times, 120)
            mock_ac.check_node_readiness = mock_readiness
            await manager._wait_for_readiness(["R1"])

        assert ns.is_ready is True
        assert any(
            "Boot in progress (POAP failure observed)" in line and
            "markers=poap_failure,startup_config_ref" in line
            for line in manager.log_parts
        )

    @pytest.mark.asyncio
    async def test_readiness_triggers_same_host_link_connection_when_node_becomes_ready(self, test_db, test_user):
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager._connect_same_host_links = AsyncMock()

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.node_lifecycle.asyncio.sleep", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle.asyncio.get_running_loop") as mock_loop:
            times = iter([0, 6, 12])
            mock_loop.return_value.time = lambda: next(times, 12)
            mock_ac.check_node_readiness = AsyncMock(return_value={"is_ready": True})
            await manager._wait_for_readiness(["R1"])

        manager._connect_same_host_links.assert_awaited_once_with({"R1"})


class TestPlacementFailover:
    """Tests for failed placement fallback to resource scoring."""

    @pytest.mark.asyncio
    async def test_failed_placement_skipped(self, test_db, test_user):
        """Node with failed placement goes to resource scoring."""
        host1 = make_host(test_db, host_id="agent-1", name="Agent 1")
        make_host(test_db, host_id="agent-2", name="Agent 2")
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        make_node(test_db, lab, "n1", "R1", "R1")
        # Failed placement on agent-1
        make_placement(test_db, lab, "R1", host1.id, status="failed")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        await manager._load_and_validate()

        # The placement exists but is "failed"
        placement = manager.placements_map.get("R1")
        assert placement is not None
        assert placement.status == "failed"

    @pytest.mark.asyncio
    async def test_all_agents_failed_graceful_error(self, test_db, test_user):
        """No reachable agents → job fails with clear message."""
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        make_node(test_db, lab, "n1", "R1", "R1")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": MagicMock(host_id=None, device="linux")}
        manager.db_nodes_by_gui_id = {}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}

        with patch("app.tasks.node_lifecycle_agents.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=False)
            mock_ac.get_healthy_agent = AsyncMock(return_value=None)
            mock_ac.get_agent_for_node = AsyncMock(return_value=None)
            mock_ac.get_agent_providers = MagicMock(return_value=["docker"])
            result = await manager._resolve_agents()

        assert result is False
        assert job.status == "failed"
        assert ns.actual_state == "error"


class TestTransientErrorHandler:
    """Tests for unified _handle_transient_failure method."""

    def test_sets_pending_state(self, test_db, test_user):
        """Transient failure sets pending state and clears timestamps."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="starting")
        ns.starting_started_at = datetime.now(timezone.utc)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)

        manager._handle_transient_failure(ns, "Agent unreachable")

        assert ns.actual_state == "pending"
        assert ns.starting_started_at is None
        assert ns.stopping_started_at is None
        assert ns.error_message == "Agent unreachable"


class TestPingAgent:
    """Tests for agent ping health check."""

    @pytest.mark.asyncio
    async def test_ping_agent_success(self):
        """Successful ping returns True."""
        from app.agent_client import ping_agent

        agent = MagicMock()
        agent.name = "test-agent"
        agent.id = "agent-1"
        agent.address = "http://localhost:8001"

        with patch("app.agent_client.selection._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"status": "ok"}
            result = await ping_agent(agent)

        assert result is True
        mock_req.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ping_agent_failure_raises(self):
        """Failed ping raises AgentUnavailableError."""
        from app.agent_client import ping_agent, AgentUnavailableError

        agent = MagicMock()
        agent.name = "test-agent"
        agent.id = "agent-1"
        agent.address = "http://localhost:8001"

        with patch("app.agent_client.selection._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.side_effect = Exception("Connection refused")
            with pytest.raises(AgentUnavailableError):
                await ping_agent(agent)


class TestPostOperationCleanup:
    """Tests for post-operation cleanup session recovery."""

    @pytest.mark.asyncio
    async def test_rolls_back_session_when_reconcile_fails(self, test_db, test_user, monkeypatch):
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        manager = _make_manager(test_db, lab, job, [], agent=host)
        manager.log_parts = []
        manager._release_db_transaction_for_io = MagicMock()

        node_a = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)
        node_b = make_node(test_db, lab, "n2", "R2", "R2", host_id=host.id)
        link = models.Link(
            lab_id=lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node_id=node_a.id,
            source_interface="eth1",
            target_node_id=node_b.id,
            target_interface="eth1",
        )
        test_db.add(link)
        test_db.commit()

        rollback_spy = MagicMock(wraps=test_db.rollback)
        monkeypatch.setattr(test_db, "rollback", rollback_spy)

        with patch(
            "app.tasks.jobs._create_cross_host_links_if_ready",
            new_callable=AsyncMock,
        ) as mock_cross_host, patch(
            "app.tasks.link_reconciliation.reconcile_lab_links",
            new_callable=AsyncMock,
            side_effect=RuntimeError("statement timeout"),
        ):
            await manager._post_operation_cleanup()

        mock_cross_host.assert_awaited_once()
        assert rollback_spy.call_count == 1
        assert any(
            "Post-op link reconciliation failed" in line
            for line in manager.log_parts
        )
        assert manager.post_operation_cleanup_failed is True

    async def test_releases_transaction_between_cleanup_phases(self, test_db, test_user):
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        manager = _make_manager(test_db, lab, job, [], agent=host)
        manager.log_parts = []
        manager._release_db_transaction_for_io = MagicMock()

        with patch(
            "app.tasks.jobs._create_cross_host_links_if_ready",
            new_callable=AsyncMock,
        ) as mock_cross_host, patch(
            "app.tasks.link_reconciliation.reconcile_lab_links",
            new_callable=AsyncMock,
            return_value={"checked": 0, "created": 0, "repaired": 0, "errors": 0, "skipped": 0},
        ), patch(
            "app.tasks.link_reconciliation.run_overlay_convergence",
            new_callable=AsyncMock,
            return_value={},
        ), patch(
            "app.tasks.link_reconciliation.refresh_interface_mappings",
            new_callable=AsyncMock,
            return_value={"updated": 0, "created": 0},
        ), patch(
            "app.tasks.link_reconciliation.run_cross_host_port_convergence",
            new_callable=AsyncMock,
            return_value={"updated": 0, "errors": 0},
        ):
            await manager._post_operation_cleanup()

        mock_cross_host.assert_awaited_once()
        contexts = [call.args[0] for call in manager._release_db_transaction_for_io.call_args_list]
        assert contexts == [
            "cross-host link creation",
            "post-op overlay convergence",
            "post-op interface mapping refresh",
            "post-op cross-host port convergence",
        ]


    @pytest.mark.asyncio
    async def test_skips_reconcile_when_initial_provisioning_resolved_links(self, test_db, test_user):
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        manager = _make_manager(test_db, lab, job, [], agent=host)
        manager.log_parts = []
        manager._release_db_transaction_for_io = MagicMock()

        with patch(
            "app.tasks.jobs._create_cross_host_links_if_ready",
            new_callable=AsyncMock,
        ) as mock_cross_host, patch(
            "app.tasks.link_reconciliation.reconcile_lab_links",
            new_callable=AsyncMock,
        ) as mock_reconcile:
            await manager._post_operation_cleanup()

        mock_cross_host.assert_awaited_once()
        mock_reconcile.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reconcile_fires_when_unresolved_links_exist(self, test_db, test_user):
        """Post-op reconciliation IS called when unresolved LinkState rows exist."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        manager = _make_manager(test_db, lab, job, [], agent=host)
        manager.log_parts = []
        manager._release_db_transaction_for_io = MagicMock()

        # Create a LinkState with desired=up, actual=pending (unresolved)
        ls = models.LinkState(
            lab_id=lab.id,
            link_name="R1:eth1<->R2:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R2",
            target_interface="eth1",
            desired_state="up",
            actual_state="pending",
        )
        test_db.add(ls)
        test_db.commit()

        with patch(
            "app.tasks.jobs._create_cross_host_links_if_ready",
            new_callable=AsyncMock,
        ), patch(
            "app.tasks.link_reconciliation.reconcile_lab_links",
            new_callable=AsyncMock,
            return_value={"checked": 1, "created": 1, "repaired": 0, "errors": 0, "skipped": 0},
        ) as mock_reconcile, patch(
            "app.tasks.link_reconciliation.run_overlay_convergence",
            new_callable=AsyncMock,
            return_value={},
        ), patch(
            "app.tasks.link_reconciliation.refresh_interface_mappings",
            new_callable=AsyncMock,
            return_value={"updated": 0, "created": 0},
        ), patch(
            "app.tasks.link_reconciliation.run_cross_host_port_convergence",
            new_callable=AsyncMock,
            return_value={"updated": 0, "errors": 0},
        ):
            await manager._post_operation_cleanup()

        mock_reconcile.assert_awaited_once()
        assert any("Post-op link reconciliation" in line for line in manager.log_parts)


class TestFinalizePostOperationFailure:
    @pytest.mark.asyncio
    async def test_finalize_fails_when_post_operation_cleanup_failed(self, test_db, test_user):
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.post_operation_cleanup_failed = True

        result = await manager._finalize()

        assert result.success is False
        assert result.error_count == 1
        assert job.status == JobStatus.FAILED.value
        assert any("state settlement failed" in line.lower() for line in manager.log_parts)