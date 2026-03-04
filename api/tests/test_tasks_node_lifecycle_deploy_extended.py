"""Extended tests for DeploymentMixin in node_lifecycle_deploy.py.

Covers gaps not addressed by test_tasks_node_lifecycle_deploy.py:
  - _deploy_single_node: all branches (no db_node, no image, create fails,
    start fails, start success, AgentUnavailableError, generic exception)
  - _start_single_node: success, "not found" fallback to redeploy, generic
    error, AgentUnavailableError
  - _deploy_single_node_with_retry: first-try success, retry on transient,
    retry exhausted, non-transient stops retry
  - _create_and_start_nodes: cEOS stagger, parallel non-cEOS, desired_state
    filter, mixed cEOS + non-cEOS
  - _get_interface_count: normal lookup, missing node (default 0 → min 4),
    high count preserved
  - _start_nodes_topology: success path, no topology, empty filter,
    misplaced nodes, lock failure, agent unavailable, deploy failed result
  - _connect_same_host_links: gather exception handling, no eligible links,
    cross-host skip
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import models
from app.agent_client import AgentUnavailableError
from app.state import JobStatus, NodeActualState, NodeDesiredState
from app.tasks.node_lifecycle import (
    NodeLifecycleManager,
    _get_container_name,
)


# ---------------------------------------------------------------------------
# Helpers (mirrors test_tasks_node_lifecycle_deploy.py patterns)
# ---------------------------------------------------------------------------


def _make_host(test_db, host_id="agent-1", name="Agent 1", status="online"):
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


def _make_node_state(
    test_db, lab, node_id, node_name, desired="running", actual="undeployed"
):
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


def _make_node_def(
    test_db, lab, gui_id, name, container_name, device="linux", host_id=None
):
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


def _make_manager(session, lab, job, node_ids, agent=None):
    manager = NodeLifecycleManager(session, lab, job, node_ids)
    if agent:
        manager.agent = agent
        manager.target_agent_id = agent.id
    manager._broadcast_state = MagicMock()
    return manager


# ---------------------------------------------------------------------------
# TestDeploySingleNode
# ---------------------------------------------------------------------------


class TestDeploySingleNode:
    """Direct tests for _deploy_single_node()."""

    @pytest.mark.asyncio
    async def test_no_db_node_returns_error(self, test_db, test_user):
        """Returns None and marks ERROR when db_nodes_map has no entry."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.db_nodes_map = {}  # no entry for R1

        result = await manager._deploy_single_node(ns)

        assert result is None
        assert ns.actual_state == NodeActualState.ERROR.value
        assert "not found" in (ns.error_message or "").lower()

    @pytest.mark.asyncio
    async def test_no_image_returns_error(self, test_db, test_user):
        """Returns None and marks ERROR when resolve_node_image returns falsy."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.db_nodes_map = {"R1": node_def}
        manager.explicit_snapshots_map = {}
        manager.latest_snapshots_map = {}
        manager._manifest = None

        with (
            patch.object(manager.topo_service, "get_interface_count_map", return_value={"R1": 4}),
            patch("app.tasks.node_lifecycle_deploy.resolve_node_image", return_value=None),
            patch("app.tasks.node_lifecycle_deploy.lab_workspace", return_value=MagicMock()),
        ):
            result = await manager._deploy_single_node(ns)

        assert result is None
        assert ns.actual_state == NodeActualState.ERROR.value
        assert "No image found" in (ns.error_message or "")

    @pytest.mark.asyncio
    async def test_create_fails_returns_error(self, test_db, test_user):
        """Returns None and marks ERROR when create_node_on_agent returns success=False."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.db_nodes_map = {"R1": node_def}
        manager.explicit_snapshots_map = {}
        manager.latest_snapshots_map = {}
        manager._manifest = None

        with (
            patch.object(manager.topo_service, "get_interface_count_map", return_value={"R1": 4}),
            patch("app.tasks.node_lifecycle_deploy.resolve_node_image", return_value="linux:latest"),
            patch("app.tasks.node_lifecycle_deploy.get_image_provider", return_value="docker"),
            patch("app.tasks.node_lifecycle_deploy.lab_workspace", return_value=MagicMock()),
            patch("app.tasks.node_lifecycle_deploy.get_device_service") as mock_ds,
            patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac,
        ):
            mock_ds.return_value.resolve_hardware_specs.return_value = {}
            mock_ac.create_node_on_agent = AsyncMock(
                return_value={"success": False, "error": "disk full"}
            )
            result = await manager._deploy_single_node(ns)

        assert result is None
        assert ns.actual_state == NodeActualState.ERROR.value
        assert "disk full" in (ns.error_message or "")

    @pytest.mark.asyncio
    async def test_start_fails_returns_error(self, test_db, test_user):
        """Returns None and marks ERROR when start_node_on_agent returns success=False."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.db_nodes_map = {"R1": node_def}
        manager.explicit_snapshots_map = {}
        manager.latest_snapshots_map = {}
        manager._manifest = None

        with (
            patch.object(manager.topo_service, "get_interface_count_map", return_value={"R1": 4}),
            patch("app.tasks.node_lifecycle_deploy.resolve_node_image", return_value="linux:latest"),
            patch("app.tasks.node_lifecycle_deploy.get_image_provider", return_value="docker"),
            patch("app.tasks.node_lifecycle_deploy.lab_workspace", return_value=MagicMock()),
            patch("app.tasks.node_lifecycle_deploy.get_device_service") as mock_ds,
            patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac,
        ):
            mock_ds.return_value.resolve_hardware_specs.return_value = {}
            mock_ac.create_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.start_node_on_agent = AsyncMock(
                return_value={"success": False, "error": "OOM killer terminated"}
            )
            result = await manager._deploy_single_node(ns)

        assert result is None
        assert ns.actual_state == NodeActualState.ERROR.value
        assert "OOM killer terminated" in (ns.error_message or "")

    @pytest.mark.asyncio
    async def test_start_success_marks_running(self, test_db, test_user):
        """Returns node_name and marks RUNNING when create + start both succeed."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.db_nodes_map = {"R1": node_def}
        manager.explicit_snapshots_map = {}
        manager.latest_snapshots_map = {}
        manager._manifest = None

        with (
            patch.object(manager.topo_service, "get_interface_count_map", return_value={"R1": 4}),
            patch("app.tasks.node_lifecycle_deploy.resolve_node_image", return_value="linux:latest"),
            patch("app.tasks.node_lifecycle_deploy.get_image_provider", return_value="docker"),
            patch("app.tasks.node_lifecycle_deploy.lab_workspace", return_value=MagicMock()),
            patch("app.tasks.node_lifecycle_deploy.get_device_service") as mock_ds,
            patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac,
        ):
            mock_ds.return_value.resolve_hardware_specs.return_value = {}
            mock_ac.create_node_on_agent = AsyncMock(return_value={"success": True})
            mock_ac.start_node_on_agent = AsyncMock(return_value={"success": True})
            result = await manager._deploy_single_node(ns)

        assert result == "R1"
        assert ns.actual_state == NodeActualState.RUNNING.value
        assert ns.error_message is None
        assert ns.boot_started_at is not None

    @pytest.mark.asyncio
    async def test_agent_unavailable_sets_pending(self, test_db, test_user):
        """AgentUnavailableError transitions node to PENDING (transient)."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.db_nodes_map = {"R1": node_def}
        manager.explicit_snapshots_map = {}
        manager.latest_snapshots_map = {}
        manager._manifest = None

        with (
            patch.object(manager.topo_service, "get_interface_count_map", return_value={"R1": 4}),
            patch("app.tasks.node_lifecycle_deploy.resolve_node_image", return_value="linux:latest"),
            patch("app.tasks.node_lifecycle_deploy.get_image_provider", return_value="docker"),
            patch("app.tasks.node_lifecycle_deploy.lab_workspace", return_value=MagicMock()),
            patch("app.tasks.node_lifecycle_deploy.get_device_service") as mock_ds,
            patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac,
        ):
            mock_ds.return_value.resolve_hardware_specs.return_value = {}
            mock_ac.create_node_on_agent = AsyncMock(
                side_effect=AgentUnavailableError("agent-1", "Connection refused")
            )
            result = await manager._deploy_single_node(ns)

        assert result is None
        assert ns.actual_state == NodeActualState.PENDING.value

    @pytest.mark.asyncio
    async def test_generic_exception_marks_error(self, test_db, test_user):
        """Unexpected exception marks node as ERROR with the exception message."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.db_nodes_map = {"R1": node_def}
        manager.explicit_snapshots_map = {}
        manager.latest_snapshots_map = {}
        manager._manifest = None

        with (
            patch.object(manager.topo_service, "get_interface_count_map", return_value={"R1": 4}),
            patch("app.tasks.node_lifecycle_deploy.resolve_node_image", return_value="linux:latest"),
            patch("app.tasks.node_lifecycle_deploy.get_image_provider", return_value="docker"),
            patch("app.tasks.node_lifecycle_deploy.lab_workspace", return_value=MagicMock()),
            patch("app.tasks.node_lifecycle_deploy.get_device_service") as mock_ds,
            patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac,
        ):
            mock_ds.return_value.resolve_hardware_specs.return_value = {}
            mock_ac.create_node_on_agent = AsyncMock(
                side_effect=RuntimeError("unexpected crash")
            )
            result = await manager._deploy_single_node(ns)

        assert result is None
        assert ns.actual_state == NodeActualState.ERROR.value
        assert "unexpected crash" in (ns.error_message or "")


