"""Comprehensive tests for api/app/agent_client/http.py"""

import sys
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Stub heavy dependencies before importing the module under test
# ---------------------------------------------------------------------------

# Provide a fake settings object so module-level reads succeed
_fake_settings = MagicMock()
_fake_settings.agent_max_retries = 3
_fake_settings.agent_retry_backoff_base = 1.0
_fake_settings.agent_retry_backoff_max = 10.0
_fake_settings.agent_stale_timeout = 90
_fake_settings.agent_secret = ""

# Fake metrics
_fake_histogram = MagicMock()
_fake_histogram.labels.return_value.observe = MagicMock()

sys.modules.setdefault("prometheus_client", MagicMock())
sys.modules.setdefault("app.metrics", MagicMock(agent_operation_duration=_fake_histogram))

# Patch settings before importing the module
with patch.dict("sys.modules", {
    "app.config": MagicMock(settings=_fake_settings),
    "app.utils.timeouts": MagicMock(AGENT_HTTP_TIMEOUT=30.0, AGENT_VTEP_TIMEOUT=60.0),
}):
    # Force-reload to pick up mocked modules
    if "app.agent_client.http" in sys.modules:
        del sys.modules["app.agent_client.http"]

    import app.agent_client.http as http_mod
    from app.agent_client.http import (
        with_retry,
        _agent_request,
        _safe_agent_request,
        get_http_client,
        close_http_client,
        _get_agent_auth_headers,
        _agent_online_cutoff,
        _timed_node_operation,
        AgentUnavailableError,
        AgentJobError,
        AgentError,
    )

import httpx  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(**kwargs):
    agent = MagicMock()
    agent.id = kwargs.get("id", "agent-1")
    agent.name = kwargs.get("name", "Agent 1")
    agent.address = kwargs.get("address", "http://10.0.0.1:8001")
    return agent


