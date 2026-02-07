"""Tests for job utility functions (utils/job.py).

This module tests:
- Job timeout calculation (get_job_timeout, get_job_timeout_at)
- Stuck job detection (is_job_stuck)
- Timeout window checking (is_job_within_timeout)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.config import settings
from app.utils.job import (
    get_job_timeout,
    get_job_timeout_at,
    is_job_stuck,
    is_job_within_timeout,
)


class TestGetJobTimeout:
    """Tests for get_job_timeout function."""

    def test_up_action_returns_deploy_timeout(self):
        """Action 'up' returns deploy timeout."""
        result = get_job_timeout("up")
        assert result == settings.job_timeout_deploy

    def test_down_action_returns_destroy_timeout(self):
        """Action 'down' returns destroy timeout."""
        result = get_job_timeout("down")
        assert result == settings.job_timeout_destroy

    def test_sync_action_returns_sync_timeout(self):
        """Sync action returns sync timeout."""
        result = get_job_timeout("sync:node:router1")
        assert result == settings.job_timeout_sync

    def test_sync_prefix_matches(self):
        """Any action starting with 'sync:' uses sync timeout."""
        assert get_job_timeout("sync:lab") == settings.job_timeout_sync
        assert get_job_timeout("sync:all") == settings.job_timeout_sync
        assert get_job_timeout("sync:image:ceos") == settings.job_timeout_sync

    def test_node_action_returns_node_timeout(self):
        """Node actions return node timeout."""
        assert get_job_timeout("node:start:router1") == settings.job_timeout_node
        assert get_job_timeout("node:stop:router1") == settings.job_timeout_node

    def test_node_prefix_matches(self):
        """Any action starting with 'node:' uses node timeout."""
        assert get_job_timeout("node:restart:switch1") == settings.job_timeout_node
        assert get_job_timeout("node:status:spine1") == settings.job_timeout_node

    def test_unknown_action_returns_deploy_timeout(self):
        """Unknown actions default to deploy timeout (longest)."""
        result = get_job_timeout("unknown_action")
        assert result == settings.job_timeout_deploy

    def test_empty_action_returns_deploy_timeout(self):
        """Empty action defaults to deploy timeout."""
        result = get_job_timeout("")
        assert result == settings.job_timeout_deploy

    def test_partial_matches_use_defaults(self):
        """Actions that partially match prefixes but don't start with them use default."""
        # "syncdata" doesn't start with "sync:"
        assert get_job_timeout("syncdata") == settings.job_timeout_deploy
        # "nodeinfo" doesn't start with "node:"
        assert get_job_timeout("nodeinfo") == settings.job_timeout_deploy

    def test_case_sensitive_matching(self):
        """Action matching is case-sensitive."""
        # Uppercase "UP" doesn't match "up"
        assert get_job_timeout("UP") == settings.job_timeout_deploy
        assert get_job_timeout("SYNC:node") == settings.job_timeout_deploy
        assert get_job_timeout("Node:start") == settings.job_timeout_deploy


class TestGetJobTimeoutAt:
    """Tests for get_job_timeout_at function."""

    def test_returns_none_when_started_at_is_none(self):
        """Returns None when job hasn't started."""
        result = get_job_timeout_at("up", None)
        assert result is None

    def test_calculates_timeout_for_up_action(self):
        """Calculates timeout correctly for 'up' action."""
        started_at = datetime.now(timezone.utc)
        result = get_job_timeout_at("up", started_at)
        expected = started_at + timedelta(seconds=settings.job_timeout_deploy)
        assert result == expected

    def test_calculates_timeout_for_down_action(self):
        """Calculates timeout correctly for 'down' action."""
        started_at = datetime.now(timezone.utc)
        result = get_job_timeout_at("down", started_at)
        expected = started_at + timedelta(seconds=settings.job_timeout_destroy)
        assert result == expected

    def test_calculates_timeout_for_sync_action(self):
        """Calculates timeout correctly for sync action."""
        started_at = datetime.now(timezone.utc)
        result = get_job_timeout_at("sync:node:r1", started_at)
        expected = started_at + timedelta(seconds=settings.job_timeout_sync)
        assert result == expected

    def test_calculates_timeout_for_node_action(self):
        """Calculates timeout correctly for node action."""
        started_at = datetime.now(timezone.utc)
        result = get_job_timeout_at("node:start:r1", started_at)
        expected = started_at + timedelta(seconds=settings.job_timeout_node)
        assert result == expected

    def test_handles_naive_datetime_as_utc(self):
        """Naive datetime is treated as UTC."""
        # Create a naive datetime
        naive_started_at = datetime(2024, 1, 15, 12, 0, 0)
        result = get_job_timeout_at("up", naive_started_at)

        # Result should be timezone-aware
        assert result is not None
        assert result.tzinfo is not None

        # Should have added the timeout
        expected_naive = naive_started_at.replace(tzinfo=timezone.utc) + timedelta(seconds=settings.job_timeout_deploy)
        assert result == expected_naive

    def test_preserves_timezone_aware_datetime(self):
        """Timezone-aware datetime is preserved."""
        started_at = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = get_job_timeout_at("up", started_at)

        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_returns_datetime_in_future(self):
        """Result is always in the future relative to started_at."""
        started_at = datetime.now(timezone.utc)
        result = get_job_timeout_at("up", started_at)

        assert result is not None
        assert result > started_at