# ---------------------------------------------------------------------------
# TestStartSingleNode
# ---------------------------------------------------------------------------


class TestStartSingleNode:
    """Tests for _start_single_node()."""

    def _setup_manager(self, test_db, test_user):
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="stopped")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", host_id=host.id)
        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.db_nodes_map = {"R1": node_def}
        return manager, ns, host

    @pytest.mark.asyncio
    async def test_start_success_marks_running(self, test_db, test_user):
        """Successful start marks node as RUNNING and returns node_name."""
        manager, ns, _ = self._setup_manager(test_db, test_user)

        with (
            patch("app.tasks.node_lifecycle_deploy.resolve_node_image", return_value="linux:latest"),
            patch("app.tasks.node_lifecycle_deploy.get_image_provider", return_value="docker"),
            patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac,
        ):
            mock_ac.start_node_on_agent = AsyncMock(return_value={"success": True})
            result = await manager._start_single_node(ns)

        assert result == "R1"
        assert ns.actual_state == NodeActualState.RUNNING.value
        assert ns.error_message is None
        assert ns.boot_started_at is not None

    @pytest.mark.asyncio
    async def test_not_found_falls_back_to_redeploy(self, test_db, test_user):
        """'not found' error in start response triggers full _deploy_single_node."""
        manager, ns, _ = self._setup_manager(test_db, test_user)

        redeploy_called = False

        async def _mock_redeploy(ns_arg):
            nonlocal redeploy_called
            redeploy_called = True
            ns_arg.actual_state = NodeActualState.RUNNING.value
            return ns_arg.node_name

        manager._deploy_single_node = _mock_redeploy

        with (
            patch("app.tasks.node_lifecycle_deploy.resolve_node_image", return_value="linux:latest"),
            patch("app.tasks.node_lifecycle_deploy.get_image_provider", return_value="docker"),
            patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac,
        ):
            mock_ac.start_node_on_agent = AsyncMock(
                return_value={"success": False, "error": "domain not found"}
            )
            result = await manager._start_single_node(ns)

        assert redeploy_called is True
        assert result == "R1"

    @pytest.mark.asyncio
    async def test_generic_error_marks_error_state(self, test_db, test_user):
        """Non-'not found' start failure marks node as ERROR."""
        manager, ns, _ = self._setup_manager(test_db, test_user)

        with (
            patch("app.tasks.node_lifecycle_deploy.resolve_node_image", return_value="linux:latest"),
            patch("app.tasks.node_lifecycle_deploy.get_image_provider", return_value="docker"),
            patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac,
        ):
            mock_ac.start_node_on_agent = AsyncMock(
                return_value={"success": False, "error": "permission denied"}
            )
            result = await manager._start_single_node(ns)

        assert result is None
        assert ns.actual_state == NodeActualState.ERROR.value
        assert "permission denied" in (ns.error_message or "")

    @pytest.mark.asyncio
    async def test_agent_unavailable_sets_pending(self, test_db, test_user):
        """AgentUnavailableError in start sets node to PENDING."""
        manager, ns, _ = self._setup_manager(test_db, test_user)

        with (
            patch("app.tasks.node_lifecycle_deploy.resolve_node_image", return_value="linux:latest"),
            patch("app.tasks.node_lifecycle_deploy.get_image_provider", return_value="docker"),
            patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac,
        ):
            mock_ac.start_node_on_agent = AsyncMock(
                side_effect=AgentUnavailableError("agent-1", "timeout")
            )
            result = await manager._start_single_node(ns)

        assert result is None
        assert ns.actual_state == NodeActualState.PENDING.value

    @pytest.mark.asyncio
    async def test_exception_marks_error(self, test_db, test_user):
        """Unexpected exception in _start_single_node marks node as ERROR."""
        manager, ns, _ = self._setup_manager(test_db, test_user)

        with (
            patch("app.tasks.node_lifecycle_deploy.resolve_node_image", return_value="linux:latest"),
            patch("app.tasks.node_lifecycle_deploy.get_image_provider", return_value="docker"),
            patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac,
        ):
            mock_ac.start_node_on_agent = AsyncMock(
                side_effect=ValueError("malformed response")
            )
            result = await manager._start_single_node(ns)

        assert result is None
        assert ns.actual_state == NodeActualState.ERROR.value
        assert "malformed response" in (ns.error_message or "")


