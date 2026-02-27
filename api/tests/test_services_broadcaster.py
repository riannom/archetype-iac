"""Tests for broadcaster service-level publish functions.

Covers publish_node_state (display_state auto-compute, enforcement attempts,
image sync fields), publish_link_state (cross-host fields), publish_lab_state,
publish_job_progress, and convenience wrapper functions.

Does NOT duplicate basic publish/subscribe tests from test_broadcaster.py.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.broadcaster import (
    StateBroadcaster,
    broadcast_link_state_change,
    broadcast_node_state_change,
    get_broadcaster,
)


@pytest.fixture
def mock_redis():
    """Create a mock Redis client that captures published messages."""
    mock = MagicMock()
    mock.publish = AsyncMock(return_value=1)
    mock.close = AsyncMock()
    return mock


def _make_broadcaster(mock_redis) -> StateBroadcaster:
    """Create a StateBroadcaster with a pre-set mock Redis."""
    b = StateBroadcaster("redis://localhost")
    b._redis = mock_redis
    return b


def _last_published_message(mock_redis) -> dict:
    """Extract the last published JSON message from mock_redis.publish calls."""
    call_args = mock_redis.publish.call_args
    return json.loads(call_args[0][1])


# ---------------------------------------------------------------------------
# TestPublishNodeState
# ---------------------------------------------------------------------------


class TestPublishNodeState:
    """Tests for publish_node_state with auto-computed display_state
    and optional fields (enforcement, image sync)."""

    @pytest.mark.asyncio
    async def test_auto_computes_display_state_starting(self, mock_redis):
        """When display_state is None, should auto-compute from actual+desired."""
        b = _make_broadcaster(mock_redis)
        await b.publish_node_state(
            lab_id="lab-1",
            node_id="n1",
            node_name="router-1",
            desired_state="running",
            actual_state="pending",
        )
        msg = _last_published_message(mock_redis)
        # pending + desired=running -> display_state "starting"
        assert msg["data"]["display_state"] == "starting"

    @pytest.mark.asyncio
    async def test_auto_computes_display_state_stopping(self, mock_redis):
        """running + desired=stopped should produce display_state=stopping."""
        b = _make_broadcaster(mock_redis)
        await b.publish_node_state(
            lab_id="lab-1",
            node_id="n1",
            node_name="router-1",
            desired_state="stopped",
            actual_state="running",
        )
        msg = _last_published_message(mock_redis)
        assert msg["data"]["display_state"] == "stopping"

    @pytest.mark.asyncio
    async def test_explicit_display_state_overrides_auto(self, mock_redis):
        """When display_state is explicitly provided, skip auto-compute."""
        b = _make_broadcaster(mock_redis)
        await b.publish_node_state(
            lab_id="lab-1",
            node_id="n1",
            node_name="router-1",
            desired_state="running",
            actual_state="pending",
            display_state="custom-state",
        )
        msg = _last_published_message(mock_redis)
        assert msg["data"]["display_state"] == "custom-state"

    @pytest.mark.asyncio
    async def test_includes_all_fields(self, mock_redis):
        """Should include every documented field in the message data."""
        b = _make_broadcaster(mock_redis)
        await b.publish_node_state(
            lab_id="lab-1",
            node_id="n1",
            node_name="router-1",
            desired_state="running",
            actual_state="running",
            is_ready=True,
            error_message="some error",
            host_id="agent-1",
            host_name="Agent 1",
            image_sync_status="syncing",
            image_sync_message="50% complete",
            will_retry=True,
            display_state="running",
            enforcement_attempts=2,
            max_enforcement_attempts=5,
            starting_started_at="2026-01-01T00:00:00Z",
        )
        data = _last_published_message(mock_redis)["data"]
        assert data["node_id"] == "n1"
        assert data["node_name"] == "router-1"
        assert data["desired_state"] == "running"
        assert data["actual_state"] == "running"
        assert data["is_ready"] is True
        assert data["error_message"] == "some error"
        assert data["host_id"] == "agent-1"
        assert data["host_name"] == "Agent 1"
        assert data["image_sync_status"] == "syncing"
        assert data["image_sync_message"] == "50% complete"
        assert data["will_retry"] is True
        assert data["display_state"] == "running"
        assert data["enforcement_attempts"] == 2
        assert data["max_enforcement_attempts"] == 5
        assert data["starting_started_at"] == "2026-01-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_enforcement_attempts_defaults_to_zero(self, mock_redis):
        """Enforcement attempts should default to 0 when not specified."""
        b = _make_broadcaster(mock_redis)
        await b.publish_node_state(
            lab_id="lab-1",
            node_id="n1",
            node_name="router-1",
            desired_state="running",
            actual_state="running",
        )
        data = _last_published_message(mock_redis)["data"]
        assert data["enforcement_attempts"] == 0
        assert data["max_enforcement_attempts"] == 0

    @pytest.mark.asyncio
    async def test_image_sync_fields_none_by_default(self, mock_redis):
        """Image sync fields should be None when not provided."""
        b = _make_broadcaster(mock_redis)
        await b.publish_node_state(
            lab_id="lab-1",
            node_id="n1",
            node_name="router-1",
            desired_state="running",
            actual_state="running",
        )
        data = _last_published_message(mock_redis)["data"]
        assert data["image_sync_status"] is None
        assert data["image_sync_message"] is None

    @pytest.mark.asyncio
    async def test_failure_returns_zero(self, mock_redis):
        """On Redis publish failure, should return 0 and not raise."""
        mock_redis.publish.side_effect = Exception("Connection refused")
        b = _make_broadcaster(mock_redis)
        result = await b.publish_node_state(
            lab_id="lab-1",
            node_id="n1",
            node_name="router-1",
            desired_state="running",
            actual_state="pending",
        )
        assert result == 0


# ---------------------------------------------------------------------------
# TestPublishLinkState
# ---------------------------------------------------------------------------


class TestPublishLinkState:
    """Tests for publish_link_state with cross-host and oper fields."""

    @pytest.mark.asyncio
    async def test_includes_all_link_fields(self, mock_redis):
        """Should include all link fields including cross-host metadata."""
        b = _make_broadcaster(mock_redis)
        await b.publish_link_state(
            lab_id="lab-1",
            link_name="R1:eth1-R2:eth1",
            desired_state="up",
            actual_state="up",
            source_node="R1",
            target_node="R2",
            error_message=None,
            source_oper_state="up",
            target_oper_state="up",
            source_oper_reason=None,
            target_oper_reason=None,
            oper_epoch=3,
            is_cross_host=True,
            vni=50001,
            source_host_id="agent-1",
            target_host_id="agent-2",
            source_vlan_tag=100,
            target_vlan_tag=200,
        )
        data = _last_published_message(mock_redis)["data"]
        assert data["link_name"] == "R1:eth1-R2:eth1"
        assert data["is_cross_host"] is True
        assert data["vni"] == 50001
        assert data["source_host_id"] == "agent-1"
        assert data["target_host_id"] == "agent-2"
        assert data["source_vlan_tag"] == 100
        assert data["target_vlan_tag"] == 200
        assert data["oper_epoch"] == 3

    @pytest.mark.asyncio
    async def test_cross_host_fields_none_for_same_host(self, mock_redis):
        """Cross-host fields should be None when not provided."""
        b = _make_broadcaster(mock_redis)
        await b.publish_link_state(
            lab_id="lab-1",
            link_name="R1:eth1-R2:eth1",
            desired_state="up",
            actual_state="up",
            source_node="R1",
            target_node="R2",
        )
        data = _last_published_message(mock_redis)["data"]
        assert data["is_cross_host"] is None
        assert data["vni"] is None
        assert data["source_host_id"] is None
        assert data["target_host_id"] is None
        assert data["source_vlan_tag"] is None
        assert data["target_vlan_tag"] is None

    @pytest.mark.asyncio
    async def test_oper_state_fields(self, mock_redis):
        """Should include per-endpoint oper state and reason."""
        b = _make_broadcaster(mock_redis)
        await b.publish_link_state(
            lab_id="lab-1",
            link_name="R1:eth1-R2:eth1",
            desired_state="up",
            actual_state="down",
            source_node="R1",
            target_node="R2",
            source_oper_state="down",
            target_oper_state="down",
            source_oper_reason="peer_host_offline",
            target_oper_reason="local_interface_down",
        )
        data = _last_published_message(mock_redis)["data"]
        assert data["source_oper_state"] == "down"
        assert data["target_oper_state"] == "down"
        assert data["source_oper_reason"] == "peer_host_offline"
        assert data["target_oper_reason"] == "local_interface_down"

    @pytest.mark.asyncio
    async def test_failure_returns_zero(self, mock_redis):
        """On Redis failure, should return 0 gracefully."""
        mock_redis.publish.side_effect = RuntimeError("timeout")
        b = _make_broadcaster(mock_redis)
        result = await b.publish_link_state(
            lab_id="lab-1",
            link_name="R1:eth1-R2:eth1",
            desired_state="up",
            actual_state="up",
            source_node="R1",
            target_node="R2",
        )
        assert result == 0


# ---------------------------------------------------------------------------
# TestPublishLabState
# ---------------------------------------------------------------------------


class TestPublishLabState:
    """Tests for publish_lab_state."""

    @pytest.mark.asyncio
    async def test_lab_state_without_error(self, mock_redis):
        """Should publish lab state with no error."""
        b = _make_broadcaster(mock_redis)
        result = await b.publish_lab_state(lab_id="lab-1", state="running")
        assert result == 1
        msg = _last_published_message(mock_redis)
        assert msg["type"] == "lab_state"
        assert msg["data"]["lab_id"] == "lab-1"
        assert msg["data"]["state"] == "running"
        assert msg["data"]["error"] is None

    @pytest.mark.asyncio
    async def test_lab_state_with_error(self, mock_redis):
        """Should include error message when provided."""
        b = _make_broadcaster(mock_redis)
        await b.publish_lab_state(
            lab_id="lab-1", state="error", error="Deploy failed"
        )
        data = _last_published_message(mock_redis)["data"]
        assert data["state"] == "error"
        assert data["error"] == "Deploy failed"

    @pytest.mark.asyncio
    async def test_failure_returns_zero(self, mock_redis):
        """On Redis failure, should return 0."""
        mock_redis.publish.side_effect = Exception("broken")
        b = _make_broadcaster(mock_redis)
        result = await b.publish_lab_state(lab_id="lab-1", state="running")
        assert result == 0


# ---------------------------------------------------------------------------
# TestPublishJobProgress
# ---------------------------------------------------------------------------


class TestPublishJobProgress:
    """Tests for publish_job_progress."""

    @pytest.mark.asyncio
    async def test_progress_message(self, mock_redis):
        """Should include progress_message in payload."""
        b = _make_broadcaster(mock_redis)
        result = await b.publish_job_progress(
            lab_id="lab-1",
            job_id="job-1",
            action="up",
            status="running",
            progress_message="Deploying node R1",
        )
        assert result == 1
        data = _last_published_message(mock_redis)["data"]
        assert data["job_id"] == "job-1"
        assert data["action"] == "up"
        assert data["status"] == "running"
        assert data["progress_message"] == "Deploying node R1"
        assert data["error_message"] is None

    @pytest.mark.asyncio
    async def test_error_message(self, mock_redis):
        """Should include error_message when job fails."""
        b = _make_broadcaster(mock_redis)
        await b.publish_job_progress(
            lab_id="lab-1",
            job_id="job-1",
            action="up",
            status="failed",
            error_message="Container create failed",
        )
        data = _last_published_message(mock_redis)["data"]
        assert data["status"] == "failed"
        assert data["error_message"] == "Container create failed"

    @pytest.mark.asyncio
    async def test_failure_returns_zero(self, mock_redis):
        """On Redis failure, should return 0."""
        mock_redis.publish.side_effect = Exception("connection lost")
        b = _make_broadcaster(mock_redis)
        result = await b.publish_job_progress(
            lab_id="lab-1",
            job_id="job-1",
            action="down",
            status="running",
        )
        assert result == 0


# ---------------------------------------------------------------------------
# TestConvenienceFunctions
# ---------------------------------------------------------------------------


class TestServiceConvenienceFunctions:
    """Tests for module-level convenience functions."""

    @pytest.mark.asyncio
    async def test_broadcast_node_state_change_passes_all_kwargs(self):
        """broadcast_node_state_change should pass all keyword args through."""
        with patch("app.services.broadcaster.get_broadcaster") as mock_get:
            mock_bc = MagicMock()
            mock_bc.publish_node_state = AsyncMock(return_value=1)
            mock_get.return_value = mock_bc

            await broadcast_node_state_change(
                lab_id="lab-1",
                node_id="n1",
                node_name="router-1",
                desired_state="running",
                actual_state="pending",
                enforcement_attempts=3,
                max_enforcement_attempts=5,
                image_sync_status="syncing",
                image_sync_message="30%",
                will_retry=True,
                starting_started_at="2026-01-01T00:00:00Z",
            )

            _, kwargs = mock_bc.publish_node_state.call_args
            assert kwargs["enforcement_attempts"] == 3
            assert kwargs["max_enforcement_attempts"] == 5
            assert kwargs["image_sync_status"] == "syncing"
            assert kwargs["image_sync_message"] == "30%"
            assert kwargs["will_retry"] is True
            assert kwargs["starting_started_at"] == "2026-01-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_broadcast_link_state_change_passes_cross_host_fields(self):
        """broadcast_link_state_change should pass cross-host fields through."""
        with patch("app.services.broadcaster.get_broadcaster") as mock_get:
            mock_bc = MagicMock()
            mock_bc.publish_link_state = AsyncMock(return_value=1)
            mock_get.return_value = mock_bc

            await broadcast_link_state_change(
                lab_id="lab-1",
                link_name="R1:eth1-R2:eth1",
                desired_state="up",
                actual_state="up",
                source_node="R1",
                target_node="R2",
                is_cross_host=True,
                vni=50001,
                source_host_id="agent-1",
                target_host_id="agent-2",
                source_vlan_tag=100,
                target_vlan_tag=200,
            )

            _, kwargs = mock_bc.publish_link_state.call_args
            assert kwargs["is_cross_host"] is True
            assert kwargs["vni"] == 50001
            assert kwargs["source_host_id"] == "agent-1"
            assert kwargs["target_host_id"] == "agent-2"
            assert kwargs["source_vlan_tag"] == 100
            assert kwargs["target_vlan_tag"] == 200

    def test_get_broadcaster_singleton_creates_instance(self):
        """get_broadcaster should create a StateBroadcaster on first call."""
        with patch("app.services.broadcaster._broadcaster", None):
            with patch("app.services.broadcaster.settings") as mock_settings:
                mock_settings.redis_url = "redis://test:6379"
                b = get_broadcaster()
                assert isinstance(b, StateBroadcaster)
                assert b._redis_url == "redis://test:6379"

    def test_get_broadcaster_singleton_returns_same_instance(self):
        """Repeated calls to get_broadcaster should return the same object."""
        with patch("app.services.broadcaster._broadcaster", None):
            with patch("app.services.broadcaster.settings") as mock_settings:
                mock_settings.redis_url = "redis://test:6379"
                b1 = get_broadcaster()
                # Patch _broadcaster to the instance we just created
                with patch("app.services.broadcaster._broadcaster", b1):
                    b2 = get_broadcaster()
                    assert b1 is b2
