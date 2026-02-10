from __future__ import annotations

import atexit
import faulthandler
import os
import sys
import threading
import asyncio
import pytest


@pytest.fixture(autouse=True)
def _set_testing_env(monkeypatch):
    """Ensure agent startup tasks are disabled during unit tests."""
    monkeypatch.setenv("ARCHETYPE_AGENT_TESTING", "1")
    yield


def _dump_threads() -> None:
    threads = [t for t in threading.enumerate() if t.is_alive()]
    print("\n[pytest-exit] live threads:", file=sys.stderr)
    for t in threads:
        print(f"- {t.name} daemon={t.daemon}", file=sys.stderr)
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)


atexit.register(_dump_threads)


def pytest_sessionfinish(session, exitstatus):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    try:
        tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
    except Exception:
        return
    if tasks:
        print("\n[pytest-exit] pending asyncio tasks:", file=sys.stderr)
        for t in tasks:
            print(f"- {t}", file=sys.stderr)
