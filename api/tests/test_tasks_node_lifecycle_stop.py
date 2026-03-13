"""Tests for StopMixin in node_lifecycle_stop.py.

Covers:
- _stop_nodes: batch stop sequencing, state transitions, agent grouping,
  fallback logic, partial failures, transient errors
- _apply_stop_result: success/failure result handling
- _auto_extract_before_stop: config extraction before stop, timeouts,
  extraction errors
- _converge_stopped_desired_error_states: error-to-stopped normalization
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_client import AgentUnavailableError
from app.state import NodeActualState, NodeDesiredState
from app.tasks.node_lifecycle import NodeLifecycleManager, _get_container_name
from tests.factories import make_host, make_job, make_lab, make_node, make_node_state, make_placement


# ---------------------------------------------------------------------------
# Helpers (mirrors test_node_lifecycle.py conventions)
# ---------------------------------------------------------------------------


def _make_manager(session, lab, job, node_ids, agent=None):
    """Create a NodeLifecycleManager with common mocks applied."""
    manager = NodeLifecycleManager(session, lab, job, node_ids)
    if agent:
        manager.agent = agent
        manager.target_agent_id = agent.id
    # Disable broadcasts by default in tests
    manager._broadcast_state = MagicMock()
    return manager


# ---------------------------------------------------------------------------
# _stop_nodes — basic sequencing and state transitions
# ---------------------------------------------------------------------------


class TestStopNodesSequencing:
    """Tests for the core _stop_nodes batch stop flow."""

    @pytest.mark.asyncio
    async def test_successful_single_node_stop(self, test_db, test_user):
        """A single running node is stopped successfully via batch reconcile."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running",
                             stopping_started_at=datetime.now(timezone.utc))
        container_name = _get_container_name(lab.id, "R1")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.reconcile_nodes_on_agent = AsyncMock(return_value={
                "results": [{"container_name": container_name, "success": True}]
            })
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={"success": False})
            await manager._stop_nodes([ns])

        assert ns.actual_state == NodeActualState.STOPPED.value
        assert ns.error_message is None
        assert ns.is_ready is False
        assert ns.boot_started_at is None
        assert ns.stopping_started_at is not None  # Kept for graceful shutdown guard

    @pytest.mark.asyncio
    async def test_stop_multiple_nodes_same_agent(self, test_db, test_user):
        """Multiple nodes on the same agent are stopped in a single batch."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns1 = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        ns2 = make_node_state(test_db, lab, "n2", "R2", desired="stopped", actual="running")
        cn1 = _get_container_name(lab.id, "R1")
        cn2 = _get_container_name(lab.id, "R2")

        manager = _make_manager(test_db, lab, job, [ns1.node_id, ns2.node_id], agent=host)
        manager.node_states = [ns1, ns2]
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.reconcile_nodes_on_agent = AsyncMock(return_value={
                "results": [
                    {"container_name": cn1, "success": True},
                    {"container_name": cn2, "success": True},
                ]
            })
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={"success": False})
            await manager._stop_nodes([ns1, ns2])

        assert ns1.actual_state == NodeActualState.STOPPED.value
        assert ns2.actual_state == NodeActualState.STOPPED.value
        # Should have called reconcile exactly once (one batch)
        assert mock_ac.reconcile_nodes_on_agent.call_count == 1

    @pytest.mark.asyncio
    async def test_stop_skips_nodes_whose_desired_state_changed(self, test_db, test_user):
        """Nodes whose desired_state changed to 'running' since job was queued are skipped."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        # Change desired state AFTER manager was created (simulates user action)
        ns.desired_state = NodeDesiredState.RUNNING.value
        test_db.commit()

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.reconcile_nodes_on_agent = AsyncMock()
            await manager._stop_nodes([ns])

        # No reconcile call should happen — node was filtered out
        mock_ac.reconcile_nodes_on_agent.assert_not_called()
        # State should be unchanged (still running)
        assert ns.actual_state == "running"

    @pytest.mark.asyncio
    async def test_stop_calls_auto_extract_before_reconcile(self, test_db, test_user):
        """_stop_nodes calls _auto_extract_before_stop before sending reconcile."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
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
                return {"results": [{"container_name": container_name, "success": True}]}

            mock_ac.reconcile_nodes_on_agent = mock_reconcile
            await manager._stop_nodes([ns])

        assert call_order == ["extract", "reconcile"]


# ---------------------------------------------------------------------------
# _stop_nodes — state transitions during stop
# ---------------------------------------------------------------------------


class TestStopStateTransitions:
    """State transitions: running -> stopped, running -> error."""

    @pytest.mark.asyncio
    async def test_running_to_stopped_on_success(self, test_db, test_user):
        """Successful stop transitions actual_state from running to stopped."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        ns.is_ready = True
        ns.boot_started_at = datetime.now(timezone.utc)
        ns.stopping_started_at = datetime.now(timezone.utc)
        test_db.commit()
        container_name = _get_container_name(lab.id, "R1")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.reconcile_nodes_on_agent = AsyncMock(return_value={
                "results": [{"container_name": container_name, "success": True}]
            })
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={"success": False})
            await manager._stop_nodes([ns])

        assert ns.actual_state == NodeActualState.STOPPED.value
        assert ns.is_ready is False
        assert ns.boot_started_at is None
        assert ns.stopping_started_at is not None  # Kept for graceful shutdown guard
        assert ns.error_message is None

    @pytest.mark.asyncio
    async def test_running_to_error_on_failure(self, test_db, test_user):
        """Failed stop transitions actual_state from running to error."""
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
                "results": [{
                    "container_name": container_name,
                    "success": False,
                    "error": "Docker daemon error",
                }]
            })
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={"success": False})
            await manager._stop_nodes([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Docker daemon error"
        assert ns.is_ready is False

    @pytest.mark.asyncio
    async def test_stop_broadcasts_on_success(self, test_db, test_user):
        """Successful stop broadcasts stopped state via _broadcast_state."""
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
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={"success": False})
            await manager._stop_nodes([ns])

        manager._broadcast_state.assert_called_with(ns, name_suffix="stopped")

    @pytest.mark.asyncio
    async def test_stop_broadcasts_on_error(self, test_db, test_user):
        """Failed stop broadcasts error state via _broadcast_state."""
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
                "results": [{
                    "container_name": container_name,
                    "success": False,
                    "error": "Fail",
                }]
            })
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={"success": False})
            await manager._stop_nodes([ns])

        manager._broadcast_state.assert_called_with(ns, name_suffix="error")


