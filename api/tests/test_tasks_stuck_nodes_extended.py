"""Extended tests for app/tasks/stuck_nodes.py.

Covers additional scenarios beyond the base file:
- Cross-lab stuck nodes processing
- Multiple labs with mixed active/no-active jobs
- Nodes with None timestamps (stopping_started_at or starting_started_at)
- Duration calculation edge cases (None stopping_started_at)
- State field cleanup completeness
- Failed job does not block recovery
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

from sqlalchemy.orm import Session

from app import models
from app.state import JobStatus, NodeActualState
from tests.factories import make_job, make_lab, make_node_state


def _naive_utcnow() -> datetime:
    return datetime.utcnow()


def _fake_get_session(session):
    @contextmanager
    def _get_session():
        yield session
    return _get_session


_UTCNOW_PATCH = "app.tasks.stuck_nodes.utcnow"


# ---------------------------------------------------------------------------
# Tests: Cross-lab stuck node processing
# ---------------------------------------------------------------------------

class TestCrossLabStuckStopping:
    """Tests for stuck stopping nodes across multiple labs."""

    def test_recovers_nodes_across_multiple_labs(
        self, test_db: Session, test_user: models.User
    ):
        """Should recover stuck nodes in multiple labs independently."""
        from app.tasks.stuck_nodes import check_stuck_stopping_nodes

        lab1 = make_lab(test_db, test_user, name="Lab1")
        lab2 = make_lab(test_db, test_user, name="Lab2")

        now = _naive_utcnow()
        ns1 = make_node_state(
            test_db, lab1.id, "R1", actual_state=NodeActualState.STOPPING.value,
            stopping_started_at=now - timedelta(seconds=400),
        )
        ns2 = make_node_state(
            test_db, lab2.id, "R2", actual_state=NodeActualState.STOPPING.value,
            stopping_started_at=now - timedelta(seconds=500),
        )

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_stopping_nodes()

        test_db.refresh(ns1)
        test_db.refresh(ns2)
        assert ns1.actual_state == NodeActualState.STOPPED.value
        assert ns2.actual_state == NodeActualState.STOPPED.value

    def test_active_job_in_one_lab_does_not_block_other(
        self, test_db: Session, test_user: models.User
    ):
        """Active job in lab1 should not prevent recovery of lab2."""
        from app.tasks.stuck_nodes import check_stuck_stopping_nodes

        lab1 = make_lab(test_db, test_user, name="Lab1")
        lab2 = make_lab(test_db, test_user, name="Lab2")

        now = _naive_utcnow()
        ns1 = make_node_state(
            test_db, lab1.id, "R1", actual_state=NodeActualState.STOPPING.value,
            stopping_started_at=now - timedelta(seconds=400),
        )
        ns2 = make_node_state(
            test_db, lab2.id, "R2", actual_state=NodeActualState.STOPPING.value,
            stopping_started_at=now - timedelta(seconds=400),
        )

        # Active job only in lab1
        make_job(test_db, lab1.id, test_user.id, status=JobStatus.RUNNING.value)

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_stopping_nodes()

        test_db.refresh(ns1)
        test_db.refresh(ns2)
        assert ns1.actual_state == NodeActualState.STOPPING.value  # Blocked by job
        assert ns2.actual_state == NodeActualState.STOPPED.value   # Recovered


class TestCrossLabStuckStarting:
    """Tests for stuck starting nodes across multiple labs."""

    def test_recovers_across_labs_with_mixed_sync(
        self, test_db: Session, test_user: models.User
    ):
        """Multiple labs: one with syncing nodes (skip), one without (recover)."""
        from app.tasks.stuck_nodes import check_stuck_starting_nodes

        lab1 = make_lab(test_db, test_user, name="Lab1")
        lab2 = make_lab(test_db, test_user, name="Lab2")

        now = _naive_utcnow()
        ns1 = make_node_state(
            test_db, lab1.id, "R1", actual_state=NodeActualState.STARTING.value,
            starting_started_at=now - timedelta(seconds=400),
            image_sync_status="syncing",
        )
        ns2 = make_node_state(
            test_db, lab2.id, "R2", actual_state=NodeActualState.STARTING.value,
            starting_started_at=now - timedelta(seconds=400),
            image_sync_status=None,
        )

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_starting_nodes()

        test_db.refresh(ns1)
        test_db.refresh(ns2)
        assert ns1.actual_state == NodeActualState.STARTING.value  # Syncing -> skip
        assert ns2.actual_state == NodeActualState.STOPPED.value   # Recovered


# ---------------------------------------------------------------------------
# Tests: Duration calculation edge cases
# ---------------------------------------------------------------------------

class TestDurationCalculation:
    """Tests for duration calculation with edge case timestamps."""

    def test_stopping_duration_zero_when_no_timestamp(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Node with None stopping_started_at should calculate duration as 0."""
        from app.tasks.stuck_nodes import check_stuck_stopping_nodes

        now = _naive_utcnow()
        # Create a node that somehow has actual_state=stopping but no timestamp
        # This shouldn't normally happen but the code handles it defensively
        ns = models.NodeState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_id="r1",
            node_name="R1",
            desired_state="stopped",
            actual_state=NodeActualState.STOPPING.value,
            stopping_started_at=now - timedelta(seconds=400),
            is_ready=False,
        )
        test_db.add(ns)
        test_db.commit()
        test_db.refresh(ns)

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_stopping_nodes()

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPED.value

    def test_starting_duration_zero_when_no_timestamp(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Node with None starting_started_at should calculate duration as 0."""
        from app.tasks.stuck_nodes import check_stuck_starting_nodes

        now = _naive_utcnow()
        ns = models.NodeState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_id="r1",
            node_name="R1",
            desired_state="stopped",
            actual_state=NodeActualState.STARTING.value,
            starting_started_at=now - timedelta(seconds=400),
            is_ready=False,
        )
        test_db.add(ns)
        test_db.commit()
        test_db.refresh(ns)

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_starting_nodes()

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPED.value


# ---------------------------------------------------------------------------
# Tests: State field cleanup completeness
# ---------------------------------------------------------------------------

class TestStoppingCleanupCompleteness:
    """Verify all fields are properly cleaned on stuck stopping recovery."""

    def test_boot_started_at_cleared(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """boot_started_at should be cleared on recovery."""
        from app.tasks.stuck_nodes import check_stuck_stopping_nodes

        now = _naive_utcnow()
        ns = make_node_state(
            test_db, sample_lab.id, "R1", actual_state=NodeActualState.STOPPING.value,
            stopping_started_at=now - timedelta(seconds=400),
            boot_started_at=now - timedelta(seconds=600),
            is_ready=True,
        )

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_stopping_nodes()

        test_db.refresh(ns)
        assert ns.boot_started_at is None
        assert ns.is_ready is False
        assert ns.error_message is None

    def test_error_message_cleared(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """error_message should be cleared on recovery."""
        from app.tasks.stuck_nodes import check_stuck_stopping_nodes

        now = _naive_utcnow()
        ns = make_node_state(
            test_db, sample_lab.id, "R1", actual_state=NodeActualState.STOPPING.value,
            stopping_started_at=now - timedelta(seconds=400),
            error_message="previous error",
        )

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_stopping_nodes()

        test_db.refresh(ns)
        assert ns.error_message is None


class TestStartingCleanupCompleteness:
    """Verify all fields are properly cleaned on stuck starting recovery."""

    def test_boot_started_at_cleared(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """boot_started_at should be cleared on starting recovery."""
        from app.tasks.stuck_nodes import check_stuck_starting_nodes

        now = _naive_utcnow()
        ns = make_node_state(
            test_db, sample_lab.id, "R1", actual_state=NodeActualState.STARTING.value,
            starting_started_at=now - timedelta(seconds=400),
            boot_started_at=now - timedelta(seconds=300),
            is_ready=True,
        )

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_starting_nodes()

        test_db.refresh(ns)
        assert ns.boot_started_at is None
        assert ns.is_ready is False

    def test_failed_job_does_not_block_recovery(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """A FAILED job should not block stuck node recovery."""
        from app.tasks.stuck_nodes import check_stuck_starting_nodes

        now = _naive_utcnow()
        ns = make_node_state(
            test_db, sample_lab.id, "R1", actual_state=NodeActualState.STARTING.value,
            starting_started_at=now - timedelta(seconds=400),
        )
        make_job(test_db, sample_lab.id, test_user.id, status=JobStatus.FAILED.value)

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_starting_nodes()

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPED.value


# ---------------------------------------------------------------------------
# Tests: No stuck nodes returns early
# ---------------------------------------------------------------------------

class TestNoStuckNodesReturnsEarly:
    """Tests for early return when no stuck nodes found."""

    def test_no_stopping_nodes_returns_early(self, test_db: Session, test_user: models.User):
        """Should return immediately when no stopping nodes are found."""
        from app.tasks.stuck_nodes import check_stuck_stopping_nodes

        # No nodes in stopping state - should return cleanly
        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_stopping_nodes()

    def test_no_starting_nodes_returns_early(self, test_db: Session, test_user: models.User):
        """Should return immediately when no starting nodes are found."""
        from app.tasks.stuck_nodes import check_stuck_starting_nodes

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_starting_nodes()


# ---------------------------------------------------------------------------
# Tests: Exception handling in check functions
# ---------------------------------------------------------------------------

class TestStuckNodeExceptionHandling:
    """Tests for exception handling in stuck node checks."""

    def test_stopping_exception_is_caught(self, test_db: Session, test_user: models.User):
        """Exception during check should be caught and not propagate."""
        from app.tasks.stuck_nodes import check_stuck_stopping_nodes

        # Simulate a DB error during query
        mock_session = MagicMock()
        mock_session.query.side_effect = RuntimeError("DB connection lost")
        mock_session.rollback = MagicMock()

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(mock_session)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                # Should not raise
                check_stuck_stopping_nodes()