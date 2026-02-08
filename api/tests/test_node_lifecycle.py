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
from app.state import HostStatus, JobStatus, NodeActualState, NodeDesiredState
from app.tasks.node_lifecycle import LifecycleResult, NodeLifecycleManager, _get_container_name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_host(test_db, host_id="agent-1", name="Agent 1", status="online"):
    """Create and persist a Host record."""
    host = models.Host(
        id=host_id,
        name=name,
        address=f"{host_id}.local:8080",
        status=status,
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
        resource_usage=json.dumps({
            "cpu_percent": 25.0,
            "memory_percent": 40.0,
            "disk_percent": 30.0,
            "disk_used_gb": 60.0,
            "disk_total_gb": 200.0,
            "containers_running": 2,
            "containers_total": 4,
            "container_details": [],
        }),
        last_heartbeat=datetime.now(timezone.utc),
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


def _make_lab(test_db, user, agent_id=None):
    """Create and persist a Lab record."""
    lab = models.Lab(
        name="Test Lab",
        owner_id=user.id,
        provider="docker",
        state="running",
        workspace_path="/tmp/test-lab",
        agent_id=agent_id,
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


def _make_job(test_db, lab, user):
    """Create and persist a queued Job record."""
    job = models.Job(
        lab_id=lab.id,
        user_id=user.id,
        action="sync",
        status=JobStatus.QUEUED.value,
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


def _make_node_state(test_db, lab, node_id, node_name, desired="running", actual="undeployed"):
    """Create and persist a NodeState record."""
    ns = models.NodeState(
        lab_id=lab.id,
        node_id=node_id,
        node_name=node_name,
        desired_state=desired,
        actual_state=actual,
    )
    test_db.add(ns)
    test_db.commit()
    test_db.refresh(ns)
    return ns


def _make_node_def(test_db, lab, gui_id, name, container_name, device="linux", host_id=None):
    """Create and persist a Node definition."""
    node = models.Node(
        lab_id=lab.id,
        gui_id=gui_id,
        display_name=name,
        container_name=container_name,
        device=device,
        host_id=host_id,
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    return node


def _make_placement(test_db, lab, node_name, host_id, status="running"):
    """Create and persist a NodePlacement record."""
    p = models.NodePlacement(
        lab_id=lab.id,
        node_name=node_name,
        host_id=host_id,
        status=status,
    )
    test_db.add(p)
    test_db.commit()
    test_db.refresh(p)
    return p


def _make_manager(session, lab, job, node_ids, agent=None, monkeypatch=None):
    """Create a NodeLifecycleManager with common mocks applied."""
    manager = NodeLifecycleManager(session, lab, job, node_ids)
    if agent:
        manager.agent = agent
        manager.target_agent_id = agent.id
    # Disable broadcasts by default in tests
    manager._broadcast_state = MagicMock()
    manager._broadcast_job_progress = AsyncMock()
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
# _load_and_validate
# ---------------------------------------------------------------------------


class TestLoadAndValidate:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_node_states(self, test_db, test_user):
        """If no NodeState rows exist for the given node_ids, returns False."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        manager = _make_manager(test_db, lab, job, ["nonexistent-id"])

        with patch.object(manager.topo_service, "get_nodes", return_value=[]):
            result = await manager._load_and_validate()

        assert result is False
        assert job.status == JobStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_returns_false_when_all_in_desired_state(self, test_db, test_user):
        """If all nodes are already in desired state, returns False."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        # Node wants running and IS running
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        with patch.object(manager.topo_service, "get_nodes", return_value=[]):
            result = await manager._load_and_validate()

        assert result is False
        assert job.status == JobStatus.COMPLETED.value
        assert "already in desired state" in job.log_path

    @pytest.mark.asyncio
    async def test_returns_true_when_nodes_need_action(self, test_db, test_user):
        """Nodes needing action cause _load_and_validate to return True."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        # Node wants running but is undeployed
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        with patch.object(manager.topo_service, "get_nodes", return_value=[node_def]):
            result = await manager._load_and_validate()

        assert result is True
        assert len(manager.node_states) == 1
        assert "R1" in manager.db_nodes_map

    @pytest.mark.asyncio
    async def test_stopped_desired_already_stopped(self, test_db, test_user):
        """Stopped nodes with desired=stopped need no action."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="stopped")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        with patch.object(manager.topo_service, "get_nodes", return_value=[]):
            result = await manager._load_and_validate()

        assert result is False

    @pytest.mark.asyncio
    async def test_fixes_placeholder_node_name(self, test_db, test_user):
        """Placeholder node_name (equals node_id) gets fixed to container_name."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        # node_name == node_id indicates a placeholder
        ns = _make_node_state(test_db, lab, "gui-id-1", "gui-id-1", desired="running", actual="undeployed")
        node_def = _make_node_def(test_db, lab, "gui-id-1", "R1", "archetype-test-R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        with patch.object(manager.topo_service, "get_nodes", return_value=[node_def]):
            result = await manager._load_and_validate()

        assert result is True
        assert ns.node_name == "archetype-test-R1"

    @pytest.mark.asyncio
    async def test_batch_loads_maps(self, test_db, test_user):
        """Batch-loaded maps are populated correctly."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)
        _make_placement(test_db, lab, "R1", host.id)

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
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]

        await manager._set_transitional_states()
        assert ns.actual_state == NodeActualState.PENDING.value
        assert ns.error_message is None

    @pytest.mark.asyncio
    async def test_stopped_to_starting(self, test_db, test_user):
        """Stopped node wanting running → starting."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]

        await manager._set_transitional_states()
        assert ns.actual_state == NodeActualState.STARTING.value
        assert ns.starting_started_at is not None

    @pytest.mark.asyncio
    async def test_running_to_stopping(self, test_db, test_user):
        """Running node wanting stopped → stopping."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]

        await manager._set_transitional_states()
        assert ns.actual_state == NodeActualState.STOPPING.value
        assert ns.stopping_started_at is not None

    @pytest.mark.asyncio
    async def test_error_to_pending(self, test_db, test_user):
        """Error node wanting running → pending (retry via state machine)."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="error")
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
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]

        await manager._set_transitional_states()
        manager._broadcast_state.assert_called_once_with(ns)

    @pytest.mark.asyncio
    async def test_no_broadcast_when_no_change(self, test_db, test_user):
        """No broadcast if state doesn't change."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        # Already running, wants running — no transition
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")

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
        host = _make_host(test_db, "host-a", "Host A")
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": test_db.query(models.Node).filter_by(container_name="R1").first()}
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.get_healthy_agent = AsyncMock(return_value=None)
            result = await manager._resolve_agents()

        assert result is True
        assert manager.agent.id == host.id

    @pytest.mark.asyncio
    async def test_explicit_host_offline_fails(self, test_db, test_user):
        """Explicit host that is offline → job fails."""
        host = _make_host(test_db, "host-a", "Host A", status="offline")
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": test_db.query(models.Node).filter_by(container_name="R1").first()}
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=False)
            result = await manager._resolve_agents()

        assert result is False
        assert job.status == JobStatus.FAILED.value
        assert ns.actual_state == NodeActualState.ERROR.value

    @pytest.mark.asyncio
    async def test_no_agent_available_fails(self, test_db, test_user):
        """No agents available → job fails."""
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {}
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=False)
            mock_ac.get_healthy_agent = AsyncMock(return_value=None)
            result = await manager._resolve_agents()

        assert result is False
        assert job.status == JobStatus.FAILED.value
        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "No agent available"

    @pytest.mark.asyncio
    async def test_placement_affinity(self, test_db, test_user):
        """Node with existing placement → uses that agent."""
        host = _make_host(test_db, "host-a", "Host A")
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        _make_placement(test_db, lab, "R1", host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id])
        manager.node_states = [ns]
        manager.db_nodes_map = {}
        manager.placements_map = {"R1": test_db.query(models.NodePlacement).first()}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.get_healthy_agent = AsyncMock(return_value=None)
            result = await manager._resolve_agents()

        assert result is True
        assert manager.agent.id == host.id

    @pytest.mark.asyncio
    async def test_multi_agent_spawns_sub_jobs(self, test_db, test_user):
        """Nodes on different agents → spawns sub-jobs for other agents."""
        host_a = _make_host(test_db, "host-a", "Host A")
        host_b = _make_host(test_db, "host-b", "Host B")
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)

        ns1 = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        ns2 = _make_node_state(test_db, lab, "n2", "R2", desired="running", actual="undeployed")
        _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host_a.id)
        _make_node_def(test_db, lab, "n2", "R2", "R2", host_id=host_b.id)

        manager = _make_manager(test_db, lab, job, [ns1.node_id, ns2.node_id])
        manager.node_states = [ns1, ns2]
        manager.db_nodes_map = {
            "R1": test_db.query(models.Node).filter_by(container_name="R1").first(),
            "R2": test_db.query(models.Node).filter_by(container_name="R2").first(),
        }
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.node_lifecycle.safe_create_task") as mock_task:
            mock_ac.is_agent_online = MagicMock(return_value=True)
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


