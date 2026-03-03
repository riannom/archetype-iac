"""Tests for app/tasks/reconciliation_refresh.py - Agent refresh and boot-readiness checks."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models
from app.state import LabState, NodeActualState, NodeDesiredState


# ---------------------------------------------------------------------------
# Module-level autouse fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _disable_broadcasts():
    """Disable background broadcast tasks during reconciliation refresh tests."""
    with patch(
        "app.tasks.reconciliation_refresh.broadcast_node_state_change",
        new_callable=AsyncMock,
    ):
        with patch(
            "app.tasks.reconciliation_db.broadcast_node_state_change",
            new_callable=AsyncMock,
        ):
            with patch(
                "app.tasks.reconciliation_db.broadcast_link_state_change",
                new_callable=AsyncMock,
            ):
                yield


@pytest.fixture(autouse=True)
def _disable_external_side_effects():
    """Prevent reconciliation from invoking external side effects."""
    with patch(
        "app.tasks.reconciliation_refresh.agent_client.check_node_readiness",
        new_callable=AsyncMock,
    ) as mock_ready:
        mock_ready.return_value = {"is_ready": False}
        with patch(
            "app.tasks.reconciliation_db._maybe_cleanup_labless_containers",
            new_callable=AsyncMock,
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ):
                yield


@pytest.fixture(autouse=True)
def _reset_sweep_counter():
    """Reset the sweep counter between tests to avoid cross-test pollution."""
    from app.tasks.reconciliation_refresh import refresh_states_from_agents

    if hasattr(refresh_states_from_agents, "_sweep_counter"):
        del refresh_states_from_agents._sweep_counter
    yield
    if hasattr(refresh_states_from_agents, "_sweep_counter"):
        del refresh_states_from_agents._sweep_counter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _override_get_session(test_db: Session):
    """Create a contextmanager that yields the test_db session."""

    @contextmanager
    def _session_ctx():
        yield test_db

    return _session_ctx


def _make_lab(
    test_db: Session,
    test_user: models.User,
    *,
    state: str = "stopped",
    state_updated_at: datetime | None = None,
    name: str | None = None,
) -> models.Lab:
    """Helper to create a lab with specific state."""
    lab = models.Lab(
        name=name or f"Lab-{uuid4().hex[:8]}",
        owner_id=test_user.id,
        provider="docker",
        state=state,
        workspace_path="/tmp/test-lab",
        state_updated_at=state_updated_at,
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


def _make_node_state(
    test_db: Session,
    lab_id: str,
    *,
    node_name: str = "R1",
    node_id: str | None = None,
    desired_state: str = "stopped",
    actual_state: str = "undeployed",
    is_ready: bool = False,
    boot_started_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> models.NodeState:
    """Helper to create a NodeState with specific state."""
    ns = models.NodeState(
        lab_id=lab_id,
        node_id=node_id or node_name.lower(),
        node_name=node_name,
        desired_state=desired_state,
        actual_state=actual_state,
        is_ready=is_ready,
        boot_started_at=boot_started_at,
    )
    test_db.add(ns)
    test_db.commit()
    if updated_at is not None:
        # Directly update the column to bypass onupdate triggers
        test_db.execute(
            models.NodeState.__table__.update()
            .where(models.NodeState.id == ns.id)
            .values(updated_at=updated_at)
        )
        test_db.commit()
    test_db.refresh(ns)
    return ns


def _make_node_def(
    test_db: Session,
    lab_id: str,
    *,
    container_name: str = "R1",
    device: str = "linux",
) -> models.Node:
    """Helper to create a Node definition."""
    node = models.Node(
        lab_id=lab_id,
        gui_id=container_name.lower(),
        display_name=container_name,
        container_name=container_name,
        node_type="device",
        device=device,
    )
    test_db.add(node)
    test_db.commit()
    test_db.refresh(node)
    return node


def _make_placement(
    test_db: Session,
    lab_id: str,
    node_name: str,
    host_id: str,
) -> models.NodePlacement:
    """Helper to create a NodePlacement."""
    placement = models.NodePlacement(
        lab_id=lab_id,
        node_name=node_name,
        host_id=host_id,
    )
    test_db.add(placement)
    test_db.commit()
    test_db.refresh(placement)
    return placement


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestSweepTriggerSelection:
    """Tests for which labs get selected for reconciliation."""

    @pytest.mark.asyncio
    async def test_no_labs_yields_empty_reconciliation(self, test_db: Session):
        """Should complete without error when no labs exist."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            # Should not raise
            await refresh_states_from_agents()

    @pytest.mark.asyncio
    async def test_transitional_starting_lab_selected(
        self, test_db: Session, test_user: models.User
    ):
        """Labs in STARTING state past threshold should be selected."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        stale_time = datetime.now(timezone.utc) - timedelta(seconds=1200)
        lab = _make_lab(
            test_db, test_user,
            state=LabState.STARTING.value,
            state_updated_at=stale_time,
        )

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                # The stale STARTING lab should have been reconciled
                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id in reconciled_ids

    @pytest.mark.asyncio
    async def test_transitional_stopping_lab_selected(
        self, test_db: Session, test_user: models.User
    ):
        """Labs in STOPPING state past threshold should be selected."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        stale_time = datetime.now(timezone.utc) - timedelta(seconds=1200)
        lab = _make_lab(
            test_db, test_user,
            state=LabState.STOPPING.value,
            state_updated_at=stale_time,
        )

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id in reconciled_ids

    @pytest.mark.asyncio
    async def test_unknown_state_lab_selected(
        self, test_db: Session, test_user: models.User
    ):
        """Labs in UNKNOWN state past threshold should be selected."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        stale_time = datetime.now(timezone.utc) - timedelta(seconds=1200)
        lab = _make_lab(
            test_db, test_user,
            state=LabState.UNKNOWN.value,
            state_updated_at=stale_time,
        )

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id in reconciled_ids

    @pytest.mark.asyncio
    async def test_stable_running_lab_not_selected_outside_sweep(
        self, test_db: Session, test_user: models.User, sample_host: models.Host
    ):
        """A running lab with no issues should NOT be selected (non-sweep cycle)."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        lab = _make_lab(test_db, test_user, state=LabState.RUNNING.value)

        # Ensure no nodes in problematic states
        _make_node_state(
            test_db, lab.id,
            actual_state=NodeActualState.RUNNING.value,
            desired_state=NodeDesiredState.RUNNING.value,
            is_ready=True,
        )
        # Add node definition and placement so this node isn't flagged as orphan
        _make_node_def(test_db, lab.id, container_name="R1")
        _make_placement(test_db, lab.id, "R1", sample_host.id)

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id not in reconciled_ids


