"""Shared pytest fixtures for agent tests."""
from __future__ import annotations

import pytest

from agent.config import settings


@pytest.fixture(autouse=True)
def override_workspace_path(tmp_path):
    """Override workspace_path for all tests to use a temp directory.

    Prevents PermissionError from accessing /var/lib/archetype-agent in CI.
    """
    original = settings.workspace_path
    object.__setattr__(settings, "workspace_path", str(tmp_path / "agent-workspace"))
    yield
    object.__setattr__(settings, "workspace_path", original)