# ---------------------------------------------------------------------------
# _stop_nodes — agent communication failures
# ---------------------------------------------------------------------------


class TestStopAgentFailures:
    """Agent communication failures during stop operations."""

    @pytest.mark.asyncio
    async def test_agent_unavailable_preserves_state(self, test_db, test_user):
        """AgentUnavailableError is transient: state is preserved, not set to error."""
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
                side_effect=AgentUnavailableError("Connection refused")
            )
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={"success": False})
            await manager._stop_nodes([ns])

        # Transient: state should NOT change to error
        assert ns.actual_state == "running"
        assert "transient" in ns.error_message.lower()

    @pytest.mark.asyncio
    async def test_generic_exception_sets_error(self, test_db, test_user):
        """Non-transient exception sets error state on all nodes in the batch."""
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
                side_effect=RuntimeError("Connection reset")
            )
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={"success": False})
            await manager._stop_nodes([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "Connection reset" in ns.error_message
        assert ns.is_ready is False

    @pytest.mark.asyncio
    async def test_fallback_to_default_agent_on_not_found(self, test_db, test_user):
        """Container not found on placement agent triggers fallback to default agent."""
        main_host = make_host(test_db, "main-host", "Main Host")
        remote_host = make_host(test_db, "remote-host", "Remote Host")
        lab = make_lab(test_db, test_user, agent_id=main_host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        make_placement(test_db, lab, "R1", remote_host.id)
        container_name = _get_container_name(lab.id, "R1")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=main_host)
        manager.node_states = [ns]
        manager._refresh_placements()

        call_agents = []

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)

            async def mock_reconcile(agent, lab_id, batch):
                call_agents.append(agent.id)
                if agent.id == remote_host.id:
                    # Not found on remote — triggers fallback
                    return {
                        "results": [{
                            "container_name": container_name,
                            "success": False,
                            "error": "Container not found",
                        }]
                    }
                else:
                    # Found on main (fallback)
                    return {
                        "results": [{
                            "container_name": container_name,
                            "success": True,
                        }]
                    }

            mock_ac.reconcile_nodes_on_agent = mock_reconcile
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={"success": False})
            await manager._stop_nodes([ns])

        assert call_agents == [remote_host.id, main_host.id]
        assert ns.actual_state == NodeActualState.STOPPED.value

    @pytest.mark.asyncio
    async def test_fallback_agent_unavailable_is_transient(self, test_db, test_user):
        """AgentUnavailableError on fallback agent is treated as transient."""
        main_host = make_host(test_db, "main-host", "Main Host")
        remote_host = make_host(test_db, "remote-host", "Remote Host")
        lab = make_lab(test_db, test_user, agent_id=main_host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        make_placement(test_db, lab, "R1", remote_host.id)
        container_name = _get_container_name(lab.id, "R1")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=main_host)
        manager.node_states = [ns]
        manager._refresh_placements()

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)

            call_count = 0

            async def mock_reconcile(agent, lab_id, batch):
                nonlocal call_count
                call_count += 1
                if agent.id == remote_host.id:
                    return {
                        "results": [{
                            "container_name": container_name,
                            "success": False,
                            "error": "Container not found",
                        }]
                    }
                else:
                    raise AgentUnavailableError("Fallback agent down")

            mock_ac.reconcile_nodes_on_agent = mock_reconcile
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={"success": False})
            await manager._stop_nodes([ns])

        # Transient on fallback — state preserved
        assert ns.actual_state == "running"
        assert "transient" in ns.error_message.lower()

    @pytest.mark.asyncio
    async def test_fallback_generic_exception_sets_error(self, test_db, test_user):
        """Non-transient exception on fallback agent sets error state."""
        main_host = make_host(test_db, "main-host", "Main Host")
        remote_host = make_host(test_db, "remote-host", "Remote Host")
        lab = make_lab(test_db, test_user, agent_id=main_host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        make_placement(test_db, lab, "R1", remote_host.id)
        container_name = _get_container_name(lab.id, "R1")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=main_host)
        manager.node_states = [ns]
        manager._refresh_placements()

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)

            async def mock_reconcile(agent, lab_id, batch):
                if agent.id == remote_host.id:
                    return {
                        "results": [{
                            "container_name": container_name,
                            "success": False,
                            "error": "Container not found",
                        }]
                    }
                else:
                    raise RuntimeError("Disk full")

            mock_ac.reconcile_nodes_on_agent = mock_reconcile
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={"success": False})
            await manager._stop_nodes([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "Disk full" in ns.error_message


# ---------------------------------------------------------------------------
# _stop_nodes — multi-agent grouping
# ---------------------------------------------------------------------------


class TestStopMultiAgentGrouping:
    """Stop groups nodes by agent and dispatches parallel batches."""

    @pytest.mark.asyncio
    async def test_nodes_grouped_by_placement_agent(self, test_db, test_user):
        """Nodes placed on different agents result in separate batch calls."""
        host_a = make_host(test_db, "host-a", "Host A")
        host_b = make_host(test_db, "host-b", "Host B")
        lab = make_lab(test_db, test_user, agent_id=host_a.id)
        job = make_job(test_db, lab, test_user)
        ns1 = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        ns2 = make_node_state(test_db, lab, "n2", "R2", desired="stopped", actual="running")
        make_placement(test_db, lab, "R1", host_a.id)
        make_placement(test_db, lab, "R2", host_b.id)
        _get_container_name(lab.id, "R1")
        _get_container_name(lab.id, "R2")

        manager = _make_manager(test_db, lab, job, [ns1.node_id, ns2.node_id], agent=host_a)
        manager.node_states = [ns1, ns2]
        manager._refresh_placements()

        agents_called = []

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)

            async def mock_reconcile(agent, lab_id, batch):
                agents_called.append(agent.id)
                return {
                    "results": [
                        {"container_name": b["container_name"], "success": True}
                        for b in batch
                    ]
                }

            mock_ac.reconcile_nodes_on_agent = mock_reconcile
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={"success": False})
            await manager._stop_nodes([ns1, ns2])

        assert set(agents_called) == {host_a.id, host_b.id}
        assert ns1.actual_state == NodeActualState.STOPPED.value
        assert ns2.actual_state == NodeActualState.STOPPED.value

    @pytest.mark.asyncio
    async def test_offline_placement_agent_falls_back_to_default(self, test_db, test_user):
        """When placement agent is offline, stop falls back to default agent."""
        main_host = make_host(test_db, "main-host", "Main Host")
        offline_host = make_host(test_db, "offline-host", "Offline Host", status="offline")
        lab = make_lab(test_db, test_user, agent_id=main_host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        make_placement(test_db, lab, "R1", offline_host.id)
        container_name = _get_container_name(lab.id, "R1")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=main_host)
        manager.node_states = [ns]
        manager._refresh_placements()

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(
                side_effect=lambda a: a.id == main_host.id
            )
            mock_ac.reconcile_nodes_on_agent = AsyncMock(return_value={
                "results": [{"container_name": container_name, "success": True}]
            })
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={"success": False})
            await manager._stop_nodes([ns])

        # Should have used main_host since offline_host is not online
        call_args = mock_ac.reconcile_nodes_on_agent.call_args
        assert call_args[0][0].id == main_host.id
        assert ns.actual_state == NodeActualState.STOPPED.value