def _make_http_status_error(status_code: int, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    req = MagicMock()
    return httpx.HTTPStatusError(
        f"HTTP {status_code}", request=req, response=resp
    )


# ---------------------------------------------------------------------------
# TestWithRetry
# ---------------------------------------------------------------------------

class TestWithRetry:
    """Tests for the with_retry() exponential-backoff wrapper."""

    @pytest.mark.asyncio
    async def test_success_first_try(self):
        func = AsyncMock(return_value={"ok": True})
        result = await with_retry(func, max_retries=3)
        assert result == {"ok": True}
        assert func.await_count == 1

    @pytest.mark.asyncio
    async def test_success_after_retries(self):
        func = AsyncMock(side_effect=[
            httpx.ConnectError("fail"),
            httpx.ConnectError("fail"),
            {"ok": True},
        ])
        with patch("app.agent_client.http.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await with_retry(func, max_retries=3)
        assert result == {"ok": True}
        assert func.await_count == 3
        assert mock_sleep.await_count == 2

    @pytest.mark.asyncio
    async def test_exhausted_retries_raises_unavailable(self):
        func = AsyncMock(side_effect=httpx.ConnectError("down"))
        with patch("app.agent_client.http.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(AgentUnavailableError, match="unreachable"):
                await with_retry(func, max_retries=2)
        assert func.await_count == 3  # initial + 2 retries

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [429, 502, 503, 504])
    async def test_retry_on_transient_http_codes(self, status_code):
        err = _make_http_status_error(status_code)
        func = AsyncMock(side_effect=[err, {"retried": True}])
        with patch("app.agent_client.http.asyncio.sleep", new_callable=AsyncMock):
            result = await with_retry(func, max_retries=2)
        assert result == {"retried": True}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [400, 404, 500])
    async def test_no_retry_on_non_transient_http(self, status_code):
        err = _make_http_status_error(status_code, text="bad request")
        func = AsyncMock(side_effect=err)
        with pytest.raises(AgentJobError):
            await with_retry(func, max_retries=3)
        assert func.await_count == 1

    @pytest.mark.asyncio
    async def test_backoff_delays_increase(self):
        func = AsyncMock(side_effect=[
            httpx.ConnectError("a"),
            httpx.ConnectError("b"),
            httpx.ConnectError("c"),
            httpx.ConnectError("d"),
        ])
        with patch("app.agent_client.http.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(AgentUnavailableError):
                await with_retry(func, max_retries=3)
        # Delays: base*2^0=1.0, base*2^1=2.0, base*2^2=4.0
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1.0, 2.0, 4.0]


# ---------------------------------------------------------------------------
# TestAgentRequest
# ---------------------------------------------------------------------------

class TestAgentRequest:
    """Tests for _agent_request()."""

    @pytest.mark.asyncio
    async def test_success_returns_json(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "value"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch.object(http_mod, "get_http_client", return_value=mock_client):
            result = await _agent_request("GET", "http://agent:8001/health")

        assert result == {"data": "value"}

    @pytest.mark.asyncio
    async def test_204_returns_empty_dict(self):
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch.object(http_mod, "get_http_client", return_value=mock_client):
            result = await _agent_request("DELETE", "http://agent:8001/resource")

        assert result == {}

    @pytest.mark.asyncio
    async def test_error_records_metric_with_error_status(self):
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=httpx.ConnectError("down"))

        mock_metric = MagicMock()
        with (
            patch.object(http_mod, "get_http_client", return_value=mock_client),
            patch.object(http_mod, "agent_operation_duration", mock_metric),
            patch("app.agent_client.http.asyncio.sleep", new_callable=AsyncMock),
        ):
            with pytest.raises(AgentUnavailableError):
                await _agent_request(
                    "GET", "http://agent/test",
                    max_retries=0,
                    metric_operation="test_op",
                    metric_host_id="host-1",
                )

        mock_metric.labels.assert_called_with(
            operation="test_op", host_id="host-1", status="error"
        )


# ---------------------------------------------------------------------------
# TestSafeAgentRequest
# ---------------------------------------------------------------------------

class TestSafeAgentRequest:
    """Tests for _safe_agent_request()."""

    @pytest.mark.asyncio
    async def test_returns_fallback_on_exception(self):
        agent = _make_agent()
        with patch.object(http_mod, "_agent_request", new_callable=AsyncMock, side_effect=Exception("boom")):
            with patch("app.agent_client.selection.get_agent_url", return_value="http://10.0.0.1:8001"):
                result = await _safe_agent_request(
                    agent, "GET", "/status",
                    fallback={"status": "unknown"},
                )
        assert result == {"status": "unknown"}

    @pytest.mark.asyncio
    async def test_logs_at_specified_level(self):
        agent = _make_agent()
        with (
            patch.object(http_mod, "_agent_request", new_callable=AsyncMock, side_effect=Exception("oops")),
            patch("app.agent_client.selection.get_agent_url", return_value="http://10.0.0.1:8001"),
            patch.object(http_mod.logger, "error") as mock_log,
        ):
            await _safe_agent_request(
                agent, "GET", "/status",
                log_level="error",
                description="Health check",
            )
        mock_log.assert_called_once()

    @pytest.mark.asyncio
    async def test_description_appears_in_log(self):
        agent = _make_agent()
        with (
            patch.object(http_mod, "_agent_request", new_callable=AsyncMock, side_effect=Exception("err")),
            patch("app.agent_client.selection.get_agent_url", return_value="http://10.0.0.1:8001"),
            patch.object(http_mod.logger, "warning") as mock_log,
        ):
            await _safe_agent_request(
                agent, "GET", "/status",
                description="Fetch overlay status",
            )
        log_msg = mock_log.call_args[0][0]
        assert "Fetch overlay status" in log_msg


# ---------------------------------------------------------------------------
# TestGetHttpClient
# ---------------------------------------------------------------------------

class TestGetHttpClient:
    """Tests for get_http_client() singleton."""

    def test_creates_singleton_on_first_call(self):
        http_mod._http_client = None
        client = get_http_client()
        assert client is not None
        # Cleanup
        http_mod._http_client = None

    def test_reuses_on_second_call(self):
        http_mod._http_client = None
        c1 = get_http_client()
        c2 = get_http_client()
        assert c1 is c2
        # Cleanup
        http_mod._http_client = None


# ---------------------------------------------------------------------------
# TestCloseHttpClient
# ---------------------------------------------------------------------------

class TestCloseHttpClient:
    """Tests for close_http_client()."""

    @pytest.mark.asyncio
    async def test_sets_client_to_none(self):
        mock_client = AsyncMock()
        http_mod._http_client = mock_client
        await close_http_client()
        assert http_mod._http_client is None
        mock_client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_noop_when_already_none(self):
        http_mod._http_client = None
        await close_http_client()  # Should not raise
        assert http_mod._http_client is None


# ---------------------------------------------------------------------------
# TestGetAgentAuthHeaders
# ---------------------------------------------------------------------------

class TestGetAgentAuthHeaders:
    """Tests for _get_agent_auth_headers()."""

    def test_with_secret_returns_bearer(self):
        with patch.object(http_mod.settings, "agent_secret", "my-secret"):
            headers = _get_agent_auth_headers()
        assert headers == {"Authorization": "Bearer my-secret"}

    def test_without_secret_returns_empty(self):
        with patch.object(http_mod.settings, "agent_secret", ""):
            headers = _get_agent_auth_headers()
        assert headers == {}


# ---------------------------------------------------------------------------
# TestAgentOnlineCutoff
# ---------------------------------------------------------------------------

class TestAgentOnlineCutoff:
    """Tests for _agent_online_cutoff()."""

    def test_returns_correct_threshold(self):
        with patch.object(http_mod.settings, "agent_stale_timeout", 90):
            cutoff = _agent_online_cutoff()
        now = datetime.now(timezone.utc)
        # The cutoff should be approximately 90 seconds ago
        diff = (now - cutoff).total_seconds()
        assert 88 <= diff <= 92

    def test_custom_timeout(self):
        cutoff = _agent_online_cutoff(timeout_seconds=300)
        now = datetime.now(timezone.utc)
        diff = (now - cutoff).total_seconds()
        assert 298 <= diff <= 302


# ---------------------------------------------------------------------------
# TestTimedNodeOperation
# ---------------------------------------------------------------------------

class TestTimedNodeOperation:
    """Tests for _timed_node_operation()."""

    @pytest.mark.asyncio
    async def test_success_records_metrics(self):
        agent = _make_agent()
        mock_metric = MagicMock()

        with (
            patch.object(http_mod, "_agent_request", new_callable=AsyncMock, return_value={"success": True}),
            patch.object(http_mod, "agent_operation_duration", mock_metric),
        ):
            result = await _timed_node_operation(
                agent, "POST", "http://agent:8001/labs/lab1/nodes/r1/start",
                operation="node_start", lab_id="lab1", node_name="r1",
            )

        assert result == {"success": True}
        mock_metric.labels.assert_called_with(
            operation="node_start", host_id="agent-1", status="success"
        )
        mock_metric.labels.return_value.observe.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_returns_failure_dict(self):
        agent = _make_agent()
        mock_metric = MagicMock()

        with (
            patch.object(http_mod, "_agent_request", new_callable=AsyncMock, side_effect=Exception("agent down")),
            patch.object(http_mod, "agent_operation_duration", mock_metric),
        ):
            result = await _timed_node_operation(
                agent, "POST", "http://agent:8001/labs/lab1/nodes/r1/start",
                operation="node_start", lab_id="lab1", node_name="r1",
            )

        assert result["success"] is False
        assert "agent down" in result["error"]
        mock_metric.labels.assert_called_with(
            operation="node_start", host_id="agent-1", status="error"
        )


# ---------------------------------------------------------------------------
# TestExceptionClasses
# ---------------------------------------------------------------------------

class TestExceptionClasses:
    """Tests for AgentError, AgentUnavailableError, AgentJobError."""

    def test_agent_error_attributes(self):
        err = AgentError("msg", agent_id="a1", retriable=True)
        assert err.message == "msg"
        assert err.agent_id == "a1"
        assert err.retriable is True
        assert str(err) == "msg"

    def test_unavailable_is_retriable(self):
        err = AgentUnavailableError("down", agent_id="a2")
        assert err.retriable is True

    def test_job_error_not_retriable(self):
        err = AgentJobError("failed", agent_id="a3", stdout="out", stderr="err")
        assert err.retriable is False
        assert err.stdout == "out"
        assert err.stderr == "err"