class TestIsJobStuck:
    """Tests for is_job_stuck function."""

    def test_running_job_within_timeout_not_stuck(self):
        """Running job within timeout is not stuck."""
        started_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        result = is_job_stuck("up", "running", started_at)
        assert result is False

    def test_running_job_past_timeout_is_stuck(self):
        """Running job past its timeout is stuck."""
        # Start job longer ago than deploy timeout
        started_at = datetime.now(timezone.utc) - timedelta(seconds=settings.job_timeout_deploy + 10)
        result = is_job_stuck("up", "running", started_at)
        assert result is True

    def test_running_job_exactly_at_timeout_boundary(self):
        """Running job at timeout boundary behavior.

        Note: Due to the time between creating started_at and the function calling
        datetime.now(), testing exact boundaries is unreliable. This test verifies
        the behavior just before the timeout threshold.
        """
        # Test just before timeout - should not be stuck
        started_at = datetime.now(timezone.utc) - timedelta(seconds=settings.job_timeout_deploy - 5)
        result = is_job_stuck("up", "running", started_at)
        assert result is False

    def test_running_job_just_past_timeout_is_stuck(self):
        """Running job just past timeout is stuck."""
        started_at = datetime.now(timezone.utc) - timedelta(seconds=settings.job_timeout_deploy + 1)
        result = is_job_stuck("up", "running", started_at)
        assert result is True

    def test_running_job_without_started_at_not_stuck(self):
        """Running job without started_at is not considered stuck."""
        result = is_job_stuck("up", "running", None)
        assert result is False

    def test_queued_job_within_timeout_not_stuck(self):
        """Queued job within 2 minutes is not stuck."""
        created_at = datetime.now(timezone.utc) - timedelta(seconds=60)
        result = is_job_stuck("up", "queued", None, created_at)
        assert result is False

    def test_queued_job_past_two_minutes_is_stuck(self):
        """Queued job past 2 minutes is stuck."""
        created_at = datetime.now(timezone.utc) - timedelta(minutes=3)
        result = is_job_stuck("up", "queued", None, created_at)
        assert result is True

    def test_queued_job_just_before_two_minutes_not_stuck(self):
        """Queued job just before 2 minute boundary is not stuck.

        Note: Due to the time between creating created_at and the function calling
        datetime.now(), testing exact boundaries is unreliable. This test verifies
        the behavior just before the timeout threshold.
        """
        created_at = datetime.now(timezone.utc) - timedelta(seconds=115)  # 5 sec before 2 min
        result = is_job_stuck("up", "queued", None, created_at)
        assert result is False

    def test_queued_job_without_created_at_not_stuck(self):
        """Queued job without created_at is not considered stuck."""
        result = is_job_stuck("up", "queued", None, None)
        assert result is False

    def test_completed_job_not_stuck(self):
        """Completed job is never stuck."""
        old_time = datetime.now(timezone.utc) - timedelta(hours=24)
        result = is_job_stuck("up", "completed", old_time, old_time)
        assert result is False

    def test_failed_job_not_stuck(self):
        """Failed job is never stuck."""
        old_time = datetime.now(timezone.utc) - timedelta(hours=24)
        result = is_job_stuck("up", "failed", old_time, old_time)
        assert result is False

    def test_cancelled_job_not_stuck(self):
        """Cancelled job is never stuck."""
        old_time = datetime.now(timezone.utc) - timedelta(hours=24)
        result = is_job_stuck("up", "cancelled", old_time, old_time)
        assert result is False

    def test_different_action_timeouts(self):
        """Different actions have different timeout thresholds."""
        # Node action has shorter timeout
        node_timeout = settings.job_timeout_node
        just_past_node_timeout = datetime.now(timezone.utc) - timedelta(seconds=node_timeout + 10)

        # Past node timeout but within deploy timeout
        assert is_job_stuck("node:start:r1", "running", just_past_node_timeout) is True

        # Not past deploy timeout
        if node_timeout + 10 < settings.job_timeout_deploy:
            assert is_job_stuck("up", "running", just_past_node_timeout) is False

    def test_naive_started_at_handled_as_utc(self):
        """Naive started_at datetime is handled as UTC."""
        # Create a naive datetime far in the past
        naive_old = datetime.now() - timedelta(hours=1)  # Naive datetime
        result = is_job_stuck("up", "running", naive_old)
        assert result is True

    def test_naive_created_at_handled_as_utc(self):
        """Naive created_at datetime is handled as UTC."""
        naive_old = datetime.now() - timedelta(minutes=10)  # Naive datetime
        result = is_job_stuck("up", "queued", None, naive_old)
        assert result is True