# ---------------------------------------------------------------------------
# _apply_stop_result — unit tests for result application
# ---------------------------------------------------------------------------


class TestApplyStopResult:
    """Unit tests for _apply_stop_result method."""

    def test_success_result_sets_stopped(self, test_db, test_user):
        """Successful result transitions to stopped and clears error."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        ns.error_message = "Previous error"
        ns.is_ready = True
        ns.stopping_started_at = datetime.now(timezone.utc)
        test_db.commit()

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        manager._apply_stop_result(ns, {"success": True}, host)

        assert ns.actual_state == NodeActualState.STOPPED.value
        assert ns.error_message is None
        assert ns.is_ready is False
        assert ns.boot_started_at is None
        assert ns.stopping_started_at is not None  # Kept for graceful shutdown guard
        manager._broadcast_state.assert_called_once_with(ns, name_suffix="stopped")

    def test_failure_result_sets_error(self, test_db, test_user):
        """Failed result sets error state with error message."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        manager._apply_stop_result(
            ns, {"success": False, "error": "Timeout"}, host
        )

        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Timeout"
        assert ns.is_ready is False
        manager._broadcast_state.assert_called_once_with(ns, name_suffix="error")

    def test_failure_result_default_error_message(self, test_db, test_user):
        """Failed result with no error key uses default message."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        manager._apply_stop_result(ns, {"success": False}, host)

        assert ns.error_message == "Stop failed"


# ---------------------------------------------------------------------------
# _auto_extract_before_stop
# ---------------------------------------------------------------------------


class TestAutoExtractBeforeStop:
    """Tests for config auto-extraction before stop."""

    @pytest.mark.asyncio
    async def test_skips_non_running_nodes(self, test_db, test_user):
        """Only running/stopping nodes have configs extracted."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="stopped")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.extract_configs_on_agent = AsyncMock()
            await manager._auto_extract_before_stop([ns])

        # No extraction attempted for already-stopped node
        mock_ac.extract_configs_on_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_extraction_timeout_does_not_block_stop(self, test_db, test_user):
        """Extraction timeout is logged but does not prevent stop."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        async def slow_extract(*args, **kwargs):
            await asyncio.sleep(60)  # Will be cancelled by timeout

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.extract_configs_on_agent = slow_extract

            # Patch EXTRACTION_TIMEOUT to a very short value
            with patch.object(
                type(manager), "_auto_extract_before_stop",
                wraps=manager._auto_extract_before_stop,
            ):
                # The method itself catches TimeoutError and returns
                await manager._auto_extract_before_stop([ns])

        # Should have logged timeout message
        assert any("timed out" in p.lower() for p in manager.log_parts)

    @pytest.mark.asyncio
    async def test_extraction_failure_does_not_block_stop(self, test_db, test_user):
        """Extraction exception is caught and does not block stop.

        When extract_configs_on_agent raises, asyncio.gather(return_exceptions=True)
        captures it. The code then skips configs from that agent, resulting in
        "No configs extracted" in log_parts (the exception itself is logged via logger).
        """
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.extract_configs_on_agent = AsyncMock(
                side_effect=RuntimeError("Agent exploded")
            )

            # Should not raise
            await manager._auto_extract_before_stop([ns])

        # The exception is caught by gather(return_exceptions=True);
        # no configs are collected, so "No configs extracted" is logged.
        assert any("no configs extracted" in p.lower() for p in manager.log_parts)

    @pytest.mark.asyncio
    async def test_successful_extraction_creates_snapshots(self, test_db, test_user):
        """Successful extraction saves autosave snapshots via ConfigService."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        make_node(test_db, lab, "n1", "R1", "R1", device="linux", host_id=host.id)

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        mock_snapshot = MagicMock()

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac, \
             patch("app.services.config_service.ConfigService.save_extracted_config",
                   return_value=mock_snapshot) as mock_save:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={
                "success": True,
                "configs": [
                    {"node_name": "R1", "content": "hostname R1\n"},
                ],
            })

            await manager._auto_extract_before_stop([ns])

        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args
        assert call_kwargs[1]["node_name"] == "R1" or call_kwargs.kwargs.get("node_name") == "R1"
        assert any("1 autosave snapshot" in p for p in manager.log_parts)


