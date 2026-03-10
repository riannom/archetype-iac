"""Tests for app/tasks/stuck_nodes.py - Recovery of nodes stuck in transitional states."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from app import models
from app.state import JobStatus, NodeActualState
from tests.factories import make_job, make_node_state


def _naive_utcnow() -> datetime:
    """Naive UTC datetime that matches SQLite round-trip (no tzinfo)."""
    return datetime.utcnow()


def _fake_get_session(session):
    """Create a fake get_session context manager that yields the test session."""
    @contextmanager
    def _get_session():
        yield session
    return _get_session


# Patch utcnow in the stuck_nodes module to return naive datetimes.
# SQLite strips timezone info on round-trip, so the production code's
# `now - ns.stopping_started_at` subtraction would fail with mixed
# tz-aware (utcnow) vs tz-naive (from SQLite) datetimes.
_UTCNOW_PATCH = "app.tasks.stuck_nodes.utcnow"


class TestCheckStuckStoppingNodes:
    """Tests for check_stuck_stopping_nodes."""

    def test_no_stuck_nodes_returns_early(self, test_db: Session):
        """Should return without error when no stuck nodes exist."""
        from app.tasks.stuck_nodes import check_stuck_stopping_nodes

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_stopping_nodes()

    def test_detects_node_stuck_past_threshold(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Node stuck in stopping for >6 minutes with no active job should be recovered."""
        from app.tasks.stuck_nodes import check_stuck_stopping_nodes

        now = _naive_utcnow()
        ns = make_node_state(
            test_db,
            sample_lab.id,
            "R1",
            actual_state=NodeActualState.STOPPING.value,
            stopping_started_at=now - timedelta(seconds=400),
        )

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_stopping_nodes()

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPED.value
        assert ns.stopping_started_at is None
        assert ns.error_message is None
        assert ns.is_ready is False
        assert ns.boot_started_at is None

    def test_ignores_node_below_threshold(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Node stopping for <6 minutes should not be touched."""
        from app.tasks.stuck_nodes import check_stuck_stopping_nodes

        now = _naive_utcnow()
        ns = make_node_state(
            test_db,
            sample_lab.id,
            "R1",
            actual_state=NodeActualState.STOPPING.value,
            stopping_started_at=now - timedelta(seconds=300),  # 5 minutes, below 6-min threshold
        )

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_stopping_nodes()

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPING.value

    def test_node_at_exact_threshold_not_recovered(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Node stopping for exactly 360 seconds should NOT be recovered (not strictly past)."""
        from app.tasks.stuck_nodes import check_stuck_stopping_nodes

        now = _naive_utcnow()
        # Exactly at 360s: the filter uses `<` so this should NOT be caught
        ns = make_node_state(
            test_db,
            sample_lab.id,
            "R1",
            actual_state=NodeActualState.STOPPING.value,
            stopping_started_at=now - timedelta(seconds=360),
        )

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, return_value=now):
                check_stuck_stopping_nodes()

        test_db.refresh(ns)
        # At exactly the threshold boundary, `stopping_started_at < stuck_threshold`
        # means `(now - 360s) < (now - 360s)` which is False, so not recovered
        assert ns.actual_state == NodeActualState.STOPPING.value

    def test_skips_when_active_job_exists(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should not recover nodes when there is an active job for the lab."""
        from app.tasks.stuck_nodes import check_stuck_stopping_nodes

        now = _naive_utcnow()
        ns = make_node_state(
            test_db,
            sample_lab.id,
            "R1",
            actual_state=NodeActualState.STOPPING.value,
            stopping_started_at=now - timedelta(seconds=400),
        )
        make_job(test_db, sample_lab.id, test_user.id, JobStatus.RUNNING.value)

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_stopping_nodes()

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPING.value

    def test_skips_when_queued_job_exists(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should not recover nodes when there is a queued job for the lab."""
        from app.tasks.stuck_nodes import check_stuck_stopping_nodes

        now = _naive_utcnow()
        ns = make_node_state(
            test_db,
            sample_lab.id,
            "R1",
            actual_state=NodeActualState.STOPPING.value,
            stopping_started_at=now - timedelta(seconds=400),
        )
        make_job(test_db, sample_lab.id, test_user.id, JobStatus.QUEUED.value)

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_stopping_nodes()

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPING.value

    def test_multiple_stuck_nodes_same_lab(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Multiple stuck nodes in the same lab should all be recovered."""
        from app.tasks.stuck_nodes import check_stuck_stopping_nodes

        now = _naive_utcnow()
        ns1 = make_node_state(
            test_db,
            sample_lab.id,
            "R1",
            actual_state=NodeActualState.STOPPING.value,
            stopping_started_at=now - timedelta(seconds=500),
        )
        ns2 = make_node_state(
            test_db,
            sample_lab.id,
            "R2",
            actual_state=NodeActualState.STOPPING.value,
            stopping_started_at=now - timedelta(seconds=600),
        )

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_stopping_nodes()

        test_db.refresh(ns1)
        test_db.refresh(ns2)
        assert ns1.actual_state == NodeActualState.STOPPED.value
        assert ns2.actual_state == NodeActualState.STOPPED.value

    def test_ignores_non_stopping_states(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Nodes in running/stopped/error states should never be recovered."""
        from app.tasks.stuck_nodes import check_stuck_stopping_nodes

        ns_running = make_node_state(
            test_db, sample_lab.id, "R1", actual_state=NodeActualState.RUNNING.value
        )
        ns_stopped = make_node_state(
            test_db, sample_lab.id, "R2", actual_state=NodeActualState.STOPPED.value
        )

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_stopping_nodes()

        test_db.refresh(ns_running)
        test_db.refresh(ns_stopped)
        assert ns_running.actual_state == NodeActualState.RUNNING.value
        assert ns_stopped.actual_state == NodeActualState.STOPPED.value

    def test_exception_triggers_rollback(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Exceptions during processing should be caught and logged, not raised."""
        from app.tasks.stuck_nodes import check_stuck_stopping_nodes

        # Use a session mock that raises on query
        mock_session = MagicMock()
        mock_session.query.side_effect = RuntimeError("DB error")
        mock_session.rollback = MagicMock()

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(mock_session)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                # Should not raise
                check_stuck_stopping_nodes()

        mock_session.rollback.assert_called_once()


class TestCheckStuckStartingNodes:
    """Tests for check_stuck_starting_nodes."""

    def test_no_stuck_nodes_returns_early(self, test_db: Session):
        """Should return without error when no stuck nodes exist."""
        from app.tasks.stuck_nodes import check_stuck_starting_nodes

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_starting_nodes()

    def test_detects_node_stuck_past_threshold(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Node stuck in starting for >6 minutes with no active job should be recovered to stopped."""
        from app.tasks.stuck_nodes import check_stuck_starting_nodes

        now = _naive_utcnow()
        ns = make_node_state(
            test_db,
            sample_lab.id,
            "R1",
            actual_state=NodeActualState.STARTING.value,
            starting_started_at=now - timedelta(seconds=400),
        )

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_starting_nodes()

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPED.value
        assert ns.starting_started_at is None
        assert ns.error_message is None
        assert ns.is_ready is False

    def test_ignores_node_below_threshold(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Node starting for <6 minutes should not be touched."""
        from app.tasks.stuck_nodes import check_stuck_starting_nodes

        now = _naive_utcnow()
        ns = make_node_state(
            test_db,
            sample_lab.id,
            "R1",
            actual_state=NodeActualState.STARTING.value,
            starting_started_at=now - timedelta(seconds=300),
        )

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_starting_nodes()

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STARTING.value

    def test_skips_node_with_active_image_sync(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Nodes with image sync in progress should not be recovered."""
        from app.tasks.stuck_nodes import check_stuck_starting_nodes

        now = _naive_utcnow()
        ns_syncing = make_node_state(
            test_db,
            sample_lab.id,
            "R1",
            actual_state=NodeActualState.STARTING.value,
            starting_started_at=now - timedelta(seconds=400),
            image_sync_status="syncing",
        )
        ns_checking = make_node_state(
            test_db,
            sample_lab.id,
            "R2",
            actual_state=NodeActualState.STARTING.value,
            starting_started_at=now - timedelta(seconds=400),
            image_sync_status="checking",
        )

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_starting_nodes()

        test_db.refresh(ns_syncing)
        test_db.refresh(ns_checking)
        assert ns_syncing.actual_state == NodeActualState.STARTING.value
        assert ns_checking.actual_state == NodeActualState.STARTING.value

    def test_skips_when_active_job_exists(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Should not recover nodes when there is an active job for the lab."""
        from app.tasks.stuck_nodes import check_stuck_starting_nodes

        now = _naive_utcnow()
        ns = make_node_state(
            test_db,
            sample_lab.id,
            "R1",
            actual_state=NodeActualState.STARTING.value,
            starting_started_at=now - timedelta(seconds=400),
        )
        make_job(test_db, sample_lab.id, test_user.id, JobStatus.RUNNING.value)

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_starting_nodes()

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STARTING.value

    def test_recovers_node_without_image_sync(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Node stuck with no image_sync_status should be recovered."""
        from app.tasks.stuck_nodes import check_stuck_starting_nodes

        now = _naive_utcnow()
        ns = make_node_state(
            test_db,
            sample_lab.id,
            "R1",
            actual_state=NodeActualState.STARTING.value,
            starting_started_at=now - timedelta(seconds=400),
            image_sync_status=None,
        )

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_starting_nodes()

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPED.value

    def test_mixed_nodes_partial_recovery(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """A mix of recoverable and non-recoverable (syncing) nodes should only recover eligible ones."""
        from app.tasks.stuck_nodes import check_stuck_starting_nodes

        now = _naive_utcnow()
        ns_recoverable = make_node_state(
            test_db,
            sample_lab.id,
            "R1",
            actual_state=NodeActualState.STARTING.value,
            starting_started_at=now - timedelta(seconds=400),
            image_sync_status=None,
        )
        ns_syncing = make_node_state(
            test_db,
            sample_lab.id,
            "R2",
            actual_state=NodeActualState.STARTING.value,
            starting_started_at=now - timedelta(seconds=400),
            image_sync_status="syncing",
        )

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_starting_nodes()

        test_db.refresh(ns_recoverable)
        test_db.refresh(ns_syncing)
        assert ns_recoverable.actual_state == NodeActualState.STOPPED.value
        assert ns_syncing.actual_state == NodeActualState.STARTING.value

    def test_completed_job_does_not_block_recovery(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """A completed job should not prevent recovery of stuck nodes."""
        from app.tasks.stuck_nodes import check_stuck_starting_nodes

        now = _naive_utcnow()
        ns = make_node_state(
            test_db,
            sample_lab.id,
            "R1",
            actual_state=NodeActualState.STARTING.value,
            starting_started_at=now - timedelta(seconds=400),
        )
        make_job(test_db, sample_lab.id, test_user.id, JobStatus.COMPLETED.value)

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(test_db)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_starting_nodes()

        test_db.refresh(ns)
        assert ns.actual_state == NodeActualState.STOPPED.value

    def test_exception_triggers_rollback(
        self, test_db: Session, sample_lab: models.Lab, test_user: models.User
    ):
        """Exceptions during starting node check should be caught and logged."""
        from app.tasks.stuck_nodes import check_stuck_starting_nodes

        mock_session = MagicMock()
        mock_session.query.side_effect = RuntimeError("DB error")
        mock_session.rollback = MagicMock()

        with patch("app.tasks.stuck_nodes.get_session", _fake_get_session(mock_session)):
            with patch(_UTCNOW_PATCH, _naive_utcnow):
                check_stuck_starting_nodes()

        mock_session.rollback.assert_called_once()