# ---------------------------------------------------------------------------
# _check_resources
# ---------------------------------------------------------------------------


class TestCheckResources:
    @pytest.mark.asyncio
    async def test_skips_when_disabled(self, test_db, test_user, monkeypatch):
        """Resource validation disabled → returns True immediately."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")

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
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        # Node is stopping, not deploying
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        from app.tasks import node_lifecycle
        monkeypatch.setattr(node_lifecycle.settings, "resource_validation_enabled", True)

        result = await manager._check_resources()
        assert result is True

    @pytest.mark.asyncio
    async def test_insufficient_resources_fails(self, test_db, test_user, monkeypatch):
        """Insufficient resources → error state, job failed."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}

        from app.tasks import node_lifecycle
        monkeypatch.setattr(node_lifecycle.settings, "resource_validation_enabled", True)

        # Mock capacity check to fail
        mock_cap_result = MagicMock()
        mock_cap_result.fits = False
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
        host = _make_host(test_db, "host-a", "Host A")
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}

        from app.tasks import node_lifecycle
        monkeypatch.setattr(node_lifecycle.settings, "resource_validation_enabled", True)

        # Mock capacity check to fail
        mock_cap_result = MagicMock()
        mock_cap_result.fits = False
        with patch("app.services.resource_capacity.check_capacity", return_value=mock_cap_result), \
             patch("app.services.resource_capacity.format_capacity_error", return_value="Host A: requires 4096MB RAM, 2048MB available"):
            result = await manager._check_resources()

        assert result is False
        assert ns.actual_state == NodeActualState.ERROR.value
        assert "Host A" in ns.error_message
        # Verify no fallback to another host was attempted
        assert manager.agent.id == host.id


