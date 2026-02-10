from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _set_testing_env(monkeypatch):
    """Ensure agent startup tasks are disabled during unit tests."""
    monkeypatch.setenv("ARCHETYPE_AGENT_TESTING", "1")
    yield
