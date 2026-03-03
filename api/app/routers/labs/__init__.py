"""Labs router package.

Re-exports the combined router and public symbols so that existing imports
like ``from app.routers.labs import router`` and ``from app.routers import labs``
continue to work without any call-site changes.
"""
from __future__ import annotations

from fastapi import APIRouter

# Import sub-routers
from .crud import router as _crud_router
from .topology import router as _topology_router
from .link_states import router as _link_states_router
from .operations import router as _operations_router

# ---------------------------------------------------------------------------
# Assemble the combined router that main.py includes
# ---------------------------------------------------------------------------
router = APIRouter(tags=["labs"])
router.include_router(_crud_router)
router.include_router(_topology_router)
router.include_router(_link_states_router)
router.include_router(_operations_router)


# ---------------------------------------------------------------------------
# Attach extracted labs subrouters (labs_configs, labs_node_states)
# ---------------------------------------------------------------------------
def _include_labs_subrouters() -> None:
    """Attach extracted labs subrouters.

    Imported lazily to avoid circular imports while `labs.py` is still loading
    shared helper functions used by these modules.
    """
    if getattr(router, "_labs_subrouters_included", False):
        return

    from app.routers.labs_configs import router as labs_configs_router
    from app.routers.labs_node_states import router as labs_node_states_router

    router.include_router(labs_node_states_router)
    router.include_router(labs_configs_router)
    setattr(router, "_labs_subrouters_included", True)


# ---------------------------------------------------------------------------
# Re-export public symbols that external modules import
# ---------------------------------------------------------------------------

# Module-level imports used by test monkeypatching (app.routers.labs.agent_client etc.)
from app import agent_client  # noqa: E402,F401
from app.services.topology import TopologyService  # noqa: E402,F401
from app.storage import (  # noqa: E402,F401
    delete_layout,
    lab_workspace,
    read_layout,
)
from app.tasks.live_links import (  # noqa: E402,F401
    _build_host_to_agent_map,
    create_link_if_ready,
    teardown_link,
    process_link_changes,
)
from app.tasks.live_nodes import process_node_changes  # noqa: E402,F401
from app.utils.agents import get_online_agent_for_lab  # noqa: E402,F401
from app.utils.async_tasks import safe_create_task  # noqa: E402,F401
from app.tasks.jobs import run_agent_job  # noqa: E402,F401
from app.tasks.link_reconciliation import reconcile_lab_links  # noqa: E402,F401
from app.services.link_reservations import sync_link_endpoint_reservations  # noqa: E402,F401
from app.services.link_operational_state import recompute_link_oper_state  # noqa: E402,F401
from app.services import interface_mapping as interface_mapping_service  # noqa: E402,F401

# Shared utilities (used by labs_node_states, labs_configs, jobs, tests)
from ._shared import (  # noqa: E402,F401
    has_conflicting_job,
    get_config_by_device,
    _enrich_node_state,
    _get_or_create_node_state,
    _create_node_sync_job,
    _converge_stopped_error_state,
    _zip_safe_name,
)

# CRUD helpers (used by topology.py, operations.py, and external modules)
from .crud import (  # noqa: E402,F401
    _upsert_node_states,
    _ensure_node_states_exist,
    _populate_lab_counts,
)

# Link state helpers (used by topology.py and external test monkeypatching)
from .link_states import (  # noqa: E402,F401
    _upsert_link_states,
    _ensure_link_states_exist,
    _find_matching_link_state,
    _choose_preferred_link_state,
    _parse_link_id_endpoints,
    _sync_link_oper_state,
    _link_endpoint_payload,
    _raise_link_endpoint_conflict,
)

# Topology helpers
from .topology import TopologyGraphWithLayout  # noqa: E402,F401

# Backward compatibility for tests still patching this symbol via app.routers.labs.
from app.routers.labs_configs import _save_config_to_workspace  # noqa: E402,F401

# ---------------------------------------------------------------------------
# Attach extracted labs subrouters AFTER all re-exports are defined
# (labs_node_states / labs_configs import helpers from this package)
# ---------------------------------------------------------------------------
_include_labs_subrouters()