class TestIsJobWithinTimeout:
    """Tests for is_job_within_timeout function."""

    def test_running_job_within_timeout_returns_true(self):
        """Running job within timeout returns True."""
        started_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        result = is_job_within_timeout("up", "running", started_at)
        assert result is True

    def test_running_job_past_timeout_with_grace_returns_true(self):
        """Running job past timeout but within grace period returns True."""
        # Past timeout but within grace period
        past_timeout = settings.job_timeout_deploy + settings.job_stuck_grace_period // 2
        started_at = datetime.now(timezone.utc) - timedelta(seconds=past_timeout)
        result = is_job_within_timeout("up", "running", started_at)
        assert result is True

    def test_running_job_past_grace_period_returns_false(self):
        """Running job past timeout + grace period returns False."""
        total_wait = settings.job_timeout_deploy + settings.job_stuck_grace_period + 10
        started_at = datetime.now(timezone.utc) - timedelta(seconds=total_wait)
        result = is_job_within_timeout("up", "running", started_at)
        assert result is False

    def test_running_job_just_before_deadline_returns_true(self):
        """Running job just before deadline returns True.

        Note: Due to the time between creating started_at and the function calling
        datetime.now(), testing exact boundaries is unreliable. This test verifies
        the behavior just before the deadline.
        """
        total_wait = settings.job_timeout_deploy + settings.job_stuck_grace_period - 5
        started_at = datetime.now(timezone.utc) - timedelta(seconds=total_wait)
        result = is_job_within_timeout("up", "running", started_at)
        assert result is True

    def test_running_job_without_started_at_returns_true(self):
        """Running job without started_at returns True (benefit of doubt)."""
        result = is_job_within_timeout("up", "running", None)
        assert result is True

    def test_queued_job_within_timeout_returns_true(self):
        """Queued job within 2 minutes returns True."""
        created_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        result = is_job_within_timeout("up", "queued", None, created_at)
        assert result is True

    def test_queued_job_within_grace_period_returns_true(self):
        """Queued job past 2 min but within grace period returns True."""
        past_queue_timeout = 120 + settings.job_stuck_grace_period // 2
        created_at = datetime.now(timezone.utc) - timedelta(seconds=past_queue_timeout)
        result = is_job_within_timeout("up", "queued", None, created_at)
        assert result is True

    def test_queued_job_past_grace_period_returns_false(self):
        """Queued job past 2 min + grace period returns False."""
        total_wait = 120 + settings.job_stuck_grace_period + 10
        created_at = datetime.now(timezone.utc) - timedelta(seconds=total_wait)
        result = is_job_within_timeout("up", "queued", None, created_at)
        assert result is False

    def test_queued_job_without_created_at_returns_true(self):
        """Queued job without created_at returns True (benefit of doubt)."""
        result = is_job_within_timeout("up", "queued", None, None)
        assert result is True

    def test_completed_job_returns_false(self):
        """Completed job returns False (not in active window)."""
        started_at = datetime.now(timezone.utc)
        result = is_job_within_timeout("up", "completed", started_at)
        assert result is False

    def test_failed_job_returns_false(self):
        """Failed job returns False (not in active window)."""
        started_at = datetime.now(timezone.utc)
        result = is_job_within_timeout("up", "failed", started_at)
        assert result is False

    def test_cancelled_job_returns_false(self):
        """Cancelled job returns False (not in active window)."""
        started_at = datetime.now(timezone.utc)
        result = is_job_within_timeout("up", "cancelled", started_at)
        assert result is False

    def test_different_action_timeouts(self):
        """Different actions have different deadline calculations."""
        # Node action has shorter timeout
        node_deadline = settings.job_timeout_node + settings.job_stuck_grace_period
        deploy_deadline = settings.job_timeout_deploy + settings.job_stuck_grace_period

        # Time between node and deploy deadlines
        mid_time = (node_deadline + deploy_deadline) // 2
        started_at = datetime.now(timezone.utc) - timedelta(seconds=mid_time)

        # Node action should be past deadline
        if mid_time > node_deadline:
            assert is_job_within_timeout("node:start:r1", "running", started_at) is False

        # Deploy action should still be within deadline
        if mid_time < deploy_deadline:
            assert is_job_within_timeout("up", "running", started_at) is True

    def test_naive_datetime_handled_as_utc(self):
        """Naive datetime is handled as UTC (replace labels it as UTC without conversion)."""
        # Use UTC-based naive datetime to avoid local timezone offset issues
        naive_recent = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=10)
        result = is_job_within_timeout("up", "running", naive_recent)
        assert result is True


