from __future__ import annotations

import os

import pytest

from agent.config import settings


@pytest.fixture(autouse=True)
def _set_testing_env(monkeypatch, tmp_path):
    """Ensure agent startup tasks are disabled during unit tests.

    Also redirect workspace_path to a temp directory so tests don't
    try to create /var/lib/archetype-agent (which fails in CI).
    """
    monkeypatch.setenv("ARCHETYPE_AGENT_TESTING", "1")
    monkeypatch.setattr(settings, "workspace_path", str(tmp_path / "workspace"))
    yield


def pytest_collection_modifyitems(config, items):
    """Skip integration tests unless explicitly enabled."""
    if os.getenv("ARCHETYPE_RUN_INTEGRATION") in {"1", "true", "TRUE", "yes", "YES"}:
        return

    skip_integration = pytest.mark.skip(reason="Integration tests require Docker. Set ARCHETYPE_RUN_INTEGRATION=1 to run.")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