# ---------------------------------------------------------------------------
# _categorize_nodes
# ---------------------------------------------------------------------------


class TestCategorizeNodes:
    def test_deploy_start_stop_groups(self, test_db, test_user):
        """Nodes are correctly categorized into deploy/start/stop groups."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)

        ns_deploy = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        ns_start = _make_node_state(test_db, lab, "n2", "R2", desired="running", actual="stopped")
        ns_stop = _make_node_state(test_db, lab, "n3", "R3", desired="stopped", actual="running")

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
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)

        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]

        deploy, start, stop = manager._categorize_nodes()
        assert len(deploy) == 1
        assert len(start) == 0

    def test_error_categorized_as_start(self, test_db, test_user):
        """Error nodes wanting running are categorized as start."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)

        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="error")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]

        deploy, start, stop = manager._categorize_nodes()
        assert len(deploy) == 0
        assert len(start) == 1

    def test_stopping_categorized_as_stop(self, test_db, test_user):
        """Stopping nodes wanting stopped are categorized as stop."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)

        ns = _make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="stopping")

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
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        _make_placement(test_db, lab, "R1", host.id)

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
        """Migration stops container on old agent before deploying to new."""
        old_host = _make_host(test_db, "old-host", "Old Host")
        new_host = _make_host(test_db, "new-host", "New Host")
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        _make_placement(test_db, lab, "R1", old_host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=new_host)
        manager.node_states = [ns]
        manager.db_nodes_map = {}
        manager._refresh_placements()

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock):
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.container_action = AsyncMock(return_value={"success": True})
            await manager._handle_migration([ns])

        # container_action called to stop on old host
        mock_ac.container_action.assert_called()
        call_args = mock_ac.container_action.call_args
        assert call_args[0][0].id == old_host.id  # old agent
        assert call_args[0][2] == "stop"  # action

    @pytest.mark.asyncio
    async def test_migration_deletes_old_placement(self, test_db, test_user):
        """Migration removes old placement records."""
        old_host = _make_host(test_db, "old-host", "Old Host")
        new_host = _make_host(test_db, "new-host", "New Host")
        lab = _make_lab(test_db, test_user)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        _make_placement(test_db, lab, "R1", old_host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=new_host)
        manager.node_states = [ns]
        manager.db_nodes_map = {}
        manager._refresh_placements()

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock):
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.container_action = AsyncMock(return_value={"success": True})
            await manager._handle_migration([ns])

        # Old placement should be deleted
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
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.container_action = AsyncMock(return_value={"success": True})
            await manager._stop_nodes([ns])

        assert ns.actual_state == NodeActualState.STOPPED.value
        assert ns.error_message is None
        assert ns.is_ready is False

    @pytest.mark.asyncio
    async def test_stop_failure_sets_error(self, test_db, test_user):
        """Failed stop sets error state with error message."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.container_action = AsyncMock(
                return_value={"success": False, "error": "Container busy"}
            )
            await manager._stop_nodes([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Container busy"

    @pytest.mark.asyncio
    async def test_stop_uses_placement_agent(self, test_db, test_user):
        """Stop uses actual container location from placements."""
        main_host = _make_host(test_db, "main-host", "Main Host")
        actual_host = _make_host(test_db, "actual-host", "Actual Host")
        lab = _make_lab(test_db, test_user, agent_id=main_host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        _make_placement(test_db, lab, "R1", actual_host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=main_host)
        manager.node_states = [ns]
        manager._refresh_placements()

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.container_action = AsyncMock(return_value={"success": True})
            await manager._stop_nodes([ns])

        # Should have called container_action on actual_host, not main_host
        call_args = mock_ac.container_action.call_args
        assert call_args[0][0].id == actual_host.id

    @pytest.mark.asyncio
    async def test_transient_error_preserves_state(self, test_db, test_user):
        """AgentUnavailableError keeps current state (transient)."""
        from app.agent_client import AgentUnavailableError

        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.container_action = AsyncMock(
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
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

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
             patch("app.tasks.node_lifecycle.graph_to_deploy_topology", return_value={}), \
             patch("app.tasks.jobs.acquire_deploy_lock", return_value=(True, [])), \
             patch("app.tasks.jobs.release_deploy_lock"), \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock), \
             patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock), \
             patch("app.tasks.jobs._cleanup_orphan_containers", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.node_lifecycle.settings") as mock_settings:
            mock_settings.per_node_lifecycle_enabled = False
            mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})
            await manager._deploy_nodes([ns])

        assert ns.actual_state == NodeActualState.RUNNING.value

    @pytest.mark.asyncio
    async def test_deploy_failure_sets_error(self, test_db, test_user):
        """Deploy failure sets error state on affected nodes."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

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
             patch("app.tasks.node_lifecycle.graph_to_deploy_topology", return_value={}), \
             patch("app.tasks.jobs.acquire_deploy_lock", return_value=(True, [])), \
             patch("app.tasks.jobs.release_deploy_lock"), \
             patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.node_lifecycle.settings") as mock_settings:
            mock_settings.per_node_lifecycle_enabled = False
            mock_ac.deploy_to_agent = AsyncMock(
                return_value={"status": "failed", "error_message": "Timeout"}
            )
            await manager._deploy_nodes([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Timeout"

    @pytest.mark.asyncio
    async def test_deploy_lock_conflict_sets_error(self, test_db, test_user):
        """Lock conflict → error state."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

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
             patch("app.tasks.node_lifecycle.graph_to_deploy_topology", return_value={}), \
             patch("app.tasks.jobs.acquire_deploy_lock", return_value=(False, ["R1"])), \
             patch("app.tasks.node_lifecycle.settings") as mock_settings:
            mock_settings.per_node_lifecycle_enabled = False
            await manager._deploy_nodes([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "Deploy lock conflict" in ns.error_message

    @pytest.mark.asyncio
    async def test_no_topology_sets_error(self, test_db, test_user):
        """No topology defined → error state."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        with patch.object(manager.topo_service, "has_nodes", return_value=False), \
             patch("app.tasks.node_lifecycle.settings") as mock_settings:
            mock_settings.per_node_lifecycle_enabled = False
            await manager._deploy_nodes([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "No topology" in ns.error_message

    @pytest.mark.asyncio
    async def test_transient_error_keeps_pending(self, test_db, test_user):
        """AgentUnavailableError → pending (not error), preserves retryability."""
        from app.agent_client import AgentUnavailableError

        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

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
             patch("app.tasks.node_lifecycle.graph_to_deploy_topology", return_value={}), \
             patch("app.tasks.jobs.acquire_deploy_lock", return_value=(True, [])), \
             patch("app.tasks.jobs.release_deploy_lock"), \
             patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.node_lifecycle.settings") as mock_settings:
            mock_settings.per_node_lifecycle_enabled = False
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
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        result = await manager._finalize()

        assert result.success is True
        assert result.error_count == 0
        assert job.status == JobStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_some_errors(self, test_db, test_user):
        """Some nodes in error → failed status with error count."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns_ok = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")
        ns_err = _make_node_state(test_db, lab, "n2", "R2", desired="running", actual="error")

        manager = _make_manager(test_db, lab, job, [ns_ok.node_id, ns_err.node_id], agent=host)
        manager.node_states = [ns_ok, ns_err]

        result = await manager._finalize()

        assert result.success is False
        assert result.error_count == 1
        assert job.status == JobStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_sets_completed_at(self, test_db, test_user):
        """Finalize always sets completed_at."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        await manager._finalize()

        assert job.completed_at is not None


# ---------------------------------------------------------------------------
# Full execute() orchestration
# ---------------------------------------------------------------------------


class TestExecuteOrchestration:
    @pytest.mark.asyncio
    async def test_noop_when_all_in_desired_state(self, test_db, test_user):
        """Execute returns noop when all nodes already in desired state."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")

        manager = _make_manager(test_db, lab, job, ["n1"])
        with patch.object(manager.topo_service, "get_nodes", return_value=[]):
            result = await manager.execute()

        assert result.success is True
        assert job.status == JobStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_deploy_flow(self, test_db, test_user):
        """Full deploy flow: undeployed → pending → running."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

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

        with patch.object(manager.topo_service, "get_nodes", return_value=[node_def]), \
             patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch.object(manager.topo_service, "has_nodes", return_value=True), \
             patch.object(manager, "_filter_topology_for_agent", return_value=(mock_graph, {"R1"})), \
             patch.object(manager, "_validate_topology_placement", return_value=[]), \
             patch("app.tasks.node_lifecycle.graph_to_deploy_topology", return_value={}), \
             patch("app.tasks.jobs.acquire_deploy_lock", return_value=(True, [])), \
             patch("app.tasks.jobs.release_deploy_lock"), \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock), \
             patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock), \
             patch("app.tasks.jobs._cleanup_orphan_containers", new_callable=AsyncMock), \
             patch("app.tasks.jobs._create_cross_host_links_if_ready", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle.settings") as mock_settings:
            mock_settings.resource_validation_enabled = False
            mock_settings.image_sync_enabled = False
            mock_settings.image_sync_pre_deploy_check = False
            mock_settings.per_node_lifecycle_enabled = False
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.get_healthy_agent = AsyncMock(return_value=None)
            mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})

            result = await manager.execute()

        assert result.success is True
        assert job.status == JobStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_stop_flow(self, test_db, test_user):
        """Full stop flow: running → stopping → stopped."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1"])

        with patch.object(manager.topo_service, "get_nodes", return_value=[node_def]), \
             patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.jobs._create_cross_host_links_if_ready", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle.settings") as mock_settings:
            mock_settings.resource_validation_enabled = False
            mock_settings.image_sync_enabled = False
            mock_settings.image_sync_pre_deploy_check = False
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.get_healthy_agent = AsyncMock(return_value=None)
            mock_ac.container_action = AsyncMock(return_value={"success": True})

            result = await manager.execute()

        assert result.success is True
        assert ns.actual_state == NodeActualState.STOPPED.value
        assert job.status == JobStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_mixed_deploy_and_stop(self, test_db, test_user):
        """Mixed: one node deploys, another stops — both succeed."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns_deploy = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        ns_stop = _make_node_state(test_db, lab, "n2", "R2", desired="stopped", actual="running")
        node_def1 = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)
        node_def2 = _make_node_def(test_db, lab, "n2", "R2", "R2", host_id=host.id)

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

        with patch.object(manager.topo_service, "get_nodes", return_value=[node_def1, node_def2]), \
             patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch.object(manager.topo_service, "has_nodes", return_value=True), \
             patch.object(manager, "_filter_topology_for_agent", return_value=(mock_graph, {"R1"})), \
             patch.object(manager, "_validate_topology_placement", return_value=[]), \
             patch("app.tasks.node_lifecycle.graph_to_deploy_topology", return_value={}), \
             patch("app.tasks.jobs.acquire_deploy_lock", return_value=(True, [])), \
             patch("app.tasks.jobs.release_deploy_lock"), \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock), \
             patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock), \
             patch("app.tasks.jobs._cleanup_orphan_containers", new_callable=AsyncMock), \
             patch("app.tasks.jobs._create_cross_host_links_if_ready", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle.settings") as mock_settings:
            mock_settings.resource_validation_enabled = False
            mock_settings.image_sync_enabled = False
            mock_settings.image_sync_pre_deploy_check = False
            mock_settings.per_node_lifecycle_enabled = False
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.get_healthy_agent = AsyncMock(return_value=None)
            mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})
            mock_ac.container_action = AsyncMock(return_value={"success": True})

            result = await manager.execute()

        assert result.success is True
        assert job.status == JobStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_resource_check_before_migration(self, test_db, test_user, monkeypatch):
        """Phase 2.2: Resource check runs BEFORE migration."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1"])

        call_order = []

        async def mock_check_resources():
            call_order.append("check_resources")
            return False  # Fail resources

        async def mock_handle_migration(nodes):
            call_order.append("handle_migration")

        with patch.object(manager.topo_service, "get_nodes", return_value=[node_def]), \
             patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.node_lifecycle.settings") as mock_settings:
            mock_settings.resource_validation_enabled = True
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.get_healthy_agent = AsyncMock(return_value=None)

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
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        # R1 will fail to deploy, R2 should still stop
        ns_deploy = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        ns_stop = _make_node_state(test_db, lab, "n2", "R2", desired="stopped", actual="running")
        node_def1 = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)
        node_def2 = _make_node_def(test_db, lab, "n2", "R2", "R2", host_id=host.id)

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

        with patch.object(manager.topo_service, "get_nodes", return_value=[node_def1, node_def2]), \
             patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch.object(manager.topo_service, "has_nodes", return_value=True), \
             patch.object(manager, "_filter_topology_for_agent", return_value=(mock_graph, {"R1"})), \
             patch.object(manager, "_validate_topology_placement", return_value=[]), \
             patch("app.tasks.node_lifecycle.graph_to_deploy_topology", return_value={}), \
             patch("app.tasks.jobs.acquire_deploy_lock", return_value=(True, [])), \
             patch("app.tasks.jobs.release_deploy_lock"), \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock), \
             patch("app.tasks.jobs._create_cross_host_links_if_ready", new_callable=AsyncMock), \
             patch("app.tasks.node_lifecycle.settings") as mock_settings:
            mock_settings.resource_validation_enabled = False
            mock_settings.image_sync_enabled = False
            mock_settings.image_sync_pre_deploy_check = False
            mock_settings.per_node_lifecycle_enabled = False
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.get_healthy_agent = AsyncMock(return_value=None)
            # Deploy fails
            mock_ac.deploy_to_agent = AsyncMock(
                return_value={"status": "failed", "error_message": "Deploy error"}
            )
            # Stop succeeds
            mock_ac.container_action = AsyncMock(return_value={"success": True})

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
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", device="linux", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock), \
             patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock), \
             patch.object(manager.topo_service, "get_interface_count_map", return_value={"R1": 4}), \
             patch.object(manager, "_connect_same_host_links", new_callable=AsyncMock):
            mock_ac.create_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.start_node_on_agent = AsyncMock(return_value={"success": True})
            await manager._deploy_nodes_per_node([ns])

        assert ns.actual_state == NodeActualState.RUNNING.value
        assert ns.error_message is None
        assert ns.boot_started_at is not None
        mock_ac.create_node_on_agent.assert_called_once()
        mock_ac.start_node_on_agent.assert_called_once()

    @pytest.mark.asyncio
    async def test_deploy_per_node_create_failure(self, test_db, test_user):
        """Per-node deploy: create failure sets error state."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
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
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
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
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")

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
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.jobs._update_node_placements", new_callable=AsyncMock), \
             patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock), \
             patch.object(manager.topo_service, "get_interface_count_map", return_value={}), \
             patch.object(manager, "_connect_same_host_links", new_callable=AsyncMock) as mock_links:
            mock_ac.create_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.start_node_on_agent = AsyncMock(return_value={"success": True})
            await manager._deploy_nodes_per_node([ns])

        mock_links.assert_called_once_with({"R1"})


class TestStartNodesPerNode:
    """Test _start_nodes_per_node — per-node start with veth repair."""

    @pytest.mark.asyncio
    async def test_start_per_node_success(self, test_db, test_user):
        """Per-node start calls start_node_on_agent (not deploy_to_agent)."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock), \
             patch.object(manager, "_connect_same_host_links", new_callable=AsyncMock):
            mock_ac.start_node_on_agent = AsyncMock(return_value={"success": True})
            # deploy_to_agent should NOT be called
            mock_ac.deploy_to_agent = AsyncMock()
            await manager._start_nodes_per_node([ns])

        assert ns.actual_state == NodeActualState.RUNNING.value
        assert ns.starting_started_at is None
        assert ns.boot_started_at is not None
        mock_ac.start_node_on_agent.assert_called_once()
        mock_ac.deploy_to_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_per_node_failure(self, test_db, test_user):
        """Per-node start failure sets error state."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac:
            mock_ac.start_node_on_agent = AsyncMock(
                return_value={"success": False, "error": "Network error"}
            )
            await manager._start_nodes_per_node([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "Network error" in ns.error_message

    @pytest.mark.asyncio
    async def test_start_per_node_reconnects_links(self, test_db, test_user):
        """Per-node start reconnects same-host links."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}

        with patch("app.tasks.node_lifecycle.agent_client") as mock_ac, \
             patch("app.tasks.jobs._capture_node_ips", new_callable=AsyncMock), \
             patch.object(manager, "_connect_same_host_links", new_callable=AsyncMock) as mock_links:
            mock_ac.start_node_on_agent = AsyncMock(return_value={"success": True})
            await manager._start_nodes_per_node([ns])

        mock_links.assert_called_once_with({"R1"})


