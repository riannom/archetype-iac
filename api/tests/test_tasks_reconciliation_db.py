"""Comprehensive tests for app/tasks/reconciliation_db.py.

Covers:
- _ensure_link_states_for_lab: link state creation, dedup, cross-host detection
- _backfill_placement_node_ids: legacy backfill stub behaviour
- cleanup_orphaned_node_states: orphan identification & safe-state filtering
- _reconcile_single_lab / _do_reconcile_lab: per-lab reconciliation loop
- Multi-agent scenarios
- Error handling paths
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models
from app.state import (
    LinkActualState,
    NodeActualState,
)


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
        "app.tasks.reconciliation.acquire_link_ops_lock",
        return_value=False,
    ):
        with patch(
            "app.tasks.reconciliation_db.agent_client.cleanup_lab_orphans",
            new_callable=AsyncMock,
        ):
            with patch(
                "app.tasks.reconciliation_db.agent_client.destroy_container_on_agent",
                new_callable=AsyncMock,
            ):
                with patch(
                    "app.tasks.reconciliation_db.agent_client.repair_endpoints_on_agent",
                    new_callable=AsyncMock,
                ):
                    with patch(
                        "app.tasks.reconciliation_db.agent_client.check_node_readiness",
                        new_callable=AsyncMock,
                    ) as mock_ready:
                        mock_ready.return_value = {"is_ready": False}
                        yield


@pytest.fixture(autouse=True)
def _disable_reconcile_redis():
    """Avoid real Redis calls during reconciliation tests."""
    fake_redis = MagicMock()
    fake_redis.set.return_value = True
    fake_redis.delete.return_value = 1
    # Lua script for lock release
    fake_redis.eval.return_value = 1
    with patch("app.tasks.reconciliation.get_redis", return_value=fake_redis):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(test_db: Session, lab_id: str, name: str, device: str = "linux",
               host_id: str | None = None) -> models.Node:
    """Create and flush a Node definition."""
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
    test_db.flush()
    return node


def _make_link(test_db: Session, lab_id: str, src_node: models.Node,
               src_iface: str, tgt_node: models.Node,
               tgt_iface: str) -> models.Link:
    """Create and flush a Link definition."""
    link = models.Link(
        lab_id=lab_id,
        link_name=f"{src_node.container_name}:{src_iface}-{tgt_node.container_name}:{tgt_iface}",
        source_node_id=src_node.id,
        source_interface=src_iface,
        target_node_id=tgt_node.id,
        target_interface=tgt_iface,
    )
    test_db.add(link)
    test_db.flush()
    return link


def _make_node_state(test_db: Session, lab_id: str, node_def: models.Node | None,
                     node_id: str = "gui-1", node_name: str = "R1",
                     desired: str = "stopped", actual: str = "undeployed",
                     **kwargs) -> models.NodeState:
    """Create and flush a NodeState record."""
    ns = models.NodeState(
        lab_id=lab_id,
        node_id=node_id,
        node_name=node_name,
        node_definition_id=node_def.id if node_def else None,
        desired_state=desired,
        actual_state=actual,
        **kwargs,
    )
    test_db.add(ns)
    test_db.flush()
    return ns


def _make_link_state(test_db: Session, lab_id: str,
                     source_node: str, source_iface: str,
                     target_node: str, target_iface: str,
                     desired: str = "up", actual: str = "unknown",
                     **kwargs) -> models.LinkState:
    """Create and flush a LinkState record."""
    ls = models.LinkState(
        lab_id=lab_id,
        link_name=f"{source_node}:{source_iface}-{target_node}:{target_iface}",
        source_node=source_node,
        source_interface=source_iface,
        target_node=target_node,
        target_interface=target_iface,
        desired_state=desired,
        actual_state=actual,
        **kwargs,
    )
    test_db.add(ls)
    test_db.flush()
    return ls


def _make_placement(test_db: Session, lab_id: str, node_def: models.Node,
                    host_id: str, status: str = "deployed") -> models.NodePlacement:
    """Create and flush a NodePlacement record."""
    p = models.NodePlacement(
        lab_id=lab_id,
        node_name=node_def.container_name,
        node_definition_id=node_def.id,
        host_id=host_id,
        status=status,
    )
    test_db.add(p)
    test_db.flush()
    return p


def _make_host(test_db: Session, host_id: str, name: str = "Agent",
               status: str = "online") -> models.Host:
    """Create and flush a Host record."""
    import json
    host = models.Host(
        id=host_id,
        name=name,
        address=f"{host_id}.local:8080",
        status=status,
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
        last_heartbeat=datetime.now(timezone.utc),
        resource_usage=json.dumps({}),
    )
    test_db.add(host)
    test_db.flush()
    return host


# ===================================================================
#  _ensure_link_states_for_lab
# ===================================================================

class TestEnsureLinkStatesForLab:

    def test_creates_link_state_from_definition(self, test_db, sample_lab):
        """New LinkState created when Link definition has no matching state."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        n1 = _make_node(test_db, sample_lab.id, "R1")
        n2 = _make_node(test_db, sample_lab.id, "R2")
        _make_link(test_db, sample_lab.id, n1, "eth1", n2, "eth1")
        test_db.commit()

        created = _ensure_link_states_for_lab(test_db, sample_lab.id)
        assert created == 1

        test_db.flush()
        states = test_db.query(models.LinkState).filter(
            models.LinkState.lab_id == sample_lab.id
        ).all()
        assert len(states) == 1
        assert states[0].desired_state == "up"
        assert states[0].actual_state == "unknown"

    def test_returns_zero_for_lab_without_links(self, test_db, sample_lab):
        """Labs with no Link definitions should return 0."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        assert _ensure_link_states_for_lab(test_db, sample_lab.id) == 0

    def test_skips_when_state_already_exists(self, test_db, sample_lab):
        """Existing LinkState with matching canonical key should not be duplicated."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        n1 = _make_node(test_db, sample_lab.id, "R1")
        n2 = _make_node(test_db, sample_lab.id, "R2")
        _make_link(test_db, sample_lab.id, n1, "eth1", n2, "eth1")
        # Pre-existing state
        _make_link_state(test_db, sample_lab.id, "R1", "eth1", "R2", "eth1")
        test_db.commit()

        created = _ensure_link_states_for_lab(test_db, sample_lab.id)
        assert created == 0

    def test_creates_multiple_link_states(self, test_db, sample_lab):
        """Multiple missing link states should all be created."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        n1 = _make_node(test_db, sample_lab.id, "R1")
        n2 = _make_node(test_db, sample_lab.id, "R2")
        n3 = _make_node(test_db, sample_lab.id, "R3")
        _make_link(test_db, sample_lab.id, n1, "eth1", n2, "eth1")
        _make_link(test_db, sample_lab.id, n2, "eth2", n3, "eth1")
        test_db.commit()

        created = _ensure_link_states_for_lab(test_db, sample_lab.id)
        assert created == 2

    def test_dedup_removes_duplicates(self, test_db, sample_lab):
        """Duplicate LinkStates with same canonical key should be consolidated."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        n1 = _make_node(test_db, sample_lab.id, "R1")
        n2 = _make_node(test_db, sample_lab.id, "R2")
        _make_link(test_db, sample_lab.id, n1, "eth1", n2, "eth1")

        # Create two link states with same canonical key but different link_names
        # (one in forward order, one reversed — both canonicalize the same)
        _make_link_state(test_db, sample_lab.id, "R1", "eth1", "R2", "eth1")
        _make_link_state(test_db, sample_lab.id, "R2", "eth1", "R1", "eth1")
        test_db.commit()

        created = _ensure_link_states_for_lab(test_db, sample_lab.id)
        # One duplicate should be removed; no new states needed
        assert created == 0

        states = test_db.query(models.LinkState).filter(
            models.LinkState.lab_id == sample_lab.id
        ).all()
        assert len(states) == 1

    def test_skips_link_with_missing_node(self, test_db, sample_lab):
        """Link whose source/target Node cannot be resolved should be skipped."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        n1 = _make_node(test_db, sample_lab.id, "R1")
        # Create link referencing a missing node
        link = models.Link(
            lab_id=sample_lab.id,
            link_name="R1:eth1-MISSING:eth1",
            source_node_id=n1.id,
            source_interface="eth1",
            target_node_id="nonexistent-id",
            target_interface="eth1",
        )
        test_db.add(link)
        test_db.commit()

        created = _ensure_link_states_for_lab(test_db, sample_lab.id)
        assert created == 0

    def test_cross_host_detection(self, test_db, sample_lab):
        """Link between nodes on different hosts should be marked is_cross_host."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        h1 = _make_host(test_db, "host-a", "Agent A")
        h2 = _make_host(test_db, "host-b", "Agent B")

        n1 = _make_node(test_db, sample_lab.id, "R1", host_id=h1.id)
        n2 = _make_node(test_db, sample_lab.id, "R2", host_id=h2.id)
        _make_link(test_db, sample_lab.id, n1, "eth1", n2, "eth1")
        test_db.commit()

        created = _ensure_link_states_for_lab(test_db, sample_lab.id)
        assert created == 1

        test_db.flush()
        ls = test_db.query(models.LinkState).filter(
            models.LinkState.lab_id == sample_lab.id
        ).first()
        assert ls is not None
        assert ls.is_cross_host is True

    def test_same_host_not_cross_host(self, test_db, sample_lab):
        """Link between nodes on the same host should have is_cross_host=False."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        h1 = _make_host(test_db, "host-same", "Agent Same")

        n1 = _make_node(test_db, sample_lab.id, "R1", host_id=h1.id)
        n2 = _make_node(test_db, sample_lab.id, "R2", host_id=h1.id)
        _make_link(test_db, sample_lab.id, n1, "eth1", n2, "eth1")
        test_db.commit()

        _ensure_link_states_for_lab(test_db, sample_lab.id)
        test_db.flush()
        ls = test_db.query(models.LinkState).filter(
            models.LinkState.lab_id == sample_lab.id
        ).first()
        assert ls is not None
        assert ls.is_cross_host is False

    def test_resolves_host_via_placement(self, test_db, sample_lab):
        """Host ID resolved from NodePlacement when Node.host_id is None."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        h1 = _make_host(test_db, "host-p1", "Agent P1")
        h2 = _make_host(test_db, "host-p2", "Agent P2")

        n1 = _make_node(test_db, sample_lab.id, "R1")  # no host_id on node
        n2 = _make_node(test_db, sample_lab.id, "R2")
        _make_placement(test_db, sample_lab.id, n1, h1.id)
        _make_placement(test_db, sample_lab.id, n2, h2.id)
        _make_link(test_db, sample_lab.id, n1, "eth1", n2, "eth1")
        test_db.commit()

        _ensure_link_states_for_lab(test_db, sample_lab.id)
        test_db.flush()
        ls = test_db.query(models.LinkState).filter(
            models.LinkState.lab_id == sample_lab.id
        ).first()
        assert ls is not None
        assert ls.is_cross_host is True

    def test_sets_link_definition_id(self, test_db, sample_lab):
        """Newly created LinkState should have link_definition_id set."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        n1 = _make_node(test_db, sample_lab.id, "R1")
        n2 = _make_node(test_db, sample_lab.id, "R2")
        link = _make_link(test_db, sample_lab.id, n1, "eth1", n2, "eth1")
        test_db.commit()

        _ensure_link_states_for_lab(test_db, sample_lab.id)
        test_db.flush()
        ls = test_db.query(models.LinkState).filter(
            models.LinkState.lab_id == sample_lab.id
        ).first()
        assert ls is not None
        assert ls.link_definition_id == link.id


# ===================================================================
#  _backfill_placement_node_ids
# ===================================================================

class TestBackfillPlacementNodeIds:

    def test_returns_zero_with_no_placements(self, test_db, sample_lab):
        """No placements in lab should return 0."""
        from app.tasks.reconciliation_db import _backfill_placement_node_ids

        result = _backfill_placement_node_ids(test_db, sample_lab.id)
        assert result == 0

    def test_returns_zero_for_nonexistent_lab(self, test_db):
        """Nonexistent lab ID should return 0."""
        from app.tasks.reconciliation_db import _backfill_placement_node_ids

        result = _backfill_placement_node_ids(test_db, "nonexistent-lab")
        assert result == 0

    def test_logs_warning_for_missing_node_definition_id(self, test_db, sample_lab):
        """Placements with NULL node_definition_id trigger a warning but no backfill."""
        from app.tasks.reconciliation_db import _backfill_placement_node_ids

        _make_node(test_db, sample_lab.id, "R1")
        h1 = _make_host(test_db, "host-bf1", "Agent BF1")

        p = models.NodePlacement(
            lab_id=sample_lab.id,
            node_name="R1",
            node_definition_id=None,  # Missing FK
            host_id=h1.id,
        )
        test_db.add(p)
        test_db.commit()

        result = _backfill_placement_node_ids(test_db, sample_lab.id)
        assert result == 0
        # Confirm the placement was NOT modified
        test_db.refresh(p)
        assert p.node_definition_id is None

    def test_already_populated_returns_zero(self, test_db, sample_lab):
        """Placements with node_definition_id set should not count as missing."""
        from app.tasks.reconciliation_db import _backfill_placement_node_ids

        n1 = _make_node(test_db, sample_lab.id, "R1")
        h1 = _make_host(test_db, "host-bf2", "Agent BF2")
        _make_placement(test_db, sample_lab.id, n1, h1.id)
        test_db.commit()

        result = _backfill_placement_node_ids(test_db, sample_lab.id)
        assert result == 0


# ===================================================================
#  cleanup_orphaned_node_states
# ===================================================================

class TestCleanupOrphanedNodeStates:

    def test_deletes_undeployed_orphan(self, test_db, sample_lab):
        """Orphaned NodeState with actual_state=undeployed should be deleted."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        _make_node_state(test_db, sample_lab.id, None, actual="undeployed")
        test_db.commit()

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 1

    def test_deletes_stopped_orphan(self, test_db, sample_lab):
        """Orphaned NodeState with actual_state=stopped should be deleted."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        _make_node_state(test_db, sample_lab.id, None, actual="stopped")
        test_db.commit()

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 1

    def test_deletes_error_orphan(self, test_db, sample_lab):
        """Orphaned NodeState with actual_state=error should be deleted."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        _make_node_state(test_db, sample_lab.id, None, actual="error")
        test_db.commit()

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 1

    def test_preserves_running_orphan(self, test_db, sample_lab):
        """Orphaned NodeState with actual_state=running should NOT be deleted."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        _make_node_state(
            test_db, sample_lab.id, None,
            actual="running", desired="running",
        )
        test_db.commit()

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 0

    def test_preserves_starting_orphan(self, test_db, sample_lab):
        """Orphaned NodeState in transitional 'starting' state should be preserved."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        _make_node_state(test_db, sample_lab.id, None, actual="starting")
        test_db.commit()

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 0

    def test_preserves_non_orphan(self, test_db, sample_lab):
        """NodeState with valid node_definition_id should not be deleted."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        n1 = _make_node(test_db, sample_lab.id, "R1")
        _make_node_state(
            test_db, sample_lab.id, n1,
            node_name="R1", actual="undeployed",
        )
        test_db.commit()

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 0

    def test_deletes_multiple_orphans(self, test_db, sample_lab):
        """Multiple orphaned NodeStates in safe states should all be deleted."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        for i, state in enumerate(["undeployed", "stopped", "error"]):
            _make_node_state(
                test_db, sample_lab.id, None,
                node_id=f"gui-{i}", node_name=f"node-{i}", actual=state,
            )
        test_db.commit()

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 3

    def test_no_orphans_returns_zero(self, test_db, sample_lab):
        """Empty lab should return 0."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 0

    def test_mixed_orphans_and_valid(self, test_db, sample_lab):
        """Only orphaned states should be deleted; valid ones preserved."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        n1 = _make_node(test_db, sample_lab.id, "R1")
        # Valid state
        _make_node_state(
            test_db, sample_lab.id, n1,
            node_id="valid-gui", node_name="R1", actual="stopped",
        )
        # Orphan (safe state)
        _make_node_state(
            test_db, sample_lab.id, None,
            node_id="orphan-gui", node_name="old-node", actual="undeployed",
        )
        # Orphan (unsafe state - running)
        _make_node_state(
            test_db, sample_lab.id, None,
            node_id="orphan-running", node_name="active-orphan", actual="running",
        )
        test_db.commit()

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 1  # Only the undeployed orphan


