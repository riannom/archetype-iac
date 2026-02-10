"""Tests for app/tasks/cleanup_handler.py - Event-driven cleanup handler.

Phase B.4: Tests for dispatch routing, retry on transient failure,
error isolation, and idempotent cleanup operations.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.events.cleanup_events import CleanupEvent, CleanupEventType
from app.tasks.cleanup_base import CleanupResult
from app.tasks.cleanup_handler import CleanupEventHandler, _cleanup_dirty_event


# ---------------------------------------------------------------------------
# Dispatch Routing Tests
# ---------------------------------------------------------------------------

class TestDispatchRouting:
    """Verify events are routed to the correct handler methods."""

    @pytest.fixture(autouse=True)
    def _reset_dirty_flag(self):
        """Clear the dirty flag before each test."""
        _cleanup_dirty_event.clear()

    @pytest.mark.asyncio
    async def test_lab_deleted_dispatches_to_handler(self):
        """LAB_DELETED event calls _handle_lab_deleted."""
        handler = CleanupEventHandler()
        handler._handle_lab_deleted = AsyncMock()

        event = CleanupEvent(event_type=CleanupEventType.LAB_DELETED, lab_id="lab-1")
        await handler._process_message(event.to_json())

        handler._handle_lab_deleted.assert_called_once()
        args = handler._handle_lab_deleted.call_args[0]
        assert args[0].lab_id == "lab-1"

    @pytest.mark.asyncio
    async def test_node_removed_dispatches_to_handler(self):
        """NODE_REMOVED event calls _handle_node_removed."""
        handler = CleanupEventHandler()
        handler._handle_node_removed = AsyncMock()

        event = CleanupEvent(
            event_type=CleanupEventType.NODE_REMOVED,
            lab_id="lab-1", node_name="r1",
        )
        await handler._process_message(event.to_json())

        handler._handle_node_removed.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_offline_dispatches_to_handler(self):
        """AGENT_OFFLINE event calls _handle_agent_offline."""
        handler = CleanupEventHandler()
        handler._handle_agent_offline = AsyncMock()

        event = CleanupEvent(
            event_type=CleanupEventType.AGENT_OFFLINE, agent_id="agent-1",
        )
        await handler._process_message(event.to_json())

        handler._handle_agent_offline.assert_called_once()

    @pytest.mark.asyncio
    async def test_deploy_finished_dispatches_to_handler(self):
        """DEPLOY_FINISHED event calls _handle_deploy_finished."""
        handler = CleanupEventHandler()
        handler._handle_deploy_finished = AsyncMock()

        event = CleanupEvent(
            event_type=CleanupEventType.DEPLOY_FINISHED, lab_id="lab-1",
        )
        await handler._process_message(event.to_json())

        handler._handle_deploy_finished.assert_called_once()

    @pytest.mark.asyncio
    async def test_destroy_finished_dispatches_to_handler(self):
        """DESTROY_FINISHED event calls _handle_destroy_finished."""
        handler = CleanupEventHandler()
        handler._handle_destroy_finished = AsyncMock()

        event = CleanupEvent(
            event_type=CleanupEventType.DESTROY_FINISHED, lab_id="lab-1",
        )
        await handler._process_message(event.to_json())

        handler._handle_destroy_finished.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_json_logged_and_skipped(self):
        """Invalid JSON payload is logged and doesn't crash."""
        handler = CleanupEventHandler()

        # Should not raise
        await handler._process_message("not valid json {{{")

    @pytest.mark.asyncio
    async def test_successful_handler_sets_dirty_flag(self):
        """Successful handler execution sets the cleanup dirty flag."""
        handler = CleanupEventHandler()
        handler._handle_lab_deleted = AsyncMock()

        event = CleanupEvent(event_type=CleanupEventType.LAB_DELETED, lab_id="lab-1")
        await handler._process_message(event.to_json())

        assert _cleanup_dirty_event.is_set()


# ---------------------------------------------------------------------------
# Handler Behavior Tests
# ---------------------------------------------------------------------------