class TestDeployDispatch:
    """Test _deploy_nodes dispatches based on per_node_lifecycle_enabled."""

    @pytest.mark.asyncio
    async def test_dispatches_to_per_node_when_enabled(self, test_db, test_user, monkeypatch):
        """With per_node_lifecycle_enabled=True, uses per-node path."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        from app.tasks import node_lifecycle
        monkeypatch.setattr(node_lifecycle.settings, "per_node_lifecycle_enabled", True)

        manager._deploy_nodes_per_node = AsyncMock()
        manager._deploy_nodes_topology = AsyncMock()

        await manager._deploy_nodes([ns])

        manager._deploy_nodes_per_node.assert_called_once_with([ns])
        manager._deploy_nodes_topology.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatches_to_topology_when_disabled(self, test_db, test_user, monkeypatch):
        """With per_node_lifecycle_enabled=False, uses topology path."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="pending")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        from app.tasks import node_lifecycle
        monkeypatch.setattr(node_lifecycle.settings, "per_node_lifecycle_enabled", False)

        manager._deploy_nodes_per_node = AsyncMock()
        manager._deploy_nodes_topology = AsyncMock()

        await manager._deploy_nodes([ns])

        manager._deploy_nodes_topology.assert_called_once_with([ns])
        manager._deploy_nodes_per_node.assert_not_called()


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
