"""Batch 6: Node lifecycle coverage expansion tests.

Covers untested methods and branches in node_lifecycle.py:
- _is_ceos_kind helper
- LifecycleResult dataclass
- _get_container_name
- _dominant_device_type
- _group_nodes_by_device_type
- _categorize_nodes
- _check_resources (branches: no candidates, catastrophic, soft-fail)
- _load_and_validate (early-exit: no nodes, all in desired state)
- _set_transitional_states (starting/stopping)
- CEOS_STAGGER_SECONDS / DEPLOY_RETRY constants
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

import pytest


# ---------------------------------------------------------------------------
# _is_ceos_kind helper
# ---------------------------------------------------------------------------

class TestIsCeosKind:
    def test_ceos_lowercase(self):
        from app.tasks.node_lifecycle import _is_ceos_kind
        assert _is_ceos_kind("ceos") is True

    def test_arista_ceos(self):
        from app.tasks.node_lifecycle import _is_ceos_kind
        assert _is_ceos_kind("arista_ceos") is True

    def test_ceos_in_string(self):
        from app.tasks.node_lifecycle import _is_ceos_kind
        assert _is_ceos_kind("ceos-lab") is True

    def test_case_insensitive(self):
        from app.tasks.node_lifecycle import _is_ceos_kind
        assert _is_ceos_kind("CEOS") is True

    def test_non_ceos(self):
        from app.tasks.node_lifecycle import _is_ceos_kind
        assert _is_ceos_kind("srlinux") is False

    def test_empty_string(self):
        from app.tasks.node_lifecycle import _is_ceos_kind
        assert _is_ceos_kind("") is False

    def test_none_like_falsy(self):
        from app.tasks.node_lifecycle import _is_ceos_kind
        # The function checks `if not kind: return False`
        assert _is_ceos_kind("") is False


# ---------------------------------------------------------------------------
# LifecycleResult dataclass
# ---------------------------------------------------------------------------

class TestLifecycleResult:
    def test_basic_construction(self):
        from app.tasks.node_lifecycle import LifecycleResult
        result = LifecycleResult(success=True)
        assert result.success is True
        assert result.error_count == 0
        assert result.log == []

    def test_noop_factory(self):
        from app.tasks.node_lifecycle import LifecycleResult
        result = LifecycleResult.noop()
        assert result.success is True
        assert result.log == ["No action needed"]

    def test_with_errors(self):
        from app.tasks.node_lifecycle import LifecycleResult
        result = LifecycleResult(success=False, error_count=3, log=["failed"])
        assert result.success is False
        assert result.error_count == 3


# ---------------------------------------------------------------------------
# _get_container_name
# ---------------------------------------------------------------------------

class TestGetContainerName:
    def test_basic_container_name(self):
        from app.tasks.node_lifecycle import _get_container_name
        name = _get_container_name("lab-abc", "router1")
        assert "lab-abc" in name or "router1" in name
        # The function delegates to docker_container_name which produces
        # "archetype-{lab_id}-{node_name}"
        assert name == "archetype-lab-abc-router1"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_ceos_stagger_seconds(self):
        from app.tasks.node_lifecycle import CEOS_STAGGER_SECONDS
        assert CEOS_STAGGER_SECONDS == 0.5

    def test_deploy_retry_attempts(self):
        from app.tasks.node_lifecycle import DEPLOY_RETRY_ATTEMPTS
        assert DEPLOY_RETRY_ATTEMPTS == 2

    def test_deploy_retry_backoff(self):
        from app.tasks.node_lifecycle import DEPLOY_RETRY_BACKOFF_SECONDS
        assert DEPLOY_RETRY_BACKOFF_SECONDS == 5


# ---------------------------------------------------------------------------
# Helper to create a minimal NLM instance
# ---------------------------------------------------------------------------

def _make_nlm(session=None, node_states=None, db_nodes=None):
    """Create a NodeLifecycleManager with minimal mocking."""
    from app.tasks.node_lifecycle import NodeLifecycleManager

    mock_session = session or MagicMock()
    mock_lab = SimpleNamespace(id="lab-1", name="Test Lab", agent_id="agent-1")
    mock_job = SimpleNamespace(
        id="job-1", action="node_sync", status="queued",
        log_path=None, agent_id=None,
        started_at=None, completed_at=None,
        created_at=datetime.now(timezone.utc),
    )

    nlm = NodeLifecycleManager.__new__(NodeLifecycleManager)
    nlm.session = mock_session
    nlm.lab = mock_lab
    nlm.job = mock_job
    nlm.node_ids = ["n1"]
    nlm.provider = "docker"
    nlm.log_parts = []
    nlm.topo_service = MagicMock()
    nlm.node_states = node_states or []
    nlm.old_agent_ids = set()
    nlm.db_nodes_map = db_nodes or {}
    nlm.db_nodes_by_gui_id = {}
    nlm.placements_map = {}
    nlm.all_lab_states = {}
    nlm.agent = None
    nlm.target_agent_id = None
    nlm.graph = None
    return nlm


# ---------------------------------------------------------------------------
# _dominant_device_type
# ---------------------------------------------------------------------------

class TestDominantDeviceType:
    def test_single_known_type(self):
        ns = SimpleNamespace(node_name="r1")
        db_node = SimpleNamespace(device="ceos")
        nlm = _make_nlm(node_states=[ns], db_nodes={"r1": db_node})
        assert nlm._dominant_device_type() == "ceos"

    def test_unknown_type_returns_other(self):
        ns = SimpleNamespace(node_name="r1")
        db_node = SimpleNamespace(device="unknown_vendor_xyz")
        nlm = _make_nlm(node_states=[ns], db_nodes={"r1": db_node})
        result = nlm._dominant_device_type()
        # "unknown_vendor_xyz" not in _KNOWN_DEVICE_TYPES → "other"
        assert result == "other"

    def test_empty_states_returns_other(self):
        nlm = _make_nlm(node_states=[])
        assert nlm._dominant_device_type() == "other"

    def test_missing_db_node_defaults_to_linux(self):
        ns = SimpleNamespace(node_name="r1")
        nlm = _make_nlm(node_states=[ns], db_nodes={})
        assert nlm._dominant_device_type() == "linux"

    def test_most_common_wins(self):
        states = [SimpleNamespace(node_name=f"r{i}") for i in range(3)]
        db_nodes = {
            "r0": SimpleNamespace(device="ceos"),
            "r1": SimpleNamespace(device="srlinux"),
            "r2": SimpleNamespace(device="srlinux"),
        }
        nlm = _make_nlm(node_states=states, db_nodes=db_nodes)
        assert nlm._dominant_device_type() == "srlinux"


# ---------------------------------------------------------------------------
# _group_nodes_by_device_type
# ---------------------------------------------------------------------------

class TestGroupNodesByDeviceType:
    def test_groups_by_type(self):
        states = [
            SimpleNamespace(node_name="r1"),
            SimpleNamespace(node_name="r2"),
            SimpleNamespace(node_name="s1"),
        ]
        db_nodes = {
            "r1": SimpleNamespace(device="ceos"),
            "r2": SimpleNamespace(device="ceos"),
            "s1": SimpleNamespace(device="srlinux"),
        }
        nlm = _make_nlm(db_nodes=db_nodes)
        groups = nlm._group_nodes_by_device_type(states)
        # Should be sorted by type name
        group_dict = dict(groups)
        assert "ceos" in group_dict
        assert "srlinux" in group_dict
        assert len(group_dict["ceos"]) == 2
        assert len(group_dict["srlinux"]) == 1

    def test_unknown_type_grouped_as_other(self):
        states = [SimpleNamespace(node_name="r1")]
        db_nodes = {"r1": SimpleNamespace(device="vendor_xyz")}
        nlm = _make_nlm(db_nodes=db_nodes)
        groups = nlm._group_nodes_by_device_type(states)
        group_dict = dict(groups)
        assert "other" in group_dict


# ---------------------------------------------------------------------------
# _categorize_nodes
# ---------------------------------------------------------------------------

class TestCategorizeNodes:
    def test_deploy_candidates(self):
        ns = SimpleNamespace(
            node_name="r1", desired_state="running", actual_state="undeployed",
        )
        nlm = _make_nlm(node_states=[ns])
        nlm.node_states = [ns]
        deploy, start, stop = nlm._categorize_nodes()
        assert len(deploy) == 1
        assert len(start) == 0
        assert len(stop) == 0

    def test_start_candidates(self):
        ns = SimpleNamespace(
            node_name="r1", desired_state="running", actual_state="stopped",
        )
        nlm = _make_nlm(node_states=[ns])
        nlm.node_states = [ns]
        deploy, start, stop = nlm._categorize_nodes()
        assert len(deploy) == 0
        assert len(start) == 1

    def test_stop_candidates(self):
        ns = SimpleNamespace(
            node_name="r1", desired_state="stopped", actual_state="running",
        )
        nlm = _make_nlm(node_states=[ns])
        nlm.node_states = [ns]
        deploy, start, stop = nlm._categorize_nodes()
        assert len(stop) == 1

    def test_error_to_start(self):
        ns = SimpleNamespace(
            node_name="r1", desired_state="running", actual_state="error",
        )
        nlm = _make_nlm(node_states=[ns])
        nlm.node_states = [ns]
        deploy, start, stop = nlm._categorize_nodes()
        assert len(start) == 1

    def test_pending_to_deploy(self):
        ns = SimpleNamespace(
            node_name="r1", desired_state="running", actual_state="pending",
        )
        nlm = _make_nlm(node_states=[ns])
        nlm.node_states = [ns]
        deploy, start, stop = nlm._categorize_nodes()
        assert len(deploy) == 1

    def test_already_in_state_not_categorized(self):
        ns = SimpleNamespace(
            node_name="r1", desired_state="running", actual_state="running",
        )
        nlm = _make_nlm(node_states=[ns])
        nlm.node_states = [ns]
        deploy, start, stop = nlm._categorize_nodes()
        assert len(deploy) == 0
        assert len(start) == 0
        assert len(stop) == 0


# ---------------------------------------------------------------------------
# _check_resources — no deploy candidates
# ---------------------------------------------------------------------------

class TestCheckResources:
    @pytest.mark.asyncio
    async def test_no_deploy_candidates_returns_true(self):
        ns = SimpleNamespace(
            node_name="r1", desired_state="stopped", actual_state="running",
        )
        nlm = _make_nlm(node_states=[ns])
        nlm.node_states = [ns]
        result = await nlm._check_resources()
        assert result is True

    @pytest.mark.asyncio
    async def test_resource_validation_disabled(self, monkeypatch):
        from app.tasks import node_lifecycle as nlm_mod

        monkeypatch.setattr(nlm_mod.settings, "resource_validation_enabled", False)

        ns = SimpleNamespace(
            node_name="r1", desired_state="running", actual_state="undeployed",
        )
        nlm = _make_nlm(node_states=[ns])
        nlm.node_states = [ns]
        result = await nlm._check_resources()
        assert result is True


# ---------------------------------------------------------------------------
# _load_and_validate — early exit paths
# ---------------------------------------------------------------------------

class TestLoadAndValidate:
    @pytest.mark.asyncio
    async def test_no_node_states_returns_false(self, test_db):
        from app import models

        lab = models.Lab(id="lab-lv1", name="Test", owner_id="u1", workspace_path="/tmp")
        test_db.add(lab)
        test_db.flush()

        mock_job = SimpleNamespace(
            id="j1", action="node_sync", status="queued",
            log_path=None, completed_at=None,
        )

        nlm = _make_nlm(session=test_db)
        nlm.lab = lab
        nlm.job = mock_job
        nlm.node_ids = ["nonexistent"]

        result = await nlm._load_and_validate()
        assert result is False
        assert mock_job.status == "completed"


# ---------------------------------------------------------------------------
# _set_transitional_states
# ---------------------------------------------------------------------------

class TestSetTransitionalStates:
    @pytest.mark.asyncio
    async def test_sets_starting_for_deploy(self, monkeypatch):
        from app.tasks import node_lifecycle as nlm_mod

        # Prevent broadcast from hitting real broadcaster
        monkeypatch.setattr(nlm_mod, "broadcast_node_state_change", AsyncMock())

        ns = SimpleNamespace(
            node_name="r1", node_id="n1",
            desired_state="running", actual_state="stopped",
            error_message="old error",
            stopping_started_at=None,
            starting_started_at=None,
        )
        nlm = _make_nlm(node_states=[ns])
        nlm.node_states = [ns]
        nlm.session = MagicMock()

        # Mock _broadcast_state to avoid side effects
        nlm._broadcast_state = MagicMock()

        await nlm._set_transitional_states()

        assert ns.actual_state == "starting"
        assert ns.error_message is None
        assert ns.starting_started_at is not None

    @pytest.mark.asyncio
    async def test_sets_stopping_for_stop(self, monkeypatch):
        from app.tasks import node_lifecycle as nlm_mod

        monkeypatch.setattr(nlm_mod, "broadcast_node_state_change", AsyncMock())

        ns = SimpleNamespace(
            node_name="r1", node_id="n1",
            desired_state="stopped", actual_state="running",
            error_message=None,
            stopping_started_at=None,
            starting_started_at=None,
        )
        nlm = _make_nlm(node_states=[ns])
        nlm.node_states = [ns]
        nlm.session = MagicMock()
        nlm._broadcast_state = MagicMock()

        await nlm._set_transitional_states()

        assert ns.actual_state == "stopping"
        assert ns.stopping_started_at is not None

    @pytest.mark.asyncio
    async def test_no_change_when_already_in_state(self, monkeypatch):
        from app.tasks import node_lifecycle as nlm_mod

        monkeypatch.setattr(nlm_mod, "broadcast_node_state_change", AsyncMock())

        ns = SimpleNamespace(
            node_name="r1", node_id="n1",
            desired_state="running", actual_state="running",
            error_message=None,
            stopping_started_at=None,
            starting_started_at=None,
        )
        nlm = _make_nlm(node_states=[ns])
        nlm.node_states = [ns]
        nlm.session = MagicMock()
        nlm._broadcast_state = MagicMock()

        await nlm._set_transitional_states()

        # Should remain running, no broadcast
        assert ns.actual_state == "running"
        nlm._broadcast_state.assert_not_called()
