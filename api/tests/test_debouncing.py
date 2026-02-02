"""Tests for debouncing behavior in live node management.

Tests the NodeChangeDebouncer class in app/tasks/live_nodes.py which
coalesces rapid canvas changes before processing them.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.tasks.live_nodes import (
    NodeChangeDebouncer,
    DEBOUNCE_DELAY,
    get_debouncer,
    process_node_changes,
)


@pytest.fixture
def debouncer():
    """Create a fresh debouncer instance for each test."""
    return NodeChangeDebouncer()


@pytest.fixture
def mock_process_impl():
    """Mock the implementation function to verify calls."""
    with patch("app.tasks.live_nodes._process_node_changes_impl", new_callable=AsyncMock) as mock:
        yield mock


class TestNodeChangeDebouncer:
    """Tests for NodeChangeDebouncer class."""

    @pytest.mark.asyncio
    async def test_debounce_coalesces_rapid_changes(
        self,
        debouncer: NodeChangeDebouncer,
        mock_process_impl: AsyncMock,
    ):
        """Multiple rapid changes should be coalesced into a single batch."""
        lab_id = "test-lab"

        # Add multiple changes rapidly (without waiting for debounce)
        await debouncer.add_changes(lab_id, ["n1"], [])
        await debouncer.add_changes(lab_id, ["n2"], [])
        await debouncer.add_changes(lab_id, ["n3"], [])

        # Wait for debounce delay plus some buffer
        await asyncio.sleep(DEBOUNCE_DELAY + 0.1)

        # Should be called once with all accumulated changes
        mock_process_impl.assert_called_once()
        call_args = mock_process_impl.call_args
        assert call_args[0][0] == lab_id
        assert set(call_args[0][1]) == {"n1", "n2", "n3"}  # All adds coalesced

    @pytest.mark.asyncio
    async def test_debounce_processes_after_delay(
        self,
        debouncer: NodeChangeDebouncer,
        mock_process_impl: AsyncMock,
    ):
        """Changes should be processed after the debounce delay."""
        lab_id = "test-lab"

        await debouncer.add_changes(lab_id, ["n1"], [])

        # Immediately after adding, should not be processed yet
        mock_process_impl.assert_not_called()

        # Wait for debounce delay
        await asyncio.sleep(DEBOUNCE_DELAY + 0.1)

        # Now should be processed
        mock_process_impl.assert_called_once()

    @pytest.mark.asyncio
    async def test_debounce_resets_timer_on_new_change(
        self,
        debouncer: NodeChangeDebouncer,
        mock_process_impl: AsyncMock,
    ):
        """Adding new changes should reset the debounce timer."""
        lab_id = "test-lab"

        await debouncer.add_changes(lab_id, ["n1"], [])

        # Wait for half the debounce delay
        await asyncio.sleep(DEBOUNCE_DELAY * 0.4)

        # Add another change (this should reset the timer)
        await debouncer.add_changes(lab_id, ["n2"], [])

        # Wait another half - still shouldn't be processed
        await asyncio.sleep(DEBOUNCE_DELAY * 0.4)
        mock_process_impl.assert_not_called()

        # Wait for the full delay from the second change
        await asyncio.sleep(DEBOUNCE_DELAY * 0.8)

        # Now should be processed with both changes
        mock_process_impl.assert_called_once()
        call_args = mock_process_impl.call_args
        assert set(call_args[0][1]) == {"n1", "n2"}

    @pytest.mark.asyncio
    async def test_debounce_handles_concurrent_labs(
        self,
        debouncer: NodeChangeDebouncer,
        mock_process_impl: AsyncMock,
    ):
        """Different labs should be debounced independently."""
        lab1 = "lab-1"
        lab2 = "lab-2"

        # Add changes to both labs
        await debouncer.add_changes(lab1, ["n1"], [])
        await debouncer.add_changes(lab2, ["n2"], [])

        # Wait for debounce
        await asyncio.sleep(DEBOUNCE_DELAY + 0.1)

        # Should be called twice, once for each lab
        assert mock_process_impl.call_count == 2

        # Verify each lab was processed with correct changes
        call_args_list = mock_process_impl.call_args_list
        labs_processed = {call[0][0] for call in call_args_list}
        assert labs_processed == {lab1, lab2}

    @pytest.mark.asyncio
    async def test_debounce_accumulates_adds_and_removes(
        self,
        debouncer: NodeChangeDebouncer,
        mock_process_impl: AsyncMock,
    ):
        """Both adds and removes should be accumulated."""
        lab_id = "test-lab"

        removed_node_1 = {"node_id": "r1", "node_name": "router-1", "host_id": "h1"}
        removed_node_2 = {"node_id": "r2", "node_name": "router-2", "host_id": "h1"}

        await debouncer.add_changes(lab_id, ["n1"], [removed_node_1])
        await debouncer.add_changes(lab_id, ["n2"], [removed_node_2])

        await asyncio.sleep(DEBOUNCE_DELAY + 0.1)

        mock_process_impl.assert_called_once()
        call_args = mock_process_impl.call_args

        # Check adds
        assert set(call_args[0][1]) == {"n1", "n2"}

        # Check removes
        removed_names = {r["node_name"] for r in call_args[0][2]}
        assert removed_names == {"router-1", "router-2"}

    @pytest.mark.asyncio
    async def test_debounce_deduplicates_adds(
        self,
        debouncer: NodeChangeDebouncer,
        mock_process_impl: AsyncMock,
    ):
        """Duplicate node IDs in adds should be deduplicated."""
        lab_id = "test-lab"

        await debouncer.add_changes(lab_id, ["n1", "n2"], [])
        await debouncer.add_changes(lab_id, ["n1", "n3"], [])  # n1 is duplicate

        await asyncio.sleep(DEBOUNCE_DELAY + 0.1)

        mock_process_impl.assert_called_once()
        call_args = mock_process_impl.call_args

        # Should have unique node IDs
        assert set(call_args[0][1]) == {"n1", "n2", "n3"}

    @pytest.mark.asyncio
    async def test_debounce_deduplicates_removes_by_name(
        self,
        debouncer: NodeChangeDebouncer,
        mock_process_impl: AsyncMock,
    ):
        """Duplicate node names in removes should be deduplicated."""
        lab_id = "test-lab"

        removed_node = {"node_id": "r1", "node_name": "router-1", "host_id": "h1"}

        await debouncer.add_changes(lab_id, [], [removed_node])
        await debouncer.add_changes(lab_id, [], [removed_node])  # Same node again

        await asyncio.sleep(DEBOUNCE_DELAY + 0.1)

        mock_process_impl.assert_called_once()
        call_args = mock_process_impl.call_args

        # Should only have one remove
        assert len(call_args[0][2]) == 1

    @pytest.mark.asyncio
    async def test_debounce_no_changes_no_process(
        self,
        debouncer: NodeChangeDebouncer,
        mock_process_impl: AsyncMock,
    ):
        """Empty changes should not trigger processing."""
        lab_id = "test-lab"

        await debouncer.add_changes(lab_id, [], [])

        await asyncio.sleep(DEBOUNCE_DELAY + 0.1)

        # Should not call impl if there are no actual changes
        mock_process_impl.assert_not_called()

    @pytest.mark.asyncio
    async def test_debounce_task_cancellation_handled(
        self,
        debouncer: NodeChangeDebouncer,
        mock_process_impl: AsyncMock,
    ):
        """Cancelled tasks should be handled gracefully."""
        lab_id = "test-lab"

        # Add first change
        await debouncer.add_changes(lab_id, ["n1"], [])

        # Immediately add another (cancels the first debounce task)
        await debouncer.add_changes(lab_id, ["n2"], [])

        # Should not raise any exceptions
        await asyncio.sleep(DEBOUNCE_DELAY + 0.1)

        # Final call should include both changes
        mock_process_impl.assert_called_once()


class TestGetDebouncer:
    """Tests for singleton debouncer pattern."""

    def test_get_debouncer_returns_same_instance(self):
        """get_debouncer should return the same instance."""
        # Reset the global debouncer
        with patch("app.tasks.live_nodes._debouncer", None):
            d1 = get_debouncer()
            d2 = get_debouncer()
            assert d1 is d2


class TestProcessNodeChanges:
    """Tests for the process_node_changes entry point."""

    @pytest.mark.asyncio
    async def test_process_node_changes_uses_debouncer(self):
        """process_node_changes should use the global debouncer."""
        mock_debouncer = MagicMock()
        mock_debouncer.add_changes = AsyncMock()

        with patch("app.tasks.live_nodes.get_debouncer", return_value=mock_debouncer):
            await process_node_changes("test-lab", ["n1"], [{"node_name": "r1"}])

        mock_debouncer.add_changes.assert_called_once_with(
            "test-lab",
            ["n1"],
            [{"node_name": "r1"}],
        )


class TestDebounceEdgeCases:
    """Tests for edge cases in debouncing."""

    @pytest.mark.asyncio
    async def test_rapid_lab_switching(self, mock_process_impl: AsyncMock):
        """Rapid changes to multiple labs should all be processed."""
        debouncer = NodeChangeDebouncer()

        # Simulate rapid lab switching scenario
        for i in range(5):
            lab_id = f"lab-{i}"
            await debouncer.add_changes(lab_id, [f"n{i}"], [])

        await asyncio.sleep(DEBOUNCE_DELAY + 0.2)

        # All 5 labs should be processed
        assert mock_process_impl.call_count == 5

    @pytest.mark.asyncio
    async def test_interleaved_changes(self, mock_process_impl: AsyncMock):
        """Interleaved changes between labs should be handled correctly."""
        debouncer = NodeChangeDebouncer()

        # Interleave changes between two labs
        await debouncer.add_changes("lab-a", ["a1"], [])
        await debouncer.add_changes("lab-b", ["b1"], [])
        await debouncer.add_changes("lab-a", ["a2"], [])
        await debouncer.add_changes("lab-b", ["b2"], [])

        await asyncio.sleep(DEBOUNCE_DELAY + 0.1)

        assert mock_process_impl.call_count == 2

        # Find calls by lab
        calls_by_lab = {call[0][0]: call[0][1] for call in mock_process_impl.call_args_list}
        assert set(calls_by_lab["lab-a"]) == {"a1", "a2"}
        assert set(calls_by_lab["lab-b"]) == {"b1", "b2"}

    @pytest.mark.asyncio
    async def test_empty_removes_not_duplicated(self, mock_process_impl: AsyncMock):
        """Empty node names in removes should be handled."""
        debouncer = NodeChangeDebouncer()

        # Node with empty name (edge case)
        removed_node = {"node_id": "r1", "node_name": "", "host_id": "h1"}

        await debouncer.add_changes("test-lab", [], [removed_node])

        await asyncio.sleep(DEBOUNCE_DELAY + 0.1)

        mock_process_impl.assert_called_once()
        call_args = mock_process_impl.call_args
        assert len(call_args[0][2]) == 1
