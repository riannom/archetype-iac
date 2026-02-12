"""Per-domain locking for virsh console access.

Three code paths compete for a VM's single serial console:
1. Readiness probe (subprocess, brief 2s reads)
2. Config extraction (pexpect, long-running 30-60s)
3. Web console (PTY, persistent user session)

Without coordination, concurrent `virsh console --force` calls cause
"stream had I/O failure" errors. This module provides a simple per-domain
threading.Lock to serialize access, plus orphan process cleanup.

Uses threading.Lock (not asyncio.Lock) because readiness and extraction
run in blocking threads via asyncio.to_thread().
"""

import logging
import os
import signal
import subprocess
import threading
from contextlib import contextmanager
from typing import Generator

logger = logging.getLogger(__name__)

# Per-domain locks: domain_name -> threading.Lock
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_active_extractions: set[str] = set()
_extractions_guard = threading.Lock()


def _get_lock(domain_name: str) -> threading.Lock:
    """Get or create a lock for a domain."""
    with _locks_guard:
        if domain_name not in _locks:
            _locks[domain_name] = threading.Lock()
        return _locks[domain_name]


def is_extraction_active(domain_name: str) -> bool:
    """Return whether a config extraction is active for this domain."""
    with _extractions_guard:
        return domain_name in _active_extractions


@contextmanager
def extraction_session(domain_name: str) -> Generator[None, None, None]:
    """Mark a domain as actively extracting config for probe backoff."""
    with _extractions_guard:
        _active_extractions.add(domain_name)
    try:
        yield
    finally:
        with _extractions_guard:
            _active_extractions.discard(domain_name)


def kill_orphaned_virsh(domain_name: str) -> int:
    """Kill orphaned virsh console processes for a domain.

    Finds processes matching 'virsh.*console.*<domain_name>' and sends
    SIGTERM, then SIGKILL if still alive. Returns number of processes killed.
    """
    killed = 0
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"virsh.*console.*{domain_name}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return 0

        my_pid = os.getpid()
        for line in result.stdout.strip().split("\n"):
            try:
                pid = int(line.strip())
                if pid == my_pid:
                    continue
                os.kill(pid, signal.SIGTERM)
                killed += 1
                logger.info(f"Killed orphaned virsh console process {pid} for {domain_name}")
            except (ValueError, ProcessLookupError):
                pass
            except OSError as e:
                logger.debug(f"Could not kill pid {line}: {e}")
    except Exception as e:
        logger.debug(f"Error scanning for orphaned virsh processes: {e}")

    return killed


@contextmanager
def console_lock(
    domain_name: str,
    timeout: float = 60,
    kill_orphans: bool = True,
) -> Generator[None, None, None]:
    """Acquire the console lock for a domain.

    Kills orphaned virsh processes before acquiring the lock.
    Blocks up to `timeout` seconds. Raises TimeoutError if lock
    cannot be acquired.

    Args:
        domain_name: Libvirt domain name
        timeout: Seconds to wait for lock acquisition
        kill_orphans: Whether to kill orphaned virsh processes first
    """
    if kill_orphans:
        kill_orphaned_virsh(domain_name)

    lock = _get_lock(domain_name)
    acquired = lock.acquire(timeout=timeout)
    if not acquired:
        raise TimeoutError(
            f"Could not acquire console lock for {domain_name} "
            f"within {timeout}s (another session is active)"
        )
    try:
        yield
    finally:
        lock.release()


@contextmanager
def try_console_lock(domain_name: str) -> Generator[bool, None, None]:
    """Non-blocking attempt to acquire the console lock.

    Yields True if the lock was acquired, False otherwise.
    Use this for readiness probes that should skip gracefully
    when the console is busy.

    Usage:
        with try_console_lock(domain) as acquired:
            if not acquired:
                return ""  # skip this cycle
            # ... do work ...
    """
    # Avoid contention while config extraction owns this domain.
    if is_extraction_active(domain_name):
        yield False
        return

    lock = _get_lock(domain_name)
    acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()