# ===================================================================
#  _reconcile_single_lab
# ===================================================================

class TestReconcileSingleLab:

    @pytest.mark.asyncio
    async def test_returns_zero_for_missing_lab(self, test_db):
        """Non-existent lab should return 0."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        result = await _reconcile_single_lab(test_db, "nonexistent-lab")
        assert result == 0

    @pytest.mark.asyncio
    async def test_skips_lab_with_active_job(self, test_db, sample_lab, test_user):
        """Lab with active deploy job within timeout should be skipped."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        sample_lab.state = "running"
        h = _make_host(test_db, "host-sj", "Agent SJ")
        job = models.Job(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            agent_id=h.id,
            started_at=datetime.now(timezone.utc),
        )
        test_db.add(job)
        test_db.commit()

        result = await _reconcile_single_lab(test_db, sample_lab.id)
        assert result == 0

    @pytest.mark.asyncio
    async def test_proceeds_with_stuck_job(self, test_db, sample_lab, test_user):
        """Lab with stuck (timed-out) job should proceed with reconciliation."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        sample_lab.state = "running"
        h = _make_host(test_db, "host-stuck", "Agent Stuck")
        sample_lab.agent_id = h.id

        # Create a job that started a long time ago (stuck)
        job = models.Job(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            user_id=test_user.id,
            action="up",
            status="running",
            agent_id=h.id,
            started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        test_db.add(job)

        n1 = _make_node(test_db, sample_lab.id, "R1")
        _make_node_state(
            test_db, sample_lab.id, n1,
            node_name="R1", desired="running", actual="running",
        )
        test_db.commit()

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {"nodes": [{"name": "R1", "status": "running"}]}
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                return_value=True,
            ):
                await _reconcile_single_lab(test_db, sample_lab.id)
                # Should have proceeded (not skipped)
                mock_status.assert_called()


# ===================================================================
#  _do_reconcile_lab — node state transitions
# ===================================================================

class TestDoReconcileLabNodeStates:

    @pytest.mark.asyncio
    async def test_running_container_updates_state(self, test_db, sample_lab, sample_host):
        """Node reported as running by agent should be updated to running."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        sample_lab.state = "running"
        sample_lab.agent_id = sample_host.id

        n1 = _make_node(test_db, sample_lab.id, "R1")
        _make_node_state(
            test_db, sample_lab.id, n1,
            node_name="R1", desired="running", actual="undeployed",
        )
        _make_placement(test_db, sample_lab.id, n1, sample_host.id)
        test_db.commit()

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {"nodes": [{"name": "R1", "status": "running"}]}
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                return_value=True,
            ):
                changes = await _reconcile_single_lab(test_db, sample_lab.id)

        assert changes >= 1
        test_db.refresh(
            test_db.query(models.NodeState).filter(
                models.NodeState.lab_id == sample_lab.id,
            ).first()
        )
        ns = test_db.query(models.NodeState).filter(
            models.NodeState.lab_id == sample_lab.id,
        ).first()
        assert ns.actual_state == NodeActualState.RUNNING.value

    @pytest.mark.asyncio
    async def test_stopped_container_updates_state(self, test_db, sample_lab, sample_host):
        """Node reported as stopped by agent should be updated to stopped."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        sample_lab.state = "running"
        sample_lab.agent_id = sample_host.id

        n1 = _make_node(test_db, sample_lab.id, "R1")
        ns = _make_node_state(
            test_db, sample_lab.id, n1,
            node_name="R1", desired="stopped", actual="running",
        )
        _make_placement(test_db, sample_lab.id, n1, sample_host.id)
        test_db.commit()

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {"nodes": [{"name": "R1", "status": "stopped"}]}
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                return_value=True,
            ):
                await _reconcile_single_lab(test_db, sample_lab.id)

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPED.value

    @pytest.mark.asyncio
    async def test_error_container_updates_state(self, test_db, sample_lab, sample_host):
        """Node reported as dead/error by agent should be updated to error."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        sample_lab.state = "running"
        sample_lab.agent_id = sample_host.id

        n1 = _make_node(test_db, sample_lab.id, "R1")
        ns = _make_node_state(
            test_db, sample_lab.id, n1,
            node_name="R1", desired="running", actual="running",
        )
        _make_placement(test_db, sample_lab.id, n1, sample_host.id)
        test_db.commit()

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {"nodes": [{"name": "R1", "status": "dead"}]}
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                return_value=True,
            ):
                await _reconcile_single_lab(test_db, sample_lab.id)

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.ERROR.value

    @pytest.mark.asyncio
    async def test_missing_container_marks_undeployed(self, test_db, sample_lab, sample_host):
        """Node not found by agent should be marked undeployed."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        sample_lab.state = "running"
        sample_lab.agent_id = sample_host.id

        n1 = _make_node(test_db, sample_lab.id, "R1")
        ns = _make_node_state(
            test_db, sample_lab.id, n1,
            node_name="R1", desired="running", actual="running",
        )
        _make_placement(test_db, sample_lab.id, n1, sample_host.id)
        test_db.commit()

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {"nodes": []}  # Container gone
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                return_value=True,
            ):
                await _reconcile_single_lab(test_db, sample_lab.id)

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.UNDEPLOYED.value

    @pytest.mark.asyncio
    async def test_skips_enforcement_failed_node(self, test_db, sample_lab, sample_host):
        """Node with enforcement_failed_at set should be skipped."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        sample_lab.state = "running"
        sample_lab.agent_id = sample_host.id

        n1 = _make_node(test_db, sample_lab.id, "R1")
        ns = _make_node_state(
            test_db, sample_lab.id, n1,
            node_name="R1", desired="running", actual="error",
            enforcement_failed_at=datetime.now(timezone.utc),
        )
        _make_placement(test_db, sample_lab.id, n1, sample_host.id)
        test_db.commit()

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {"nodes": [{"name": "R1", "status": "running"}]}
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                return_value=True,
            ):
                await _reconcile_single_lab(test_db, sample_lab.id)

        test_db.refresh(ns)
        # State should NOT have changed
        assert ns.actual_state == "error"

    @pytest.mark.asyncio
    async def test_skips_image_syncing_node(self, test_db, sample_lab, sample_host):
        """Node with active image sync should be skipped."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        sample_lab.state = "running"
        sample_lab.agent_id = sample_host.id

        n1 = _make_node(test_db, sample_lab.id, "R1")
        ns = _make_node_state(
            test_db, sample_lab.id, n1,
            node_name="R1", desired="running", actual="undeployed",
            image_sync_status="syncing",
        )
        _make_placement(test_db, sample_lab.id, n1, sample_host.id)
        test_db.commit()

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {"nodes": []}
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                return_value=True,
            ):
                await _reconcile_single_lab(test_db, sample_lab.id)

        test_db.refresh(ns)
        assert ns.actual_state == "undeployed"  # Not changed


# ===================================================================
#  Multi-agent scenarios
# ===================================================================

class TestMultiAgentReconciliation:

    @pytest.mark.asyncio
    async def test_queries_all_agents(self, test_db, sample_lab):
        """Reconciliation should query all agents that have placements for a lab."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        h1 = _make_host(test_db, "agent-ma1", "Agent MA1")
        h2 = _make_host(test_db, "agent-ma2", "Agent MA2")
        sample_lab.state = "running"

        n1 = _make_node(test_db, sample_lab.id, "R1", host_id=h1.id)
        n2 = _make_node(test_db, sample_lab.id, "R2", host_id=h2.id)
        _make_node_state(
            test_db, sample_lab.id, n1,
            node_id="g1", node_name="R1", desired="running", actual="running",
        )
        _make_node_state(
            test_db, sample_lab.id, n2,
            node_id="g2", node_name="R2", desired="running", actual="running",
        )
        _make_placement(test_db, sample_lab.id, n1, h1.id)
        _make_placement(test_db, sample_lab.id, n2, h2.id)
        test_db.commit()

        agent_ids_queried = []

        async def _mock_get_status(agent, lab_id):
            agent_ids_queried.append(agent.id)
            if agent.id == "agent-ma1":
                return {"nodes": [{"name": "R1", "status": "running"}]}
            return {"nodes": [{"name": "R2", "status": "running"}]}

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            side_effect=_mock_get_status,
        ):
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                return_value=True,
            ):
                await _reconcile_single_lab(test_db, sample_lab.id)

        assert "agent-ma1" in agent_ids_queried
        assert "agent-ma2" in agent_ids_queried

    @pytest.mark.asyncio
    async def test_handles_one_agent_failure(self, test_db, sample_lab):
        """When one agent fails, nodes on successful agent should still be updated."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        h1 = _make_host(test_db, "agent-ok", "Agent OK")
        h2 = _make_host(test_db, "agent-fail", "Agent Fail")
        sample_lab.state = "running"

        n1 = _make_node(test_db, sample_lab.id, "R1", host_id=h1.id)
        n2 = _make_node(test_db, sample_lab.id, "R2", host_id=h2.id)
        ns1 = _make_node_state(
            test_db, sample_lab.id, n1,
            node_id="g1", node_name="R1", desired="running", actual="undeployed",
        )
        ns2 = _make_node_state(
            test_db, sample_lab.id, n2,
            node_id="g2", node_name="R2", desired="running", actual="undeployed",
        )
        _make_placement(test_db, sample_lab.id, n1, h1.id)
        _make_placement(test_db, sample_lab.id, n2, h2.id)
        test_db.commit()

        async def _mock_get_status(agent, lab_id):
            if agent.id == "agent-fail":
                raise ConnectionError("agent unreachable")
            return {"nodes": [{"name": "R1", "status": "running"}]}

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            side_effect=_mock_get_status,
        ):
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                return_value=True,
            ):
                await _reconcile_single_lab(test_db, sample_lab.id)

        test_db.refresh(ns1)
        test_db.refresh(ns2)
        # R1's agent responded — state updated
        assert ns1.actual_state == NodeActualState.RUNNING.value
        # R2's agent failed — state preserved
        assert ns2.actual_state == "undeployed"

    @pytest.mark.asyncio
    async def test_skips_offline_agents(self, test_db, sample_lab):
        """Offline agents should be skipped without error."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        h1 = _make_host(test_db, "agent-online", "Agent Online")
        h2 = _make_host(test_db, "agent-offline", "Agent Offline", status="offline")
        sample_lab.state = "running"

        n1 = _make_node(test_db, sample_lab.id, "R1", host_id=h1.id)
        n2 = _make_node(test_db, sample_lab.id, "R2", host_id=h2.id)
        _make_node_state(
            test_db, sample_lab.id, n1,
            node_id="g1", node_name="R1", desired="running", actual="undeployed",
        )
        _make_node_state(
            test_db, sample_lab.id, n2,
            node_id="g2", node_name="R2", desired="running", actual="undeployed",
        )
        _make_placement(test_db, sample_lab.id, n1, h1.id)
        _make_placement(test_db, sample_lab.id, n2, h2.id)
        test_db.commit()

        def _is_online(agent):
            return agent.id == "agent-online"

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {"nodes": [{"name": "R1", "status": "running"}]}
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                side_effect=_is_online,
            ):
                await _reconcile_single_lab(test_db, sample_lab.id)

        # Should only have queried the online agent
        mock_status.assert_called_once()


