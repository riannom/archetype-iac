"""Tests for app/tasks/health.py - Agent health monitoring background task."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestAgentHealthMonitor:
    """Tests for the agent_health_monitor background task."""

    @pytest.mark.asyncio
    async def test_marks_stale_agents_offline(self):
        """Should mark stale agents as offline periodically."""
        from app.tasks.health import agent_health_monitor
        from contextlib import contextmanager

        mock_session = MagicMock()
        marked_offline = ["agent-1", "agent-2"]

        @contextmanager
        def fake_get_session():
            yield mock_session

        with patch("app.tasks.health.get_session", fake_get_session):
            with patch("app.tasks.health.agent_client.update_stale_agents", new_callable=AsyncMock) as mock_update:
                mock_update.return_value = marked_offline

                # Use side_effect to stop after first iteration
                with patch("app.tasks.health.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                    call_count = 0
                    async def sleep_and_cancel(seconds):
                        nonlocal call_count
                        call_count += 1
                        if call_count > 1:
                            raise asyncio.CancelledError()
                    mock_sleep.side_effect = sleep_and_cancel

                    await agent_health_monitor()

                    mock_update.assert_called_once_with(mock_session)

    @pytest.mark.asyncio
    async def test_handles_no_stale_agents(self):
        """Should handle case when no agents are stale."""
        from app.tasks.health import agent_health_monitor
        from contextlib import contextmanager

        mock_session = MagicMock()

        @contextmanager
        def fake_get_session():
            yield mock_session

        with patch("app.tasks.health.get_session", fake_get_session):
            with patch("app.tasks.health.agent_client.update_stale_agents", new_callable=AsyncMock) as mock_update:
                mock_update.return_value = []  # No stale agents

                with patch("app.tasks.health.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                    call_count = 0
                    async def sleep_and_cancel(seconds):
                        nonlocal call_count
                        call_count += 1
                        if call_count > 1:
                            raise asyncio.CancelledError()
                    mock_sleep.side_effect = sleep_and_cancel

                    await agent_health_monitor()

                    mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_stops_on_cancelled_error(self):
        """Should stop gracefully when cancelled."""
        from app.tasks.health import agent_health_monitor

        with patch("app.tasks.health.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_sleep.side_effect = asyncio.CancelledError()

            # Should not raise, just return
            await agent_health_monitor()

    @pytest.mark.asyncio
    async def test_continues_on_general_exception(self):
        """Should continue running after handling an exception."""
        from app.tasks.health import agent_health_monitor
        from contextlib import contextmanager

        mock_session = MagicMock()

        @contextmanager
        def fake_get_session():
            yield mock_session

        with patch("app.tasks.health.get_session", fake_get_session):
            with patch("app.tasks.health.agent_client.update_stale_agents", new_callable=AsyncMock) as mock_update:
                call_count = 0
                async def update_with_error(session):
                    nonlocal call_count
                    call_count += 1
                    if call_count == 1:
                        raise Exception("Database error")
                    return []
                mock_update.side_effect = update_with_error

                with patch("app.tasks.health.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                    sleep_count = 0
                    async def sleep_and_cancel(seconds):
                        nonlocal sleep_count
                        sleep_count += 1
                        if sleep_count > 2:
                            raise asyncio.CancelledError()
                    mock_sleep.side_effect = sleep_and_cancel

                    await agent_health_monitor()

                    # Should have continued after first error
                    assert call_count >= 2

    @pytest.mark.asyncio
    async def test_closes_session_after_each_iteration(self):
        """Should properly manage session lifecycle via get_session context manager."""
        from app.tasks.health import agent_health_monitor
        from contextlib import contextmanager

        session_entered = False
        session_exited = False

        @contextmanager
        def tracking_get_session():
            nonlocal session_entered, session_exited
            session_entered = True
            mock_session = MagicMock()
            try:
                yield mock_session
            finally:
                session_exited = True

        with patch("app.tasks.health.get_session", tracking_get_session):
            with patch("app.tasks.health.agent_client.update_stale_agents", new_callable=AsyncMock) as mock_update:
                mock_update.return_value = []

                with patch("app.tasks.health.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                    call_count = 0
                    async def sleep_and_cancel(seconds):
                        nonlocal call_count
                        call_count += 1
                        if call_count > 1:
                            raise asyncio.CancelledError()
                    mock_sleep.side_effect = sleep_and_cancel

                    await agent_health_monitor()

                    assert session_entered, "get_session context manager was never entered"
                    assert session_exited, "get_session context manager was never exited (session not cleaned up)"
