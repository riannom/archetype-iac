"""Tests for agent callback delivery, dead letter queue, heartbeat, and carrier reporting.

Covers:
- CallbackPayload serialization
- deliver_callback retry and dead letter behaviour
- _try_deliver HTTP mechanics
- Dead letter storage, pruning, and retrieval
- send_heartbeat success/failure
- HeartbeatSender background loop
- report_carrier_state_change fire-and-forget
- execute_with_callback orchestration
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent.callbacks as _cb_mod
from agent.callbacks import (
    CallbackPayload,
    HeartbeatSender,
    PendingCallback,
    deliver_callback,
    execute_with_callback,
    get_dead_letters,
    report_carrier_state_change,
    send_heartbeat,
    send_to_dead_letter,
    _try_deliver,
    _prune_dead_letters,
    DEAD_LETTER_TTL,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_payload(**overrides) -> CallbackPayload:
    """Build a CallbackPayload with sane defaults."""
    defaults = dict(
        job_id="job-1",
        agent_id="agent-abc",
        status="completed",
        stdout="ok",
        stderr="",
    )
    defaults.update(overrides)
    return CallbackPayload(**defaults)


def _mock_response(status_code: int = 200, text: str = "OK"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


@pytest.fixture(autouse=True)
def _clear_dead_letters():
    """Reset the module-level dead letter list between tests.

    _prune_dead_letters() rebinds the global via ``global _dead_letters``,
    so we must assign through the module reference.
    """
    _cb_mod._dead_letters = []
    yield
    _cb_mod._dead_letters = []


# ---------------------------------------------------------------------------
# TestCallbackPayload
# ---------------------------------------------------------------------------


class TestCallbackPayload:
    """Tests for CallbackPayload.to_dict serialization."""

    def test_to_dict_includes_all_fields(self):
        """to_dict should return all expected keys."""
        payload = _make_payload()
        d = payload.to_dict()
        expected_keys = {
            "job_id", "agent_id", "status", "stdout", "stderr",
            "error_message", "node_states", "started_at", "completed_at",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_serializes_datetime(self):
        """Datetime fields should be ISO-formatted strings."""
        now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        payload = _make_payload(started_at=now, completed_at=now)
        d = payload.to_dict()
        assert d["started_at"] == now.isoformat()
        assert d["completed_at"] == now.isoformat()

    def test_to_dict_none_dates(self):
        """None datetimes should serialize as None, not raise."""
        payload = _make_payload(started_at=None, completed_at=None)
        d = payload.to_dict()
        assert d["started_at"] is None
        assert d["completed_at"] is None


# ---------------------------------------------------------------------------
# TestDeliverCallback
# ---------------------------------------------------------------------------


class TestDeliverCallback:
    """Tests for deliver_callback retry logic."""

    @pytest.mark.asyncio
    async def test_success_first_try(self):
        """Should return True when first delivery succeeds."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(200))

        with patch("agent.callbacks.get_http_client", return_value=mock_client), \
             patch("agent.callbacks.get_controller_auth_headers", return_value={}):
            result = await deliver_callback(
                "http://ctrl/callback/job-1",
                _make_payload(),
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_retries_on_failure_then_succeeds(self):
        """Should retry on failure and return True once it works."""
        call_count = 0

        async def flaky_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("refused")
            return _mock_response(200)

        mock_client = MagicMock()
        mock_client.post = flaky_post

        with patch("agent.callbacks.get_http_client", return_value=mock_client), \
             patch("agent.callbacks.get_controller_auth_headers", return_value={}), \
             patch("agent.callbacks.asyncio.sleep", new_callable=AsyncMock):
            result = await deliver_callback(
                "http://ctrl/callback/job-1",
                _make_payload(),
                retry_delays=[0, 0, 0],
            )
        assert result is True
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_dead_letter_after_exhaustion(self):
        """Should send to dead letter and return False after all retries fail."""
        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=ConnectionError("down"))

        with patch("agent.callbacks.get_http_client", return_value=mock_client), \
             patch("agent.callbacks.get_controller_auth_headers", return_value={}), \
             patch("agent.callbacks.asyncio.sleep", new_callable=AsyncMock), \
             patch("agent.callbacks.send_to_dead_letter", new_callable=AsyncMock) as mock_dl:
            result = await deliver_callback(
                "http://ctrl/callback/job-1",
                _make_payload(),
                retry_delays=[0, 0],
            )
        assert result is False
        mock_dl.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_custom_retry_delays(self):
        """Should use the supplied retry_delays list."""
        sleep_calls = []

        async def track_sleep(seconds):
            sleep_calls.append(seconds)

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=ConnectionError("fail"))

        with patch("agent.callbacks.get_http_client", return_value=mock_client), \
             patch("agent.callbacks.get_controller_auth_headers", return_value={}), \
             patch("agent.callbacks.asyncio.sleep", side_effect=track_sleep), \
             patch("agent.callbacks.send_to_dead_letter", new_callable=AsyncMock):
            await deliver_callback(
                "http://ctrl/callback/job-1",
                _make_payload(),
                retry_delays=[5, 15],
            )
        assert sleep_calls == [5, 15]