class TestHandlerBehavior:
    """Test individual handler methods dispatch to correct cleanup functions."""

    @pytest.mark.asyncio
    async def test_lab_deleted_runs_four_cleanup_tasks(self):
        """LAB_DELETED should run workspace, config snapshots, placements, and vxlan ports cleanup."""
        handler = CleanupEventHandler()
        event = CleanupEvent(event_type=CleanupEventType.LAB_DELETED, lab_id="lab-1")

        with patch("app.tasks.cleanup_handler._runner") as mock_runner:
            mock_runner.run_task = AsyncMock(
                return_value=CleanupResult(task_name="test", deleted=1),
            )

            await handler._handle_lab_deleted(event)

            assert mock_runner.run_task.call_count == 4

    @pytest.mark.asyncio
    async def test_lab_deleted_skips_when_no_lab_id(self):
        """LAB_DELETED with no lab_id returns early without cleanup."""
        handler = CleanupEventHandler()
        event = CleanupEvent(event_type=CleanupEventType.LAB_DELETED, lab_id=None)

        with patch("app.tasks.cleanup_handler._runner") as mock_runner:
            mock_runner.run_task = AsyncMock()
            await handler._handle_lab_deleted(event)
            mock_runner.run_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_node_removed_cleans_placement(self):
        """NODE_REMOVED should clean up the node's placement."""
        handler = CleanupEventHandler()
        event = CleanupEvent(
            event_type=CleanupEventType.NODE_REMOVED,
            lab_id="lab-1", node_name="r1",
        )

        with patch("app.tasks.cleanup_handler._runner") as mock_runner:
            mock_runner.run_task = AsyncMock(
                return_value=CleanupResult(task_name="test", deleted=1),
            )
            await handler._handle_node_removed(event)
            mock_runner.run_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_node_removed_skips_when_missing_fields(self):
        """NODE_REMOVED with missing lab_id or node_name returns early."""
        handler = CleanupEventHandler()

        with patch("app.tasks.cleanup_handler._runner") as mock_runner:
            mock_runner.run_task = AsyncMock()

            # Missing lab_id
            event = CleanupEvent(
                event_type=CleanupEventType.NODE_REMOVED,
                lab_id=None, node_name="r1",
            )
            await handler._handle_node_removed(event)

            # Missing node_name
            event = CleanupEvent(
                event_type=CleanupEventType.NODE_REMOVED,
                lab_id="lab-1", node_name=None,
            )
            await handler._handle_node_removed(event)

            mock_runner.run_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_agent_offline_cleans_image_hosts(self):
        """AGENT_OFFLINE should clean up image host records."""
        handler = CleanupEventHandler()
        event = CleanupEvent(
            event_type=CleanupEventType.AGENT_OFFLINE, agent_id="agent-1",
        )

        with patch("app.tasks.cleanup_handler._runner") as mock_runner:
            mock_runner.run_task = AsyncMock(
                return_value=CleanupResult(task_name="test", deleted=2),
            )
            await handler._handle_agent_offline(event)
            mock_runner.run_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_offline_skips_when_no_agent_id(self):
        """AGENT_OFFLINE with no agent_id returns early."""
        handler = CleanupEventHandler()
        event = CleanupEvent(
            event_type=CleanupEventType.AGENT_OFFLINE, agent_id=None,
        )

        with patch("app.tasks.cleanup_handler._runner") as mock_runner:
            mock_runner.run_task = AsyncMock()
            await handler._handle_agent_offline(event)
            mock_runner.run_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_destroy_finished_cleans_placements_and_vxlan(self):
        """DESTROY_FINISHED should clean up orphaned placements and vxlan ports."""
        handler = CleanupEventHandler()
        event = CleanupEvent(
            event_type=CleanupEventType.DESTROY_FINISHED, lab_id="lab-1",
        )

        with patch("app.tasks.cleanup_handler._runner") as mock_runner:
            mock_runner.run_task = AsyncMock(
                return_value=CleanupResult(task_name="test", deleted=3),
            )
            await handler._handle_destroy_finished(event)
            assert mock_runner.run_task.call_count == 2


# ---------------------------------------------------------------------------
# Retry and Error Isolation Tests
# ---------------------------------------------------------------------------

class TestRetryBehavior:
    """Test single retry on transient failure and error isolation."""

    @pytest.fixture(autouse=True)
    def _reset_dirty_flag(self):
        _cleanup_dirty_event.clear()

    @pytest.mark.asyncio
    async def test_retry_on_first_failure(self):
        """Handler retries once on first failure."""
        handler = CleanupEventHandler()
        call_count = 0

        async def handler_fails_once(event):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient error")

        handler._handle_lab_deleted = handler_fails_once

        event = CleanupEvent(event_type=CleanupEventType.LAB_DELETED, lab_id="lab-1")
        await handler._process_message(event.to_json())

        assert call_count == 2  # Called twice: first fail, then retry
        assert _cleanup_dirty_event.is_set()  # Retry succeeded

    @pytest.mark.asyncio
    async def test_both_attempts_fail_logs_error(self):
        """When both attempts fail, error is logged and dirty flag NOT set."""
        handler = CleanupEventHandler()

        async def always_fails(event):
            raise RuntimeError("persistent error")

        handler._handle_lab_deleted = always_fails

        event = CleanupEvent(event_type=CleanupEventType.LAB_DELETED, lab_id="lab-1")
        await handler._process_message(event.to_json())

        assert not _cleanup_dirty_event.is_set()

    @pytest.mark.asyncio
    async def test_handler_error_doesnt_crash_process_message(self):
        """Exception in handler doesn't propagate to caller."""
        handler = CleanupEventHandler()

        async def raise_error(event):
            raise ValueError("handler exploded")

        handler._handle_node_removed = raise_error

        event = CleanupEvent(
            event_type=CleanupEventType.NODE_REMOVED,
            lab_id="lab-1", node_name="r1",
        )
        # Should not raise
        await handler._process_message(event.to_json())


