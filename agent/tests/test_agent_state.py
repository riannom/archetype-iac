"""Unit tests for agent_state.py — shared mutable state for the Archetype agent.

Tests cover:
- Identity and lifecycle state (AGENT_ID, AGENT_STARTED_AT)
- Registration and background task setters
- Active jobs counter (increment/decrement/boundary)
- Lock manager and event listener accessors
- Local IP detection (async, caching, error handling)
- Compiled regex patterns for validation
- Deploy results cache
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


class TestIdentityAndLifecycle:
    """Tests for AGENT_ID, AGENT_STARTED_AT, and set_agent_id."""

    def test_agent_id_is_string(self):
        import agent.agent_state as state
        assert isinstance(state.AGENT_ID, str)
        assert len(state.AGENT_ID) > 0

    def test_agent_started_at_is_utc_datetime(self):
        import agent.agent_state as state
        assert isinstance(state.AGENT_STARTED_AT, datetime)
        assert state.AGENT_STARTED_AT.tzinfo is not None

    def test_set_agent_id_updates_value(self):
        import agent.agent_state as state
        original = state.AGENT_ID
        try:
            state.set_agent_id("test-id-42")
            assert state.AGENT_ID == "test-id-42"
        finally:
            state.set_agent_id(original)

    def test_set_agent_id_allows_reassignment(self):
        import agent.agent_state as state
        original = state.AGENT_ID
        try:
            state.set_agent_id("first")
            state.set_agent_id("second")
            assert state.AGENT_ID == "second"
        finally:
            state.set_agent_id(original)


class TestRegistrationAndTasks:
    """Tests for registration flag and task setters."""

    def test_set_registered_true(self):
        import agent.agent_state as state
        state.set_registered(True)
        assert state._registered is True

    def test_set_registered_false(self):
        import agent.agent_state as state
        state.set_registered(False)
        assert state._registered is False

    def test_set_heartbeat_task(self):
        import agent.agent_state as state
        mock_task = MagicMock(spec=asyncio.Task)
        state.set_heartbeat_task(mock_task)
        assert state._heartbeat_task is mock_task
        state.set_heartbeat_task(None)
        assert state._heartbeat_task is None

    def test_set_event_listener_task(self):
        import agent.agent_state as state
        mock_task = MagicMock(spec=asyncio.Task)
        state.set_event_listener_task(mock_task)
        assert state._event_listener_task is mock_task
        state.set_event_listener_task(None)
        assert state._event_listener_task is None

    def test_set_fix_interfaces_task(self):
        import agent.agent_state as state
        mock_task = MagicMock(spec=asyncio.Task)
        state.set_fix_interfaces_task(mock_task)
        assert state._fix_interfaces_task is mock_task
        state.set_fix_interfaces_task(None)
        assert state._fix_interfaces_task is None


class TestActiveJobsCounter:
    """Tests for the active jobs counter."""

    def test_get_active_jobs_initial(self):
        import agent.agent_state as state
        # Reset for test isolation
        state._active_jobs = 0
        assert state.get_active_jobs() == 0

    def test_increment_active_jobs(self):
        import agent.agent_state as state
        state._active_jobs = 0
        state._increment_active_jobs()
        assert state.get_active_jobs() == 1

    def test_decrement_active_jobs(self):
        import agent.agent_state as state
        state._active_jobs = 3
        state._decrement_active_jobs()
        assert state.get_active_jobs() == 2

    def test_decrement_active_jobs_floor_at_zero(self):
        """Decrement should never go below zero."""
        import agent.agent_state as state
        state._active_jobs = 0
        state._decrement_active_jobs()
        assert state.get_active_jobs() == 0

    def test_multiple_increments(self):
        import agent.agent_state as state
        state._active_jobs = 0
        for _ in range(5):
            state._increment_active_jobs()
        assert state.get_active_jobs() == 5


class TestLockManager:
    """Tests for lock manager get/set."""

    def test_get_lock_manager_initial_none(self):
        import agent.agent_state as state
        state._lock_manager = None
        assert state.get_lock_manager() is None

    def test_set_and_get_lock_manager(self):
        import agent.agent_state as state
        mock_mgr = MagicMock()
        state.set_lock_manager(mock_mgr)
        assert state.get_lock_manager() is mock_mgr
        state.set_lock_manager(None)


class TestEventListener:
    """Tests for lazy-init event listener."""

    def test_get_event_listener_creates_instance(self):
        import agent.agent_state as state
        state._event_listener = None
        with patch("agent.agent_state.DockerEventListener", create=True) as MockListener:
            # Patch the import inside get_event_listener
            with patch.dict("sys.modules", {"agent.events": MagicMock(DockerEventListener=MockListener)}):
                listener = state.get_event_listener()
                assert listener is not None

    def test_get_event_listener_returns_cached(self):
        import agent.agent_state as state
        sentinel = MagicMock()
        state._event_listener = sentinel
        result = state.get_event_listener()
        assert result is sentinel
        state._event_listener = None


class TestLocalIPDetection:
    """Tests for _detect_local_ip and _async_detect_local_ip."""

    def test_detect_local_ip_returns_cached(self):
        import agent.agent_state as state
        state._cached_local_ip = "10.0.0.1"
        assert state._detect_local_ip() == "10.0.0.1"
        state._cached_local_ip = None

    def test_detect_local_ip_returns_none_when_uncached(self):
        import agent.agent_state as state
        state._cached_local_ip = None
        assert state._detect_local_ip() is None

    @pytest.mark.asyncio
    async def test_async_detect_local_ip_success(self):
        import agent.agent_state as state
        state._cached_local_ip = None
        state._local_ip_detected = False

        async def fake_run_cmd(cmd):
            return (0, "1.1.1.1 via 10.0.0.1 dev eth0 src 192.168.1.100 uid 0", "")

        with patch("agent.agent_state._async_run_cmd", fake_run_cmd, create=True):
            with patch("agent.network.cmd.run_cmd", fake_run_cmd):
                result = await state._async_detect_local_ip()

        assert result == "192.168.1.100"
        assert state._cached_local_ip == "192.168.1.100"
        assert state._local_ip_detected is True
        # Reset
        state._cached_local_ip = None
        state._local_ip_detected = False

    @pytest.mark.asyncio
    async def test_async_detect_local_ip_caches_result(self):
        """Second call returns cached result without running command."""
        import agent.agent_state as state
        state._cached_local_ip = "10.0.0.50"
        state._local_ip_detected = True

        result = await state._async_detect_local_ip()
        assert result == "10.0.0.50"
        # Reset
        state._cached_local_ip = None
        state._local_ip_detected = False

    @pytest.mark.asyncio
    async def test_async_detect_local_ip_command_failure(self):
        """Non-zero return code should set _local_ip_detected but leave cache None."""
        import agent.agent_state as state
        state._cached_local_ip = None
        state._local_ip_detected = False

        async def fail_run_cmd(cmd):
            return (1, "", "error")

        with patch("agent.network.cmd.run_cmd", fail_run_cmd):
            result = await state._async_detect_local_ip()

        assert result is None
        assert state._local_ip_detected is True
        # Reset
        state._cached_local_ip = None
        state._local_ip_detected = False

    @pytest.mark.asyncio
    async def test_async_detect_local_ip_exception(self):
        """Exception during detection should not propagate."""
        import agent.agent_state as state
        state._cached_local_ip = None
        state._local_ip_detected = False

        async def error_run_cmd(cmd):
            raise OSError("Network unavailable")

        with patch("agent.network.cmd.run_cmd", error_run_cmd):
            result = await state._async_detect_local_ip()

        assert result is None
        assert state._local_ip_detected is True
        # Reset
        state._cached_local_ip = None
        state._local_ip_detected = False

    @pytest.mark.asyncio
    async def test_async_detect_local_ip_no_src_in_output(self):
        """Output without 'src' keyword should not crash."""
        import agent.agent_state as state
        state._cached_local_ip = None
        state._local_ip_detected = False

        async def no_src_run_cmd(cmd):
            return (0, "1.1.1.1 via 10.0.0.1 dev eth0 uid 0", "")

        with patch("agent.network.cmd.run_cmd", no_src_run_cmd):
            result = await state._async_detect_local_ip()

        assert result is None
        assert state._local_ip_detected is True
        # Reset
        state._cached_local_ip = None
        state._local_ip_detected = False


class TestCompiledRegexes:
    """Tests for compiled regex patterns."""

    def test_safe_id_re_accepts_valid(self):
        import agent.agent_state as state
        assert state._SAFE_ID_RE.match("abc-123_DEF")
        assert state._SAFE_ID_RE.match("node1")
        assert state._SAFE_ID_RE.match("A")

    def test_safe_id_re_rejects_invalid(self):
        import agent.agent_state as state
        assert state._SAFE_ID_RE.match("has space") is None
        assert state._SAFE_ID_RE.match("has.dot") is None
        assert state._SAFE_ID_RE.match("has/slash") is None
        assert state._SAFE_ID_RE.match("") is None

    def test_port_name_re_accepts_valid(self):
        import agent.agent_state as state
        assert state._PORT_NAME_RE.match("eth0")
        assert state._PORT_NAME_RE.match("GigabitEthernet0.1")
        assert state._PORT_NAME_RE.match("ge-0_0_0")

    def test_port_name_re_rejects_invalid(self):
        import agent.agent_state as state
        assert state._PORT_NAME_RE.match("has space") is None
        assert state._PORT_NAME_RE.match("has/slash") is None
        assert state._PORT_NAME_RE.match("") is None

    def test_container_prefix_re_matches(self):
        import agent.agent_state as state
        assert state._CONTAINER_PREFIX_RE.match("archetype-lab1-node1")
        assert state._CONTAINER_PREFIX_RE.match("arch-lab1-node1")

    def test_container_prefix_re_rejects(self):
        import agent.agent_state as state
        assert state._CONTAINER_PREFIX_RE.match("clab-lab1-node1") is None
        assert state._CONTAINER_PREFIX_RE.match("docker-node") is None