class TestTransitionalStateAgeGuards:
    """Tests for filtering labs by how old transitional states are."""

    @pytest.mark.asyncio
    async def test_fresh_starting_lab_not_selected(
        self, test_db: Session, test_user: models.User
    ):
        """A lab that just entered STARTING state should NOT be selected."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        # Set state_updated_at to now (within threshold)
        fresh_time = datetime.now(timezone.utc)
        lab = _make_lab(
            test_db, test_user,
            state=LabState.STARTING.value,
            state_updated_at=fresh_time,
        )

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id not in reconciled_ids

    @pytest.mark.asyncio
    async def test_stale_pending_nodes_trigger_reconciliation(
        self, test_db: Session, test_user: models.User
    ):
        """Nodes in PENDING state past threshold should trigger reconciliation."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        lab = _make_lab(test_db, test_user, state=LabState.RUNNING.value)
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=1200)
        _make_node_state(
            test_db, lab.id,
            node_name="R1",
            actual_state=NodeActualState.PENDING.value,
            updated_at=stale_time,
        )

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id in reconciled_ids

    @pytest.mark.asyncio
    async def test_fresh_pending_nodes_not_selected(
        self, test_db: Session, test_user: models.User, sample_host: models.Host
    ):
        """Nodes that just entered PENDING state should NOT trigger reconciliation
        via the stale-pending path (the node's updated_at is within the threshold)."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        # Use STARTING lab state so it matches the computed state (pending node -> starting)
        # and thus avoids triggering the inconsistent-state path.
        # The lab's state_updated_at must also be recent to avoid the transitional threshold.
        lab = _make_lab(
            test_db, test_user,
            state=LabState.STARTING.value,
            state_updated_at=datetime.now(timezone.utc),
        )
        # Default updated_at is now() — within threshold
        _make_node_state(
            test_db, lab.id,
            node_name="R1",
            actual_state=NodeActualState.PENDING.value,
        )
        _make_node_def(test_db, lab.id, container_name="R1")
        _make_placement(test_db, lab.id, "R1", sample_host.id)

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id not in reconciled_ids


class TestPerLabTriggers:
    """Tests for individual lab reconciliation triggers based on node states."""

    @pytest.mark.asyncio
    async def test_error_nodes_trigger_reconciliation(
        self, test_db: Session, test_user: models.User
    ):
        """Labs with ERROR nodes should be selected for reconciliation."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        lab = _make_lab(test_db, test_user, state=LabState.ERROR.value)
        _make_node_state(
            test_db, lab.id,
            node_name="R1",
            actual_state=NodeActualState.ERROR.value,
        )

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id in reconciled_ids

    @pytest.mark.asyncio
    async def test_unready_running_nodes_trigger_reconciliation(
        self, test_db: Session, test_user: models.User
    ):
        """Labs with running but not-ready nodes should trigger reconciliation."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        lab = _make_lab(test_db, test_user, state=LabState.RUNNING.value)
        _make_node_state(
            test_db, lab.id,
            node_name="R1",
            actual_state=NodeActualState.RUNNING.value,
            desired_state=NodeDesiredState.RUNNING.value,
            is_ready=False,
        )

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id in reconciled_ids

    @pytest.mark.asyncio
    async def test_desired_running_but_stopped_triggers_reconciliation(
        self, test_db: Session, test_user: models.User
    ):
        """Nodes where desired=running but actual=stopped should trigger reconciliation."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        lab = _make_lab(test_db, test_user, state=LabState.RUNNING.value)
        _make_node_state(
            test_db, lab.id,
            node_name="R1",
            desired_state=NodeDesiredState.RUNNING.value,
            actual_state=NodeActualState.STOPPED.value,
        )

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id in reconciled_ids

    @pytest.mark.asyncio
    async def test_running_node_without_placement_triggers_reconciliation(
        self, test_db: Session, test_user: models.User
    ):
        """Running nodes missing NodePlacement should trigger reconciliation."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        lab = _make_lab(test_db, test_user, state=LabState.RUNNING.value)
        _make_node_state(
            test_db, lab.id,
            node_name="R1",
            actual_state=NodeActualState.RUNNING.value,
            desired_state=NodeDesiredState.RUNNING.value,
            is_ready=True,
        )
        # Intentionally NO NodePlacement created

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id in reconciled_ids

    @pytest.mark.asyncio
    async def test_orphan_placement_triggers_reconciliation(
        self, test_db: Session, test_user: models.User, sample_host: models.Host
    ):
        """Placements for deleted nodes should trigger reconciliation."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        lab = _make_lab(test_db, test_user, state=LabState.RUNNING.value)
        # Create placement for a node that does NOT have a matching Node definition
        _make_placement(test_db, lab.id, "deleted-node", sample_host.id)

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id in reconciled_ids