class TestJobTimeoutWithMockedSettings:
    """Tests with mocked settings to verify timeout calculations."""

    def test_custom_deploy_timeout(self):
        """Custom deploy timeout is used correctly."""
        with patch.object(settings, "job_timeout_deploy", 100):
            assert get_job_timeout("up") == 100

    def test_custom_destroy_timeout(self):
        """Custom destroy timeout is used correctly."""
        with patch.object(settings, "job_timeout_destroy", 50):
            assert get_job_timeout("down") == 50

    def test_custom_sync_timeout(self):
        """Custom sync timeout is used correctly."""
        with patch.object(settings, "job_timeout_sync", 75):
            assert get_job_timeout("sync:lab") == 75

    def test_custom_node_timeout(self):
        """Custom node timeout is used correctly."""
        with patch.object(settings, "job_timeout_node", 30):
            assert get_job_timeout("node:start:r1") == 30

    def test_stuck_detection_with_custom_timeout(self):
        """Stuck detection works with custom timeout values."""
        with patch.object(settings, "job_timeout_deploy", 60):
            # Job started 70 seconds ago with 60 second timeout
            started_at = datetime.now(timezone.utc) - timedelta(seconds=70)
            assert is_job_stuck("up", "running", started_at) is True

            # Job started 50 seconds ago with 60 second timeout
            started_at = datetime.now(timezone.utc) - timedelta(seconds=50)
            assert is_job_stuck("up", "running", started_at) is False

    def test_within_timeout_with_custom_grace_period(self):
        """Within timeout check respects custom grace period."""
        with patch.object(settings, "job_timeout_deploy", 60):
            with patch.object(settings, "job_stuck_grace_period", 30):
                # Job started 80 seconds ago (past 60s timeout, within 90s total)
                started_at = datetime.now(timezone.utc) - timedelta(seconds=80)
                assert is_job_within_timeout("up", "running", started_at) is True

                # Job started 100 seconds ago (past 90s total)
                started_at = datetime.now(timezone.utc) - timedelta(seconds=100)
                assert is_job_within_timeout("up", "running", started_at) is False


class TestJobTimeoutEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_zero_timeout_behavior(self):
        """Zero timeout is handled correctly."""
        with patch.object(settings, "job_timeout_deploy", 0):
            started_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            # Any job with 0 timeout should immediately be stuck
            assert is_job_stuck("up", "running", started_at) is True

    def test_very_large_timeout(self):
        """Very large timeout values work correctly."""
        with patch.object(settings, "job_timeout_deploy", 86400 * 365):  # 1 year
            started_at = datetime.now(timezone.utc) - timedelta(days=30)
            assert is_job_stuck("up", "running", started_at) is False

    def test_job_started_in_future_not_stuck(self):
        """Job with future started_at is not stuck (clock skew scenario)."""
        started_at = datetime.now(timezone.utc) + timedelta(hours=1)
        result = is_job_stuck("up", "running", started_at)
        assert result is False

    def test_job_created_in_future_not_stuck(self):
        """Job with future created_at is not stuck (clock skew scenario)."""
        created_at = datetime.now(timezone.utc) + timedelta(hours=1)
        result = is_job_stuck("up", "queued", None, created_at)
        assert result is False

    def test_concurrent_status_transitions(self):
        """Status combinations are handled correctly."""
        now = datetime.now(timezone.utc)
        old = now - timedelta(hours=1)

        # Various status/timestamp combinations
        assert is_job_stuck("up", "pending", old, old) is False
        assert is_job_stuck("up", "unknown", old, old) is False

        # is_job_within_timeout for non-active statuses
        assert is_job_within_timeout("up", "pending", old, old) is False
        assert is_job_within_timeout("up", "unknown", old, old) is False

    def test_microsecond_precision(self):
        """Microsecond-level precision in timing is handled."""
        # Create timestamps with microseconds
        base = datetime(2024, 1, 15, 12, 0, 0, 123456, tzinfo=timezone.utc)
        result = get_job_timeout_at("up", base)
        assert result is not None
        assert result.microsecond == 123456

    def test_special_action_strings(self):
        """Special characters in action strings are handled."""
        # Actions with special characters default to deploy timeout
        assert get_job_timeout("up:special!@#$") == settings.job_timeout_deploy
        assert get_job_timeout("node:start:router-1_v2") == settings.job_timeout_node
        assert get_job_timeout("sync:node:spine.1") == settings.job_timeout_sync