# ===================================================================
#  Link state reconciliation
# ===================================================================

class TestReconcileLabLinkStates:

    @pytest.mark.asyncio
    async def test_both_running_link_pending(self, test_db, sample_lab, sample_host):
        """Same-host link with both nodes running should go to PENDING (not speculatively UP)."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        sample_lab.state = "running"
        sample_lab.agent_id = sample_host.id

        n1 = _make_node(test_db, sample_lab.id, "R1")
        n2 = _make_node(test_db, sample_lab.id, "R2")
        _make_node_state(
            test_db, sample_lab.id, n1,
            node_id="g1", node_name="R1", desired="running", actual="running",
        )
        _make_node_state(
            test_db, sample_lab.id, n2,
            node_id="g2", node_name="R2", desired="running", actual="running",
        )
        _make_placement(test_db, sample_lab.id, n1, sample_host.id)
        _make_placement(test_db, sample_lab.id, n2, sample_host.id)

        ls = _make_link_state(
            test_db, sample_lab.id,
            "R1", "eth1", "R2", "eth1",
            desired="up", actual="unknown",
        )
        test_db.commit()

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {"nodes": [
                {"name": "R1", "status": "running"},
                {"name": "R2", "status": "running"},
            ]}
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                return_value=True,
            ):
                await _reconcile_single_lab(test_db, sample_lab.id)

        test_db.refresh(ls)
        assert ls.actual_state == LinkActualState.PENDING.value

    @pytest.mark.asyncio
    async def test_one_node_stopped_link_down(self, test_db, sample_lab, sample_host):
        """Link with one node stopped should be marked DOWN."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        sample_lab.state = "running"
        sample_lab.agent_id = sample_host.id

        n1 = _make_node(test_db, sample_lab.id, "R1")
        n2 = _make_node(test_db, sample_lab.id, "R2")
        _make_node_state(
            test_db, sample_lab.id, n1,
            node_id="g1", node_name="R1", desired="running", actual="running",
        )
        _make_node_state(
            test_db, sample_lab.id, n2,
            node_id="g2", node_name="R2", desired="stopped", actual="stopped",
        )
        _make_placement(test_db, sample_lab.id, n1, sample_host.id)
        _make_placement(test_db, sample_lab.id, n2, sample_host.id)

        ls = _make_link_state(
            test_db, sample_lab.id,
            "R1", "eth1", "R2", "eth1",
            desired="up", actual="up",
        )
        test_db.commit()

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {"nodes": [
                {"name": "R1", "status": "running"},
                {"name": "R2", "status": "stopped"},
            ]}
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                return_value=True,
            ):
                await _reconcile_single_lab(test_db, sample_lab.id)

        test_db.refresh(ls)
        assert ls.actual_state == LinkActualState.DOWN.value

    @pytest.mark.asyncio
    async def test_one_node_error_link_error(self, test_db, sample_lab, sample_host):
        """Link with one node in error should be marked ERROR."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        sample_lab.state = "running"
        sample_lab.agent_id = sample_host.id

        n1 = _make_node(test_db, sample_lab.id, "R1")
        n2 = _make_node(test_db, sample_lab.id, "R2")
        _make_node_state(
            test_db, sample_lab.id, n1,
            node_id="g1", node_name="R1", desired="running", actual="running",
        )
        _make_node_state(
            test_db, sample_lab.id, n2,
            node_id="g2", node_name="R2", desired="running", actual="error",
        )
        _make_placement(test_db, sample_lab.id, n1, sample_host.id)
        _make_placement(test_db, sample_lab.id, n2, sample_host.id)

        ls = _make_link_state(
            test_db, sample_lab.id,
            "R1", "eth1", "R2", "eth1",
            desired="up", actual="up",
        )
        test_db.commit()

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {"nodes": [
                {"name": "R1", "status": "running"},
                {"name": "R2", "status": "error"},
            ]}
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                return_value=True,
            ):
                await _reconcile_single_lab(test_db, sample_lab.id)

        test_db.refresh(ls)
        assert ls.actual_state == LinkActualState.ERROR.value


# ===================================================================
#  Error handling
# ===================================================================

class TestReconcileErrorHandling:

    @pytest.mark.asyncio
    async def test_agent_query_exception_handled(self, test_db, sample_lab, sample_host):
        """Exception during agent query should be caught and logged."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        sample_lab.state = "running"
        sample_lab.agent_id = sample_host.id

        n1 = _make_node(test_db, sample_lab.id, "R1")
        _make_node_state(
            test_db, sample_lab.id, n1,
            node_name="R1", desired="running", actual="running",
        )
        _make_placement(test_db, sample_lab.id, n1, sample_host.id)
        test_db.commit()

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
            side_effect=Exception("connection refused"),
        ):
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                return_value=True,
            ):
                # Should not raise
                await _reconcile_single_lab(test_db, sample_lab.id)
                # Changes count doesn't matter — important that it didn't crash

    @pytest.mark.asyncio
    async def test_no_agents_available(self, test_db, sample_lab):
        """Lab with no reachable agents should return 0."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        sample_lab.state = "running"
        test_db.commit()

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_agent_for_lab",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await _reconcile_single_lab(test_db, sample_lab.id)
            assert result == 0


# ===================================================================
#  _maybe_cleanup_labless_containers
# ===================================================================

class TestMaybeCleanupLablessContainers:

    @pytest.mark.asyncio
    async def test_counter_increments(self, monkeypatch):
        """Counter should increment on each call."""
        import app.tasks.reconciliation_db as recon_db

        monkeypatch.setattr(recon_db, "_lab_orphan_check_counter", 0)
        monkeypatch.setattr(recon_db, "_LAB_ORPHAN_CHECK_INTERVAL", 10)

        mock_session = MagicMock()
        await recon_db._maybe_cleanup_labless_containers(mock_session)
        assert recon_db._lab_orphan_check_counter == 1

    @pytest.mark.asyncio
    async def test_skips_before_interval(self, monkeypatch):
        """Cleanup should not run before the interval is reached."""
        import app.tasks.reconciliation_db as recon_db

        monkeypatch.setattr(recon_db, "_lab_orphan_check_counter", 0)
        monkeypatch.setattr(recon_db, "_LAB_ORPHAN_CHECK_INTERVAL", 5)

        mock_session = MagicMock()
        # Call 4 times — should not trigger cleanup
        for _ in range(4):
            await recon_db._maybe_cleanup_labless_containers(mock_session)

        assert recon_db._lab_orphan_check_counter == 4
        # Session.query should not have been called (cleanup not reached)
        mock_session.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_runs_at_interval(self, monkeypatch):
        """Cleanup should run when counter reaches the interval threshold."""
        import app.tasks.reconciliation_db as recon_db

        monkeypatch.setattr(recon_db, "_lab_orphan_check_counter", 4)
        monkeypatch.setattr(recon_db, "_LAB_ORPHAN_CHECK_INTERVAL", 5)

        mock_session = MagicMock()
        # Mock get_valid_lab_ids to avoid import chain issues
        with patch(
            "app.tasks.cleanup_base.get_valid_lab_ids",
            return_value=["lab-1"],
        ):
            mock_session.query.return_value.all.return_value = []

            with patch(
                "app.tasks.reconciliation_db.agent_client.cleanup_orphans_on_agent",
                new_callable=AsyncMock,
            ):
                with patch(
                    "app.tasks.reconciliation_db.agent_client.is_agent_online",
                    return_value=False,
                ):
                    await recon_db._maybe_cleanup_labless_containers(mock_session)

        # Counter should have been reset to 0
        assert recon_db._lab_orphan_check_counter == 0


# ===================================================================
#  Transitional state handling
# ===================================================================

class TestTransitionalStateHandling:

    @pytest.mark.asyncio
    async def test_skips_active_stopping_operation(self, test_db, sample_lab, sample_host):
        """Node with recent stopping_started_at should be skipped."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        sample_lab.state = "running"
        sample_lab.agent_id = sample_host.id

        now = datetime.now(timezone.utc)
        n1 = _make_node(test_db, sample_lab.id, "R1")
        ns = _make_node_state(
            test_db, sample_lab.id, n1,
            node_name="R1", desired="stopped", actual="running",
            stopping_started_at=now,
        )
        _make_placement(test_db, sample_lab.id, n1, sample_host.id)
        test_db.commit()

        # Patch utcnow to return a naive datetime (SQLite strips tzinfo)
        # so the subtraction `utcnow() - ns.stopping_started_at` works.
        mock_now = now.replace(tzinfo=None) + timedelta(seconds=5)

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {"nodes": [{"name": "R1", "status": "running"}]}
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                return_value=True,
            ):
                with patch("app.tasks.reconciliation_db.utcnow", return_value=mock_now):
                    await _reconcile_single_lab(test_db, sample_lab.id)

        test_db.refresh(ns)
        # Should still be running — stopping operation is active
        assert ns.actual_state == "running"

    @pytest.mark.asyncio
    async def test_recovers_stuck_stopping(self, test_db, sample_lab, sample_host):
        """Node stuck in stopping for too long should be recovered."""
        from app.tasks.reconciliation_db import _reconcile_single_lab

        sample_lab.state = "running"
        sample_lab.agent_id = sample_host.id

        stopping_time = datetime.now(timezone.utc) - timedelta(minutes=30)
        n1 = _make_node(test_db, sample_lab.id, "R1")
        ns = _make_node_state(
            test_db, sample_lab.id, n1,
            node_name="R1", desired="stopped", actual="running",
            stopping_started_at=stopping_time,
        )
        _make_placement(test_db, sample_lab.id, n1, sample_host.id)
        test_db.commit()

        # Patch utcnow to return a naive datetime (SQLite strips tzinfo)
        mock_now = datetime.now(timezone.utc).replace(tzinfo=None)

        with patch(
            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
            new_callable=AsyncMock,
        ) as mock_status:
            mock_status.return_value = {"nodes": [{"name": "R1", "status": "stopped"}]}
            with patch(
                "app.tasks.reconciliation_db.agent_client.is_agent_online",
                return_value=True,
            ):
                with patch("app.tasks.reconciliation_db.utcnow", return_value=mock_now):
                    await _reconcile_single_lab(test_db, sample_lab.id)

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPED.value
        assert ns.stopping_started_at is None  # Cleared