# ---------------------------------------------------------------------------
# TestDeploySingleNodeWithRetry
# ---------------------------------------------------------------------------


class TestDeploySingleNodeWithRetry:
    """Tests for _deploy_single_node_with_retry()."""

    @pytest.mark.asyncio
    async def test_success_first_try(self, test_db, test_user):
        """Returns node_name immediately when first attempt succeeds."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        call_count = 0

        async def _mock_deploy(ns_arg):
            nonlocal call_count
            call_count += 1
            ns_arg.actual_state = NodeActualState.RUNNING.value
            return ns_arg.node_name

        manager._deploy_single_node = _mock_deploy

        with patch("app.tasks.node_lifecycle_deploy.DEPLOY_RETRY_ATTEMPTS", 3):
            result = await manager._deploy_single_node_with_retry(ns)

        assert result == "R1"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_transient_then_succeeds(self, test_db, test_user):
        """Retries after transient failure (PENDING state) and succeeds on second attempt."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        call_count = 0

        async def _mock_deploy(ns_arg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                ns_arg.actual_state = NodeActualState.PENDING.value
                ns_arg.error_message = "Agent unreachable"
                return None
            ns_arg.actual_state = NodeActualState.RUNNING.value
            ns_arg.error_message = None
            return ns_arg.node_name

        manager._deploy_single_node = _mock_deploy

        with (
            patch("app.tasks.node_lifecycle_deploy.DEPLOY_RETRY_ATTEMPTS", 3),
            patch("app.tasks.node_lifecycle_deploy.DEPLOY_RETRY_BACKOFF_SECONDS", 0),
        ):
            result = await manager._deploy_single_node_with_retry(ns)

        assert result == "R1"
        assert call_count == 2
        assert ns.actual_state == NodeActualState.RUNNING.value

    @pytest.mark.asyncio
    async def test_retry_exhausted_converts_to_error(self, test_db, test_user):
        """When all retries fail with PENDING, converts to a non-transient error."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)

        async def _always_pending(ns_arg):
            ns_arg.actual_state = NodeActualState.PENDING.value
            ns_arg.error_message = "Agent offline"
            return None

        manager._deploy_single_node = _always_pending

        with (
            patch("app.tasks.node_lifecycle_deploy.DEPLOY_RETRY_ATTEMPTS", 2),
            patch("app.tasks.node_lifecycle_deploy.DEPLOY_RETRY_BACKOFF_SECONDS", 0),
        ):
            result = await manager._deploy_single_node_with_retry(ns)

        assert result is None
        # After exhaustion with a transient failure, _handle_transient_failure is called.
        # The node remains in PENDING so the enforcement/reconciliation loop can retry it.
        assert ns.actual_state == NodeActualState.PENDING.value

    @pytest.mark.asyncio
    async def test_non_transient_stops_retry_immediately(self, test_db, test_user):
        """ERROR state (non-transient) stops retrying without further attempts."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1")

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        call_count = 0

        async def _always_error(ns_arg):
            nonlocal call_count
            call_count += 1
            ns_arg.actual_state = NodeActualState.ERROR.value
            ns_arg.error_message = "Image not found"
            return None

        manager._deploy_single_node = _always_error

        with (
            patch("app.tasks.node_lifecycle_deploy.DEPLOY_RETRY_ATTEMPTS", 3),
            patch("app.tasks.node_lifecycle_deploy.DEPLOY_RETRY_BACKOFF_SECONDS", 0),
        ):
            result = await manager._deploy_single_node_with_retry(ns)

        assert result is None
        assert call_count == 1  # No retries after non-transient failure
        assert ns.actual_state == NodeActualState.ERROR.value