# ---------------------------------------------------------------------------
# _converge_stopped_desired_error_states
# ---------------------------------------------------------------------------


class TestConvergeStoppedDesiredErrorStates:
    """Normalize desired=stopped + actual=error to stopped."""

    def test_error_to_stopped_when_desired_stopped(self, test_db, test_user):
        """desired=stopped + actual=error normalizes to actual=stopped."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="error")
        ns.error_message = "Stop failed previously"
        ns.enforcement_attempts = 3
        ns.image_sync_status = "failed"
        ns.image_sync_message = "sync error"
        test_db.commit()

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        normalized = manager._converge_stopped_desired_error_states()

        assert normalized == 1
        assert ns.actual_state == NodeActualState.STOPPED.value
        assert ns.error_message is None
        assert ns.image_sync_status is None
        assert ns.image_sync_message is None
        assert ns.stopping_started_at is None
        assert ns.starting_started_at is None
        assert ns.boot_started_at is None
        assert ns.is_ready is False
        assert ns.enforcement_attempts == 0
        manager._broadcast_state.assert_called_once_with(ns, name_suffix="stopped")

    def test_does_not_touch_running_nodes(self, test_db, test_user):
        """Nodes with desired=running are not normalized."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="error")
        ns.error_message = "Deploy failed"
        test_db.commit()

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        normalized = manager._converge_stopped_desired_error_states()

        assert normalized == 0
        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Deploy failed"

    def test_does_not_touch_already_stopped(self, test_db, test_user):
        """Nodes already stopped are not counted as normalized."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="stopped")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]

        normalized = manager._converge_stopped_desired_error_states()

        assert normalized == 0
        manager._broadcast_state.assert_not_called()

    def test_multiple_error_nodes_all_normalized(self, test_db, test_user):
        """All desired=stopped + actual=error nodes are normalized in one call."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns1 = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="error")
        ns2 = make_node_state(test_db, lab, "n2", "R2", desired="stopped", actual="error")

        manager = _make_manager(test_db, lab, job, [ns1.node_id, ns2.node_id], agent=host)
        manager.node_states = [ns1, ns2]

        normalized = manager._converge_stopped_desired_error_states()

        assert normalized == 2
        assert ns1.actual_state == NodeActualState.STOPPED.value
        assert ns2.actual_state == NodeActualState.STOPPED.value