# ---------------------------------------------------------------------------
# Idempotency Tests
# ---------------------------------------------------------------------------

class TestIdempotency:
    """Test that cleanup operations are safe to run multiple times."""

    @pytest.mark.asyncio
    async def test_duplicate_lab_deleted_safe(self):
        """Second LAB_DELETED for same lab is a no-op (workspace already gone)."""
        handler = CleanupEventHandler()

        with patch("app.tasks.cleanup_handler._runner") as mock_runner:
            mock_runner.run_task = AsyncMock(
                return_value=CleanupResult(task_name="test", deleted=0),
            )

            event = CleanupEvent(event_type=CleanupEventType.LAB_DELETED, lab_id="lab-1")
            # Run twice
            await handler._handle_lab_deleted(event)
            await handler._handle_lab_deleted(event)

            # Both calls succeed without error
            assert mock_runner.run_task.call_count == 8  # 4 tasks × 2 calls

    @pytest.mark.asyncio
    async def test_duplicate_node_removed_safe(self):
        """Second NODE_REMOVED for same node is a no-op (placement already gone)."""
        handler = CleanupEventHandler()

        with patch("app.tasks.cleanup_handler._runner") as mock_runner:
            mock_runner.run_task = AsyncMock(
                return_value=CleanupResult(task_name="test", deleted=0),
            )

            event = CleanupEvent(
                event_type=CleanupEventType.NODE_REMOVED,
                lab_id="lab-1", node_name="r1",
            )
            # Run twice
            await handler._handle_node_removed(event)
            await handler._handle_node_removed(event)

            assert mock_runner.run_task.call_count == 2  # 1 task × 2 calls

    @pytest.mark.asyncio
    async def test_cleanup_workspace_idempotent(self, tmp_path):
        """_cleanup_lab_workspace is safe when workspace doesn't exist."""
        from app.tasks.cleanup_handler import _cleanup_lab_workspace

        with patch("app.tasks.cleanup_handler.lab_workspace", return_value=tmp_path / "nonexistent"):
            result = await _cleanup_lab_workspace("lab-1")
            assert result.deleted == 0
            assert result.success

    @pytest.mark.asyncio
    async def test_cleanup_workspace_removes_existing(self, tmp_path):
        """_cleanup_lab_workspace removes existing workspace directory."""
        from app.tasks.cleanup_handler import _cleanup_lab_workspace

        workspace = tmp_path / "lab-workspace"
        workspace.mkdir()
        (workspace / "topology.yml").write_text("test")

        with patch("app.tasks.cleanup_handler.lab_workspace", return_value=workspace):
            result = await _cleanup_lab_workspace("lab-1")
            assert result.deleted == 1
            assert not workspace.exists()


# ---------------------------------------------------------------------------
# CleanupEvent Serialization Tests
# ---------------------------------------------------------------------------

class TestCleanupEventSerialization:
    """Test CleanupEvent to_json/from_json round-trip."""

    def test_round_trip_with_all_fields(self):
        """Event with all fields serializes and deserializes correctly."""
        event = CleanupEvent(
            event_type=CleanupEventType.LAB_DELETED,
            lab_id="lab-1",
            node_name="r1",
            agent_id="agent-1",
            old_agent_id="agent-0",
            job_id="job-1",
            job_action="up",
            metadata={"key": "value"},
        )
        json_str = event.to_json()
        restored = CleanupEvent.from_json(json_str)

        assert restored.event_type == CleanupEventType.LAB_DELETED
        assert restored.lab_id == "lab-1"
        assert restored.node_name == "r1"
        assert restored.agent_id == "agent-1"
        assert restored.metadata == {"key": "value"}

    def test_round_trip_minimal(self):
        """Event with only event_type serializes correctly."""
        event = CleanupEvent(event_type=CleanupEventType.JOB_COMPLETED)
        restored = CleanupEvent.from_json(event.to_json())
        assert restored.event_type == CleanupEventType.JOB_COMPLETED
        assert restored.lab_id is None

    def test_from_json_invalid_type_raises(self):
        """Invalid event_type in JSON raises ValueError."""
        import json
        bad = json.dumps({"event_type": "nonexistent_type"})
        with pytest.raises(ValueError):
            CleanupEvent.from_json(bad)
