"""Tests for app.tasks.live_links — create_link_if_ready, teardown_link, _build_host_to_agent_map."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.state import LinkActualState, NodeActualState
from tests.factories import make_node_state, make_placement


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _build_host_to_agent_map
# ---------------------------------------------------------------------------

class TestBuildHostToAgentMap:
    @pytest.mark.asyncio
    async def test_empty_lab(self, test_db: Session, sample_lab: models.Lab):
        from app.tasks.live_links import _build_host_to_agent_map

        with patch("app.agent_client.is_agent_online", return_value=True):
            result = await _build_host_to_agent_map(test_db, sample_lab.id)
        assert result == {}

    @pytest.mark.asyncio
    async def test_with_placements(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        from app.tasks.live_links import _build_host_to_agent_map

        make_placement(test_db, sample_lab.id, "R1", sample_host.id)

        with patch("app.agent_client.is_agent_online", return_value=True):
            result = await _build_host_to_agent_map(test_db, sample_lab.id)
        assert sample_host.id in result

    @pytest.mark.asyncio
    async def test_includes_lab_agent(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        from app.tasks.live_links import _build_host_to_agent_map

        sample_lab.agent_id = sample_host.id
        test_db.commit()

        with patch("app.agent_client.is_agent_online", return_value=True):
            result = await _build_host_to_agent_map(test_db, sample_lab.id)
        assert sample_host.id in result

    @pytest.mark.asyncio
    async def test_offline_agent_excluded(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        from app.tasks.live_links import _build_host_to_agent_map

        make_placement(test_db, sample_lab.id, "R1", sample_host.id)

        with patch("app.agent_client.is_agent_online", return_value=False):
            result = await _build_host_to_agent_map(test_db, sample_lab.id)
        assert result == {}


# ---------------------------------------------------------------------------
# _sync_oper_state
# ---------------------------------------------------------------------------

class TestSyncOperState:
    def test_calls_recompute(self, test_db: Session, sample_link_state: models.LinkState):
        from app.tasks.live_links import _sync_oper_state

        with patch("app.tasks.live_links.recompute_link_oper_state") as mock_recompute:
            _sync_oper_state(test_db, sample_link_state)
            mock_recompute.assert_called_once_with(test_db, sample_link_state)


# ---------------------------------------------------------------------------
# create_link_if_ready
# ---------------------------------------------------------------------------

class TestCreateLinkIfReady:
    @pytest.mark.asyncio
    async def test_link_not_found_returns_false(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        from app.tasks.live_links import create_link_if_ready

        # Create a link state, then mock get_link_state_for_update to return None
        link_state = models.LinkState(
            lab_id=sample_lab.id,
            link_name="fake:eth1-fake2:eth1",
            source_node="fake",
            source_interface="eth1",
            target_node="fake2",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
        )
        test_db.add(link_state)
        test_db.commit()

        log_parts = []
        with patch("app.tasks.live_links.get_link_state_for_update", return_value=None):
            result = await create_link_if_ready(
                test_db, sample_lab.id, link_state, {}, log_parts
            )
        assert result is False
        assert any("not found" in p for p in log_parts)

    @pytest.mark.asyncio
    async def test_skip_locked_returns_false_silently(
        self, test_db: Session, sample_lab: models.Lab
    ):
        from app.tasks.live_links import create_link_if_ready

        link_state = models.LinkState(
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R2",
            target_interface="eth1",
            desired_state="up",
            actual_state="unknown",
        )
        test_db.add(link_state)
        test_db.commit()

        log_parts = []
        with patch("app.tasks.live_links.get_link_state_for_update", return_value=None):
            result = await create_link_if_ready(
                test_db, sample_lab.id, link_state, {}, log_parts, skip_locked=True
            )
        assert result is False
        assert log_parts == []  # silent when skip_locked=True

    @pytest.mark.asyncio
    async def test_endpoint_conflict_sets_error(
        self, test_db: Session, sample_lab: models.Lab, sample_link_state: models.LinkState
    ):
        from app.tasks.live_links import create_link_if_ready

        log_parts = []
        with (
            patch("app.tasks.live_links.get_link_state_for_update", return_value=sample_link_state),
            patch("app.tasks.live_links.claim_link_endpoints", return_value=(False, ["other-link"])),
            patch("app.tasks.live_links.recompute_link_oper_state"),
        ):
            result = await create_link_if_ready(
                test_db, sample_lab.id, sample_link_state, {}, log_parts
            )
        assert result is False
        assert sample_link_state.actual_state == LinkActualState.ERROR
        assert "Endpoint already in use" in (sample_link_state.error_message or "")

    @pytest.mark.asyncio
    async def test_nodes_not_running_sets_pending(
        self, test_db: Session, sample_lab: models.Lab, sample_link_state: models.LinkState
    ):
        from app.tasks.live_links import create_link_if_ready

        make_node_state(test_db, sample_lab.id, "R1", actual_state="stopped", desired="running")
        make_node_state(test_db, sample_lab.id, "R2", actual_state="stopped", desired="running")

        log_parts = []
        with (
            patch("app.tasks.live_links.get_link_state_for_update", return_value=sample_link_state),
            patch("app.tasks.live_links.claim_link_endpoints", return_value=(True, [])),
            patch("app.tasks.live_links.recompute_link_oper_state"),
        ):
            result = await create_link_if_ready(
                test_db, sample_lab.id, sample_link_state, {}, log_parts
            )
        assert result is False
        assert sample_link_state.actual_state == LinkActualState.PENDING

    @pytest.mark.asyncio
    async def test_missing_host_placement_sets_error(
        self, test_db: Session, sample_lab: models.Lab, sample_link_state: models.LinkState
    ):
        from app.tasks.live_links import create_link_if_ready

        make_node_state(test_db, sample_lab.id, "R1", actual_state=NodeActualState.RUNNING, is_ready=True, desired="running")
        make_node_state(test_db, sample_lab.id, "R2", actual_state=NodeActualState.RUNNING, is_ready=True, desired="running")

        log_parts = []
        with (
            patch("app.tasks.live_links.get_link_state_for_update", return_value=sample_link_state),
            patch("app.tasks.live_links.claim_link_endpoints", return_value=(True, [])),
            patch("app.tasks.live_links.lookup_endpoint_hosts", return_value=(None, None)),
            patch("app.tasks.live_links.recompute_link_oper_state"),
        ):
            result = await create_link_if_ready(
                test_db, sample_lab.id, sample_link_state, {}, log_parts
            )
        assert result is False
        assert sample_link_state.actual_state == LinkActualState.ERROR


# ---------------------------------------------------------------------------
# teardown_link
# ---------------------------------------------------------------------------

class TestTeardownLink:
    @pytest.mark.asyncio
    async def test_skip_inactive_link(self, test_db: Session, sample_lab: models.Lab):
        from app.tasks.live_links import teardown_link

        log_parts = []
        link_info = {"link_name": "R1:eth1-R2:eth1", "actual_state": "down"}
        result = await teardown_link(test_db, sample_lab.id, link_info, {}, log_parts)
        assert result is True
        assert any("skipped" in p for p in log_parts)

    @pytest.mark.asyncio
    async def test_same_host_no_host_id(self, test_db: Session, sample_lab: models.Lab):
        from app.tasks.live_links import teardown_link

        log_parts = []
        link_info = {
            "link_name": "R1:eth1-R2:eth1",
            "actual_state": LinkActualState.UP,
            "is_cross_host": False,
            "source_host_id": None,
            "target_host_id": None,
        }
        result = await teardown_link(test_db, sample_lab.id, link_info, {}, log_parts)
        assert result is False
        assert any("deferred" in p for p in log_parts)

    @pytest.mark.asyncio
    async def test_same_host_agent_unavailable(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        from app.tasks.live_links import teardown_link

        log_parts = []
        link_info = {
            "link_name": "R1:eth1-R2:eth1",
            "actual_state": LinkActualState.UP,
            "is_cross_host": False,
            "source_host_id": sample_host.id,
            "target_host_id": sample_host.id,
        }
        # host_to_agent is empty = agent unavailable
        result = await teardown_link(test_db, sample_lab.id, link_info, {}, log_parts)
        assert result is False

    @pytest.mark.asyncio
    async def test_same_host_success(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
        sample_link_state: models.LinkState
    ):
        from app.tasks.live_links import teardown_link

        log_parts = []
        link_info = {
            "link_name": sample_link_state.link_name,
            "actual_state": LinkActualState.UP,
            "is_cross_host": False,
            "source_host_id": sample_host.id,
            "target_host_id": sample_host.id,
            "source_node": "R1",
            "target_node": "R2",
            "source_interface": "eth1",
            "target_interface": "eth1",
        }
        host_to_agent = {sample_host.id: sample_host}

        with (
            patch("app.agent_client.delete_link_on_agent", new_callable=AsyncMock, return_value={"success": True}),
            patch("app.tasks.live_links.normalize_for_node", side_effect=lambda *a: a[3]),
            patch("app.tasks.live_links.recompute_link_oper_state"),
        ):
            result = await teardown_link(
                test_db, sample_lab.id, link_info, host_to_agent, log_parts
            )
        assert result is True
        assert any("removed" in p for p in log_parts)

    @pytest.mark.asyncio
    async def test_same_host_agent_error(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
        sample_link_state: models.LinkState
    ):
        from app.tasks.live_links import teardown_link

        log_parts = []
        link_info = {
            "link_name": sample_link_state.link_name,
            "actual_state": LinkActualState.UP,
            "is_cross_host": False,
            "source_host_id": sample_host.id,
            "target_host_id": sample_host.id,
            "source_node": "R1",
            "target_node": "R2",
            "source_interface": "eth1",
            "target_interface": "eth1",
        }
        host_to_agent = {sample_host.id: sample_host}

        with (
            patch("app.agent_client.delete_link_on_agent", new_callable=AsyncMock, side_effect=Exception("timeout")),
            patch("app.tasks.live_links.normalize_for_node", side_effect=lambda *a: a[3]),
            patch("app.tasks.live_links.recompute_link_oper_state"),
        ):
            result = await teardown_link(
                test_db, sample_lab.id, link_info, host_to_agent, log_parts
            )
        assert result is False
        assert any("FAILED" in p for p in log_parts)

    @pytest.mark.asyncio
    async def test_cross_host_agents_unavailable(
        self, test_db: Session, sample_lab: models.Lab
    ):
        from app.tasks.live_links import teardown_link

        log_parts = []
        link_info = {
            "link_name": "R1:eth1-R3:eth1",
            "actual_state": LinkActualState.UP,
            "is_cross_host": True,
            "source_host_id": "host-a",
            "target_host_id": "host-b",
        }
        result = await teardown_link(test_db, sample_lab.id, link_info, {}, log_parts)
        assert result is False
        assert any("deferred" in p for p in log_parts)


# ---------------------------------------------------------------------------
# _update_job_log
# ---------------------------------------------------------------------------

class TestUpdateJobLog:
    def test_updates_log_path(self, test_db: Session, sample_job: models.Job):
        from app.tasks.live_links import _update_job_log

        _update_job_log(test_db, sample_job, ["line1", "line2"])
        assert sample_job.log_path == "line1\nline2"