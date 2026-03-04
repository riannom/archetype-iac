"""Tests for app/events/publisher.py — cleanup event publisher functions."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events.cleanup_events import CLEANUP_CHANNEL, CleanupEvent, CleanupEventType


# All tests are async because the publisher functions are async.
pytestmark = pytest.mark.asyncio


async def _capture_publish(emit_fn, *args, **kwargs) -> CleanupEvent:
    """Call an emit_* function and capture the CleanupEvent it publishes.

    Returns the deserialized CleanupEvent from the publish call.
    """
    mock_redis = AsyncMock()
    with patch("app.events.publisher._publisher_redis", None):
        with patch("app.events.publisher._get_redis", AsyncMock(return_value=mock_redis)):
            await emit_fn(*args, **kwargs)

    mock_redis.publish.assert_awaited_once()
    call_args = mock_redis.publish.call_args
    channel = call_args[0][0]
    payload_json = call_args[0][1]

    assert channel == CLEANUP_CHANNEL
    return CleanupEvent.from_json(payload_json)


class TestEmitLabDeleted:
    async def test_event_construction(self):
        from app.events.publisher import emit_lab_deleted

        event = await _capture_publish(emit_lab_deleted, lab_id="lab-123")
        assert event.event_type == CleanupEventType.LAB_DELETED
        assert event.lab_id == "lab-123"
        assert event.node_name is None
        assert event.agent_id is None

    async def test_redis_error_swallowed(self):
        from app.events.publisher import emit_lab_deleted

        mock_redis = AsyncMock()
        mock_redis.publish.side_effect = ConnectionError("Redis down")
        with patch("app.events.publisher._publisher_redis", None):
            with patch("app.events.publisher._get_redis", AsyncMock(return_value=mock_redis)):
                # Should not raise
                await emit_lab_deleted(lab_id="lab-123")


class TestEmitNodeRemoved:
    async def test_event_construction(self):
        from app.events.publisher import emit_node_removed

        event = await _capture_publish(
            emit_node_removed, lab_id="lab-1", node_name="R1", agent_id="agent-x",
        )
        assert event.event_type == CleanupEventType.NODE_REMOVED
        assert event.lab_id == "lab-1"
        assert event.node_name == "R1"
        assert event.agent_id == "agent-x"

    async def test_agent_id_optional(self):
        from app.events.publisher import emit_node_removed

        event = await _capture_publish(
            emit_node_removed, lab_id="lab-1", node_name="R1",
        )
        assert event.agent_id is None

    async def test_redis_error_swallowed(self):
        from app.events.publisher import emit_node_removed

        mock_redis = AsyncMock()
        mock_redis.publish.side_effect = ConnectionError("Redis down")
        with patch("app.events.publisher._publisher_redis", None):
            with patch("app.events.publisher._get_redis", AsyncMock(return_value=mock_redis)):
                await emit_node_removed(lab_id="lab-1", node_name="R1")


class TestEmitNodePlacementChanged:
    async def test_event_construction(self):
        from app.events.publisher import emit_node_placement_changed

        event = await _capture_publish(
            emit_node_placement_changed,
            lab_id="lab-1",
            node_name="R1",
            agent_id="new-agent",
            old_agent_id="old-agent",
        )
        assert event.event_type == CleanupEventType.NODE_PLACEMENT_CHANGED
        assert event.lab_id == "lab-1"
        assert event.node_name == "R1"
        assert event.agent_id == "new-agent"
        assert event.old_agent_id == "old-agent"

    async def test_optional_agent_ids(self):
        from app.events.publisher import emit_node_placement_changed

        event = await _capture_publish(
            emit_node_placement_changed, lab_id="lab-1", node_name="R1",
        )
        assert event.agent_id is None
        assert event.old_agent_id is None

    async def test_redis_error_swallowed(self):
        from app.events.publisher import emit_node_placement_changed

        mock_redis = AsyncMock()
        mock_redis.publish.side_effect = ConnectionError("Redis down")
        with patch("app.events.publisher._publisher_redis", None):
            with patch("app.events.publisher._get_redis", AsyncMock(return_value=mock_redis)):
                await emit_node_placement_changed(
                    lab_id="lab-1", node_name="R1",
                )


class TestEmitLinkRemoved:
    async def test_event_construction(self):
        from app.events.publisher import emit_link_removed

        event = await _capture_publish(emit_link_removed, lab_id="lab-42")
        assert event.event_type == CleanupEventType.LINK_REMOVED
        assert event.lab_id == "lab-42"

    async def test_redis_error_swallowed(self):
        from app.events.publisher import emit_link_removed

        mock_redis = AsyncMock()
        mock_redis.publish.side_effect = ConnectionError("Redis down")
        with patch("app.events.publisher._publisher_redis", None):
            with patch("app.events.publisher._get_redis", AsyncMock(return_value=mock_redis)):
                await emit_link_removed(lab_id="lab-42")


class TestEmitDeployFinished:
    async def test_event_construction(self):
        from app.events.publisher import emit_deploy_finished

        event = await _capture_publish(
            emit_deploy_finished, lab_id="lab-1", agent_id="a1", job_id="j1",
        )
        assert event.event_type == CleanupEventType.DEPLOY_FINISHED
        assert event.lab_id == "lab-1"
        assert event.agent_id == "a1"
        assert event.job_id == "j1"

    async def test_optional_fields(self):
        from app.events.publisher import emit_deploy_finished

        event = await _capture_publish(emit_deploy_finished, lab_id="lab-1")
        assert event.agent_id is None
        assert event.job_id is None

    async def test_redis_error_swallowed(self):
        from app.events.publisher import emit_deploy_finished

        mock_redis = AsyncMock()
        mock_redis.publish.side_effect = ConnectionError("Redis down")
        with patch("app.events.publisher._publisher_redis", None):
            with patch("app.events.publisher._get_redis", AsyncMock(return_value=mock_redis)):
                await emit_deploy_finished(lab_id="lab-1")


class TestEmitDestroyFinished:
    async def test_event_construction(self):
        from app.events.publisher import emit_destroy_finished

        event = await _capture_publish(
            emit_destroy_finished, lab_id="lab-2", agent_id="a2", job_id="j2",
        )
        assert event.event_type == CleanupEventType.DESTROY_FINISHED
        assert event.lab_id == "lab-2"
        assert event.agent_id == "a2"
        assert event.job_id == "j2"

    async def test_redis_error_swallowed(self):
        from app.events.publisher import emit_destroy_finished

        mock_redis = AsyncMock()
        mock_redis.publish.side_effect = ConnectionError("Redis down")
        with patch("app.events.publisher._publisher_redis", None):
            with patch("app.events.publisher._get_redis", AsyncMock(return_value=mock_redis)):
                await emit_destroy_finished(lab_id="lab-2")


class TestEmitJobFailed:
    async def test_event_construction(self):
        from app.events.publisher import emit_job_failed

        event = await _capture_publish(
            emit_job_failed, lab_id="lab-3", job_id="j3", job_action="up",
        )
        assert event.event_type == CleanupEventType.JOB_FAILED
        assert event.lab_id == "lab-3"
        assert event.job_id == "j3"
        assert event.job_action == "up"

    async def test_redis_error_swallowed(self):
        from app.events.publisher import emit_job_failed

        mock_redis = AsyncMock()
        mock_redis.publish.side_effect = ConnectionError("Redis down")
        with patch("app.events.publisher._publisher_redis", None):
            with patch("app.events.publisher._get_redis", AsyncMock(return_value=mock_redis)):
                await emit_job_failed(lab_id="lab-3")


class TestEmitAgentOffline:
    async def test_event_construction(self):
        from app.events.publisher import emit_agent_offline

        event = await _capture_publish(emit_agent_offline, agent_id="agent-99")
        assert event.event_type == CleanupEventType.AGENT_OFFLINE
        assert event.agent_id == "agent-99"
        assert event.lab_id is None

    async def test_redis_error_swallowed(self):
        from app.events.publisher import emit_agent_offline

        mock_redis = AsyncMock()
        mock_redis.publish.side_effect = ConnectionError("Redis down")
        with patch("app.events.publisher._publisher_redis", None):
            with patch("app.events.publisher._get_redis", AsyncMock(return_value=mock_redis)):
                await emit_agent_offline(agent_id="agent-99")


class TestEmitStateCheckRequested:
    async def test_event_construction(self):
        from app.events.publisher import emit_state_check_requested

        event = await _capture_publish(
            emit_state_check_requested, lab_id="lab-check",
        )
        assert event.event_type == CleanupEventType.STATE_CHECK_REQUESTED
        assert event.lab_id == "lab-check"

    async def test_redis_error_swallowed(self):
        from app.events.publisher import emit_state_check_requested

        mock_redis = AsyncMock()
        mock_redis.publish.side_effect = ConnectionError("Redis down")
        with patch("app.events.publisher._publisher_redis", None):
            with patch("app.events.publisher._get_redis", AsyncMock(return_value=mock_redis)):
                await emit_state_check_requested(lab_id="lab-check")


class TestPublishCleanupEvent:
    """Tests for the core publish_cleanup_event function."""

    async def test_publishes_to_cleanup_channel(self):
        from app.events.publisher import publish_cleanup_event

        mock_redis = AsyncMock()
        with patch("app.events.publisher._publisher_redis", None):
            with patch("app.events.publisher._get_redis", AsyncMock(return_value=mock_redis)):
                event = CleanupEvent(
                    event_type=CleanupEventType.LAB_DELETED, lab_id="test-lab",
                )
                await publish_cleanup_event(event)

        mock_redis.publish.assert_awaited_once()
        channel = mock_redis.publish.call_args[0][0]
        assert channel == CLEANUP_CHANNEL

    async def test_serializes_event_to_json(self):
        from app.events.publisher import publish_cleanup_event

        mock_redis = AsyncMock()
        with patch("app.events.publisher._publisher_redis", None):
            with patch("app.events.publisher._get_redis", AsyncMock(return_value=mock_redis)):
                event = CleanupEvent(
                    event_type=CleanupEventType.NODE_REMOVED,
                    lab_id="lab-x",
                    node_name="R1",
                )
                await publish_cleanup_event(event)

        payload_json = mock_redis.publish.call_args[0][1]
        parsed = json.loads(payload_json)
        assert parsed["event_type"] == "node_removed"
        assert parsed["lab_id"] == "lab-x"
        assert parsed["node_name"] == "R1"

    async def test_get_redis_error_swallowed(self):
        """Error obtaining Redis connection should not raise."""
        from app.events.publisher import publish_cleanup_event

        with patch("app.events.publisher._publisher_redis", None):
            with patch(
                "app.events.publisher._get_redis",
                AsyncMock(side_effect=ConnectionError("Cannot connect")),
            ):
                event = CleanupEvent(
                    event_type=CleanupEventType.LAB_DELETED, lab_id="fail-lab",
                )
                # Should not raise
                await publish_cleanup_event(event)


class TestClosePublisher:
    async def test_close_when_connected(self):
        from app.events.publisher import close_publisher

        mock_redis = AsyncMock()
        with patch("app.events.publisher._publisher_redis", mock_redis):
            await close_publisher()
        mock_redis.aclose.assert_awaited_once()

    async def test_close_when_not_connected(self):
        from app.events.publisher import close_publisher

        with patch("app.events.publisher._publisher_redis", None):
            # Should not raise
            await close_publisher()

    async def test_close_swallows_event_loop_closed_error(self):
        from app.events.publisher import close_publisher

        mock_redis = AsyncMock()
        mock_redis.aclose.side_effect = RuntimeError("Event loop is closed")
        with patch("app.events.publisher._publisher_redis", mock_redis):
            # Should not raise
            await close_publisher()

    async def test_close_reraises_other_runtime_errors(self):
        from app.events.publisher import close_publisher

        mock_redis = AsyncMock()
        mock_redis.aclose.side_effect = RuntimeError("Something else broke")
        with patch("app.events.publisher._publisher_redis", mock_redis):
            with pytest.raises(RuntimeError, match="Something else broke"):
                await close_publisher()
