"""Integration tests for agent-controller communication.

These tests verify that:
1. Agent registration works correctly
2. Heartbeat mechanism works
3. Job dispatch and execution works
4. Error handling and retry logic works
5. Reconciliation works

Note: These tests require a running agent. For unit tests without
an agent, use mocking.
"""

import asyncio
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app import agent_client
from app.agent_client import (
    AgentError,
    AgentUnavailableError,
    AgentJobError,
    with_retry,
    MAX_RETRIES,
)


# --- Unit Tests for Retry Logic ---

@pytest.mark.asyncio
async def test_with_retry_success_first_attempt():
    """Test that retry wrapper succeeds on first attempt."""
    mock_func = AsyncMock(return_value={"status": "ok"})

    result = await with_retry(mock_func, "arg1", kwarg1="value1")

    assert result == {"status": "ok"}
    assert mock_func.call_count == 1


@pytest.mark.asyncio
async def test_with_retry_success_after_failures():
    """Test that retry wrapper succeeds after transient failures."""
    mock_func = AsyncMock(side_effect=[
        httpx.ConnectError("Connection refused"),
        httpx.ConnectTimeout("Timeout"),
        {"status": "ok"},  # Success on 3rd attempt
    ])

    result = await with_retry(mock_func, "arg1", max_retries=3)

    assert result == {"status": "ok"}
    assert mock_func.call_count == 3


@pytest.mark.asyncio
async def test_with_retry_exhausted():
    """Test that retry wrapper raises after max retries."""
    mock_func = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    with pytest.raises(AgentUnavailableError) as exc_info:
        await with_retry(mock_func, "arg1", max_retries=2)

    assert "unreachable after 3 attempts" in str(exc_info.value)
    assert mock_func.call_count == 3


@pytest.mark.asyncio
async def test_with_retry_http_error_no_retry():
    """Test that HTTP errors are not retried."""
    response = MagicMock()
    response.status_code = 500
    mock_func = AsyncMock(side_effect=httpx.HTTPStatusError(
        "Server error",
        request=MagicMock(),
        response=response,
    ))

    with pytest.raises(AgentJobError):
        await with_retry(mock_func, "arg1", max_retries=3)

    # Should only try once - HTTP errors are not retried
    assert mock_func.call_count == 1


# --- Unit Tests for Agent Health ---

@pytest.mark.asyncio
async def test_check_agent_health_success():
    """Test health check returns True for healthy agent."""
    mock_agent = MagicMock()
    mock_agent.address = "localhost:8001"

    with patch("app.agent_client.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get.return_value = MagicMock(status_code=200)
        mock_client_class.return_value = mock_client

        result = await agent_client.check_agent_health(mock_agent)

    assert result is True


@pytest.mark.asyncio
async def test_check_agent_health_failure():
    """Test health check returns False for unhealthy agent."""
    mock_agent = MagicMock()
    mock_agent.address = "localhost:8001"
    mock_agent.id = "test-agent"

    with patch("app.agent_client.httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client_class.return_value = mock_client

        result = await agent_client.check_agent_health(mock_agent)

    assert result is False


# --- Unit Tests for Agent URL Construction ---

def test_get_agent_url_with_http():
    """Test URL construction when address already has http."""
    mock_agent = MagicMock()
    mock_agent.address = "http://localhost:8001"

    url = agent_client.get_agent_url(mock_agent)

    assert url == "http://localhost:8001"


def test_get_agent_url_without_http():
    """Test URL construction adds http prefix."""
    mock_agent = MagicMock()
    mock_agent.address = "localhost:8001"

    url = agent_client.get_agent_url(mock_agent)

    assert url == "http://localhost:8001"


def test_get_agent_console_url():
    """Test WebSocket URL construction."""
    mock_agent = MagicMock()
    mock_agent.address = "http://localhost:8001"

    url = agent_client.get_agent_console_url(mock_agent, "lab123", "node1")

    assert url == "ws://localhost:8001/console/lab123/node1"


# --- Unit Tests for Stale Agent Detection ---

@pytest.mark.asyncio
async def test_update_stale_agents():
    """Test marking stale agents as offline."""
    from unittest.mock import MagicMock
    from datetime import datetime, timedelta

    # Create mock database session
    mock_db = MagicMock()

    # Create mock stale agent
    stale_agent = MagicMock()
    stale_agent.id = "stale-agent"
    stale_agent.name = "stale"
    stale_agent.status = "online"
    stale_agent.last_heartbeat = datetime.utcnow() - timedelta(seconds=120)

    # Create mock healthy agent
    healthy_agent = MagicMock()
    healthy_agent.id = "healthy-agent"
    healthy_agent.status = "online"
    healthy_agent.last_heartbeat = datetime.utcnow() - timedelta(seconds=10)

    # Mock query to return stale agent
    mock_query = MagicMock()
    mock_query.filter.return_value.all.return_value = [stale_agent]
    mock_db.query.return_value = mock_query

    marked = await agent_client.update_stale_agents(mock_db, timeout_seconds=90)

    assert marked == ["stale-agent"]
    assert stale_agent.status == "offline"
    mock_db.commit.assert_called_once()


# --- Error Class Tests ---

def test_agent_unavailable_error():
    """Test AgentUnavailableError properties."""
    error = AgentUnavailableError("Agent unreachable", agent_id="agent1")

    assert error.message == "Agent unreachable"
    assert error.agent_id == "agent1"
    assert error.retriable is True


def test_agent_job_error():
    """Test AgentJobError properties."""
    error = AgentJobError(
        "Deploy failed",
        agent_id="agent1",
        stdout="Some output",
        stderr="Error details",
    )

    assert error.message == "Deploy failed"
    assert error.agent_id == "agent1"
    assert error.stdout == "Some output"
    assert error.stderr == "Error details"
    assert error.retriable is False


# --- Integration Test Helpers ---

class MockAgent:
    """Helper class for creating mock agents in tests."""

    def __init__(self, agent_id: str, address: str, status: str = "online"):
        self.id = agent_id
        self.address = address
        self.status = status
        self.last_heartbeat = datetime.utcnow()
        self.name = f"agent-{agent_id}"
        self.capabilities = "{}"
        self.version = "0.1.0"
        self.created_at = datetime.utcnow()


# To run these tests:
# cd api && pytest tests/test_agent_client.py -v