# ---------------------------------------------------------------------------
# _stop_nodes — partial failure recovery
# ---------------------------------------------------------------------------


class TestStopPartialFailure:
    """Partial stop recovery: some nodes succeed, some fail."""

    @pytest.mark.asyncio
    async def test_partial_stop_isolates_errors(self, test_db, test_user):
        """In a batch, one node fails and another succeeds independently."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns1 = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        ns2 = make_node_state(test_db, lab, "n2", "R2", desired="stopped", actual="running")
        cn1 = _get_container_name(lab.id, "R1")
        cn2 = _get_container_name(lab.id, "R2")

        manager = _make_manager(test_db, lab, job, [ns1.node_id, ns2.node_id], agent=host)
        manager.node_states = [ns1, ns2]
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.reconcile_nodes_on_agent = AsyncMock(return_value={
                "results": [
                    {"container_name": cn1, "success": True},
                    {"container_name": cn2, "success": False, "error": "Cannot stop"},
                ]
            })
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={"success": False})
            await manager._stop_nodes([ns1, ns2])

        assert ns1.actual_state == NodeActualState.STOPPED.value
        assert ns2.actual_state == NodeActualState.ERROR.value
        assert ns2.error_message == "Cannot stop"

    @pytest.mark.asyncio
    async def test_no_result_for_node_uses_empty_dict(self, test_db, test_user):
        """When agent returns no result for a node, empty dict is used (treated as failure)."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        _get_container_name(lab.id, "R1")

        manager = _make_manager(test_db, lab, job, [ns.node_id], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {}

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            # Return results with a different container_name (so R1 has no match)
            mock_ac.reconcile_nodes_on_agent = AsyncMock(return_value={
                "results": [{"container_name": "archetype-lab-OTHER", "success": True}]
            })
            mock_ac.extract_configs_on_agent = AsyncMock(return_value={"success": False})
            await manager._stop_nodes([ns])

        # No result found -> empty dict -> success=False -> error state
        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "Stop failed"