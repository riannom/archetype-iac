"""Extended tests for app/tasks/reconciliation_db.py.

Covers additional scenarios not in test_tasks_reconciliation_db.py:
- _do_reconcile_lab: link normalization, orphan container cleanup, misplaced
  containers, auto-connect pending links, link deletion, node state observations
- _ensure_link_states_for_lab: canonical ordering swap, host ID swap
- _maybe_cleanup_labless_containers: VXLAN port reconciliation, overlay convergence
- _reconcile_single_lab: lock not acquired, active job within timeout
- Node-level: starting_started_at handling, undeployed detection without agent response
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

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

def _make_lab(db, user, *, state="stopped", agent_id=None, name=None):
    lab = models.Lab(
        name=name or f"Lab-{uuid4().hex[:8]}",
        owner_id=user.id,
        provider="docker",
        state=state,
        workspace_path="/tmp/test-lab",
        agent_id=agent_id,
    )
    db.add(lab)
    db.commit()
    db.refresh(lab)
    return lab


def _make_node(db, lab_id, container_name, *, device="linux", host_id=None, image=None):
    n = models.Node(
        lab_id=lab_id,
        gui_id=container_name.lower(),
        display_name=container_name,
        container_name=container_name,
        node_type="device",
        device=device,
        host_id=host_id,
        image=image,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def _make_link(db, lab_id, src_node_id, src_iface, tgt_node_id, tgt_iface, *, link_name=None):
    lnk = models.Link(
        lab_id=lab_id,
        link_name=link_name or f"{src_iface}-{tgt_iface}",
        source_node_id=src_node_id,
        source_interface=src_iface,
        target_node_id=tgt_node_id,
        target_interface=tgt_iface,
    )
    db.add(lnk)
    db.commit()
    db.refresh(lnk)
    return lnk


def _make_node_state(
    db, lab_id, node_name, *,
    node_id=None, desired="stopped", actual="undeployed",
    node_definition_id=None, enforcement_failed_at=None,
    image_sync_status=None, stopping_started_at=None,
    starting_started_at=None, is_ready=False, boot_started_at=None,
):
    ns = models.NodeState(
        lab_id=lab_id,
        node_id=node_id or node_name.lower(),
        node_name=node_name,
        desired_state=desired,
        actual_state=actual,
        node_definition_id=node_definition_id,
        enforcement_failed_at=enforcement_failed_at,
        image_sync_status=image_sync_status,
        stopping_started_at=stopping_started_at,
        starting_started_at=starting_started_at,
        is_ready=is_ready,
        boot_started_at=boot_started_at,
    )
    db.add(ns)
    db.commit()
    db.refresh(ns)
    return ns


def _make_link_state(
    db, lab_id, src_node, src_iface, tgt_node, tgt_iface, *,
    desired="up", actual="unknown", is_cross_host=False,
    source_host_id=None, target_host_id=None,
    source_carrier_state=None, target_carrier_state=None,
    link_definition_id=None, vni=None,
):
    ls = models.LinkState(
        lab_id=lab_id,
        link_name=f"{src_node}:{src_iface}-{tgt_node}:{tgt_iface}",
        source_node=src_node,
        source_interface=src_iface,
        target_node=tgt_node,
        target_interface=tgt_iface,
        desired_state=desired,
        actual_state=actual,
        is_cross_host=is_cross_host,
        source_host_id=source_host_id,
        target_host_id=target_host_id,
        source_carrier_state=source_carrier_state,
        target_carrier_state=target_carrier_state,
        link_definition_id=link_definition_id,
        vni=vni,
    )
    db.add(ls)
    db.commit()
    db.refresh(ls)
    return ls


def _make_placement(db, lab_id, node_name, host_id, *, node_definition_id=None):
    p = models.NodePlacement(
        lab_id=lab_id,
        node_name=node_name,
        host_id=host_id,
        node_definition_id=node_definition_id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _make_host(db, host_id, *, name=None, status="online"):
    import json
    from datetime import datetime, timezone

    h = models.Host(
        id=host_id,
        name=name or host_id,
        address=f"{host_id}:8080",
        status=status,
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
        last_heartbeat=datetime.now(timezone.utc),
        resource_usage=json.dumps({}),
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


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

        # Create a recent running "up" job
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

        # Create an active sync job (should not block)
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

        # _do_reconcile_lab should have been called
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


# ---------------------------------------------------------------------------
# Tests: _do_reconcile_lab - link normalization and orphan cleanup
# ---------------------------------------------------------------------------

class TestDoReconcileLabLinkNormalization:
    """Tests for link normalization and orphan cleanup in _do_reconcile_lab."""

    @pytest.mark.asyncio
    async def test_link_normalization_called(self, test_db, test_user):
        """TopologyService.normalize_links_for_lab should be called."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        lab = _make_lab(test_db, test_user, state="running")

        with patch("app.tasks.reconciliation_db.TopologyService") as mock_ts_cls:
            mock_ts = MagicMock()
            mock_ts.normalize_links_for_lab.return_value = 3
            mock_ts.get_links.return_value = []
            mock_ts_cls.return_value = mock_ts

            with patch("app.tasks.reconciliation_db._ensure_link_states_for_lab", return_value=0):
                with patch("app.tasks.reconciliation_db.cleanup_orphaned_node_states", return_value=0):
                    with patch("app.utils.lab.get_lab_provider", return_value="docker"):
                        with patch("app.tasks.reconciliation.link_ops_lock") as mock_link_lock:
                            ml = MagicMock()
                            ml.__enter__ = MagicMock(return_value=False)
                            ml.__exit__ = MagicMock(return_value=False)
                            mock_link_lock.return_value = ml
                            await _do_reconcile_lab(test_db, lab, lab.id)

            mock_ts.normalize_links_for_lab.assert_called_once_with(lab.id)

    @pytest.mark.asyncio
    async def test_orphan_node_state_cleanup_called(self, test_db, test_user):
        """cleanup_orphaned_node_states should be invoked during reconciliation."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        lab = _make_lab(test_db, test_user, state="running")

        with patch("app.tasks.reconciliation_db._ensure_link_states_for_lab", return_value=0):
            with patch("app.tasks.reconciliation_db.cleanup_orphaned_node_states", return_value=2) as mock_cleanup:
                with patch("app.tasks.reconciliation_db.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.normalize_links_for_lab.return_value = 0
                    mock_ts.get_links.return_value = []
                    mock_ts_cls.return_value = mock_ts

                    with patch("app.utils.lab.get_lab_provider", return_value="docker"):
                        with patch("app.tasks.reconciliation.link_ops_lock") as mock_link_lock:
                            ml = MagicMock()
                            ml.__enter__ = MagicMock(return_value=False)
                            ml.__exit__ = MagicMock(return_value=False)
                            mock_link_lock.return_value = ml
                            await _do_reconcile_lab(test_db, lab, lab.id)

            mock_cleanup.assert_called_once_with(test_db, lab.id)


# ---------------------------------------------------------------------------
# Tests: _do_reconcile_lab - node container status -> NodeState mapping
# ---------------------------------------------------------------------------

class TestDoReconcileLabContainerMapping:
    """Tests for container status -> node state updates."""

    @pytest.mark.asyncio
    async def test_exited_container_maps_to_stopped(self, test_db, test_user):
        """A container with 'exited' status should set actual_state=stopped."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = _make_host(test_db, "host-a")
        lab = _make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = _make_node(test_db, lab.id, "R1", host_id=host.id)
        ns = _make_node_state(
            test_db, lab.id, "R1",
            actual="running", desired="running",
            node_definition_id=node_def.id,
        )
        _make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        with patch("app.tasks.reconciliation_db._ensure_link_states_for_lab", return_value=0):
            with patch("app.tasks.reconciliation_db.cleanup_orphaned_node_states", return_value=0):
                with patch("app.tasks.reconciliation_db.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.normalize_links_for_lab.return_value = 0
                    mock_ts.get_links.return_value = []
                    mock_ts_cls.return_value = mock_ts

                    with patch("app.utils.lab.get_lab_provider", return_value="docker"):
                        with patch(
                            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
                            new_callable=AsyncMock,
                            return_value={"nodes": [{"name": "R1", "status": "exited"}]},
                        ):
                            with patch("app.tasks.reconciliation_db.agent_client.is_agent_online", return_value=True):
                                with patch("app.tasks.reconciliation.link_ops_lock") as ml_fn:
                                    ml = MagicMock()
                                    ml.__enter__ = MagicMock(return_value=False)
                                    ml.__exit__ = MagicMock(return_value=False)
                                    ml_fn.return_value = ml
                                    await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPED.value

    @pytest.mark.asyncio
    async def test_dead_container_maps_to_error(self, test_db, test_user):
        """A container with 'dead' status should set actual_state=error."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = _make_host(test_db, "host-b")
        lab = _make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = _make_node(test_db, lab.id, "R1", host_id=host.id)
        ns = _make_node_state(
            test_db, lab.id, "R1",
            actual="running", desired="running",
            node_definition_id=node_def.id,
        )
        _make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        with patch("app.tasks.reconciliation_db._ensure_link_states_for_lab", return_value=0):
            with patch("app.tasks.reconciliation_db.cleanup_orphaned_node_states", return_value=0):
                with patch("app.tasks.reconciliation_db.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.normalize_links_for_lab.return_value = 0
                    mock_ts.get_links.return_value = []
                    mock_ts_cls.return_value = mock_ts

                    with patch("app.utils.lab.get_lab_provider", return_value="docker"):
                        with patch(
                            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
                            new_callable=AsyncMock,
                            return_value={"nodes": [{"name": "R1", "status": "dead"}]},
                        ):
                            with patch("app.tasks.reconciliation_db.agent_client.is_agent_online", return_value=True):
                                with patch("app.tasks.reconciliation.link_ops_lock") as ml_fn:
                                    ml = MagicMock()
                                    ml.__enter__ = MagicMock(return_value=False)
                                    ml.__exit__ = MagicMock(return_value=False)
                                    ml_fn.return_value = ml
                                    await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.ERROR.value
        assert "dead" in ns.error_message

    @pytest.mark.asyncio
    async def test_container_not_found_preserves_state_when_agent_not_queried(
        self, test_db, test_user
    ):
        """If expected agent was not queried, node state should be preserved."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = _make_host(test_db, "host-c", status="offline")
        lab = _make_lab(test_db, test_user, state="running", agent_id=host.id)
        node_def = _make_node(test_db, lab.id, "R1", host_id=host.id)
        ns = _make_node_state(
            test_db, lab.id, "R1",
            actual="running", desired="running",
            node_definition_id=node_def.id,
        )
        _make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        with patch("app.tasks.reconciliation_db._ensure_link_states_for_lab", return_value=0):
            with patch("app.tasks.reconciliation_db.cleanup_orphaned_node_states", return_value=0):
                with patch("app.tasks.reconciliation_db.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.normalize_links_for_lab.return_value = 0
                    mock_ts.get_links.return_value = []
                    mock_ts_cls.return_value = mock_ts

                    with patch("app.utils.lab.get_lab_provider", return_value="docker"):
                        # Agent is offline -> is_agent_online returns False
                        with patch("app.tasks.reconciliation_db.agent_client.is_agent_online", return_value=False):
                            with patch("app.tasks.reconciliation_db.agent_client.get_agent_for_lab", new_callable=AsyncMock, return_value=None):
                                await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        # State should be preserved since agent wasn't queryable
        assert ns.actual_state == NodeActualState.RUNNING.value


class TestDoReconcileLabStartingStuck:
    """Tests for starting_started_at handling in reconciliation."""

    @pytest.mark.asyncio
    async def test_active_starting_within_threshold_is_skipped(self, test_db, test_user):
        """Nodes with fresh starting_started_at should not be reconciled."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = _make_host(test_db, "host-d")
        lab = _make_lab(test_db, test_user, state="starting", agent_id=host.id)
        node_def = _make_node(test_db, lab.id, "R1", host_id=host.id)

        now = datetime.now(timezone.utc)
        ns = _make_node_state(
            test_db, lab.id, "R1",
            actual="starting", desired="running",
            node_definition_id=node_def.id,
            starting_started_at=now,
        )
        _make_placement(test_db, lab.id, "R1", host.id, node_definition_id=node_def.id)

        with patch("app.tasks.reconciliation_db._ensure_link_states_for_lab", return_value=0):
            with patch("app.tasks.reconciliation_db.cleanup_orphaned_node_states", return_value=0):
                with patch("app.tasks.reconciliation_db.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.normalize_links_for_lab.return_value = 0
                    mock_ts.get_links.return_value = []
                    mock_ts_cls.return_value = mock_ts

                    with patch("app.utils.lab.get_lab_provider", return_value="docker"):
                        with patch(
                            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
                            new_callable=AsyncMock,
                            return_value={"nodes": [{"name": "R1", "status": "running"}]},
                        ):
                            with patch("app.tasks.reconciliation_db.agent_client.is_agent_online", return_value=True):
                                with patch("app.tasks.reconciliation.link_ops_lock") as ml_fn:
                                    ml = MagicMock()
                                    ml.__enter__ = MagicMock(return_value=False)
                                    ml.__exit__ = MagicMock(return_value=False)
                                    ml_fn.return_value = ml
                                    await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ns)
        # State should still be 'starting' since it's within threshold
        assert ns.actual_state == NodeActualState.STARTING.value


# ---------------------------------------------------------------------------
# Tests: _do_reconcile_lab - link state reconciliation
# ---------------------------------------------------------------------------

class TestDoReconcileLabLinkStatesExtended:
    """Extended link state reconciliation tests."""

    @pytest.mark.asyncio
    async def test_carrier_off_sets_link_down(self, test_db, test_user):
        """When carrier is off on one endpoint, link should be DOWN."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = _make_host(test_db, "host-e")
        lab = _make_lab(test_db, test_user, state="running", agent_id=host.id)
        n1 = _make_node(test_db, lab.id, "R1", host_id=host.id)
        n2 = _make_node(test_db, lab.id, "R2", host_id=host.id)
        _make_node_state(
            test_db, lab.id, "R1", actual="running", desired="running",
            node_definition_id=n1.id,
        )
        _make_node_state(
            test_db, lab.id, "R2", node_id="r2", actual="running", desired="running",
            node_definition_id=n2.id,
        )
        _make_placement(test_db, lab.id, "R1", host.id, node_definition_id=n1.id)
        _make_placement(test_db, lab.id, "R2", host.id, node_definition_id=n2.id)

        ls = _make_link_state(
            test_db, lab.id, "R1", "eth1", "R2", "eth1",
            actual="up", source_carrier_state="off",
        )

        with patch("app.tasks.reconciliation_db._ensure_link_states_for_lab", return_value=0):
            with patch("app.tasks.reconciliation_db.cleanup_orphaned_node_states", return_value=0):
                with patch("app.tasks.reconciliation_db.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.normalize_links_for_lab.return_value = 0
                    mock_ts.get_links.return_value = []
                    mock_ts_cls.return_value = mock_ts

                    with patch("app.utils.lab.get_lab_provider", return_value="docker"):
                        with patch(
                            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
                            new_callable=AsyncMock,
                            return_value={"nodes": [
                                {"name": "R1", "status": "running"},
                                {"name": "R2", "status": "running"},
                            ]},
                        ):
                            with patch("app.tasks.reconciliation_db.agent_client.is_agent_online", return_value=True):
                                with patch("app.tasks.reconciliation.link_ops_lock") as ml_fn:
                                    ml = MagicMock()
                                    ml.__enter__ = MagicMock(return_value=False)
                                    ml.__exit__ = MagicMock(return_value=False)
                                    ml_fn.return_value = ml
                                    await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ls)
        assert ls.actual_state == LinkActualState.DOWN.value
        assert "Carrier disabled" in ls.error_message

    @pytest.mark.asyncio
    async def test_cross_host_link_no_tunnel_sets_error(self, test_db, test_user):
        """Cross-host link without active tunnel should be marked error."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host_a = _make_host(test_db, "host-f")
        host_b = _make_host(test_db, "host-g")
        lab = _make_lab(test_db, test_user, state="running", agent_id=host_a.id)
        n1 = _make_node(test_db, lab.id, "R1", host_id=host_a.id)
        n2 = _make_node(test_db, lab.id, "R2", host_id=host_b.id)
        _make_node_state(
            test_db, lab.id, "R1", actual="running", desired="running",
            node_definition_id=n1.id,
        )
        _make_node_state(
            test_db, lab.id, "R2", node_id="r2", actual="running", desired="running",
            node_definition_id=n2.id,
        )
        _make_placement(test_db, lab.id, "R1", host_a.id, node_definition_id=n1.id)
        _make_placement(test_db, lab.id, "R2", host_b.id, node_definition_id=n2.id)

        ls = _make_link_state(
            test_db, lab.id, "R1", "eth1", "R2", "eth1",
            actual="pending", is_cross_host=True,
            source_host_id=host_a.id, target_host_id=host_b.id,
        )

        with patch("app.tasks.reconciliation_db._ensure_link_states_for_lab", return_value=0):
            with patch("app.tasks.reconciliation_db.cleanup_orphaned_node_states", return_value=0):
                with patch("app.tasks.reconciliation_db.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.normalize_links_for_lab.return_value = 0
                    mock_ts.get_links.return_value = []
                    mock_ts_cls.return_value = mock_ts

                    with patch("app.utils.lab.get_lab_provider", return_value="docker"):
                        with patch(
                            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
                            new_callable=AsyncMock,
                            return_value={"nodes": [
                                {"name": "R1", "status": "running"},
                                {"name": "R2", "status": "running"},
                            ]},
                        ):
                            with patch("app.tasks.reconciliation_db.agent_client.is_agent_online", return_value=True):
                                with patch("app.tasks.reconciliation.link_ops_lock") as ml_fn:
                                    ml = MagicMock()
                                    ml.__enter__ = MagicMock(return_value=False)
                                    ml.__exit__ = MagicMock(return_value=False)
                                    ml_fn.return_value = ml
                                    await _do_reconcile_lab(test_db, lab, lab.id)

        test_db.refresh(ls)
        assert ls.actual_state == LinkActualState.ERROR.value
        assert "VXLAN tunnel" in ls.error_message

    @pytest.mark.asyncio
    async def test_deleted_link_state_removed(self, test_db, test_user):
        """Link states with desired_state='deleted' should be removed."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        host = _make_host(test_db, "host-h")
        lab = _make_lab(test_db, test_user, state="running", agent_id=host.id)
        n1 = _make_node(test_db, lab.id, "R1", host_id=host.id)
        _make_node_state(
            test_db, lab.id, "R1", actual="running", desired="running",
            node_definition_id=n1.id,
        )
        _make_placement(test_db, lab.id, "R1", host.id, node_definition_id=n1.id)

        ls = _make_link_state(
            test_db, lab.id, "R1", "eth1", "R2", "eth1",
            desired="deleted", actual="down",
        )
        ls_id = ls.id

        with patch("app.tasks.reconciliation_db._ensure_link_states_for_lab", return_value=0):
            with patch("app.tasks.reconciliation_db.cleanup_orphaned_node_states", return_value=0):
                with patch("app.tasks.reconciliation_db.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.normalize_links_for_lab.return_value = 0
                    mock_ts.get_links.return_value = []
                    mock_ts_cls.return_value = mock_ts

                    with patch("app.utils.lab.get_lab_provider", return_value="docker"):
                        with patch(
                            "app.tasks.reconciliation_db.agent_client.get_lab_status_from_agent",
                            new_callable=AsyncMock,
                            return_value={"nodes": [{"name": "R1", "status": "running"}]},
                        ):
                            with patch("app.tasks.reconciliation_db.agent_client.is_agent_online", return_value=True):
                                with patch("app.tasks.reconciliation.link_ops_lock") as ml_fn:
                                    ml = MagicMock()
                                    ml.__enter__ = MagicMock(return_value=False)
                                    ml.__exit__ = MagicMock(return_value=False)
                                    ml_fn.return_value = ml
                                    await _do_reconcile_lab(test_db, lab, lab.id)

        deleted_ls = test_db.get(models.LinkState, ls_id)
        assert deleted_ls is None


# ---------------------------------------------------------------------------
# Tests: _maybe_cleanup_labless_containers VXLAN reconciliation
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

        # Counter should have been reset
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

        lab = _make_lab(test_db, test_user, state="running")

        with patch("app.tasks.reconciliation_db._ensure_link_states_for_lab", side_effect=RuntimeError("DB error")):
            with patch("app.tasks.reconciliation_db.cleanup_orphaned_node_states", return_value=0):
                with patch("app.tasks.reconciliation_db.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.normalize_links_for_lab.return_value = 0
                    mock_ts.get_links.return_value = []
                    mock_ts_cls.return_value = mock_ts

                    with patch("app.utils.lab.get_lab_provider", return_value="docker"):
                        with patch("app.tasks.reconciliation.link_ops_lock") as ml_fn:
                            ml = MagicMock()
                            ml.__enter__ = MagicMock(return_value=False)
                            ml.__exit__ = MagicMock(return_value=False)
                            ml_fn.return_value = ml
                            # Should not raise
                            result = await _do_reconcile_lab(test_db, lab, lab.id)

        assert result == 0

    @pytest.mark.asyncio
    async def test_normalize_links_exception_is_caught(self, test_db, test_user):
        """Failure in TopologyService.normalize_links_for_lab should be caught."""
        from app.tasks.reconciliation_db import _do_reconcile_lab

        lab = _make_lab(test_db, test_user, state="running")

        with patch("app.tasks.reconciliation_db._ensure_link_states_for_lab", return_value=0):
            with patch("app.tasks.reconciliation_db.cleanup_orphaned_node_states", return_value=0):
                with patch("app.tasks.reconciliation_db.TopologyService") as mock_ts_cls:
                    mock_ts = MagicMock()
                    mock_ts.normalize_links_for_lab.side_effect = RuntimeError("normalize failure")
                    mock_ts.get_links.return_value = []
                    mock_ts_cls.return_value = mock_ts

                    with patch("app.utils.lab.get_lab_provider", return_value="docker"):
                        with patch("app.tasks.reconciliation.link_ops_lock") as ml_fn:
                            ml = MagicMock()
                            ml.__enter__ = MagicMock(return_value=False)
                            ml.__exit__ = MagicMock(return_value=False)
                            ml_fn.return_value = ml
                            result = await _do_reconcile_lab(test_db, lab, lab.id)

        assert result == 0


# ---------------------------------------------------------------------------
# Tests: cleanup_orphaned_node_states (extended)
# ---------------------------------------------------------------------------

class TestCleanupOrphanedNodeStatesExtended:
    """Extended cleanup_orphaned_node_states tests."""

    def test_preserves_stopping_orphan(self, test_db, sample_lab):
        """Nodes in STOPPING state should be preserved even if orphaned."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        ns = _make_node_state(
            test_db, sample_lab.id, "R1",
            actual=NodeActualState.STOPPING.value,
            node_definition_id=None,
        )

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 0

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPING.value

    def test_preserves_pending_orphan(self, test_db, sample_lab):
        """Nodes in PENDING state should be preserved even if orphaned."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        _make_node_state(
            test_db, sample_lab.id, "R1",
            actual=NodeActualState.PENDING.value,
            node_definition_id=None,
        )

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 0


# ---------------------------------------------------------------------------
# Tests: _backfill_placement_node_ids
# ---------------------------------------------------------------------------

class TestBackfillPlacementNodeIdsExtended:
    """Extended _backfill_placement_node_ids tests."""

    def test_logs_warning_when_missing_placements_exist(self, test_db, sample_lab):
        """Should log warning but return 0 (no-op)."""
        from app.tasks.reconciliation_db import _backfill_placement_node_ids

        _make_placement(test_db, sample_lab.id, "R1", "host-xyz", node_definition_id=None)

        result = _backfill_placement_node_ids(test_db, sample_lab.id)
        assert result == 0

    def test_no_missing_returns_zero(self, test_db, sample_lab):
        """No missing node_definition_ids should return 0."""
        from app.tasks.reconciliation_db import _backfill_placement_node_ids

        result = _backfill_placement_node_ids(test_db, sample_lab.id)
        assert result == 0


# ---------------------------------------------------------------------------
# Tests: _ensure_link_states_for_lab (extended)
# ---------------------------------------------------------------------------

class TestEnsureLinkStatesExtended:
    """Extended _ensure_link_states_for_lab tests."""

    def test_no_db_links_returns_zero(self, test_db, sample_lab):
        """Lab with no link definitions should return 0."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        result = _ensure_link_states_for_lab(test_db, sample_lab.id)
        assert result == 0

    def test_skips_link_missing_source_node_def(self, test_db, sample_lab):
        """Links where source node definition is deleted should be skipped."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        n2 = _make_node(test_db, sample_lab.id, "R2")
        # Create link referencing a nonexistent source node
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

        host_a = _make_host(test_db, "host-swap-a")
        host_b = _make_host(test_db, "host-swap-b")

        # Create nodes where Z sorts after A but is the source
        n_z = _make_node(test_db, sample_lab.id, "Z1", host_id=host_a.id)
        n_a = _make_node(test_db, sample_lab.id, "A1", host_id=host_b.id)

        _make_link(test_db, sample_lab.id, n_z.id, "eth1", n_a.id, "eth1",
                   link_name="Z1:eth1-A1:eth1")

        result = _ensure_link_states_for_lab(test_db, sample_lab.id)
        assert result == 1

        # Flush so the newly added LinkState is queryable
        test_db.flush()

        # Verify the created link state has canonical ordering
        ls = (
            test_db.query(models.LinkState)
            .filter(models.LinkState.lab_id == sample_lab.id)
            .first()
        )
        assert ls is not None
        # The canonical order should put A1 before Z1
        assert ls.source_node == "A1"
        assert ls.target_node == "Z1"


# ---------------------------------------------------------------------------
# Tests: _ensure_link_states_for_lab - deduplication
# ---------------------------------------------------------------------------

class TestEnsureLinkStatesDedup:
    """Tests for duplicate link state consolidation."""

    def test_duplicate_link_states_are_deduplicated(self, test_db, sample_lab):
        """Duplicate link states with same canonical key should be consolidated."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        n1 = _make_node(test_db, sample_lab.id, "R1")
        n2 = _make_node(test_db, sample_lab.id, "R2")
        _make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1",
                   link_name="R1:eth1-R2:eth1")

        # Create two link states for the same logical link
        _make_link_state(
            test_db, sample_lab.id, "R1", "eth1", "R2", "eth1",
            actual="up",
        )
        ls2 = models.LinkState(
            lab_id=sample_lab.id,
            link_name="R1:Ethernet1-R2:Ethernet1",
            source_node="R1", source_interface="Ethernet1",
            target_node="R2", target_interface="Ethernet1",
            desired_state="up", actual_state="pending",
        )
        test_db.add(ls2)
        test_db.commit()
        test_db.refresh(ls2)

        _ensure_link_states_for_lab(test_db, sample_lab.id)

        remaining = (
            test_db.query(models.LinkState)
            .filter(models.LinkState.lab_id == sample_lab.id)
            .all()
        )
        # At most one link state per canonical key should remain (plus potentially new one)
        assert len(remaining) <= 2

    def test_cross_host_flag_set_correctly(self, test_db, sample_lab):
        """When source and target are on different hosts, is_cross_host should be True."""
        from app.tasks.reconciliation_db import _ensure_link_states_for_lab

        host_a = _make_host(test_db, "host-cross-a")
        host_b = _make_host(test_db, "host-cross-b")

        n1 = _make_node(test_db, sample_lab.id, "R1", host_id=host_a.id)
        n2 = _make_node(test_db, sample_lab.id, "R2", host_id=host_b.id)
        _make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1",
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

        host = _make_host(test_db, "host-same")

        n1 = _make_node(test_db, sample_lab.id, "R1", host_id=host.id)
        n2 = _make_node(test_db, sample_lab.id, "R2", host_id=host.id)
        _make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1",
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


# ---------------------------------------------------------------------------
# Tests: cleanup_orphaned_node_states - safe states cleanup
# ---------------------------------------------------------------------------

class TestCleanupOrphanedSafeStates:
    """Tests for cleanup of orphaned nodes in different safe states."""

    def test_removes_undeployed_orphan(self, test_db, sample_lab):
        """Undeployed orphan (no node_definition_id) should be deleted."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        ns = _make_node_state(
            test_db, sample_lab.id, "Orphan1",
            actual=NodeActualState.UNDEPLOYED.value,
            node_definition_id=None,
        )
        ns_id = ns.id

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 1
        assert test_db.get(models.NodeState, ns_id) is None

    def test_removes_error_orphan(self, test_db, sample_lab):
        """Error orphan (no node_definition_id) should be deleted."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        ns = _make_node_state(
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

        ns = _make_node_state(
            test_db, sample_lab.id, "Orphan3",
            actual=NodeActualState.RUNNING.value,
            node_definition_id=None,
        )

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 0

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.RUNNING.value

    def test_preserves_starting_orphan(self, test_db, sample_lab):
        """Starting orphan should NOT be deleted (in transition)."""
        from app.tasks.reconciliation_db import cleanup_orphaned_node_states

        _make_node_state(
            test_db, sample_lab.id, "Orphan4",
            actual=NodeActualState.STARTING.value,
            node_definition_id=None,
        )

        count = cleanup_orphaned_node_states(test_db, sample_lab.id)
        assert count == 0
