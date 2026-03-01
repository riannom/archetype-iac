"""Tests for agent virsh console lock module.

Source: agent/virsh_console_lock.py
Covers: _get_lock, is_extraction_active, extraction_session,
        kill_orphaned_virsh, console_lock, try_console_lock,
        concurrent access safety.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from agent.virsh_console_lock import (
    _get_lock,
    _locks,
    _locks_guard,
    _active_extractions,
    _extractions_guard,
    console_lock,
    extraction_session,
    is_extraction_active,
    kill_orphaned_virsh,
    try_console_lock,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_locks():
    """Clear module-level state between tests."""
    with _locks_guard:
        _locks.clear()
    with _extractions_guard:
        _active_extractions.clear()
    yield
    with _locks_guard:
        _locks.clear()
    with _extractions_guard:
        _active_extractions.clear()


# ---------------------------------------------------------------------------
# TestGetLock
# ---------------------------------------------------------------------------


class TestGetLock:
    """Tests for _get_lock() — per-domain lock creation and reuse."""

    def test_creates_new_lock(self):
        """Creates a new lock for an unseen domain."""
        lock = _get_lock("arch-lab1-r1")
        assert lock is not None
        assert isinstance(lock, threading.Lock)

    def test_reuses_existing_lock(self):
        """Returns the same lock for the same domain."""
        lock1 = _get_lock("arch-lab1-r1")
        lock2 = _get_lock("arch-lab1-r1")
        assert lock1 is lock2

    def test_different_domains_get_different_locks(self):
        """Different domains get independent locks."""
        lock1 = _get_lock("arch-lab1-r1")
        lock2 = _get_lock("arch-lab1-r2")
        assert lock1 is not lock2

    def test_thread_safe_creation(self):
        """Concurrent calls to _get_lock for the same domain return the same lock."""
        results = []

        def _get():
            results.append(_get_lock("concurrent-domain"))

        threads = [threading.Thread(target=_get) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All results should be the same lock
        assert len(set(id(r) for r in results)) == 1


# ---------------------------------------------------------------------------
# TestIsExtractionActive
# ---------------------------------------------------------------------------


class TestIsExtractionActive:
    """Tests for is_extraction_active()."""

    def test_returns_false_by_default(self):
        """No extraction active returns False."""
        assert is_extraction_active("arch-lab1-r1") is False

    def test_returns_true_during_extraction(self):
        """Returns True when extraction session is active."""
        with _extractions_guard:
            _active_extractions.add("arch-lab1-r1")

        assert is_extraction_active("arch-lab1-r1") is True

    def test_different_domain_not_affected(self):
        """Active extraction on one domain does not affect others."""
        with _extractions_guard:
            _active_extractions.add("arch-lab1-r1")

        assert is_extraction_active("arch-lab1-r2") is False


# ---------------------------------------------------------------------------
# TestExtractionSession
# ---------------------------------------------------------------------------


class TestExtractionSession:
    """Tests for extraction_session() context manager."""

    def test_sets_and_clears_flag(self):
        """Extraction session marks domain as active then clears it."""
        assert is_extraction_active("arch-lab1-r1") is False

        with extraction_session("arch-lab1-r1"):
            assert is_extraction_active("arch-lab1-r1") is True

        assert is_extraction_active("arch-lab1-r1") is False

    def test_clears_on_exception(self):
        """Extraction flag is cleared even if exception occurs."""
        try:
            with extraction_session("arch-lab1-r1"):
                assert is_extraction_active("arch-lab1-r1") is True
                raise ValueError("simulated error")
        except ValueError:
            pass

        assert is_extraction_active("arch-lab1-r1") is False

    def test_multiple_domains_independent(self):
        """Multiple extraction sessions on different domains are independent."""
        with extraction_session("arch-lab1-r1"):
            with extraction_session("arch-lab1-r2"):
                assert is_extraction_active("arch-lab1-r1") is True
                assert is_extraction_active("arch-lab1-r2") is True

            # r2 exited, r1 still active
            assert is_extraction_active("arch-lab1-r1") is True
            assert is_extraction_active("arch-lab1-r2") is False


# ---------------------------------------------------------------------------
# TestKillOrphanedVirsh
# ---------------------------------------------------------------------------


class TestKillOrphanedVirsh:
    """Tests for kill_orphaned_virsh()."""

    def test_no_orphans_found(self):
        """Returns 0 when no orphaned processes exist."""
        mock_result = MagicMock()
        mock_result.returncode = 1  # pgrep returns 1 when no match
        mock_result.stdout = ""

        with patch("agent.virsh_console_lock.subprocess.run", return_value=mock_result):
            killed = kill_orphaned_virsh("arch-lab1-r1")

        assert killed == 0

    def test_kills_orphaned_process(self):
        """Kills an orphaned virsh console process."""
        mock_pgrep = MagicMock()
        mock_pgrep.returncode = 0
        mock_pgrep.stdout = "12345\n"

        def _fake_kill(pid, sig):
            if sig == 0:
                raise ProcessLookupError()  # Process gone after SIGTERM

        with patch("agent.virsh_console_lock.subprocess.run", return_value=mock_pgrep):
            with patch("agent.virsh_console_lock.os.kill", side_effect=_fake_kill):
                with patch("agent.virsh_console_lock.os.getpid", return_value=99999):
                    killed = kill_orphaned_virsh("arch-lab1-r1")

        # Process was killed (os.kill with SIGTERM) then confirmed gone
        assert killed == 1

    def test_skips_own_pid(self):
        """Does not kill its own process."""
        mock_pgrep = MagicMock()
        mock_pgrep.returncode = 0
        mock_pgrep.stdout = "12345\n"

        with patch("agent.virsh_console_lock.subprocess.run", return_value=mock_pgrep):
            with patch("agent.virsh_console_lock.os.getpid", return_value=12345):
                killed = kill_orphaned_virsh("arch-lab1-r1")

        assert killed == 0

    def test_handles_pgrep_exception(self):
        """Handles pgrep subprocess failure gracefully."""
        with patch("agent.virsh_console_lock.subprocess.run",
                    side_effect=Exception("pgrep crashed")):
            killed = kill_orphaned_virsh("arch-lab1-r1")

        assert killed == 0


# ---------------------------------------------------------------------------
# TestConsoleLock
# ---------------------------------------------------------------------------


class TestConsoleLock:
    """Tests for console_lock() context manager."""

    def test_acquires_and_releases(self):
        """Lock is acquired and released properly."""
        lock = _get_lock("arch-lab1-r1")

        with patch("agent.virsh_console_lock.kill_orphaned_virsh", return_value=0):
            with console_lock("arch-lab1-r1", timeout=5):
                # Lock should be acquired
                assert not lock.acquire(blocking=False)  # Already held

            # Lock should be released
            assert lock.acquire(blocking=False)
            lock.release()

    def test_kills_orphans_before_acquire(self):
        """Orphan virsh processes are killed before lock acquisition."""
        with patch("agent.virsh_console_lock.kill_orphaned_virsh",
                    return_value=1) as mock_kill:
            with console_lock("arch-lab1-r1", timeout=5, kill_orphans=True):
                pass

        mock_kill.assert_called_once_with("arch-lab1-r1")

    def test_skip_orphan_kill(self):
        """kill_orphans=False skips orphan cleanup."""
        with patch("agent.virsh_console_lock.kill_orphaned_virsh") as mock_kill:
            with console_lock("arch-lab1-r1", timeout=5, kill_orphans=False):
                pass

        mock_kill.assert_not_called()

    def test_timeout_raises(self):
        """Timeout raises TimeoutError when lock cannot be acquired."""
        lock = _get_lock("arch-lab1-r1")
        lock.acquire()  # Hold the lock

        try:
            with patch("agent.virsh_console_lock.kill_orphaned_virsh", return_value=0):
                with pytest.raises(TimeoutError) as exc_info:
                    with console_lock("arch-lab1-r1", timeout=0.1):
                        pass

            assert "arch-lab1-r1" in str(exc_info.value)
        finally:
            lock.release()

    def test_releases_on_exception(self):
        """Lock is released even when exception occurs inside context."""
        lock = _get_lock("arch-lab1-r1")

        with patch("agent.virsh_console_lock.kill_orphaned_virsh", return_value=0):
            try:
                with console_lock("arch-lab1-r1", timeout=5):
                    raise RuntimeError("something broke")
            except RuntimeError:
                pass

        # Lock should be released
        assert lock.acquire(blocking=False)
        lock.release()


# ---------------------------------------------------------------------------
# TestTryConsoleLock
# ---------------------------------------------------------------------------


class TestTryConsoleLock:
    """Tests for try_console_lock() non-blocking context manager."""

    def test_acquired_when_free(self):
        """Returns True when lock is available."""
        with try_console_lock("arch-lab1-r1") as acquired:
            assert acquired is True

    def test_not_acquired_when_held(self):
        """Returns False when lock is already held."""
        lock = _get_lock("arch-lab1-r1")
        lock.acquire()

        try:
            with try_console_lock("arch-lab1-r1") as acquired:
                assert acquired is False
        finally:
            lock.release()

    def test_not_acquired_during_extraction(self):
        """Returns False when extraction is active (without trying lock)."""
        with extraction_session("arch-lab1-r1"):
            with try_console_lock("arch-lab1-r1") as acquired:
                assert acquired is False

    def test_releases_lock_after_use(self):
        """Lock is released after context manager exits."""
        with try_console_lock("arch-lab1-r1") as acquired:
            assert acquired is True

        # Lock should be free now
        lock = _get_lock("arch-lab1-r1")
        assert lock.acquire(blocking=False)
        lock.release()

    def test_does_not_release_if_not_acquired(self):
        """Does not attempt to release lock if it was never acquired."""
        lock = _get_lock("arch-lab1-r1")
        lock.acquire()

        try:
            with try_console_lock("arch-lab1-r1") as acquired:
                assert acquired is False
            # Lock should still be held by the original acquirer
            # (not released by try_console_lock)
        finally:
            lock.release()


# ---------------------------------------------------------------------------
# TestConcurrentAccess
# ---------------------------------------------------------------------------


class TestConcurrentAccess:
    """Tests for threading safety of console lock primitives."""

    def test_exclusive_access(self):
        """Only one thread can hold the console lock at a time."""
        counter = {"value": 0, "max_concurrent": 0}
        lock_obj = threading.Lock()

        def _worker():
            with patch("agent.virsh_console_lock.kill_orphaned_virsh", return_value=0):
                with console_lock("shared-domain", timeout=10):
                    with lock_obj:
                        counter["value"] += 1
                        if counter["value"] > counter["max_concurrent"]:
                            counter["max_concurrent"] = counter["value"]
                    time.sleep(0.01)
                    with lock_obj:
                        counter["value"] -= 1

        threads = [threading.Thread(target=_worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert counter["max_concurrent"] == 1

    def test_different_domains_concurrent(self):
        """Different domains can be locked concurrently."""
        both_held = threading.Event()
        results = {"domain1": False, "domain2": False}

        def _worker1():
            with patch("agent.virsh_console_lock.kill_orphaned_virsh", return_value=0):
                with console_lock("domain-1", timeout=5):
                    results["domain1"] = True
                    both_held.wait(timeout=2)

        def _worker2():
            with patch("agent.virsh_console_lock.kill_orphaned_virsh", return_value=0):
                with console_lock("domain-2", timeout=5):
                    results["domain2"] = True
                    both_held.set()

        t1 = threading.Thread(target=_worker1)
        t2 = threading.Thread(target=_worker2)
        t1.start()
        time.sleep(0.05)  # Let t1 acquire first
        t2.start()

        t1.join(timeout=5)
        t2.join(timeout=5)

        assert results["domain1"] is True
        assert results["domain2"] is True