# ---------------------------------------------------------------------------
# TestCreateAndStartNodes
# ---------------------------------------------------------------------------


class TestCreateAndStartNodes:
    """Tests for _create_and_start_nodes()."""

    def _base_manager(self, test_db, test_user):
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        return host, lab, job

    @pytest.mark.asyncio
    async def test_desired_state_filter_skips_changed_nodes(self, test_db, test_user):
        """Nodes whose desired_state changed away from 'running' are skipped."""
        host, lab, job = self._base_manager(test_db, test_user)
        # desired changed to 'stopped' before _create_and_start_nodes runs
        ns = _make_node_state(
            test_db, lab, "n1", "R1", desired="stopped", actual="undeployed"
        )
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", device="linux", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.db_nodes_map = {"R1": node_def}
        manager._manifest = None

        deploy_called = False

        async def _spy_deploy(ns_arg):
            nonlocal deploy_called
            deploy_called = True
            return None

        manager._deploy_single_node_with_retry = _spy_deploy
        manager._connect_same_host_links = AsyncMock()

        with (
            patch("app.tasks.node_lifecycle_deploy._update_node_placements", new_callable=AsyncMock),
            patch("app.tasks.node_lifecycle_deploy._capture_node_ips", new_callable=AsyncMock),
        ):
            deployed = await manager._create_and_start_nodes([ns], "=== Test Phase ===")

        assert deployed == []
        assert deploy_called is False

    @pytest.mark.asyncio
    async def test_non_ceos_deploys_in_parallel(self, test_db, test_user):
        """Multiple non-cEOS nodes are submitted as parallel tasks via gather."""
        host, lab, job = self._base_manager(test_db, test_user)
        ns1 = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        ns2 = _make_node_state(test_db, lab, "n2", "R2", desired="running", actual="undeployed")
        node_def1 = _make_node_def(test_db, lab, "n1", "R1", "R1", device="linux", host_id=host.id)
        node_def2 = _make_node_def(test_db, lab, "n2", "R2", "R2", device="linux", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1", "n2"], agent=host)
        manager.db_nodes_map = {"R1": node_def1, "R2": node_def2}
        manager._manifest = None

        deployed_set = []

        async def _mock_retry(ns_arg):
            ns_arg.actual_state = NodeActualState.RUNNING.value
            deployed_set.append(ns_arg.node_name)
            return ns_arg.node_name

        manager._deploy_single_node_with_retry = _mock_retry
        manager._connect_same_host_links = AsyncMock()

        with (
            patch("app.tasks.node_lifecycle_deploy._update_node_placements", new_callable=AsyncMock),
            patch("app.tasks.node_lifecycle_deploy._capture_node_ips", new_callable=AsyncMock),
        ):
            deployed = await manager._create_and_start_nodes([ns1, ns2], "=== Test ===")

        assert set(deployed) == {"R1", "R2"}
        assert set(deployed_set) == {"R1", "R2"}

    @pytest.mark.asyncio
    async def test_ceos_stagger_sequential_with_sleep(self, test_db, test_user):
        """Two cEOS nodes deploy sequentially with asyncio.sleep between them."""
        host, lab, job = self._base_manager(test_db, test_user)
        ns1 = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        ns2 = _make_node_state(test_db, lab, "n2", "R2", desired="running", actual="undeployed")
        node_def1 = _make_node_def(test_db, lab, "n1", "R1", "R1", device="arista_ceos", host_id=host.id)
        node_def2 = _make_node_def(test_db, lab, "n2", "R2", "R2", device="arista_ceos", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1", "n2"], agent=host)
        manager.db_nodes_map = {"R1": node_def1, "R2": node_def2}
        manager._manifest = None

        deploy_order = []
        sleep_calls = []

        async def _mock_retry(ns_arg):
            deploy_order.append(ns_arg.node_name)
            ns_arg.actual_state = NodeActualState.RUNNING.value
            return ns_arg.node_name

        async def _mock_sleep(seconds):
            sleep_calls.append(seconds)

        manager._deploy_single_node_with_retry = _mock_retry
        manager._connect_same_host_links = AsyncMock()

        with (
            patch("app.tasks.node_lifecycle_deploy.asyncio.sleep", _mock_sleep),
            patch("app.tasks.node_lifecycle_deploy._update_node_placements", new_callable=AsyncMock),
            patch("app.tasks.node_lifecycle_deploy._capture_node_ips", new_callable=AsyncMock),
        ):
            deployed = await manager._create_and_start_nodes([ns1, ns2], "=== Test ===")

        assert "R1" in deployed and "R2" in deployed
        # Sequential: R1 first, then sleep, then R2
        assert deploy_order == ["R1", "R2"]
        assert len(sleep_calls) == 1

    @pytest.mark.asyncio
    async def test_mixed_ceos_and_non_ceos(self, test_db, test_user):
        """Mixed cEOS + non-cEOS: non-cEOS parallel then cEOS sequential."""
        host, lab, job = self._base_manager(test_db, test_user)
        ns_linux = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        ns_ceos = _make_node_state(test_db, lab, "n2", "R2", desired="running", actual="undeployed")
        node_def_linux = _make_node_def(test_db, lab, "n1", "R1", "R1", device="linux", host_id=host.id)
        node_def_ceos = _make_node_def(test_db, lab, "n2", "R2", "R2", device="arista_ceos", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1", "n2"], agent=host)
        manager.db_nodes_map = {"R1": node_def_linux, "R2": node_def_ceos}
        manager._manifest = None

        async def _mock_retry(ns_arg):
            ns_arg.actual_state = NodeActualState.RUNNING.value
            return ns_arg.node_name

        async def _noop_sleep(seconds):
            pass

        manager._deploy_single_node_with_retry = _mock_retry
        manager._connect_same_host_links = AsyncMock()

        with (
            patch("app.tasks.node_lifecycle_deploy.asyncio.sleep", _noop_sleep),
            patch("app.tasks.node_lifecycle_deploy._update_node_placements", new_callable=AsyncMock),
            patch("app.tasks.node_lifecycle_deploy._capture_node_ips", new_callable=AsyncMock),
        ):
            deployed = await manager._create_and_start_nodes(
                [ns_linux, ns_ceos], "=== Test ==="
            )

        assert set(deployed) == {"R1", "R2"}

    @pytest.mark.asyncio
    async def test_gather_exception_is_logged_not_raised(self, test_db, test_user):
        """Exceptions raised inside parallel gather are caught and logged, not propagated."""
        host, lab, job = self._base_manager(test_db, test_user)
        ns = _make_node_state(test_db, lab, "n1", "R1", desired="running", actual="undeployed")
        node_def = _make_node_def(test_db, lab, "n1", "R1", "R1", device="linux", host_id=host.id)

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.db_nodes_map = {"R1": node_def}
        manager._manifest = None

        async def _raises(ns_arg):
            raise RuntimeError("internal crash in gather")

        manager._deploy_single_node_with_retry = _raises
        manager._connect_same_host_links = AsyncMock()

        with (
            patch("app.tasks.node_lifecycle_deploy._update_node_placements", new_callable=AsyncMock),
            patch("app.tasks.node_lifecycle_deploy._capture_node_ips", new_callable=AsyncMock),
        ):
            # Should not raise; exception is swallowed by gather(return_exceptions=True)
            deployed = await manager._create_and_start_nodes([ns], "=== Test ===")

        assert deployed == []


# ---------------------------------------------------------------------------
# TestGetInterfaceCount
# ---------------------------------------------------------------------------


class TestGetInterfaceCount:
    """Tests for _get_interface_count()."""

    def test_returns_map_value_when_present(self, test_db, test_user):
        """Returns count from topology service when node is found."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)

        with patch.object(
            manager.topo_service,
            "get_interface_count_map",
            return_value={"R1": 8},
        ):
            count = manager._get_interface_count("R1")

        assert count == 8

    def test_missing_node_defaults_to_minimum_4(self, test_db, test_user):
        """When node is absent from the map (returns 0), minimum of 4 is enforced."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)

        with patch.object(
            manager.topo_service,
            "get_interface_count_map",
            return_value={},  # R1 not present → defaults to 0
        ):
            count = manager._get_interface_count("R1")

        assert count == 4

    def test_high_count_above_minimum_preserved(self, test_db, test_user):
        """Count above minimum is returned unchanged."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)

        with patch.object(
            manager.topo_service,
            "get_interface_count_map",
            return_value={"R1": 24},
        ):
            count = manager._get_interface_count("R1")

        assert count == 24

    def test_count_below_minimum_is_raised_to_4(self, test_db, test_user):
        """Count below minimum (e.g. 2) is clamped to 4."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)

        with patch.object(
            manager.topo_service,
            "get_interface_count_map",
            return_value={"R1": 2},
        ):
            count = manager._get_interface_count("R1")

        assert count == 4


# ---------------------------------------------------------------------------
# TestStartNodesTopology
# ---------------------------------------------------------------------------


class TestStartNodesTopology:
    """Tests for _start_nodes_topology()."""

    def _setup(self, test_db, test_user):
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)
        ns = _make_node_state(
            test_db, lab, "n1", "R1", desired="running", actual="stopped"
        )
        return host, lab, job, ns

    @pytest.mark.asyncio
    async def test_success_marks_running(self, test_db, test_user):
        """Successful topology redeploy marks nodes as RUNNING."""
        host, lab, job, ns = self._setup(test_db, test_user)
        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.old_agent_ids = set()

        mock_node = MagicMock()
        mock_node.container_name = "R1"
        mock_node.name = "R1"
        mock_node.id = "n1"
        mock_graph = MagicMock()
        mock_graph.nodes = [mock_node]

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
            await manager._start_nodes_topology([ns])

        assert ns.actual_state == NodeActualState.RUNNING.value
        assert ns.error_message is None

    @pytest.mark.asyncio
    async def test_no_topology_marks_error(self, test_db, test_user):
        """No topology in DB marks all nodes as ERROR."""
        host, lab, job, ns = self._setup(test_db, test_user)
        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)

        with patch.object(manager.topo_service, "has_nodes", return_value=False):
            await manager._start_nodes_topology([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "No topology" in (ns.error_message or "")

    @pytest.mark.asyncio
    async def test_empty_filter_marks_error(self, test_db, test_user):
        """When filter returns no nodes for this agent, marks ERROR."""
        host, lab, job, ns = self._setup(test_db, test_user)
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
            await manager._start_nodes_topology([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "No nodes" in (ns.error_message or "")

    @pytest.mark.asyncio
    async def test_misplaced_nodes_marks_error_and_fails_job(self, test_db, test_user):
        """Misplaced nodes from _validate_topology_placement marks ERROR and fails job."""
        host, lab, job, ns = self._setup(test_db, test_user)
        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)

        mock_node = MagicMock()
        mock_node.container_name = "R1"
        mock_node.name = "R1"
        mock_graph = MagicMock()
        mock_graph.nodes = [mock_node]

        with (
            patch.object(manager.topo_service, "has_nodes", return_value=True),
            patch.object(
                manager, "_filter_topology_for_agent",
                return_value=(mock_graph, {"R1"}),
            ),
            patch.object(
                manager, "_validate_topology_placement",
                return_value=["R1"],  # R1 is on wrong agent
            ),
        ):
            await manager._start_nodes_topology([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert job.status == "failed"

    @pytest.mark.asyncio
    async def test_lock_failure_marks_error_and_fails_job(self, test_db, test_user):
        """Failed lock acquisition marks nodes as ERROR and fails the job."""
        host, lab, job, ns = self._setup(test_db, test_user)
        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)

        mock_node = MagicMock()
        mock_node.container_name = "R1"
        mock_node.name = "R1"
        mock_graph = MagicMock()
        mock_graph.nodes = [mock_node]

        with (
            patch.object(manager.topo_service, "has_nodes", return_value=True),
            patch.object(
                manager, "_filter_topology_for_agent",
                return_value=(mock_graph, {"R1"}),
            ),
            patch.object(manager, "_validate_topology_placement", return_value=[]),
            patch("app.tasks.node_lifecycle_deploy.graph_to_deploy_topology", return_value="{}"),
            patch(
                "app.tasks.node_lifecycle_deploy.acquire_deploy_lock",
                return_value=(False, ["R1"]),
            ),
        ):
            await manager._start_nodes_topology([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "lock" in (ns.error_message or "").lower()
        assert job.status == "failed"

    @pytest.mark.asyncio
    async def test_agent_unavailable_sets_pending(self, test_db, test_user):
        """AgentUnavailableError during redeploy sets nodes to PENDING (transient)."""
        host, lab, job, ns = self._setup(test_db, test_user)
        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.old_agent_ids = set()

        mock_node = MagicMock()
        mock_node.container_name = "R1"
        mock_node.name = "R1"
        mock_graph = MagicMock()
        mock_graph.nodes = [mock_node]

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
                side_effect=AgentUnavailableError("agent-1", "timeout")
            )
            await manager._start_nodes_topology([ns])

        assert ns.actual_state == NodeActualState.PENDING.value

    @pytest.mark.asyncio
    async def test_deploy_failed_result_marks_error(self, test_db, test_user):
        """Deploy result with status != 'completed' marks nodes as ERROR."""
        host, lab, job, ns = self._setup(test_db, test_user)
        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.old_agent_ids = set()

        mock_node = MagicMock()
        mock_node.container_name = "R1"
        mock_node.name = "R1"
        mock_graph = MagicMock()
        mock_graph.nodes = [mock_node]

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
                return_value={"status": "failed", "error_message": "timeout on agent side"}
            )
            await manager._start_nodes_topology([ns])

        assert ns.actual_state == NodeActualState.ERROR.value
        assert "timeout on agent side" in (ns.error_message or "")

    @pytest.mark.asyncio
    async def test_desired_state_changed_skips_deploy(self, test_db, test_user):
        """Nodes whose desired_state changed to 'stopped' before execution are skipped."""
        host, lab, job, ns = self._setup(test_db, test_user)
        # Change desired state so it no longer matches
        ns.desired_state = NodeDesiredState.STOPPED.value
        test_db.commit()

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)

        deploy_called = False

        async def _spy_deploy(*args, **kwargs):
            nonlocal deploy_called
            deploy_called = True

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac:
            mock_ac.deploy_to_agent = AsyncMock(side_effect=_spy_deploy)
            # has_nodes is never reached when desired_state filter exits early
            await manager._start_nodes_topology([ns])

        assert deploy_called is False


# ---------------------------------------------------------------------------
# TestConnectSameHostLinksExtended
# ---------------------------------------------------------------------------


class TestConnectSameHostLinksExtended:
    """Extended tests for _connect_same_host_links() beyond the base suite."""

    @pytest.mark.asyncio
    async def test_no_graph_builds_from_topo_service(self, test_db, test_user):
        """When manager.graph is None, loads graph from topo_service."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)

        manager = _make_manager(test_db, lab, job, ["n1"], agent=host)
        manager.graph = None  # Force load from service
        manager.all_lab_states = {}
        manager.placements_map = {}

        mock_graph = MagicMock()
        mock_graph.links = []  # No links → returns immediately after load
        mock_graph.nodes = []

        with patch.object(
            manager.topo_service, "export_to_graph", return_value=mock_graph
        ) as mock_export:
            await manager._connect_same_host_links({"R1"})
            mock_export.assert_called_once_with(lab.id)

        # After the call, manager.graph should be set
        assert manager.graph is mock_graph

    @pytest.mark.asyncio
    async def test_no_eligible_links_is_noop(self, test_db, test_user):
        """When no links qualify, create_link_on_agent is never called."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)

        ns1 = _make_node_state(test_db, lab, "n1", "R1", actual="running")
        ns2 = _make_node_state(test_db, lab, "n2", "R2", actual="stopped")  # Not running
        _make_placement(test_db, lab, "R1", host.id)
        _make_placement(test_db, lab, "R2", host.id)

        manager = _make_manager(test_db, lab, job, ["n1", "n2"], agent=host)
        manager.all_lab_states = {"R1": ns1, "R2": ns2}
        manager.placements_map = {
            "R1": test_db.query(models.NodePlacement).filter_by(node_name="R1").first(),
            "R2": test_db.query(models.NodePlacement).filter_by(node_name="R2").first(),
        }

        ep_a = MagicMock()
        ep_a.node = "n1"
        ep_a.ifname = "eth1"
        ep_b = MagicMock()
        ep_b.node = "n2"
        ep_b.ifname = "eth1"
        mock_link = MagicMock()
        mock_link.endpoints = [ep_a, ep_b]

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

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac:
            mock_ac.create_link_on_agent = AsyncMock()
            await manager._connect_same_host_links({"R1"})
            mock_ac.create_link_on_agent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gather_exception_is_logged_not_raised(self, test_db, test_user):
        """Exception raised inside _connect_one gather task is logged, not propagated."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)

        ns1 = _make_node_state(test_db, lab, "n1", "R1", actual="running")
        ns2 = _make_node_state(test_db, lab, "n2", "R2", actual="running")
        _make_placement(test_db, lab, "R1", host.id)
        _make_placement(test_db, lab, "R2", host.id)

        manager = _make_manager(test_db, lab, job, ["n1", "n2"], agent=host)
        manager.all_lab_states = {"R1": ns1, "R2": ns2}
        manager.placements_map = {
            "R1": test_db.query(models.NodePlacement).filter_by(node_name="R1").first(),
            "R2": test_db.query(models.NodePlacement).filter_by(node_name="R2").first(),
        }

        ep_a = MagicMock()
        ep_a.node = "n1"
        ep_a.ifname = "eth1"
        ep_b = MagicMock()
        ep_b.node = "n2"
        ep_b.ifname = "eth1"
        mock_link = MagicMock()
        mock_link.endpoints = [ep_a, ep_b]

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

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac:
            # Raise an exception inside the gather coroutine
            mock_ac.create_link_on_agent = AsyncMock(
                side_effect=RuntimeError("OVS bridge down")
            )
            # Should not raise — gather uses return_exceptions=True
            await manager._connect_same_host_links({"R1", "R2"})

    @pytest.mark.asyncio
    async def test_link_with_no_placement_is_skipped(self, test_db, test_user):
        """Links where one endpoint has no placement entry are silently skipped."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)

        ns1 = _make_node_state(test_db, lab, "n1", "R1", actual="running")
        ns2 = _make_node_state(test_db, lab, "n2", "R2", actual="running")

        manager = _make_manager(test_db, lab, job, ["n1", "n2"], agent=host)
        manager.all_lab_states = {"R1": ns1, "R2": ns2}
        # R2 has no placement
        manager.placements_map = {}

        ep_a = MagicMock()
        ep_a.node = "n1"
        ep_a.ifname = "eth1"
        ep_b = MagicMock()
        ep_b.node = "n2"
        ep_b.ifname = "eth1"
        mock_link = MagicMock()
        mock_link.endpoints = [ep_a, ep_b]

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

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac:
            mock_ac.create_link_on_agent = AsyncMock()
            await manager._connect_same_host_links({"R1"})
            mock_ac.create_link_on_agent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_node_not_in_deployed_set_skips_link(self, test_db, test_user):
        """Links where neither endpoint is in the deployed names set are skipped."""
        host = _make_host(test_db)
        lab = _make_lab(test_db, test_user, agent_id=host.id)
        job = _make_job(test_db, lab, test_user)

        ns1 = _make_node_state(test_db, lab, "n1", "R1", actual="running")
        ns2 = _make_node_state(test_db, lab, "n2", "R2", actual="running")
        _make_placement(test_db, lab, "R1", host.id)
        _make_placement(test_db, lab, "R2", host.id)

        manager = _make_manager(test_db, lab, job, ["n1", "n2"], agent=host)
        manager.all_lab_states = {"R1": ns1, "R2": ns2}
        manager.placements_map = {
            "R1": test_db.query(models.NodePlacement).filter_by(node_name="R1").first(),
            "R2": test_db.query(models.NodePlacement).filter_by(node_name="R2").first(),
        }

        ep_a = MagicMock()
        ep_a.node = "n1"
        ep_a.ifname = "eth1"
        ep_b = MagicMock()
        ep_b.node = "n2"
        ep_b.ifname = "eth1"
        mock_link = MagicMock()
        mock_link.endpoints = [ep_a, ep_b]

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

        with patch("app.tasks.node_lifecycle_deploy.agent_client") as mock_ac:
            mock_ac.create_link_on_agent = AsyncMock()
            # Neither R1 nor R2 is in the deployed set
            await manager._connect_same_host_links({"R3"})
            mock_ac.create_link_on_agent.assert_not_awaited()