# ---------------------------------------------------------------------------
# TestTryDeliver
# ---------------------------------------------------------------------------


class TestTryDeliver:
    """Tests for _try_deliver HTTP POST logic."""

    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        """2xx response should return True."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(200))

        with patch("agent.callbacks.get_http_client", return_value=mock_client), \
             patch("agent.callbacks.get_controller_auth_headers", return_value={}):
            assert await _try_deliver("http://ctrl/cb", _make_payload()) is True

    @pytest.mark.asyncio
    async def test_non_2xx_raises(self):
        """Non-2xx response should raise an exception."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(500, "Internal"))

        with patch("agent.callbacks.get_http_client", return_value=mock_client), \
             patch("agent.callbacks.get_controller_auth_headers", return_value={}):
            with pytest.raises(Exception, match="HTTP 500"):
                await _try_deliver("http://ctrl/cb", _make_payload())

    @pytest.mark.asyncio
    async def test_correct_headers_sent(self):
        """Auth headers from get_controller_auth_headers should be forwarded."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(200))
        auth = {"Authorization": "Bearer secret"}

        with patch("agent.callbacks.get_http_client", return_value=mock_client), \
             patch("agent.callbacks.get_controller_auth_headers", return_value=auth):
            await _try_deliver("http://ctrl/cb", _make_payload())
        _, kwargs = mock_client.post.call_args
        assert kwargs["headers"] == auth

    @pytest.mark.asyncio
    async def test_json_payload(self):
        """Payload should be serialized via to_dict and passed as json."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(200))
        payload = _make_payload(job_id="j-99")

        with patch("agent.callbacks.get_http_client", return_value=mock_client), \
             patch("agent.callbacks.get_controller_auth_headers", return_value={}):
            await _try_deliver("http://ctrl/cb", payload)
        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["job_id"] == "j-99"


# ---------------------------------------------------------------------------
# TestDeadLetter
# ---------------------------------------------------------------------------


class TestDeadLetter:
    """Tests for dead letter queue management."""

    @pytest.mark.asyncio
    async def test_stores_locally(self):
        """send_to_dead_letter should add an entry to the dead letter list."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(200))

        with patch("agent.callbacks.get_http_client", return_value=mock_client), \
             patch("agent.callbacks.get_controller_auth_headers", return_value={}):
            await send_to_dead_letter("http://ctrl/cb/job-1", _make_payload(), "err")

        assert len(_cb_mod._dead_letters) == 1
        assert _cb_mod._dead_letters[0].payload.job_id == "job-1"

    @pytest.mark.asyncio
    async def test_notifies_controller(self):
        """send_to_dead_letter should POST to a dead-letter endpoint."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(200))

        with patch("agent.callbacks.get_http_client", return_value=mock_client), \
             patch("agent.callbacks.get_controller_auth_headers", return_value={}):
            await send_to_dead_letter("http://ctrl/cb/job-1", _make_payload(), "err")

        # Should have been called with the dead-letter URL
        call_url = mock_client.post.call_args_list[-1][0][0]
        assert "dead-letter" in call_url

    def test_prune_removes_expired(self):
        """_prune_dead_letters should remove entries older than DEAD_LETTER_TTL."""
        old_entry = PendingCallback(
            callback_url="http://ctrl/cb",
            payload=_make_payload(),
            created_at=datetime.now(timezone.utc) - timedelta(seconds=DEAD_LETTER_TTL + 100),
        )
        new_entry = PendingCallback(
            callback_url="http://ctrl/cb",
            payload=_make_payload(job_id="new"),
            created_at=datetime.now(timezone.utc),
        )
        _cb_mod._dead_letters.extend([old_entry, new_entry])

        _prune_dead_letters()

        assert len(_cb_mod._dead_letters) == 1
        assert _cb_mod._dead_letters[0].payload.job_id == "new"

    def test_get_returns_list(self):
        """get_dead_letters should return serializable dicts."""
        _cb_mod._dead_letters.append(
            PendingCallback(
                callback_url="http://ctrl/cb",
                payload=_make_payload(job_id="dl-1"),
            )
        )
        entries = get_dead_letters()
        assert len(entries) == 1
        assert entries[0]["job_id"] == "dl-1"
        assert "created_at" in entries[0]


# ---------------------------------------------------------------------------
# TestSendHeartbeat
# ---------------------------------------------------------------------------


