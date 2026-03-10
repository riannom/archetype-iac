"""Tests for app.tasks.live_nodes — deploy/destroy, cleanup, debouncer, host map."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from tests.factories import make_node_state, make_placement


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _cleanup_node_records
# ---------------------------------------------------------------------------

class TestCleanupNodeRecords:
    """Tests for _cleanup_node_records()."""

    def test_deletes_by_node_definition_id(self, test_db: Session, sample_lab: models.Lab):
        from app.tasks.live_nodes import _cleanup_node_records

        ns = make_node_state(test_db, sample_lab.id, "n1", "R1")
        ns.node_definition_id = "ndef-1"
        test_db.commit()

        p = models.NodePlacement(
            lab_id=sample_lab.id,
            node_name="R1",
            host_id="host-1",
            node_definition_id="ndef-1",
        )
        test_db.add(p)
        test_db.commit()

        _cleanup_node_records(
            test_db, sample_lab.id, node_name="R1", node_definition_id="ndef-1"
        )

        remaining = test_db.query(models.NodeState).filter(
            models.NodeState.lab_id == sample_lab.id,
            models.NodeState.node_definition_id == "ndef-1",
        ).all()
        assert remaining == []

    def test_deletes_by_node_name_fallback(self, test_db: Session, sample_lab: models.Lab):
        from app.tasks.live_nodes import _cleanup_node_records

        make_node_state(test_db, sample_lab.id, "n1", "R1")
        make_placement(test_db, sample_lab.id, "R1", "host-1")

        _cleanup_node_records(
            test_db, sample_lab.id, node_name="R1", node_definition_id=None
        )

        remaining_ns = test_db.query(models.NodeState).filter(
            models.NodeState.lab_id == sample_lab.id,
            models.NodeState.node_name == "R1",
        ).all()
        assert remaining_ns == []

        remaining_np = test_db.query(models.NodePlacement).filter(
            models.NodePlacement.lab_id == sample_lab.id,
            models.NodePlacement.node_name == "R1",
        ).all()
        assert remaining_np == []

    def test_no_records_to_delete(self, test_db: Session, sample_lab: models.Lab):
        from app.tasks.live_nodes import _cleanup_node_records

        # Should not raise
        _cleanup_node_records(test_db, sample_lab.id, node_name="nonexistent")


# ---------------------------------------------------------------------------
# _build_host_to_agent_map (live_nodes version)
# ---------------------------------------------------------------------------

class TestBuildHostToAgentMapLiveNodes:
    @pytest.mark.asyncio
    async def test_empty_lab(self, test_db: Session, sample_lab: models.Lab):
        from app.tasks.live_nodes import _build_host_to_agent_map

        with patch("app.agent_client.is_agent_online", return_value=True):
            result = await _build_host_to_agent_map(test_db, sample_lab.id, sample_lab)
        assert result == {}

    @pytest.mark.asyncio
    async def test_with_placements(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        from app.tasks.live_nodes import _build_host_to_agent_map

        make_placement(test_db, sample_lab.id, "R1", sample_host.id)

        with patch("app.agent_client.is_agent_online", return_value=True):
            result = await _build_host_to_agent_map(test_db, sample_lab.id, sample_lab)
        assert sample_host.id in result

    @pytest.mark.asyncio
    async def test_includes_lab_agent_id(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        from app.tasks.live_nodes import _build_host_to_agent_map

        sample_lab.agent_id = sample_host.id
        test_db.commit()

        with patch("app.agent_client.is_agent_online", return_value=True):
            result = await _build_host_to_agent_map(test_db, sample_lab.id, sample_lab)
        assert sample_host.id in result

    @pytest.mark.asyncio
    async def test_offline_agent_excluded(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        from app.tasks.live_nodes import _build_host_to_agent_map

        make_placement(test_db, sample_lab.id, "R1", sample_host.id)

        with patch("app.agent_client.is_agent_online", return_value=False):
            result = await _build_host_to_agent_map(test_db, sample_lab.id, sample_lab)
        assert result == {}


# ---------------------------------------------------------------------------
# destroy_node_immediately
# ---------------------------------------------------------------------------

class TestDestroyNodeImmediately:
    @pytest.mark.asyncio
    async def test_empty_node_name_returns_false(self, test_db: Session, sample_lab: models.Lab):
        from app.tasks.live_nodes import destroy_node_immediately

        result = await destroy_node_immediately(
            test_db, sample_lab.id, {"node_name": ""}, {}
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_undeployed_node_skips_agent(
        self, test_db: Session, sample_lab: models.Lab
    ):
        from app.tasks.live_nodes import destroy_node_immediately

        make_node_state(test_db, sample_lab.id, "n1", "R1", actual_state="undeployed")

        result = await destroy_node_immediately(
            test_db,
            sample_lab.id,
            {"node_name": "R1", "actual_state": "undeployed"},
            {},
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_no_agent_available_returns_false(
        self, test_db: Session, sample_lab: models.Lab
    ):
        from app.tasks.live_nodes import destroy_node_immediately

        with patch("app.agent_client.is_agent_online", return_value=False):
            result = await destroy_node_immediately(
                test_db,
                sample_lab.id,
                {"node_name": "R1", "actual_state": "running", "host_id": "missing"},
                {},
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_agent_success_cleans_up(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        from app.tasks.live_nodes import destroy_node_immediately

        make_node_state(test_db, sample_lab.id, "n1", "R1", actual_state="running")

        host_to_agent = {sample_host.id: sample_host}

        with patch(
            "app.agent_client.destroy_node_on_agent",
            new_callable=AsyncMock,
            return_value={"success": True},
        ):
            result = await destroy_node_immediately(
                test_db,
                sample_lab.id,
                {
                    "node_name": "R1",
                    "node_id": "n1",
                    "actual_state": "running",
                    "host_id": sample_host.id,
                    "provider": "docker",
                },
                host_to_agent,
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_agent_failure_returns_false(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        from app.tasks.live_nodes import destroy_node_immediately

        host_to_agent = {sample_host.id: sample_host}

        with patch(
            "app.agent_client.destroy_node_on_agent",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "container not found"},
        ):
            result = await destroy_node_immediately(
                test_db,
                sample_lab.id,
                {
                    "node_name": "R1",
                    "actual_state": "running",
                    "host_id": sample_host.id,
                    "provider": "docker",
                },
                host_to_agent,
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_agent_exception_returns_false(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        from app.tasks.live_nodes import destroy_node_immediately

        host_to_agent = {sample_host.id: sample_host}

        with patch(
            "app.agent_client.destroy_node_on_agent",
            new_callable=AsyncMock,
            side_effect=Exception("connection timeout"),
        ):
            result = await destroy_node_immediately(
                test_db,
                sample_lab.id,
                {
                    "node_name": "R1",
                    "actual_state": "running",
                    "host_id": sample_host.id,
                },
                host_to_agent,
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_falls_back_to_any_online_agent(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        from app.tasks.live_nodes import destroy_node_immediately

        host_to_agent = {sample_host.id: sample_host}

        with (
            patch("app.agent_client.is_agent_online", return_value=True),
            patch(
                "app.agent_client.destroy_node_on_agent",
                new_callable=AsyncMock,
                return_value={"success": True},
            ),
        ):
            result = await destroy_node_immediately(
                test_db,
                sample_lab.id,
                {
                    "node_name": "R1",
                    "actual_state": "running",
                    "host_id": None,  # no host_id -> falls back
                    "provider": "docker",
                },
                host_to_agent,
            )
        assert result is True


# ---------------------------------------------------------------------------
# deploy_node_immediately
# ---------------------------------------------------------------------------

class TestDeployNodeImmediately:
    @pytest.mark.asyncio
    async def test_no_agent_sets_pending(
        self, test_db: Session, running_lab: models.Lab
    ):
        from app.tasks.live_nodes import deploy_node_immediately

        ns = make_node_state(test_db, running_lab.id, "n1", "R1")

        with (
            patch(
                "app.tasks.live_nodes.broadcast_node_state_change",
                new_callable=AsyncMock,
            ),
            patch(
                "app.agent_client.get_agent_for_lab",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("app.utils.lab.get_lab_provider", return_value="docker"),
        ):
            result = await deploy_node_immediately(test_db, running_lab.id, ns, running_lab)

        assert result is False
        assert ns.error_message == "Waiting for agent"

    @pytest.mark.asyncio
    async def test_success_creates_job(
        self, test_db: Session, running_lab: models.Lab, sample_host: models.Host
    ):
        from app.tasks.live_nodes import deploy_node_immediately

        ns = make_node_state(test_db, running_lab.id, "n1", "R1")

        with (
            patch(
                "app.tasks.live_nodes.broadcast_node_state_change",
                new_callable=AsyncMock,
            ),
            patch(
                "app.agent_client.get_agent_for_lab",
                new_callable=AsyncMock,
                return_value=sample_host,
            ),
            patch("app.utils.lab.get_lab_provider", return_value="docker"),
            patch("app.tasks.live_nodes.safe_create_task") as mock_task,
        ):
            result = await deploy_node_immediately(test_db, running_lab.id, ns, running_lab)

        assert result is True
        assert ns.desired_state == "running"
        assert ns.actual_state == "pending"
        mock_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_node_provider_when_def_exists(
        self, test_db: Session, running_lab: models.Lab, sample_host: models.Host
    ):
        from app.tasks.live_nodes import deploy_node_immediately

        # Create a Node definition
        node_def = models.Node(
            id="ndef-1",
            lab_id=running_lab.id,
            gui_id="n1",
            display_name="R1",
            container_name="archetype-test-r1",
            device="linux",
            host_id=sample_host.id,
        )
        test_db.add(node_def)
        test_db.commit()

        ns = make_node_state(test_db, running_lab.id, "n1", "R1")
        ns.node_definition_id = "ndef-1"
        test_db.commit()

        with (
            patch(
                "app.tasks.live_nodes.broadcast_node_state_change",
                new_callable=AsyncMock,
            ),
            patch(
                "app.agent_client.get_agent_for_lab",
                new_callable=AsyncMock,
                return_value=sample_host,
            ),
            patch("app.utils.lab.get_node_provider", return_value="docker") as mock_np,
            patch("app.tasks.live_nodes.safe_create_task"),
        ):
            result = await deploy_node_immediately(test_db, running_lab.id, ns, running_lab)

        assert result is True
        mock_np.assert_called_once()


# ---------------------------------------------------------------------------
# NodeChangeDebouncer
# ---------------------------------------------------------------------------

class TestNodeChangeDebouncer:
    def test_init(self):
        from app.tasks.live_nodes import NodeChangeDebouncer

        d = NodeChangeDebouncer()
        assert d._pending_adds == {}
        assert d._pending_removes == {}
        assert d._debounce_tasks == {}

    @pytest.mark.asyncio
    async def test_add_changes_accumulates(self):
        from app.tasks.live_nodes import NodeChangeDebouncer

        d = NodeChangeDebouncer()

        with patch.object(d, "_process_after_delay", new_callable=AsyncMock):
            await d.add_changes("lab1", ["n1"], [])
            assert "n1" in d._pending_adds["lab1"]

            await d.add_changes("lab1", ["n2"], [{"node_name": "R3"}])
            assert "n2" in d._pending_adds["lab1"]
            assert len(d._pending_removes["lab1"]) == 1

    @pytest.mark.asyncio
    async def test_duplicate_removes_skipped(self):
        from app.tasks.live_nodes import NodeChangeDebouncer

        d = NodeChangeDebouncer()

        with patch.object(d, "_process_after_delay", new_callable=AsyncMock):
            await d.add_changes("lab1", [], [{"node_name": "R1"}])
            await d.add_changes("lab1", [], [{"node_name": "R1"}])
            assert len(d._pending_removes["lab1"]) == 1


# ---------------------------------------------------------------------------
# get_debouncer singleton
# ---------------------------------------------------------------------------

class TestGetDebouncer:
    def test_returns_singleton(self):
        from app.tasks.live_nodes import get_debouncer, NodeChangeDebouncer
        import app.tasks.live_nodes as mod

        mod._debouncer = None
        d1 = get_debouncer()
        d2 = get_debouncer()
        assert d1 is d2
        assert isinstance(d1, NodeChangeDebouncer)
        # Clean up
        mod._debouncer = None