class TestInconsistentLabState:
    """Tests for labs with state that doesn't match computed state from nodes."""

    @pytest.mark.asyncio
    async def test_running_lab_with_all_stopped_nodes_triggers_reconciliation(
        self, test_db: Session, test_user: models.User
    ):
        """Lab state='running' but all nodes stopped -> inconsistent -> selected."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        lab = _make_lab(test_db, test_user, state=LabState.RUNNING.value)
        _make_node_state(
            test_db, lab.id,
            node_name="R1",
            actual_state=NodeActualState.STOPPED.value,
            desired_state=NodeDesiredState.STOPPED.value,
        )
        _make_node_state(
            test_db, lab.id,
            node_name="R2",
            node_id="r2",
            actual_state=NodeActualState.STOPPED.value,
            desired_state=NodeDesiredState.STOPPED.value,
        )

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id in reconciled_ids


class TestPeriodicFullSweep:
    """Tests for the periodic full sweep that reconciles all deployed labs."""

    @pytest.mark.asyncio
    async def test_full_sweep_on_10th_cycle(
        self, test_db: Session, test_user: models.User
    ):
        """Every 10th cycle should add all deployed labs to reconciliation set."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        lab = _make_lab(test_db, test_user, state=LabState.RUNNING.value)
        # Make all nodes consistent so it wouldn't be selected normally
        _make_node_state(
            test_db, lab.id,
            node_name="R1",
            actual_state=NodeActualState.RUNNING.value,
            desired_state=NodeDesiredState.RUNNING.value,
            is_ready=True,
        )
        _make_placement(test_db, lab.id, "R1", "dummy-host")
        # Add a Node definition so the placement isn't orphaned
        _make_node_def(test_db, lab.id, container_name="R1")

        # Set sweep counter to 9 so the next call is the 10th
        refresh_states_from_agents._sweep_counter = 9

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id in reconciled_ids

    @pytest.mark.asyncio
    async def test_no_sweep_on_non_10th_cycle(
        self, test_db: Session, test_user: models.User
    ):
        """Non-10th cycles should NOT add healthy deployed labs."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        lab = _make_lab(test_db, test_user, state=LabState.RUNNING.value)
        _make_node_state(
            test_db, lab.id,
            node_name="R1",
            actual_state=NodeActualState.RUNNING.value,
            desired_state=NodeDesiredState.RUNNING.value,
            is_ready=True,
        )
        _make_placement(test_db, lab.id, "R1", "dummy-host")
        _make_node_def(test_db, lab.id, container_name="R1")

        # Counter at 4 -> next call is 5 (not a sweep cycle)
        refresh_states_from_agents._sweep_counter = 4

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id not in reconciled_ids


class TestErrorHandling:
    """Tests for error handling during the sweep."""

    @pytest.mark.asyncio
    async def test_exception_in_sweep_is_caught(
        self, test_db: Session, test_user: models.User
    ):
        """Exceptions during reconciliation should be caught, not propagated."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        stale_time = datetime.now(timezone.utc) - timedelta(seconds=1200)
        _make_lab(
            test_db, test_user,
            state=LabState.STARTING.value,
            state_updated_at=stale_time,
        )

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                side_effect=RuntimeError("agent unreachable"),
            ):
                # Should NOT raise — error is logged and caught
                await refresh_states_from_agents()

    @pytest.mark.asyncio
    async def test_metrics_recorded_after_error(
        self, test_db: Session, test_user: models.User
    ):
        """Metrics should still be recorded even when reconciliation fails."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        stale_time = datetime.now(timezone.utc) - timedelta(seconds=1200)
        _make_lab(
            test_db, test_user,
            state=LabState.STARTING.value,
            state_updated_at=stale_time,
        )

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ):
                with patch(
                    "app.tasks.reconciliation_refresh.record_reconciliation_cycle"
                ) as mock_metrics:
                    await refresh_states_from_agents()

                    # Metrics should always be called in the finally block
                    mock_metrics.assert_called_once()
                    args = mock_metrics.call_args[0]
                    # First arg is elapsed time (float), should be >= 0
                    assert args[0] >= 0

    @pytest.mark.asyncio
    async def test_cleanup_always_called(
        self, test_db: Session, test_user: models.User
    ):
        """_maybe_cleanup_labless_containers should always be called."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._maybe_cleanup_labless_containers",
                new_callable=AsyncMock,
            ) as mock_cleanup:
                await refresh_states_from_agents()
                mock_cleanup.assert_called_once()


