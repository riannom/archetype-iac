from __future__ import annotations

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