class TestSendHeartbeat:
    """Tests for send_heartbeat."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Should return True on 2xx."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(200))

        with patch("agent.callbacks.get_http_client", return_value=mock_client), \
             patch("agent.callbacks.get_controller_auth_headers", return_value={}):
            result = await send_heartbeat("http://ctrl/cb/job-1", "job-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_failure_returns_false(self):
        """Should return False on network error."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=ConnectionError("down"))

        with patch("agent.callbacks.get_http_client", return_value=mock_client), \
             patch("agent.callbacks.get_controller_auth_headers", return_value={}):
            result = await send_heartbeat("http://ctrl/cb/job-1", "job-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_url_derived_from_callback(self):
        """Heartbeat URL should be callback_url + /heartbeat."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(200))

        with patch("agent.callbacks.get_http_client", return_value=mock_client), \
             patch("agent.callbacks.get_controller_auth_headers", return_value={}):
            await send_heartbeat("http://ctrl/cb/job-1", "job-1")
        call_url = mock_client.post.call_args[0][0]
        assert call_url == "http://ctrl/cb/job-1/heartbeat"


# ---------------------------------------------------------------------------
# TestHeartbeatSender
# ---------------------------------------------------------------------------


class TestHeartbeatSender:
    """Tests for the HeartbeatSender async context manager."""

    @pytest.mark.asyncio
    async def test_starts_and_stops_background_task(self):
        """Task should be created on enter and cancelled on exit."""
        with patch("agent.callbacks.send_heartbeat", new_callable=AsyncMock):
            sender = HeartbeatSender("http://cb", "j1", interval=0.05)
            async with sender:
                assert sender._task is not None
                assert not sender._task.done()
            # After exit, task should be cancelled
            assert sender._task.done()

    @pytest.mark.asyncio
    async def test_sends_at_interval(self):
        """Should invoke send_heartbeat at least once within the context."""
        with patch("agent.callbacks.send_heartbeat", new_callable=AsyncMock) as mock_hb:
            async with HeartbeatSender("http://cb", "j1", interval=0.05):
                await asyncio.sleep(0.15)
            assert mock_hb.await_count >= 1

    @pytest.mark.asyncio
    async def test_cancels_on_exit(self):
        """__aexit__ should cancel even if heartbeat is mid-sleep."""
        with patch("agent.callbacks.send_heartbeat", new_callable=AsyncMock):
            sender = HeartbeatSender("http://cb", "j1", interval=100)
            async with sender:
                pass
            assert sender._running is False


# ---------------------------------------------------------------------------
# TestReportCarrierStateChange
# ---------------------------------------------------------------------------


class TestReportCarrierStateChange:
    """Tests for report_carrier_state_change."""

    @pytest.mark.asyncio
    async def test_sends_to_controller(self):
        """Should POST carrier state data and return True."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_mock_response(200))

        with patch("agent.callbacks.get_http_client", return_value=mock_client), \
             patch("agent.callbacks.get_controller_auth_headers", return_value={}):
            result = await report_carrier_state_change("lab-1", "r1", "eth1", "on")
        assert result is True
        _, kwargs = mock_client.post.call_args
        assert kwargs["json"]["lab_id"] == "lab-1"
        assert kwargs["json"]["carrier_state"] == "on"

    @pytest.mark.asyncio
    async def test_handles_failure_gracefully(self):
        """Should return False on error, not raise."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=ConnectionError("unreachable"))

        with patch("agent.callbacks.get_http_client", return_value=mock_client), \
             patch("agent.callbacks.get_controller_auth_headers", return_value={}):
            result = await report_carrier_state_change("lab-1", "r1", "eth1", "off")
        assert result is False


# ---------------------------------------------------------------------------
# TestExecuteWithCallback
# ---------------------------------------------------------------------------


class TestExecuteWithCallback:
    """Tests for execute_with_callback orchestration."""

    @pytest.mark.asyncio
    async def test_success_status(self):
        """Successful operation should deliver a 'completed' callback."""
        result_obj = MagicMock()
        result_obj.success = True
        result_obj.stdout = "done"
        result_obj.stderr = ""
        result_obj.error = None

        delivered = {}

        async def capture(url, payload):
            delivered["payload"] = payload
            return True

        op = AsyncMock(return_value=result_obj)

        with patch("agent.callbacks.deliver_callback", side_effect=capture):
            await execute_with_callback("j1", "a1", "http://cb", op)

        assert delivered["payload"].status == "completed"
        assert delivered["payload"].stdout == "done"

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Exception in operation should deliver a 'failed' callback."""
        delivered = {}

        async def capture(url, payload):
            delivered["payload"] = payload
            return True

        op = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("agent.callbacks.deliver_callback", side_effect=capture):
            await execute_with_callback("j1", "a1", "http://cb", op)

        assert delivered["payload"].status == "failed"
        assert "boom" in delivered["payload"].error_message
