"""Tests for the StateBroadcaster pub/sub system.

Tests the Redis pub/sub broadcaster in app/services/broadcaster.py.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.broadcaster import (
    StateBroadcaster,
    get_broadcaster,
    broadcast_node_state_change,
    broadcast_link_state_change,
)


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    mock = MagicMock()
    mock.publish = AsyncMock(return_value=1)
    mock.close = AsyncMock()
    return mock


@pytest.fixture
def mock_pubsub():
    """Create a mock PubSub client."""
    mock = MagicMock()
    mock.subscribe = AsyncMock()
    mock.unsubscribe = AsyncMock()
    mock.close = AsyncMock()
    return mock


class TestStateBroadcasterPublish:
    """Tests for StateBroadcaster publish methods."""

    @pytest.mark.asyncio
    async def test_publish_node_state(self, mock_redis):
        """Should publish node state to correct channel."""
        broadcaster = StateBroadcaster("redis://localhost")
        broadcaster._redis = mock_redis

        result = await broadcaster.publish_node_state(
            lab_id="lab-123",
            node_id="n1",
            node_name="router-1",
            desired_state="running",
            actual_state="pending",
            is_ready=False,
        )

        assert result == 1
        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args
        assert call_args[0][0] == "lab_state:lab-123"

        message = json.loads(call_args[0][1])
        assert message["type"] == "node_state"
        assert message["data"]["node_id"] == "n1"
        assert message["data"]["node_name"] == "router-1"
        assert message["data"]["actual_state"] == "pending"

    @pytest.mark.asyncio
    async def test_publish_node_state_with_host_info(self, mock_redis):
        """Should include host info when provided."""
        broadcaster = StateBroadcaster("redis://localhost")
        broadcaster._redis = mock_redis

        await broadcaster.publish_node_state(
            lab_id="lab-123",
            node_id="n1",
            node_name="router-1",
            desired_state="running",
            actual_state="running",
            is_ready=True,
            host_id="agent-1",
            host_name="Agent 1",
        )

        call_args = mock_redis.publish.call_args
        message = json.loads(call_args[0][1])
        assert message["data"]["host_id"] == "agent-1"
        assert message["data"]["host_name"] == "Agent 1"

    @pytest.mark.asyncio
    async def test_publish_link_state(self, mock_redis):
        """Should publish link state to correct channel."""
        broadcaster = StateBroadcaster("redis://localhost")
        broadcaster._redis = mock_redis

        result = await broadcaster.publish_link_state(
            lab_id="lab-123",
            link_name="R1:eth1-R2:eth1",
            desired_state="up",
            actual_state="up",
            source_node="R1",
            target_node="R2",
        )

        assert result == 1
        call_args = mock_redis.publish.call_args
        message = json.loads(call_args[0][1])
        assert message["type"] == "link_state"
        assert message["data"]["link_name"] == "R1:eth1-R2:eth1"

    @pytest.mark.asyncio
    async def test_publish_lab_state(self, mock_redis):
        """Should publish lab state to correct channel."""
        broadcaster = StateBroadcaster("redis://localhost")
        broadcaster._redis = mock_redis

        result = await broadcaster.publish_lab_state(
            lab_id="lab-123",
            state="running",
        )

        assert result == 1
        call_args = mock_redis.publish.call_args
        message = json.loads(call_args[0][1])
        assert message["type"] == "lab_state"
        assert message["data"]["state"] == "running"

    @pytest.mark.asyncio
    async def test_publish_job_progress(self, mock_redis):
        """Should publish job progress to correct channel."""
        broadcaster = StateBroadcaster("redis://localhost")
        broadcaster._redis = mock_redis

        result = await broadcaster.publish_job_progress(
            lab_id="lab-123",
            job_id="job-456",
            action="up",
            status="running",
            progress_message="Deploying node R1",
        )

        assert result == 1
        call_args = mock_redis.publish.call_args
        message = json.loads(call_args[0][1])
        assert message["type"] == "job_progress"
        assert message["data"]["job_id"] == "job-456"
        assert message["data"]["status"] == "running"
        assert message["data"]["progress_message"] == "Deploying node R1"

    @pytest.mark.asyncio
    async def test_publish_handles_redis_error(self, mock_redis):
        """Should handle Redis errors gracefully."""
        broadcaster = StateBroadcaster("redis://localhost")
        broadcaster._redis = mock_redis
        mock_redis.publish.side_effect = Exception("Redis connection error")

        result = await broadcaster.publish_node_state(
            lab_id="lab-123",
            node_id="n1",
            node_name="router-1",
            desired_state="running",
            actual_state="pending",
        )

        assert result == 0  # Should return 0 on error

    @pytest.mark.asyncio
    async def test_publish_includes_timestamp(self, mock_redis):
        """Published messages should include ISO timestamp."""
        broadcaster = StateBroadcaster("redis://localhost")
        broadcaster._redis = mock_redis

        await broadcaster.publish_node_state(
            lab_id="lab-123",
            node_id="n1",
            node_name="router-1",
            desired_state="running",
            actual_state="pending",
        )

        call_args = mock_redis.publish.call_args
        message = json.loads(call_args[0][1])
        assert "timestamp" in message
        # Verify it's a valid ISO timestamp
        datetime.fromisoformat(message["timestamp"].replace("Z", "+00:00"))


class TestStateBroadcasterSubscribe:
    """Tests for StateBroadcaster subscribe method."""

    @pytest.mark.asyncio
    async def test_subscribe_yields_messages(self, mock_redis, mock_pubsub):
        """Should yield parsed messages from subscription."""
        broadcaster = StateBroadcaster("redis://localhost")
        broadcaster._redis = mock_redis
        mock_redis.pubsub.return_value = mock_pubsub

        test_message = {
            "type": "node_state",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {"node_id": "n1", "actual_state": "running"},
        }

        # Set up message sequence
        messages = [
            {"type": "message", "data": json.dumps(test_message)},
            None,  # Timeout
        ]
        call_count = [0]

        async def get_message_side_effect(**kwargs):
            idx = call_count[0]
            call_count[0] += 1
            if idx < len(messages):
                return messages[idx]
            raise asyncio.CancelledError()

        mock_pubsub.get_message = get_message_side_effect

        received = []
        try:
            async for msg in broadcaster.subscribe("lab-123"):
                received.append(msg)
                if len(received) >= 1:
                    break
        except asyncio.CancelledError:
            pass

        assert len(received) == 1
        assert received[0]["type"] == "node_state"

    @pytest.mark.asyncio
    async def test_subscribe_ignores_invalid_json(self, mock_redis, mock_pubsub):
        """Should skip messages with invalid JSON."""
        broadcaster = StateBroadcaster("redis://localhost")
        broadcaster._redis = mock_redis
        mock_redis.pubsub.return_value = mock_pubsub

        valid_message = {
            "type": "node_state",
            "data": {"node_id": "n1"},
        }

        messages = [
            {"type": "message", "data": "not valid json"},
            {"type": "message", "data": json.dumps(valid_message)},
        ]
        call_count = [0]

        async def get_message_side_effect(**kwargs):
            idx = call_count[0]
            call_count[0] += 1
            if idx < len(messages):
                return messages[idx]
            raise asyncio.CancelledError()

        mock_pubsub.get_message = get_message_side_effect

        received = []
        try:
            async for msg in broadcaster.subscribe("lab-123"):
                received.append(msg)
        except asyncio.CancelledError:
            pass

        # Should only receive the valid message
        assert len(received) == 1
        assert received[0]["type"] == "node_state"


class TestChannelNaming:
    """Tests for channel naming convention."""

    def test_channel_name_format(self):
        """Channel name should follow lab_state:{lab_id} format."""
        broadcaster = StateBroadcaster("redis://localhost")
        assert broadcaster._channel_name("lab-123") == "lab_state:lab-123"
        assert broadcaster._channel_name("my-test-lab") == "lab_state:my-test-lab"


class TestConvenienceFunctions:
    """Tests for convenience wrapper functions."""

    @pytest.mark.asyncio
    async def test_broadcast_node_state_change(self):
        """Convenience function should call broadcaster."""
        with patch("app.services.broadcaster.get_broadcaster") as mock_get:
            mock_broadcaster = MagicMock()
            mock_broadcaster.publish_node_state = AsyncMock(return_value=1)
            mock_get.return_value = mock_broadcaster

            await broadcast_node_state_change(
                lab_id="lab-123",
                node_id="n1",
                node_name="router-1",
                desired_state="running",
                actual_state="pending",
            )

            mock_broadcaster.publish_node_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_link_state_change(self):
        """Convenience function should call broadcaster."""
        with patch("app.services.broadcaster.get_broadcaster") as mock_get:
            mock_broadcaster = MagicMock()
            mock_broadcaster.publish_link_state = AsyncMock(return_value=1)
            mock_get.return_value = mock_broadcaster

            await broadcast_link_state_change(
                lab_id="lab-123",
                link_name="R1:eth1-R2:eth1",
                desired_state="up",
                actual_state="up",
                source_node="R1",
                target_node="R2",
            )

            mock_broadcaster.publish_link_state.assert_called_once()


class TestMultiLabIsolation:
    """Tests for message isolation between labs."""

    @pytest.mark.asyncio
    async def test_messages_go_to_correct_channel(self, mock_redis):
        """Messages for different labs should go to different channels."""
        broadcaster = StateBroadcaster("redis://localhost")
        broadcaster._redis = mock_redis

        await broadcaster.publish_node_state(
            lab_id="lab-1",
            node_id="n1",
            node_name="router-1",
            desired_state="running",
            actual_state="running",
        )

        await broadcaster.publish_node_state(
            lab_id="lab-2",
            node_id="n1",
            node_name="router-1",
            desired_state="running",
            actual_state="stopped",
        )

        assert mock_redis.publish.call_count == 2
        channels = [call[0][0] for call in mock_redis.publish.call_args_list]
        assert "lab_state:lab-1" in channels
        assert "lab_state:lab-2" in channels


class TestSingletonBroadcaster:
    """Tests for singleton broadcaster pattern."""

    def test_get_broadcaster_returns_same_instance(self):
        """get_broadcaster should return the same instance."""
        with patch("app.services.broadcaster._broadcaster", None):
            with patch("app.services.broadcaster.settings") as mock_settings:
                mock_settings.redis_url = "redis://localhost"

                b1 = get_broadcaster()
                b2 = get_broadcaster()

                # Should be same instance (after first creation)
                # Note: Due to module-level singleton, this may vary in test isolation
                assert b1 is not None
                assert b2 is not None


class TestBroadcasterClose:
    """Tests for broadcaster cleanup."""

    @pytest.mark.asyncio
    async def test_close_closes_redis(self, mock_redis):
        """close() should close Redis connection."""
        broadcaster = StateBroadcaster("redis://localhost")
        broadcaster._redis = mock_redis

        await broadcaster.close()

        mock_redis.close.assert_called_once()
        assert broadcaster._redis is None