class TestLabStateFiltering:
    """Tests for lab state filtering (running, stopped, error labs)."""

    @pytest.mark.asyncio
    async def test_stopped_lab_included_in_sweep(
        self, test_db: Session, test_user: models.User
    ):
        """STOPPED labs should be included in the periodic full sweep."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        lab = _make_lab(test_db, test_user, state=LabState.STOPPED.value)
        _make_node_state(
            test_db, lab.id,
            node_name="R1",
            actual_state=NodeActualState.STOPPED.value,
            desired_state=NodeDesiredState.STOPPED.value,
        )

        # Set sweep counter to trigger full sweep
        refresh_states_from_agents._sweep_counter = 9

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id in reconciled_ids

    @pytest.mark.asyncio
    async def test_error_lab_included_in_sweep(
        self, test_db: Session, test_user: models.User
    ):
        """ERROR labs should be included in the periodic full sweep."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        lab = _make_lab(test_db, test_user, state=LabState.ERROR.value)
        _make_node_state(
            test_db, lab.id,
            node_name="R1",
            actual_state=NodeActualState.ERROR.value,
        )

        # Even without sweep, ERROR nodes trigger reconciliation
        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                reconciled_ids = {call.args[1] for call in mock_reconcile.call_args_list}
                assert lab.id in reconciled_ids

    @pytest.mark.asyncio
    async def test_multiple_triggers_deduplicate_lab_ids(
        self, test_db: Session, test_user: models.User
    ):
        """A lab matching multiple triggers should only be reconciled once."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        stale_time = datetime.now(timezone.utc) - timedelta(seconds=1200)
        lab = _make_lab(
            test_db, test_user,
            state=LabState.ERROR.value,
            state_updated_at=stale_time,
        )
        # Error node AND desired=running but actual=stopped -- two triggers
        _make_node_state(
            test_db, lab.id,
            node_name="R1",
            actual_state=NodeActualState.ERROR.value,
        )
        _make_node_state(
            test_db, lab.id,
            node_name="R2",
            node_id="r2",
            desired_state=NodeDesiredState.RUNNING.value,
            actual_state=NodeActualState.STOPPED.value,
        )

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_db._reconcile_single_lab",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_reconcile:
                await refresh_states_from_agents()

                # Lab should appear exactly once despite multiple triggers
                lab_ids = [call.args[1] for call in mock_reconcile.call_args_list]
                assert lab_ids.count(lab.id) == 1


class TestReadinessChecks:
    """Tests for the _check_readiness_for_nodes function."""

    @pytest.mark.asyncio
    async def test_readiness_check_called_for_unready_running_nodes(
        self, test_db: Session, test_user: models.User
    ):
        """Unready running nodes should trigger readiness checks."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        lab = _make_lab(test_db, test_user, state=LabState.RUNNING.value)
        _make_node_state(
            test_db, lab.id,
            node_name="R1",
            actual_state=NodeActualState.RUNNING.value,
            desired_state=NodeDesiredState.RUNNING.value,
            is_ready=False,
        )

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_refresh._check_readiness_for_nodes",
                new_callable=AsyncMock,
            ) as mock_check:
                await refresh_states_from_agents()

                mock_check.assert_called_once()
                # First arg is session, second is the list of unready nodes
                nodes_arg = mock_check.call_args[0][1]
                assert len(nodes_arg) == 1
                assert nodes_arg[0].node_name == "R1"

    @pytest.mark.asyncio
    async def test_readiness_check_skipped_for_ready_nodes(
        self, test_db: Session, test_user: models.User
    ):
        """Already-ready nodes should NOT trigger readiness checks."""
        from app.tasks.reconciliation_refresh import refresh_states_from_agents

        lab = _make_lab(test_db, test_user, state=LabState.RUNNING.value)
        _make_node_state(
            test_db, lab.id,
            node_name="R1",
            actual_state=NodeActualState.RUNNING.value,
            desired_state=NodeDesiredState.RUNNING.value,
            is_ready=True,
        )
        _make_placement(test_db, lab.id, "R1", "dummy-host")
        _make_node_def(test_db, lab.id, container_name="R1")

        with patch(
            "app.tasks.reconciliation_refresh.get_session",
            _override_get_session(test_db),
        ):
            with patch(
                "app.tasks.reconciliation_refresh._check_readiness_for_nodes",
                new_callable=AsyncMock,
            ) as mock_check:
                await refresh_states_from_agents()

                mock_check.assert_not_called()
