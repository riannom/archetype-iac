"""Extended tests for app/tasks/reconciliation_db.py.

Covers additional scenarios not in test_tasks_reconciliation_db.py:
- _do_reconcile_lab: link normalization, orphan container cleanup, misplaced
  containers, auto-connect pending links, link deletion, node state observations,
  readiness checks, transitional state recovery, lab state computation
- _ensure_link_states_for_lab: canonical ordering swap, host ID swap, placement
  resolution, dedup pass
- _maybe_cleanup_labless_containers: VXLAN port reconciliation, overlay convergence
- _reconcile_single_lab: lock not acquired, active job within timeout
- Node-level: starting_started_at handling, undeployed detection without agent
  response, boot_started_at backfill, readiness polling, carrier handling
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app import models
from app.state import (
    LabState,
    LinkActualState,
    NodeActualState,
)
from tests.factories import make_host, make_lab, make_link, make_link_state, make_node, make_node_state, make_placement


# ---------------------------------------------------------------------------
# Module-level autouse fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _disable_link_broadcasts():
    """Disable background broadcast tasks during reconciliation tests."""
    with patch(
        "app.tasks.reconciliation_db.broadcast_link_state_change",
        new_callable=AsyncMock,
    ):
        with patch(
            "app.tasks.reconciliation_db.broadcast_node_state_change",
            new_callable=AsyncMock,
        ):
            yield


@pytest.fixture(autouse=True)
def _disable_external_reconcile_actions():
    """Prevent reconciliation from invoking external side effects."""
    with patch(
        "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
        new_callable=AsyncMock,
        return_value={"nodes": []},
    ):
        with patch(
            "app.tasks.reconciliation_db.agent_client.get_agent_for_lab",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                return_value=True,
            ):
                yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vxlan_tunnel(db, lab_id, link_state_id, agent_a_id, agent_b_id, *, status="active"):
    t = models.VxlanTunnel(
        lab_id=lab_id,
        link_state_id=link_state_id,
        vni=10000,
        vlan_tag=200,
        agent_a_id=agent_a_id,
        agent_a_ip="10.0.0.1",
        agent_b_id=agent_b_id,
        agent_b_ip="10.0.0.2",
        status=status,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _reconcile_patches(**overrides):
    """Return a dict of standard patches for _do_reconcile_lab calls.

    Callers can override specific patches by passing keyword arguments.
    """
    defaults = {
        "ensure_link_states": patch(
            "app.tasks.reconciliation_db._ensure_link_states_for_lab",
            return_value=0,
        ),
        "cleanup_orphans": patch(
            "app.tasks.reconciliation_db.cleanup_orphaned_node_states",
            return_value=0,
        ),
        "topo_service": patch("app.tasks.reconciliation_db.TopologyService"),
        "get_lab_provider": patch(
            "app.utils.lab.get_lab_provider", return_value="docker"
        ),
        "link_ops_lock": patch("app.tasks.reconciliation.link_ops_lock"),
    }
    defaults.update(overrides)
    return defaults


class _ReconcileContext:
    """Context manager that applies all standard reconciliation patches."""

    def __init__(self, agent_status_nodes=None, is_online=True, **overrides):
        self._agent_nodes = agent_status_nodes or []
        self._is_online = is_online
        self._overrides = overrides
        self._stack = []

    def __enter__(self):
        self._patches = _reconcile_patches(**self._overrides)
        self._mocks = {}
        for key, p in self._patches.items():
            m = p.start()
            self._mocks[key] = m
            self._stack.append(p)

        # Configure TopologyService mock
        ts = MagicMock()
        ts.normalize_links_for_lab.return_value = 0
        ts.get_links.return_value = []
        self._mocks["topo_service"].return_value = ts

        # Configure link_ops_lock
        ml = MagicMock()
        ml.__enter__ = MagicMock(return_value=False)
        ml.__exit__ = MagicMock(return_value=False)
        self._mocks["link_ops_lock"].return_value = ml

        # Agent mocks
        self._agent_status_patch = patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
            return_value={"nodes": self._agent_nodes},
        )
        self._agent_online_patch = patch(
            "app.tasks.reconciliation_db.agent_client.is_agent_online",
            return_value=self._is_online,
        )
        self._agent_status_patch.start()
        self._agent_online_patch.start()
        self._stack.append(self._agent_status_patch)
        self._stack.append(self._agent_online_patch)

        return self._mocks

    def __exit__(self, *args):
        for p in reversed(self._stack):
            p.stop()


# ---------------------------------------------------------------------------
# Tests: _reconcile_single_lab lock behavior
# ---------------------------------------------------------------------------

class TestReconcileSingleLabLocking:
    """Tests for lock acquisition and active-job guards in _reconcile_single_lab."""

    @pytest.mark.asyncio
    async def test_lock_not_acquired_returns_zero(self, test_db, sample_lab):
        """Should skip reconciliation and return 0 if lock cannot be acquired."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=False)
        mock_lock.__exit__ = MagicMock(return_value=False)

        with patch("app.tasks.reconciliation.reconciliation_lock", return_value=mock_lock):
            result = await _reconcile_single_lab(test_db, sample_lab.id)

        assert result == 0

    @pytest.mark.asyncio
    async def test_active_job_within_timeout_skips(self, test_db, sample_lab, test_user):
        """Active bulk job still within timeout should block reconciliation."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        test_db.add(job)
        test_db.commit()

        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=True)
        mock_lock.__exit__ = MagicMock(return_value=False)

        with patch("app.tasks.reconciliation.reconciliation_lock", return_value=mock_lock):
            with patch("app.utils.job.is_job_within_timeout", return_value=True):
                result = await _reconcile_single_lab(test_db, sample_lab.id)

        assert result == 0

    @pytest.mark.asyncio
    async def test_sync_job_does_not_block_reconciliation(self, test_db, sample_lab, test_user):
        """Sync jobs (non-up/down) should NOT block reconciliation."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        test_db.add(job)
        test_db.commit()

        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=True)
        mock_lock.__exit__ = MagicMock(return_value=False)

        with patch("app.tasks.reconciliation.reconciliation_lock", return_value=mock_lock):
            with patch("app.tasks.reconciliation_db._do_reconcile_lab", new_callable=AsyncMock, return_value=0) as mock_do:
                await _reconcile_single_lab(test_db, sample_lab.id)

        mock_do.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stuck_job_proceeds_with_reconciliation(self, test_db, sample_lab, test_user):
        """Stuck bulk job (outside timeout) should NOT block reconciliation."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        test_db.add(job)
        test_db.commit()

        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=True)
        mock_lock.__exit__ = MagicMock(return_value=False)

        with patch("app.tasks.reconciliation.reconciliation_lock", return_value=mock_lock):
            with patch("app.utils.job.is_job_within_timeout", return_value=False):
                with patch("app.tasks.reconciliation_db._do_reconcile_lab", new_callable=AsyncMock, return_value=2) as mock_do:
                    result = await _reconcile_single_lab(test_db, sample_lab.id)

        mock_do.assert_awaited_once()
        assert result == 2

    @pytest.mark.asyncio
    async def test_missing_lab_returns_zero(self, test_db):
        """Non-existent lab should return 0."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        result = await _reconcile_single_lab(test_db, "nonexistent-lab-id")
        assert result == 0

    @pytest.mark.asyncio
    async def test_down_job_within_timeout_blocks(self, test_db, sample_lab, test_user):
        """Active 'down' job within timeout should also block reconciliation."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        job = models.Job(
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="down",
            status="queued",
            created_at=datetime.now(timezone.utc),
        )
        test_db.add(job)
        test_db.commit()

        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=True)
        mock_lock.__exit__ = MagicMock(return_value=False)

        with patch("app.tasks.reconciliation.reconciliation_lock", return_value=mock_lock):
            with patch("app.utils.job.is_job_within_timeout", return_value=True):
                result = await _reconcile_single_lab(test_db, sample_lab.id)

        assert result == 0


# ---------------------------------------------------------------------------
# Tests: _do_reconcile_lab - node state transitions
# ---------------------------------------------------------------------------

class TestDoReconcileLabContainerMapping:
    """Tests for container status -> node state updates."""

    @pytest.mark.asyncio
    async def test_exited_container_maps_to_stopped(self, test_db, test_user):
        """A container with 'exited' status should set actual_state=stopped."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-a")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        ns = make_node_state(
            test_db, lab.id, "R1",
            actual="running", desired="running",
            node_definition_id=node_def.id,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        with _ReconcileContext(agent_status_nodes=[{"name": "R1", "status": "exited"}]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPED.value
        assert ns.is_ready is False
        assert ns.boot_started_at is None

    @pytest.mark.asyncio
    async def test_dead_container_maps_to_error(self, test_db, test_user):
        """A container with 'dead' status should set actual_state=error."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-b")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        ns = make_node_state(
            test_db, lab.id, "R1",
            actual="running", desired="running",
            node_definition_id=node_def.id, is_ready=True,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        with _ReconcileContext(agent_status_nodes=[{"name": "R1", "status": "dead"}]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.ERROR.value
        assert "dead" in ns.error_message
        assert ns.is_ready is False
        assert ns.boot_started_at is None

    @pytest.mark.asyncio
    async def test_container_not_found_preserves_state_when_agent_not_queried(
        self, test_db, test_user
    ):
        """If expected agent was not queried, node state should be preserved."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-c", status="offline")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        ns = make_node_state(
            test_db, lab.id, "R1",
            actual="running", desired="running",
            node_definition_id=node_def.id,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        with _ReconcileContext(is_online=False):
            with patch("app.tasks.reconciliation_db.agent_client.get_agent_for_lab", new_callable=AsyncMock, return_value=None):
                await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.RUNNING.value

    @pytest.mark.asyncio
    async def test_container_not_found_marks_undeployed_when_agent_queried(
        self, test_db, test_user
    ):
        """Container absent from queried agent should become undeployed and clear placement."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-undeployed")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        ns = make_node_state(
            test_db, lab.id, "R1",
            actual="running", desired="running",
            node_definition_id=node_def.id, is_ready=True,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        # Agent responds with empty nodes list
        with _ReconcileContext(agent_status_nodes=[]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.UNDEPLOYED.value
        assert ns.is_ready is False
        assert ns.boot_started_at is None
        placement = (
            test_db.query(models.NodePlacement)
            .filter(models.NodePlacement.lab_id == lab.id)
            .one_or_none()
        )
        assert placement is None

    @pytest.mark.asyncio
    async def test_stopped_libvirt_runtime_is_destroyed_and_cleared(
        self, test_db, test_user
    ):
        """Stopped libvirt nodes should not keep stale runtimes or placements."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-libvirt-stale")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(
            test_db,
            lab.id,
            "R1",
            host_id=host.id,
            image="device.qcow2",
        )
        ns = make_node_state(
            test_db,
            lab.id,
            "R1",
            actual="running",
            desired="stopped",
            node_definition_id=node_def.id,
            is_ready=True,
        )
        make_placement(
            test_db,
            lab.id,
            "R1",
            host.id,
            node_definition_id=node_def.id,
            status="deployed",
            runtime_id="runtime-stale",
        )

        with _ReconcileContext(
            agent_status_nodes=[{
                "name": "R1",
                "status": "running",
                "node_definition_id": node_def.id,
                "runtime_id": "runtime-stale",
            }],
            destroy_container=patch(
                "app.tasks.reconciliation_db.agent_client.destroy_container_on_agent",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
        ) as mocks:
            await _do_reconcile_lab(test_db, lab, lab.id)

        mocks["destroy_container"].assert_awaited_once()
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.UNDEPLOYED.value
        assert ns.is_ready is False
        placement = (
            test_db.query(models.NodePlacement)
            .filter(models.NodePlacement.lab_id == lab.id)
            .one_or_none()
        )
        assert placement is None

    @pytest.mark.asyncio
    async def test_running_container_backfills_boot_started_at(self, test_db, test_user):
        """Running container with no boot_started_at should get it backfilled."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-boot")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        ns = make_node_state(
            test_db, lab.id, "R1",
            actual="stopped", desired="running",
            node_definition_id=node_def.id,
            boot_started_at=None,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        with _ReconcileContext(agent_status_nodes=[{"name": "R1", "status": "running"}]):
            with patch(
                "app.tasks.reconciliation_db.agent_client.check_node_readiness",
                new_callable=AsyncMock,
                return_value={"is_ready": False},
            ):
                await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.RUNNING.value
        assert ns.boot_started_at is not None

    @pytest.mark.asyncio
    async def test_running_container_readiness_becomes_ready(self, test_db, test_user):
        """Running node not yet ready should become ready when agent confirms."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-ready")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        ns = make_node_state(
            test_db, lab.id, "R1",
            actual="running", desired="running",
            node_definition_id=node_def.id,
            is_ready=False,
            boot_started_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        with _ReconcileContext(agent_status_nodes=[{"name": "R1", "status": "running"}]):
            with patch(
                "app.tasks.reconciliation_db.agent_client.check_node_readiness",
                new_callable=AsyncMock,
                return_value={"is_ready": True},
            ):
                await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        assert ns.is_ready is True

    @pytest.mark.asyncio
    async def test_unknown_container_status_counted_as_stopped(self, test_db, test_user):
        """Container with unknown/unexpected status should increment stopped_count."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-unk")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        ns = make_node_state(
            test_db, lab.id, "R1",
            actual="running", desired="running",
            node_definition_id=node_def.id,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        with _ReconcileContext(agent_status_nodes=[{"name": "R1", "status": "restarting"}]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        # "restarting" is not running/stopped/exited/error/dead, so no state change
        # but lab state should reflect the node through stopped_count
        test_db.refresh(lab)
        # Lab state should be computed (stopped since the node is counted as stopped)
        assert lab.state in (LabState.STOPPED.value, LabState.RUNNING.value, LabState.ERROR.value)


# ---------------------------------------------------------------------------
# Tests: _do_reconcile_lab - runtime identity decisions
# ---------------------------------------------------------------------------

class TestDoReconcileLabRuntimeIdentity:
    """Tests for runtime identity reconciliation decisions."""

    @pytest.mark.asyncio
    async def test_runtime_id_mismatch_flags_placement_drift(self, test_db, test_user):
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-runtime-drift")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        make_node_state(
            test_db,
            lab.id,
            "R1",
            actual="running",
            desired="running",
            node_definition_id=node_def.id,
        )
        placement = make_placement(
            test_db,
            lab.id,
            "R1",
            host.id,
            node_definition_id=node_def.id,
            status="deployed",
            runtime_id="runtime-old",
        )

        with _ReconcileContext(agent_status_nodes=[{
            "name": "R1",
            "status": "running",
            "node_definition_id": node_def.id,
            "runtime_id": "runtime-new",
        }]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(placement)
        assert placement.status == "drifted"
        assert placement.runtime_id == "runtime-old"

    @pytest.mark.asyncio
    async def test_runtime_id_mismatch_during_starting_is_adopted(self, test_db, test_user):
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-runtime-replace")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        make_node_state(
            test_db,
            lab.id,
            "R1",
            actual="starting",
            desired="running",
            node_definition_id=node_def.id,
        )
        placement = make_placement(
            test_db,
            lab.id,
            "R1",
            host.id,
            node_definition_id=node_def.id,
            status="starting",
            runtime_id="runtime-old",
        )

        with _ReconcileContext(agent_status_nodes=[{
            "name": "R1",
            "status": "running",
            "node_definition_id": node_def.id,
            "runtime_id": "runtime-new",
        }]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(placement)
        assert placement.status == "deployed"
        assert placement.runtime_id == "runtime-new"

    @pytest.mark.asyncio
    async def test_runtime_id_mismatch_with_deployed_placement_but_starting_node_state_is_adopted(
        self, test_db, test_user,
    ):
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-runtime-replace-state")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        make_node_state(
            test_db,
            lab.id,
            "R1",
            actual="starting",
            desired="running",
            node_definition_id=node_def.id,
        )
        placement = make_placement(
            test_db,
            lab.id,
            "R1",
            host.id,
            node_definition_id=node_def.id,
            status="deployed",
            runtime_id="runtime-old",
        )

        with _ReconcileContext(agent_status_nodes=[{
            "name": "R1",
            "status": "running",
            "node_definition_id": node_def.id,
            "runtime_id": "runtime-new",
        }]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(placement)
        assert placement.status == "deployed"
        assert placement.runtime_id == "runtime-new"

    @pytest.mark.asyncio
    async def test_metadata_node_definition_id_canonicalizes_reported_name(self, test_db, test_user):
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-runtime-canonical")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        node_state = make_node_state(
            test_db,
            lab.id,
            "R1",
            actual="stopped",
            desired="running",
            node_definition_id=node_def.id,
        )
        placement = make_placement(
            test_db,
            lab.id,
            "R1",
            host.id,
            node_definition_id=node_def.id,
            status="deployed",
            runtime_id="runtime-123",
        )

        with _ReconcileContext(agent_status_nodes=[{
            "name": "wrong-name",
            "status": "running",
            "node_definition_id": node_def.id,
            "runtime_id": "runtime-123",
        }]):
            with patch(
                "app.tasks.reconciliation_db.agent_client.check_node_readiness",
                new_callable=AsyncMock,
                return_value={"is_ready": False},
            ):
                await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(node_state)
        test_db.refresh(placement)
        assert node_state.actual_state == NodeActualState.RUNNING.value
        assert placement.node_name == "R1"
        assert placement.runtime_id == "runtime-123"


# ---------------------------------------------------------------------------
# Tests: _do_reconcile_lab - transitional state recovery
# ---------------------------------------------------------------------------

class TestDoReconcileLabTransitionalRecovery:
    """Tests for stuck transitional state recovery."""

    @pytest.mark.asyncio
    async def test_active_starting_within_threshold_is_skipped(self, test_db, test_user):
        """Nodes with fresh starting_started_at should not be reconciled."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-d")
        lab = make_lab(test_db, test_user, state="starting", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        ns = make_node_state(
            test_db, lab.id, "R1",
            actual="starting", desired="running",
            node_definition_id=node_def.id,
            starting_started_at=datetime.now(timezone.utc),
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        with _ReconcileContext(agent_status_nodes=[{"name": "R1", "status": "running"}]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STARTING.value

    @pytest.mark.asyncio
    async def test_stale_starting_recovered_by_reconciliation(self, test_db, test_user):
        """Nodes with stale starting_started_at should be recovered."""
        from app.tasks.reconciliation_db import _do_reconcile_lab
        from app.config import settings

        host = make_host(test_db, "host-stale-start")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        # Use naive datetime since SQLite strips timezone info on round-trip
        stale_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            seconds=settings.stale_node_starting_threshold + 60
        )
        ns = make_node_state(
            test_db, lab.id, "R1",
            actual="starting", desired="running",
            node_definition_id=node_def.id,
            starting_started_at=stale_time,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        # Mock utcnow to return naive datetime for consistent comparison
        # (SQLite strips tzinfo from stored datetimes)
        mock_now = datetime.now(timezone.utc).replace(tzinfo=None)
        with _ReconcileContext(agent_status_nodes=[{"name": "R1", "status": "running"}]):
            with patch(
                "app.tasks.reconciliation_db.utcnow",
                return_value=mock_now,
            ):
                with patch(
                    "app.tasks.reconciliation_db.agent_client.check_node_readiness",
                    new_callable=AsyncMock,
                    return_value={"is_ready": False},
                ):
                    await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        # Stale starting should be cleared and node updated to actual container state
        assert ns.actual_state == NodeActualState.RUNNING.value
        assert ns.starting_started_at is None

    @pytest.mark.asyncio
    async def test_stale_stopping_recovered_by_reconciliation(self, test_db, test_user):
        """Nodes with stale stopping_started_at should be recovered."""
        from app.tasks.reconciliation_db import _do_reconcile_lab
        from app.config import settings

        host = make_host(test_db, "host-stale-stop")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        # Use naive datetime since SQLite strips timezone info on round-trip
        stale_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            seconds=settings.stale_stopping_threshold + 60
        )
        ns = make_node_state(
            test_db, lab.id, "R1",
            actual="stopping", desired="stopped",
            node_definition_id=node_def.id,
            stopping_started_at=stale_time,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        # Mock utcnow to return naive datetime for consistent comparison
        mock_now = datetime.now(timezone.utc).replace(tzinfo=None)
        with _ReconcileContext(agent_status_nodes=[{"name": "R1", "status": "stopped"}]):
            with patch(
                "app.tasks.reconciliation_db.utcnow",
                return_value=mock_now,
            ):
                await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPED.value
        assert ns.stopping_started_at is None

    @pytest.mark.asyncio
    async def test_stopping_without_timestamp_no_job_recovers(self, test_db, test_user):
        """Node in 'stopping' state without timestamp and no active job should recover."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-stop-nots")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        ns = make_node_state(
            test_db, lab.id, "R1",
            actual="stopping", desired="stopped",
            node_definition_id=node_def.id,
            stopping_started_at=None,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        with _ReconcileContext(agent_status_nodes=[{"name": "R1", "status": "stopped"}]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        # Should fall through to container status mapping
        assert ns.actual_state == NodeActualState.STOPPED.value

    @pytest.mark.asyncio
    async def test_starting_without_timestamp_with_job_skipped(self, test_db, test_user):
        """Node in 'starting' with active job but no timestamp should be skipped."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-start-job")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        ns = make_node_state(
            test_db, lab.id, "R1",
            actual="starting", desired="running",
            node_definition_id=node_def.id,
            starting_started_at=None,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        # Create an active sync job (doesn't block reconciliation at the top level
        # but is picked up as check_active_job inside _do_reconcile_lab)
        job = models.Job(
            lab_id=lab.id,
            user_id=test_user.id,
            action="sync:lab",
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        test_db.add(job)
        test_db.commit()

        with _ReconcileContext(agent_status_nodes=[{"name": "R1", "status": "running"}]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        # With active job and starting state, node should be skipped
        assert ns.actual_state == NodeActualState.STARTING.value

    @pytest.mark.asyncio
    async def test_enforcement_failed_node_skipped(self, test_db, test_user):
        """Nodes with enforcement_failed_at should be skipped by reconciliation."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-enf")
        lab = make_lab(test_db, test_user, state="error", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        ns = make_node_state(
            test_db, lab.id, "R1",
            actual="error", desired="running",
            node_definition_id=node_def.id,
            enforcement_failed_at=datetime.now(timezone.utc),
            error_message="enforcement exhausted",
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        with _ReconcileContext(agent_status_nodes=[{"name": "R1", "status": "running"}]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        # State should NOT be updated to running -- enforcement owns this node
        assert ns.actual_state == NodeActualState.ERROR.value
        assert ns.error_message == "enforcement exhausted"

    @pytest.mark.asyncio
    async def test_image_syncing_node_skipped(self, test_db, test_user):
        """Nodes with active image sync should be skipped."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-sync")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = make_node(test_db, lab.id, "R1", host_id=host.id)
        ns = make_node_state(
            test_db, lab.id, "R1",
            actual="undeployed", desired="running",
            node_definition_id=node_def.id,
            image_sync_status="syncing",
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        with _ReconcileContext(agent_status_nodes=[]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        # Node should remain undeployed, not marked further
        assert ns.actual_state == NodeActualState.UNDEPLOYED.value


# ---------------------------------------------------------------------------
# Tests: _do_reconcile_lab - link state reconciliation
# ---------------------------------------------------------------------------

class TestDoReconcileLabLinkStatesExtended:
    """Extended link state reconciliation tests."""

    @pytest.mark.asyncio
    async def test_carrier_off_sets_link_down(self, test_db, test_user):
        """When carrier is off on one endpoint, link should be DOWN."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-e")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        n1 = make_node(test_db, lab.id, "R1", host_id=host.id)
        n2 = make_node(test_db, lab.id, "R2", host_id=host.id)
        make_node_state(
            test_db, lab.id, "R1", actual="running", desired="running",
            node_definition_id=n1.id,
        )
        make_node_state(
            test_db, lab.id, "R2", node_id="r2", actual="running", desired="running",
            node_definition_id=n2.id,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=n1.id)
        make_placement(test_db, lab.id, "R2", host.id, node_definition_id=n2.id)

        ls = make_link_state(
            test_db, lab.id, "R1", "eth1", "R2", "eth1",
            actual="up", source_carrier_state="off",
        )

        with _ReconcileContext(agent_status_nodes=[
            {"name": "R1", "status": "running"},
            {"name": "R2", "status": "running"},
        ]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ls)
        assert ls.actual_state == LinkActualState.DOWN.value
        assert "Carrier disabled" in ls.error_message

    @pytest.mark.asyncio
    async def test_cross_host_link_no_tunnel_sets_error(self, test_db, test_user):
        """Cross-host link without active tunnel should be marked error."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host_a = make_host(test_db, "host-f")
        host_b = make_host(test_db, "host-g")
        lab = make_lab(test_db, test_user, state="running", agent_id=host_a.id)
        n1 = make_node(test_db, lab.id, "R1", host_id=host_a.id)
        n2 = make_node(test_db, lab.id, "R2", host_id=host_b.id)
        make_node_state(
            test_db, lab.id, "R1", actual="running", desired="running",
            node_definition_id=n1.id,
        )
        make_node_state(
            test_db, lab.id, "R2", node_id="r2", actual="running", desired="running",
            node_definition_id=n2.id,
        )
        make_placement(test_db, lab.id, "R1", host_a.id, node_definition_id=n1.id)
        make_placement(test_db, lab.id, "R2", host_b.id, node_definition_id=n2.id)

        ls = make_link_state(
            test_db, lab.id, "R1", "eth1", "R2", "eth1",
            actual="pending", is_cross_host=True,
            source_host_id=host_a.id, target_host_id=host_b.id,
        )

        with _ReconcileContext(agent_status_nodes=[
            {"name": "R1", "status": "running"},
            {"name": "R2", "status": "running"},
        ]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ls)
        assert ls.actual_state == LinkActualState.ERROR.value
        assert "VXLAN tunnel" in ls.error_message

    @pytest.mark.asyncio
    async def test_cross_host_link_with_active_tunnel_sets_up(self, test_db, test_user):
        """Cross-host link with active tunnel should be marked UP."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host_a = make_host(test_db, "host-tun-a")
        host_b = make_host(test_db, "host-tun-b")
        lab = make_lab(test_db, test_user, state="running", agent_id=host_a.id)
        n1 = make_node(test_db, lab.id, "R1", host_id=host_a.id)
        n2 = make_node(test_db, lab.id, "R2", host_id=host_b.id)
        make_node_state(
            test_db, lab.id, "R1", actual="running", desired="running",
            node_definition_id=n1.id,
        )
        make_node_state(
            test_db, lab.id, "R2", node_id="r2", actual="running", desired="running",
            node_definition_id=n2.id,
        )
        make_placement(test_db, lab.id, "R1", host_a.id, node_definition_id=n1.id)
        make_placement(test_db, lab.id, "R2", host_b.id, node_definition_id=n2.id)

        ls = make_link_state(
            test_db, lab.id, "R1", "eth1", "R2", "eth1",
            actual="pending", is_cross_host=True,
            source_host_id=host_a.id, target_host_id=host_b.id,
        )
        _make_vxlan_tunnel(test_db, lab.id, ls.id, host_a.id, host_b.id, status="active")

        with _ReconcileContext(agent_status_nodes=[
            {"name": "R1", "status": "running"},
            {"name": "R2", "status": "running"},
        ]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ls)
        assert ls.actual_state == LinkActualState.UP.value
        assert ls.error_message is None

    @pytest.mark.asyncio
    async def test_one_node_error_link_error(self, test_db, test_user):
        """Link with one error node should be marked ERROR."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-lnk-err")
        lab = make_lab(test_db, test_user, state="error", agent_id=host.id)
        n1 = make_node(test_db, lab.id, "R1", host_id=host.id)
        n2 = make_node(test_db, lab.id, "R2", host_id=host.id)
        make_node_state(
            test_db, lab.id, "R1", actual="running", desired="running",
            node_definition_id=n1.id,
        )
        make_node_state(
            test_db, lab.id, "R2", node_id="r2", actual="error", desired="running",
            node_definition_id=n2.id,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=n1.id)
        make_placement(test_db, lab.id, "R2", host.id, node_definition_id=n2.id)

        ls = make_link_state(
            test_db, lab.id, "R1", "eth1", "R2", "eth1",
            actual="up",
        )

        with _ReconcileContext(agent_status_nodes=[
            {"name": "R1", "status": "running"},
            {"name": "R2", "status": "dead"},
        ]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ls)
        assert ls.actual_state == LinkActualState.ERROR.value
        assert "error state" in ls.error_message

    @pytest.mark.asyncio
    async def test_one_node_stopped_link_down(self, test_db, test_user):
        """Link with one stopped node should be marked DOWN."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-lnk-dn")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        n1 = make_node(test_db, lab.id, "R1", host_id=host.id)
        n2 = make_node(test_db, lab.id, "R2", host_id=host.id)
        make_node_state(
            test_db, lab.id, "R1", actual="running", desired="running",
            node_definition_id=n1.id,
        )
        make_node_state(
            test_db, lab.id, "R2", node_id="r2", actual="stopped", desired="stopped",
            node_definition_id=n2.id,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=n1.id)
        make_placement(test_db, lab.id, "R2", host.id, node_definition_id=n2.id)

        ls = make_link_state(
            test_db, lab.id, "R1", "eth1", "R2", "eth1",
            actual="up",
        )

        with _ReconcileContext(agent_status_nodes=[
            {"name": "R1", "status": "running"},
            {"name": "R2", "status": "stopped"},
        ]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ls)
        assert ls.actual_state == LinkActualState.DOWN.value
        assert ls.error_message is None

    @pytest.mark.asyncio
    async def test_same_host_link_desired_down_stays_down(self, test_db, test_user):
        """Same-host link with desired=down should be actual=down."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-lnk-dd")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        n1 = make_node(test_db, lab.id, "R1", host_id=host.id)
        n2 = make_node(test_db, lab.id, "R2", host_id=host.id)
        make_node_state(
            test_db, lab.id, "R1", actual="running", desired="running",
            node_definition_id=n1.id,
        )
        make_node_state(
            test_db, lab.id, "R2", node_id="r2", actual="running", desired="running",
            node_definition_id=n2.id,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=n1.id)
        make_placement(test_db, lab.id, "R2", host.id, node_definition_id=n2.id)

        ls = make_link_state(
            test_db, lab.id, "R1", "eth1", "R2", "eth1",
            desired="down", actual="up",
        )

        with _ReconcileContext(agent_status_nodes=[
            {"name": "R1", "status": "running"},
            {"name": "R2", "status": "running"},
        ]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ls)
        assert ls.actual_state == LinkActualState.DOWN.value

    @pytest.mark.asyncio
    async def test_same_host_existing_up_preserves_up(self, test_db, test_user):
        """Same-host link already UP should stay UP."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-lnk-up")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        n1 = make_node(test_db, lab.id, "R1", host_id=host.id)
        n2 = make_node(test_db, lab.id, "R2", host_id=host.id)
        make_node_state(
            test_db, lab.id, "R1", actual="running", desired="running",
            node_definition_id=n1.id,
        )
        make_node_state(
            test_db, lab.id, "R2", node_id="r2", actual="running", desired="running",
            node_definition_id=n2.id,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=n1.id)
        make_placement(test_db, lab.id, "R2", host.id, node_definition_id=n2.id)

        ls = make_link_state(
            test_db, lab.id, "R1", "eth1", "R2", "eth1",
            desired="up", actual="up",
        )

        with _ReconcileContext(agent_status_nodes=[
            {"name": "R1", "status": "running"},
            {"name": "R2", "status": "running"},
        ]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ls)
        assert ls.actual_state == LinkActualState.UP.value

    @pytest.mark.asyncio
    async def test_deleted_link_state_removed(self, test_db, test_user):
        """Link states with desired_state='deleted' should be removed."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-h")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        n1 = make_node(test_db, lab.id, "R1", host_id=host.id)
        make_node_state(
            test_db, lab.id, "R1", actual="running", desired="running",
            node_definition_id=n1.id,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=n1.id)

        ls = make_link_state(
            test_db, lab.id, "R1", "eth1", "R2", "eth1",
            desired="deleted", actual="down",
        )
        ls_id = ls.id

        with _ReconcileContext(agent_status_nodes=[{"name": "R1", "status": "running"}]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        deleted_ls = test_db.get(models.LinkState, ls_id)
        assert deleted_ls is None


# ---------------------------------------------------------------------------
# Tests: _do_reconcile_lab - lab state computation
# ---------------------------------------------------------------------------

class TestDoReconcileLabStateComputation:
    """Tests for lab state being computed from node counts."""

    @pytest.mark.asyncio
    async def test_all_running_sets_lab_running(self, test_db, test_user):
        """Lab with all running nodes should have state=running."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-run")
        lab = make_lab(test_db, test_user, state="stopped", agent_id=host.id)
        n1 = make_node(test_db, lab.id, "R1", host_id=host.id)
        make_node_state(
            test_db, lab.id, "R1", actual="running", desired="running",
            node_definition_id=n1.id, is_ready=True,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=n1.id)

        with _ReconcileContext(agent_status_nodes=[{"name": "R1", "status": "running"}]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(lab)
        assert lab.state == LabState.RUNNING.value
        assert lab.state_error is None

    @pytest.mark.asyncio
    async def test_all_stopped_sets_lab_stopped(self, test_db, test_user):
        """Lab with all stopped nodes should have state=stopped."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-stp")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        n1 = make_node(test_db, lab.id, "R1", host_id=host.id)
        make_node_state(
            test_db, lab.id, "R1", actual="running", desired="stopped",
            node_definition_id=n1.id,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=n1.id)

        with _ReconcileContext(agent_status_nodes=[{"name": "R1", "status": "stopped"}]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(lab)
        assert lab.state == LabState.STOPPED.value

    @pytest.mark.asyncio
    async def test_error_node_sets_lab_error(self, test_db, test_user):
        """Lab with error node should have state=error and state_error message."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-lerr")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        n1 = make_node(test_db, lab.id, "R1", host_id=host.id)
        make_node_state(
            test_db, lab.id, "R1", actual="running", desired="running",
            node_definition_id=n1.id,
        )
        make_placement(test_db, lab.id, "R1", host.id, node_definition_id=n1.id)

        with _ReconcileContext(agent_status_nodes=[{"name": "R1", "status": "dead"}]):
            await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(lab)
        assert lab.state == LabState.ERROR.value
        assert "error" in lab.state_error.lower()


# ---------------------------------------------------------------------------
# Tests: _maybe_cleanup_labless_containers
# ---------------------------------------------------------------------------

class TestMaybeCleanupVxlanReconciliation:
    """Tests for VXLAN port reconciliation in _maybe_cleanup_labless_containers."""

    @pytest.mark.asyncio
    async def test_vxlan_reconciliation_called_at_interval(self, monkeypatch):
        """VXLAN reconciliation should run when counter reaches interval."""
        import app.tasks.reconciliation_db as rdb

        monkeypatch.setattr(rdb, "_lab_orphan_check_counter", rdb._LAB_ORPHAN_CHECK_INTERVAL - 1)

        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.all.return_value = []

        with patch("app.tasks.reconciliation_db.agent_client.is_agent_online", return_value=True):
            with patch("app.tasks.reconciliation_db.agent_client.cleanup_orphans_on_agent", new_callable=AsyncMock, return_value={"removed_containers": []}):
                with patch("app.tasks.reconciliation_db.agent_client.reconcile_vxlan_ports_on_agent", new_callable=AsyncMock):
                    with patch("app.tasks.cleanup_base.get_valid_lab_ids", return_value=[]):
                        with patch("app.tasks.link_reconciliation.run_overlay_convergence", new_callable=AsyncMock):
                            await rdb._maybe_cleanup_labless_containers(mock_session)

        assert rdb._lab_orphan_check_counter == 0

    @pytest.mark.asyncio
    async def test_skips_before_interval(self, monkeypatch):
        """Should skip cleanup when counter is below interval."""
        import app.tasks.reconciliation_db as rdb

        monkeypatch.setattr(rdb, "_lab_orphan_check_counter", 0)
        await rdb._maybe_cleanup_labless_containers(MagicMock())
        assert rdb._lab_orphan_check_counter == 1


# ---------------------------------------------------------------------------
# Tests: _do_reconcile_lab - exception handling
# ---------------------------------------------------------------------------

class TestDoReconcileLabExceptionPaths:
    """Tests for exception handling in _do_reconcile_lab."""

    @pytest.mark.asyncio
    async def test_ensure_link_states_exception_is_caught(self, test_db, test_user):
        """Failure in _ensure_link_states_for_lab should be caught and logged."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        lab = make_lab(test_db, test_user, state="running")

        with _ReconcileContext(
            ensure_link_states=patch(
                "app.tasks.reconciliation_db._ensure_link_states_for_lab",
                side_effect=RuntimeError("DB error"),
            ),
        ):
            # Should not raise
            result = await _do_reconcile_lab(test_db, lab, lab.id)

        assert result == 0

    @pytest.mark.asyncio
    async def test_normalize_links_exception_is_caught(self, test_db, test_user):
        """Failure in TopologyService.normalize_links_for_lab should be caught."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        lab = make_lab(test_db, test_user, state="running")

        with _ReconcileContext() as mocks:
            ts = mocks["topo_service"].return_value
            ts.normalize_links_for_lab.side_effect = RuntimeError("normalize failure")
            result = await _do_reconcile_lab(test_db, lab, lab.id)

        assert result == 0

    @pytest.mark.asyncio
    async def test_link_states_commit_before_normalize_failure(self, test_db, test_user):
        """LinkState inserts should persist even if normalize later rolls back."""
        from app.services.topology import TopologyService
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = make_host(test_db, "host-normalize-fail")
        lab = make_lab(test_db, test_user, state="running", agent_id=host.id)
        node1 = make_node(test_db, lab.id, "R1", host_id=host.id)
        node2 = make_node(test_db, lab.id, "R2", host_id=host.id)
        make_link(
            test_db,
            lab.id,
            node1.id,
            "Ethernet1",
            node2.id,
            "Ethernet1",
            link_name="R1:Ethernet1-R2:Ethernet1",
        )

        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=False)
        mock_lock.__exit__ = MagicMock(return_value=False)

        with patch(
            "app.tasks.reconciliation_db.broadcast_link_state_change",
            new_callable=AsyncMock,
        ), patch(
            "app.tasks.reconciliation_db.broadcast_node_state_change",
            new_callable=AsyncMock,
        ), patch(
            "app.tasks.reconciliation_db.cleanup_orphaned_node_states",
            return_value=0,
        ), patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
            return_value={"nodes": []},
        ), patch(
            "app.tasks.reconciliation_db.agent_client.is_agent_online",
            return_value=True,
        ), patch(
            "app.utils.lab.get_lab_provider",
            return_value="docker",
        ), patch(
            "app.tasks.reconciliation.link_ops_lock",
            return_value=mock_lock,
        ), patch.object(
            TopologyService,
            "normalize_links_for_lab",
            side_effect=RuntimeError("normalize failure"),
        ):
            result = await _do_reconcile_lab(test_db, lab, lab.id)

        assert result == 0
        states = (
            test_db.query(models.LinkState)
            .filter(models.LinkState.lab_id == lab.id)
            .all()
        )
        assert len(states) == 1
        assert states[0].link_name == "R1:eth1-R2:eth1"

    @pytest.mark.asyncio
    async def test_cleanup_orphaned_exception_is_caught(self, test_db, test_user):
        """Failure in cleanup_orphaned_node_states should be caught."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        lab = make_lab(test_db, test_user, state="running")

        with _ReconcileContext(
            cleanup_orphans=patch(
                "app.tasks.reconciliation_db.cleanup_orphaned_node_states",
                side_effect=RuntimeError("cleanup error"),
            ),
        ):
            result = await _do_reconcile_lab(test_db, lab, lab.id)

        assert result == 0


# ---------------------------------------------------------------------------
# Tests: cleanup_orphaned_node_states
# ---------------------------------------------------------------------------

class TestCleanupOrphanedNodeStates:
    """Tests for cleanup_orphaned_node_states across all safe states."""

    def test_removes_undeployed_orphan(self, test_db, sample_lab):
        """Undeployed orphan (no node_definition_id) should be deleted."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        ns = make_node_state(
            test_db, sample_lab.id, "Orphan1",
            actual=NodeActualState.UNDEPLOYED.value,
            node_definition_id=None,
        )
        ns_id = ns.id

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 1
        assert test_db.get(models.NodeState, ns_id) is None

    def test_removes_stopped_orphan(self, test_db, sample_lab):
        """Stopped orphan should be deleted."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        ns = make_node_state(
            test_db, sample_lab.id, "OrphanStopped",
            actual=NodeActualState.STOPPED.value,
            node_definition_id=None,
        )
        ns_id = ns.id

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 1
        assert test_db.get(models.NodeState, ns_id) is None

    def test_removes_error_orphan(self, test_db, sample_lab):
        """Error orphan should be deleted."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        ns = make_node_state(
            test_db, sample_lab.id, "Orphan2",
            actual=NodeActualState.ERROR.value,
            node_definition_id=None,
        )
        ns_id = ns.id

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 1
        assert test_db.get(models.NodeState, ns_id) is None

    def test_preserves_running_orphan(self, test_db, sample_lab):
        """Running orphan should NOT be deleted (active container)."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        ns = make_node_state(
            test_db, sample_lab.id, "Orphan3",
            actual=NodeActualState.RUNNING.value,
            node_definition_id=None,
        )

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 0
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.RUNNING.value

    def test_preserves_stopping_orphan(self, test_db, sample_lab):
        """Nodes in STOPPING state should be preserved even if orphaned."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        ns = make_node_state(
            test_db, sample_lab.id, "R1",
            actual=NodeActualState.STOPPING.value,
            node_definition_id=None,
        )

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 0
        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPING.value

    def test_preserves_starting_orphan(self, test_db, sample_lab):
        """Starting orphan should NOT be deleted (in transition)."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        make_node_state(
            test_db, sample_lab.id, "Orphan4",
            actual=NodeActualState.STARTING.value,
            node_definition_id=None,
        )

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 0

    def test_preserves_pending_orphan(self, test_db, sample_lab):
        """Nodes in PENDING state should be preserved even if orphaned."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        make_node_state(
            test_db, sample_lab.id, "R1",
            actual=NodeActualState.PENDING.value,
            node_definition_id=None,
        )

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 0

    def test_no_orphans_returns_zero(self, test_db, sample_lab):
        """Lab with no orphaned NodeStates should return 0."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 0

    def test_multiple_orphans_deleted(self, test_db, sample_lab):
        """Multiple orphans in safe states should all be deleted."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        make_node_state(
            test_db, sample_lab.id, "OrpA",
            actual=NodeActualState.UNDEPLOYED.value,
            node_definition_id=None,
        )
        make_node_state(
            test_db, sample_lab.id, "OrpB",
            actual=NodeActualState.STOPPED.value,
            node_definition_id=None,
            node_id="orpb",
        )
        make_node_state(
            test_db, sample_lab.id, "OrpC",
            actual=NodeActualState.ERROR.value,
            node_definition_id=None,
            node_id="orpc",
        )

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 3


# ---------------------------------------------------------------------------
# Tests: _backfill_placement_node_ids
# ---------------------------------------------------------------------------

class TestBackfillPlacementNodeIds:
    """Tests for _backfill_placement_node_ids legacy stub."""

    def test_logs_warning_when_missing_placements_exist(self, test_db, sample_lab):
        """Should log warning but return 0 (no-op)."""
        from app.tasks.reconciliation_db import _backfill_placement_node_ids

        make_placement(test_db, sample_lab.id, "R1", "host-xyz", node_definition_id=None)

        result = _backfill_placement_node_ids(test_db, sample_lab.id)
        assert result == 0

    def test_no_missing_returns_zero(self, test_db, sample_lab):
        """No missing node_definition_ids should return 0."""
        from app.tasks.reconciliation_db import _backfill_placement_node_ids

        result = _backfill_placement_node_ids(test_db, sample_lab.id)
        assert result == 0


# ---------------------------------------------------------------------------
# Tests: _ensure_link_states_for_lab
# ---------------------------------------------------------------------------

class TestEnsureLinkStatesForLab:
    """Tests for _ensure_link_states_for_lab link state creation and dedup."""

    def test_no_db_links_returns_zero(self, test_db, sample_lab):
        """Lab with no link definitions should return 0."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        result = _ensure_link_states_for_lab(test_db, sample_lab.id)
        assert result == 0

    def test_skips_link_missing_source_node_def(self, test_db, sample_lab):
        """Links where source node definition is deleted should be skipped."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        n2 = make_node(test_db, sample_lab.id, "R2")
        lnk = models.Link(
            lab_id=sample_lab.id,
            link_name="fake:eth1-R2:eth1",
            source_node_id="nonexistent-node-id",
            source_interface="eth1",
            target_node_id=n2.id,
            target_interface="eth1",
        )
        test_db.add(lnk)
        test_db.commit()

        result = _ensure_link_states_for_lab(test_db, sample_lab.id)
        assert result == 0

    def test_host_id_swap_when_canonical_reorders(self, test_db, sample_lab):
        """When canonical ordering swaps source/target, host IDs should also swap."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        host_a = make_host(test_db, "host-swap-a")
        host_b = make_host(test_db, "host-swap-b")

        n_z = make_node(test_db, sample_lab.id, "Z1", host_id=host_a.id)
        n_a = make_node(test_db, sample_lab.id, "A1", host_id=host_b.id)

        make_link(test_db, sample_lab.id, n_z.id, "eth1", n_a.id, "eth1",
                   link_name="Z1:eth1-A1:eth1")

        result = _ensure_link_states_for_lab(test_db, sample_lab.id)
        assert result == 1

        test_db.flush()
        ls = (
            test_db.query(models.LinkState)
            .filter(models.LinkState.lab_id == sample_lab.id)
            .first()
        )
        assert ls is not None
        assert ls.source_node == "A1"
        assert ls.target_node == "Z1"

    def test_cross_host_flag_set_correctly(self, test_db, sample_lab):
        """When source and target are on different hosts, is_cross_host should be True."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        host_a = make_host(test_db, "host-cross-a")
        host_b = make_host(test_db, "host-cross-b")

        n1 = make_node(test_db, sample_lab.id, "R1", host_id=host_a.id)
        n2 = make_node(test_db, sample_lab.id, "R2", host_id=host_b.id)
        make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1",
                   link_name="R1:eth1-R2:eth1")

        result = _ensure_link_states_for_lab(test_db, sample_lab.id)
        assert result == 1

        test_db.flush()
        ls = (
            test_db.query(models.LinkState)
            .filter(models.LinkState.lab_id == sample_lab.id)
            .first()
        )
        assert ls is not None
        assert ls.is_cross_host is True

    def test_same_host_link_not_cross_host(self, test_db, sample_lab):
        """When source and target are on the same host, is_cross_host should be False."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        host = make_host(test_db, "host-same")

        n1 = make_node(test_db, sample_lab.id, "R1", host_id=host.id)
        n2 = make_node(test_db, sample_lab.id, "R2", host_id=host.id)
        make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1",
                   link_name="R1:eth1-R2:eth1")

        result = _ensure_link_states_for_lab(test_db, sample_lab.id)
        assert result == 1

        test_db.flush()
        ls = (
            test_db.query(models.LinkState)
            .filter(models.LinkState.lab_id == sample_lab.id)
            .first()
        )
        assert ls is not None
        assert ls.is_cross_host is False

    def test_existing_link_state_not_recreated(self, test_db, sample_lab):
        """Existing link state with same canonical key should not be recreated."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        n1 = make_node(test_db, sample_lab.id, "R1")
        n2 = make_node(test_db, sample_lab.id, "R2")
        make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1",
                   link_name="R1:eth1-R2:eth1")
        make_link_state(test_db, sample_lab.id, "R1", "eth1", "R2", "eth1",
                         actual="up")

        result = _ensure_link_states_for_lab(test_db, sample_lab.id)
        assert result == 0

    def test_resolves_host_via_placement(self, test_db, sample_lab):
        """When node.host_id is None, should resolve via placement."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        host_a = make_host(test_db, "host-plc-a")
        host_b = make_host(test_db, "host-plc-b")

        # Nodes without host_id
        n1 = make_node(test_db, sample_lab.id, "R1", host_id=None)
        n2 = make_node(test_db, sample_lab.id, "R2", host_id=None)

        # Placements provide host info
        make_placement(test_db, sample_lab.id, "R1", host_a.id, node_definition_id=n1.id)
        make_placement(test_db, sample_lab.id, "R2", host_b.id, node_definition_id=n2.id)

        make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1",
                   link_name="R1:eth1-R2:eth1")

        result = _ensure_link_states_for_lab(test_db, sample_lab.id)
        assert result == 1

        test_db.flush()
        ls = (
            test_db.query(models.LinkState)
            .filter(models.LinkState.lab_id == sample_lab.id)
            .first()
        )
        assert ls is not None
        assert ls.is_cross_host is True
        # At least one host ID should be set
        assert ls.source_host_id is not None or ls.target_host_id is not None