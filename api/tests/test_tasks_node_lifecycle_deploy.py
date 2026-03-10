"""Tests for DeploymentMixin (node_lifecycle_deploy.py) and StopMixin (node_lifecycle_stop.py).

Covers deploy/start/stop dispatching, per-node lifecycle, topology deploy,
same-host link connection, startup config resolution, and auto-extract.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import models
from app.agent_client import AgentUnavailableError
from app.state import NodeActualState
from app.tasks.node_lifecycle import (
    NodeLifecycleManager,
    _get_container_name,
)
from tests.factories import make_host, make_job, make_lab, make_node, make_node_state, make_placement


# ---------------------------------------------------------------------------
# Helpers
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
# TestDeployNodes — dispatch routing
# ---------------------------------------------------------------------------


class TestDeployNodes:
    """Tests for _deploy_nodes() dispatch logic."""

    @pytest.mark.asyncio
    async def test_dispatches_to_per_node_when_enabled(self, test_db, test_user, monkeypatch):
        """When per_node_lifecycle_enabled is True, dispatches to _deploy_nodes_per_node."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager._deploy_nodes_per_node = AsyncMock()
        manager._deploy_nodes_topology = AsyncMock()

        monkeypatch.setattr("app.tasks.node_lifecycle_deploy.settings.per_node_lifecycle_enabled", True)
        await manager._deploy_nodes([ns])

        manager._deploy_nodes_per_node.assert_awaited_once_with([ns])
        manager._deploy_nodes_topology.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatches_to_topology_when_disabled(self, test_db, test_user, monkeypatch):
        """When per_node_lifecycle_enabled is False, dispatches to _deploy_nodes_topology."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager._deploy_nodes_per_node = AsyncMock()
        manager._deploy_nodes_topology = AsyncMock()

        monkeypatch.setattr("app.tasks.node_lifecycle_deploy.settings.per_node_lifecycle_enabled", False)
        await manager._deploy_nodes([ns])

        manager._deploy_nodes_topology.assert_awaited_once_with([ns])
        manager._deploy_nodes_per_node.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestDeployNodesTopology
# ---------------------------------------------------------------------------


class TestDeployNodesTopology:
    """Tests for _deploy_nodes_topology() path."""

    @pytest.mark.asyncio
    async def test_success_marks_running(self, test_db, test_user):
        """Successful topology deploy marks nodes as running."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]
        manager.old_agent_ids = set()

        # Mock graph node
        mock_graph_node = MagicMock()
        mock_graph_node.container_name = "R1"
        mock_graph_node.name = "R1"
        mock_graph_node.id = "n1"

        mock_graph = MagicMock()
        mock_graph.nodes = [mock_graph_node]

        with (
            patch.object(manager.topo_service, "has_nodes", return_value=True),
            patch.object(
                manager, "_filter_topology_for_agent",
                return_value=(mock_graph, {"R1"}),
            ),
            patch.object(manager, "_validate_topology_placement", return_value=[]),
            patch("app.tasks.node_lifecycle_deploy.graph_to_deploy_topology", return_value="{}"),
            patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac,
            patch("app.tasks.node_lifecycle_deploy.acquire_deploy_lock", return_value=(True, [])),
            patch("app.tasks.node_lifecycle_deploy.release_deploy_lock"),
            patch("app.tasks.node_lifecycle_deploy._update_node_placements", new_callable=AsyncMock),
            patch("app.tasks.node_lifecycle_deploy._capture_node_ips", new_callable=AsyncMock),
            patch("app.tasks.node_lifecycle_deploy._cleanup_orphan_containers", new_callable=AsyncMock),
        ):
            mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})
            mock_ac.get_lab_status_from_agent = AsyncMock(
                return_value={
                    "nodes": [
                        {
                            "name": "R1",
                            "node_definition_id": "n1",
                            "runtime_id": "runtime-1",
                        }
                    ]
                }
            )
            await manager._deploy_nodes_topology([ns])

        assert ns.actual_state == NodeActualState.RUNNING.value
        assert ns.error_message is None

    @pytest.mark.asyncio
    async def test_success_missing_runtime_identity_marks_error(self, test_db, test_user):
        """Topology deploy is not trusted until agent status exposes identity fields."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]
        manager.old_agent_ids = set()

        mock_graph_node = MagicMock()
        mock_graph_node.container_name = "R1"
        mock_graph_node.name = "R1"
        mock_graph_node.id = "n1"
        mock_graph = MagicMock()
        mock_graph.nodes = [mock_graph_node]

        with (
            patch.object(manager.topo_service, "has_nodes", return_value=True),
            patch.object(manager, "_filter_topology_for_agent", return_value=(mock_graph, {"R1"})),
            patch.object(manager, "_validate_topology_placement", return_value=[]),
            patch("app.tasks.node_lifecycle_deploy.graph_to_deploy_topology", return_value="{}"),
            patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac,
            patch("app.tasks.node_lifecycle_deploy.acquire_deploy_lock", return_value=(True, [])),
            patch("app.tasks.node_lifecycle_deploy.release_deploy_lock"),
            patch("app.tasks.node_lifecycle_deploy._update_node_placements", new_callable=AsyncMock),
            patch("app.tasks.node_lifecycle_deploy._capture_node_ips", new_callable=AsyncMock),
            patch("app.tasks.node_lifecycle_deploy._cleanup_orphan_containers", new_callable=AsyncMock),
        ):
            mock_ac.deploy_to_agent = AsyncMock(return_value={"status": "completed"})
            mock_ac.get_lab_status_from_agent = AsyncMock(
                return_value={"nodes": [{"name": "R1", "node_definition_id": "n1"}]}
            )
            await manager._deploy_nodes_topology([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "runtime_id" in (ns.error_message or "")

    @pytest.mark.asyncio
    async def test_no_topology_sets_error(self, test_db, test_user):
        """No topology defined causes error state and job failure."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)

        with patch.object(manager.topo_service, "has_nodes", return_value=False):
            await manager._deploy_nodes_topology([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "No topology defined" in ns.error_message
        assert job.status == "failed"

    @pytest.mark.asyncio
    async def test_no_nodes_for_agent_sets_error(self, test_db, test_user):
        """When filter returns no nodes for agent, sets error."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)

        mock_graph = MagicMock()
        mock_graph.nodes = []

        with (
            patch.object(manager.topo_service, "has_nodes", return_value=True),
            patch.object(
                manager, "_filter_topology_for_agent",
                return_value=(mock_graph, set()),
            ),
        ):
            await manager._deploy_nodes_topology([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "No nodes to deploy" in ns.error_message

    @pytest.mark.asyncio
    async def test_agent_unavailable_handles_transient(self, test_db, test_user):
        """AgentUnavailableError marks nodes as pending (transient)."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.old_agent_ids = set()

        mock_graph_node = MagicMock()
        mock_graph_node.container_name = "R1"
        mock_graph_node.name = "R1"
        mock_graph = MagicMock()
        mock_graph.nodes = [mock_graph_node]

        with (
            patch.object(manager.topo_service, "has_nodes", return_value=True),
            patch.object(
                manager, "_filter_topology_for_agent",
                return_value=(mock_graph, {"R1"}),
            ),
            patch.object(manager, "_validate_topology_placement", return_value=[]),
            patch("app.tasks.node_lifecycle_deploy.graph_to_deploy_topology", return_value="{}"),
            patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac,
            patch("app.tasks.node_lifecycle_deploy.acquire_deploy_lock", return_value=(True, [])),
            patch("app.tasks.node_lifecycle_deploy.release_deploy_lock"),
        ):
            mock_ac.deploy_to_agent = AsyncMock(
                side_effect=AgentUnavailableError("agent-1", "Connection refused"),
            )
            await manager._deploy_nodes_topology([ns])

        assert ns.actual_state == NodeActualState.PENDING.value

    @pytest.mark.asyncio
    async def test_exception_marks_error(self, test_db, test_user):
        """Unexpected exception marks nodes as error."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.old_agent_ids = set()

        mock_graph_node = MagicMock()
        mock_graph_node.container_name = "R1"
        mock_graph_node.name = "R1"
        mock_graph = MagicMock()
        mock_graph.nodes = [mock_graph_node]

        with (
            patch.object(manager.topo_service, "has_nodes", return_value=True),
            patch.object(
                manager, "_filter_topology_for_agent",
                return_value=(mock_graph, {"R1"}),
            ),
            patch.object(manager, "_validate_topology_placement", return_value=[]),
            patch("app.tasks.node_lifecycle_deploy.graph_to_deploy_topology", return_value="{}"),
            patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac,
            patch("app.tasks.node_lifecycle_deploy.acquire_deploy_lock", return_value=(True, [])),
            patch("app.tasks.node_lifecycle_deploy.release_deploy_lock"),
        ):
            mock_ac.deploy_to_agent = AsyncMock(
                side_effect=RuntimeError("Unexpected failure"),
            )
            await manager._deploy_nodes_topology([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "Unexpected failure" in ns.error_message


# ---------------------------------------------------------------------------
# TestStartNodesPerNode
# ---------------------------------------------------------------------------


class TestStartNodesPerNode:
    """Tests for per-node start path (_start_nodes_per_node via _create_and_start_nodes)."""

    @pytest.mark.asyncio
    async def test_success_marks_running(self, test_db, test_user, monkeypatch):
        """Successful per-node deploy marks node as running."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.db_nodes_by_gui_id = {"n1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager.graph = None
        manager.explicit_snapshots_map = {}
        manager.latest_snapshots_map = {}
        manager._manifest = None

        monkeypatch.setattr("app.tasks.node_lifecycle_deploy.settings.per_node_lifecycle_enabled", True)

        with (
            patch.object(manager.topo_service, "get_interface_count_map", return_value={"R1": 4}),
            patch("app.tasks.node_lifecycle_deploy.resolve_node_image", return_value="linux:latest"),
            patch("app.tasks.node_lifecycle_deploy.get_image_provider", return_value="docker"),
            patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac,
            patch("app.tasks.node_lifecycle_deploy.lab_workspace", return_value=MagicMock()),
            patch("app.tasks.node_lifecycle_deploy._update_node_placements", new_callable=AsyncMock),
            patch("app.tasks.node_lifecycle_deploy._capture_node_ips", new_callable=AsyncMock),
            patch("app.tasks.node_lifecycle_deploy.get_device_service") as mock_ds,
        ):
            mock_ds.return_value.resolve_hardware_specs.return_value = {}
            mock_ac.create_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.start_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.get_lab_status_from_agent = AsyncMock(
                return_value={
                    "nodes": [
                        {
                            "name": "R1",
                            "node_definition_id": node_def.id,
                            "runtime_id": "runtime-1",
                        }
                    ]
                }
            )
            mock_ac.create_link_on_agent = AsyncMock(return_value={"success": True})
            await manager._start_nodes([ns])

        assert ns.actual_state == NodeActualState.RUNNING.value

    @pytest.mark.asyncio
    async def test_retry_on_transient_failure(self, test_db, test_user, monkeypatch):
        """Transient failure is retried via _deploy_single_node_with_retry."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.db_nodes_by_gui_id = {"n1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager.graph = None
        manager.explicit_snapshots_map = {}
        manager.latest_snapshots_map = {}
        manager._manifest = None

        call_count = 0

        async def _mock_deploy(ns_arg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: transient failure (pending state)
                ns_arg.actual_state = NodeActualState.PENDING.value
                ns_arg.error_message = "Agent unreachable"
                return None
            # Second call: success
            ns_arg.actual_state = NodeActualState.RUNNING.value
            ns_arg.error_message = None
            return ns_arg.node_name

        manager._deploy_single_node = _mock_deploy

        # Use short backoff for test speed
        with (
            patch("app.tasks.node_lifecycle_deploy.DEPLOY_RETRY_BACKOFF_SECONDS", 0),
            patch("app.tasks.node_lifecycle_deploy._update_node_placements", new_callable=AsyncMock),
            patch("app.tasks.node_lifecycle_deploy._capture_node_ips", new_callable=AsyncMock),
        ):
            manager._connect_same_host_links = AsyncMock()
            monkeypatch.setattr("app.tasks.node_lifecycle_deploy.settings.per_node_lifecycle_enabled", True)
            await manager._start_nodes([ns])

        assert call_count == 2
        assert ns.actual_state == NodeActualState.RUNNING.value

    @pytest.mark.asyncio
    async def test_ceos_stagger_delay(self, test_db, test_user, monkeypatch):
        """cEOS nodes deploy sequentially with stagger delay between them."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns1 = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        ns2 = make_node_state(test_db, lab, "n2", "R2", desired="running", actual="stopped")
        node_def1 = make_node(test_db, lab, "n1", "R1", "R1", device="arista_ceos", host_id=host.id)
        node_def2 = make_node(test_db, lab, "n2", "R2", "R2", device="arista_ceos", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1", "n2"], agent=host)
        manager.node_states = [ns1, ns2]
        manager.db_nodes_map = {"R1": node_def1, "R2": node_def2}
        manager.db_nodes_by_gui_id = {"n1": node_def1, "n2": node_def2}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns1, "R2": ns2}
        manager.graph = None
        manager.explicit_snapshots_map = {}
        manager.latest_snapshots_map = {}
        manager._manifest = None

        sleep_calls = []

        async def mock_sleep(seconds):
            sleep_calls.append(seconds)

        deploy_order = []

        async def _mock_deploy(ns_arg):
            deploy_order.append(ns_arg.node_name)
            ns_arg.actual_state = NodeActualState.RUNNING.value
            ns_arg.error_message = None
            return ns_arg.node_name

        manager._deploy_single_node = _mock_deploy
        manager._connect_same_host_links = AsyncMock()

        monkeypatch.setattr("app.tasks.node_lifecycle_deploy.settings.per_node_lifecycle_enabled", True)
        with (
            patch("app.tasks.node_lifecycle_deploy.asyncio.sleep", mock_sleep),
            patch("app.tasks.node_lifecycle_deploy._update_node_placements", new_callable=AsyncMock),
            patch("app.tasks.node_lifecycle_deploy._capture_node_ips", new_callable=AsyncMock),
        ):
            await manager._start_nodes([ns1, ns2])

        # Both cEOS nodes should deploy, with a stagger delay between them
        assert deploy_order == ["R1", "R2"]
        assert len(sleep_calls) >= 1  # At least one stagger sleep between cEOS nodes

    @pytest.mark.asyncio
    async def test_agent_unavailable_for_per_node(self, test_db, test_user, monkeypatch):
        """AgentUnavailableError in per-node deploy is handled gracefully."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        node_def = make_node(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]
        manager.db_nodes_map = {"R1": node_def}
        manager.db_nodes_by_gui_id = {"n1": node_def}
        manager.placements_map = {}
        manager.all_lab_states = {"R1": ns}
        manager.graph = None
        manager.explicit_snapshots_map = {}
        manager.latest_snapshots_map = {}
        manager._manifest = None

        async def _mock_deploy(ns_arg):
            ns_arg.actual_state = NodeActualState.PENDING.value
            ns_arg.error_message = "Agent unreachable"
            return None

        manager._deploy_single_node = _mock_deploy
        manager._connect_same_host_links = AsyncMock()

        monkeypatch.setattr("app.tasks.node_lifecycle_deploy.settings.per_node_lifecycle_enabled", True)
        with (
            patch("app.tasks.node_lifecycle_deploy.DEPLOY_RETRY_ATTEMPTS", 1),
            patch("app.tasks.node_lifecycle_deploy._update_node_placements", new_callable=AsyncMock),
            patch("app.tasks.node_lifecycle_deploy._capture_node_ips", new_callable=AsyncMock),
        ):
            await manager._start_nodes([ns])

        # After all retries exhausted, node stays in pending/error state
        assert ns.actual_state in (NodeActualState.PENDING.value, NodeActualState.ERROR.value)


# ---------------------------------------------------------------------------
# TestStopNodes
# ---------------------------------------------------------------------------


class TestStopNodes:
    """Tests for _stop_nodes() method."""

    @pytest.mark.asyncio
    async def test_success_marks_stopped(self, test_db, test_user):
        """Successful stop marks nodes as stopped."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        make_placement(test_db, lab, "R1", host.id)

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {"R1": test_db.query(models.NodePlacement).first()}

        with (
            patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac,
            patch("app.tasks.node_lifecycle_stop.settings") as mock_settings,
        ):
            mock_settings.feature_auto_extract_on_stop = False
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.reconcile_nodes_on_agent = AsyncMock(return_value={
                "results": [
                    {"container_name": _get_container_name(lab.id, "R1"), "success": True}
                ]
            })
            await manager._stop_nodes([ns])

        assert ns.actual_state == NodeActualState.STOPPED.value
        assert ns.is_ready is False

    @pytest.mark.asyncio
    async def test_auto_extracts_config_before_stop(self, test_db, test_user):
        """When feature_auto_extract_on_stop is enabled, configs are extracted first."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        make_placement(test_db, lab, "R1", host.id)

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {"R1": test_db.query(models.NodePlacement).first()}

        extract_called = False

        async def mock_extract(nodes_to_stop):
            nonlocal extract_called
            extract_called = True

        manager._auto_extract_before_stop = mock_extract

        with patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.reconcile_nodes_on_agent = AsyncMock(return_value={
                "results": [
                    {"container_name": _get_container_name(lab.id, "R1"), "success": True}
                ]
            })
            await manager._stop_nodes([ns])

        assert extract_called is True

    @pytest.mark.asyncio
    async def test_agent_unavailable_sets_error_message(self, test_db, test_user):
        """AgentUnavailableError during stop logs transient failure."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="running")
        make_placement(test_db, lab, "R1", host.id)

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]
        manager.placements_map = {"R1": test_db.query(models.NodePlacement).first()}

        with (
            patch("app.tasks.node_lifecycle_stop.agent_client") as mock_ac,
            patch("app.tasks.node_lifecycle_stop.settings") as mock_settings,
        ):
            mock_settings.feature_auto_extract_on_stop = False
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.reconcile_nodes_on_agent = AsyncMock(
                side_effect=AgentUnavailableError("agent-1", "Connection refused"),
            )
            await manager._stop_nodes([ns])

        assert ns.error_message is not None
        assert "transient" in ns.error_message.lower() or "unreachable" in ns.error_message.lower()


# ---------------------------------------------------------------------------
# TestConnectSameHostLinks
# ---------------------------------------------------------------------------


class TestConnectSameHostLinks:
    """Tests for _connect_same_host_links()."""

    @pytest.mark.asyncio
    async def test_connects_running_nodes(self, test_db, test_user):
        """Links between running nodes on same agent are connected."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)

        ns1 = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running", is_ready=True)
        ns2 = make_node_state(test_db, lab, "n2", "R2", desired="running", actual="running", is_ready=True)
        make_placement(test_db, lab, "R1", host.id)
        make_placement(test_db, lab, "R2", host.id)

        manager = _make_manager(test_db, lab, job, ["n1", "n2"], agent=host)
        manager.all_lab_states = {"R1": ns1, "R2": ns2}
        manager.placements_map = {
            "R1": test_db.query(models.NodePlacement).filter_by(node_name="R1").first(),
            "R2": test_db.query(models.NodePlacement).filter_by(node_name="R2").first(),
        }

        # Build a mock graph with a link
        mock_ep_a = MagicMock()
        mock_ep_a.node = "n1"
        mock_ep_a.ifname = "eth1"
        mock_ep_b = MagicMock()
        mock_ep_b.node = "n2"
        mock_ep_b.ifname = "eth1"
        mock_link = MagicMock()
        mock_link.endpoints = [mock_ep_a, mock_ep_b]

        mock_node1 = MagicMock()
        mock_node1.id = "n1"
        mock_node1.container_name = "R1"
        mock_node1.name = "R1"
        mock_node2 = MagicMock()
        mock_node2.id = "n2"
        mock_node2.container_name = "R2"
        mock_node2.name = "R2"

        mock_graph = MagicMock()
        mock_graph.links = [mock_link]
        mock_graph.nodes = [mock_node1, mock_node2]
        manager.graph = mock_graph

        with patch(
            "app.tasks.node_lifecycle_deploy._create_same_host_link",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_create:
            await manager._connect_same_host_links({"R1", "R2"})
            mock_create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_cross_host_links(self, test_db, test_user):
        """Links with endpoints on different agents are skipped."""
        host1 = make_host(test_db, "host-1", "Host 1")
        host2 = make_host(test_db, "host-2", "Host 2")
        lab = make_lab(test_db, test_user, agent_id=host1.id)
        job = make_job(test_db, lab, test_user)

        ns1 = make_node_state(test_db, lab, "n1", "R1", desired="running", actual="running")
        ns2 = make_node_state(test_db, lab, "n2", "R2", desired="running", actual="running")
        make_placement(test_db, lab, "R1", host1.id)
        make_placement(test_db, lab, "R2", host2.id)  # Different host

        manager = _make_manager(test_db, lab, job, ["n1", "n2"], agent=host1)
        manager.all_lab_states = {"R1": ns1, "R2": ns2}
        manager.placements_map = {
            "R1": test_db.query(models.NodePlacement).filter_by(node_name="R1").first(),
            "R2": test_db.query(models.NodePlacement).filter_by(node_name="R2").first(),
        }

        mock_ep_a = MagicMock()
        mock_ep_a.node = "n1"
        mock_ep_a.ifname = "eth1"
        mock_ep_b = MagicMock()
        mock_ep_b.node = "n2"
        mock_ep_b.ifname = "eth1"
        mock_link = MagicMock()
        mock_link.endpoints = [mock_ep_a, mock_ep_b]

        mock_node1 = MagicMock()
        mock_node1.id = "n1"
        mock_node1.container_name = "R1"
        mock_node1.name = "R1"
        mock_node2 = MagicMock()
        mock_node2.id = "n2"
        mock_node2.container_name = "R2"
        mock_node2.name = "R2"

        mock_graph = MagicMock()
        mock_graph.links = [mock_link]
        mock_graph.nodes = [mock_node1, mock_node2]
        manager.graph = mock_graph

        with patch(
            "app.tasks.node_lifecycle_deploy._create_same_host_link",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_create:
            await manager._connect_same_host_links({"R1", "R2"})
            # R2 is on host2, not host1 — link should be skipped
            mock_create.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestGetStartupConfig
# ---------------------------------------------------------------------------


class TestGetStartupConfig:
    """Tests for _get_startup_config() config resolution."""

    def test_returns_config_from_active_snapshot(self, test_db, test_user, tmp_path):
        """Active config snapshot is preferred for non-n9kv devices."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        node_def = make_node(test_db, lab, "n1", "R1", "R1", device="linux", host_id=host.id)
        node_def.active_config_snapshot_id = "snap-1"
        test_db.commit()

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.explicit_snapshots_map = {"snap-1": MagicMock(content="snapshot-config")}
        manager.latest_snapshots_map = {}

        ws = tmp_path / lab.id
        ws.mkdir(parents=True, exist_ok=True)

        with patch("app.tasks.node_lifecycle_deploy.lab_workspace", return_value=ws):
            result = manager._get_startup_config("R1", node_def)

        assert result == "snapshot-config"

    def test_returns_none_when_no_config(self, test_db, test_user, tmp_path):
        """Returns None when no config source is available."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        node_def = make_node(test_db, lab, "n1", "R1", "R1", device="linux", host_id=host.id)
        node_def.active_config_snapshot_id = None
        node_def.config_json = None
        test_db.commit()

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.explicit_snapshots_map = {}
        manager.latest_snapshots_map = {}

        ws = tmp_path / lab.id
        ws.mkdir(parents=True, exist_ok=True)

        with patch("app.tasks.node_lifecycle_deploy.lab_workspace", return_value=ws):
            result = manager._get_startup_config("R1", node_def)

        assert result is None

    def test_n9kv_prefers_workspace(self, test_db, test_user, tmp_path):
        """N9Kv devices prefer saved workspace config over snapshots."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        node_def = make_node(test_db, lab, "n1", "R1", "R1", device="cisco_n9kv", host_id=host.id)
        node_def.active_config_snapshot_id = "snap-1"
        test_db.commit()

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.explicit_snapshots_map = {"snap-1": MagicMock(content="snapshot-config")}
        manager.latest_snapshots_map = {}

        ws = tmp_path / lab.id
        cfg = ws / "configs" / "R1" / "startup-config"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("from-workspace", encoding="utf-8")

        with patch("app.tasks.node_lifecycle_deploy.lab_workspace", return_value=ws):
            result = manager._get_startup_config("R1", node_def)

        assert result == "from-workspace"


# ---------------------------------------------------------------------------
# TestConvergeStoppedDesiredErrorStates
# ---------------------------------------------------------------------------


class TestConvergeStoppedDesiredErrorStates:
    """Tests for _converge_stopped_desired_error_states()."""

    def test_normalizes_error_to_stopped(self, test_db, test_user):
        """Nodes with desired=stopped and actual=error are normalized to stopped."""
        host = make_host(test_db)
        lab = make_lab(test_db, test_user, agent_id=host.id)
        job = make_job(test_db, lab, test_user)
        ns = make_node_state(test_db, lab, "n1", "R1", desired="stopped", actual="error")
        ns.error_message = "Previous failure"
        test_db.commit()

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.node_states = [ns]

        count = manager._converge_stopped_desired_error_states()
        assert count == 1
        assert ns.actual_state == NodeActualState.STOPPED.value
        assert ns.error_message is None
        assert ns.is_ready is False