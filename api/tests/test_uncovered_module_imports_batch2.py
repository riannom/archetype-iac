from __future__ import annotations

from datetime import timezone

import app.agent_auth as agent_auth
import app.routers.dashboard as dashboard_router
import app.routers.lab_tests as lab_tests_router
import app.routers.labs_configs as labs_configs_router
import app.routers.labs_node_states as labs_node_states_router
import app.utils.time as time_utils


def test_modules_import() -> None:
    assert agent_auth is not None
    assert dashboard_router is not None
    assert lab_tests_router is not None
    assert labs_configs_router is not None
    assert labs_node_states_router is not None
    assert time_utils is not None


def test_imported_modules_expose_expected_symbols() -> None:
    assert callable(agent_auth.verify_agent_secret)
    assert dashboard_router.router.prefix == "/dashboard"
    assert "lab_tests" in lab_tests_router.router.tags
    assert "labs" in labs_configs_router.router.tags
    assert "labs" in labs_node_states_router.router.tags

    now = time_utils.utcnow()
    assert now.tzinfo is timezone.utc
