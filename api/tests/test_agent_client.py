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

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app import agent_client
from app.agent_client import (
    AgentUnavailableError,
    AgentJobError,
    with_retry,
    is_agent_online,
)
from app.config import settings


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


@pytest.mark.asyncio
async def test_with_retry_http_429_retries():
    """Test that 429 is retried."""
    response = MagicMock()
    response.status_code = 429
    mock_func = AsyncMock(side_effect=[
        httpx.HTTPStatusError("Too Many Requests", request=MagicMock(), response=response),
        {"status": "ok"},
    ])

    result = await with_retry(mock_func, "arg1", max_retries=1)

    assert result == {"status": "ok"}
    assert mock_func.call_count == 2


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

    mock_client = AsyncMock()
    mock_client.get.side_effect = httpx.ConnectError("Connection refused")

    with patch("app.agent_client.get_http_client", return_value=mock_client):
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
    from datetime import datetime

    # Create mock database session
    mock_db = MagicMock()

    # Create mock stale agent
    stale_agent = MagicMock()
    stale_agent.id = "stale-agent"
    stale_agent.name = "stale"
    stale_agent.status = "online"
    stale_agent.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=120)

    # Create mock healthy agent
    healthy_agent = MagicMock()
    healthy_agent.id = "healthy-agent"
    healthy_agent.status = "online"
    healthy_agent.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=10)

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


def test_is_agent_online_uses_settings_cutoff(monkeypatch):
    """Uses settings.agent_stale_timeout for online cutoff."""
    monkeypatch.setattr(settings, "agent_stale_timeout", 5)
    agent = MagicMock()
    agent.status = "online"
    agent.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=4)
    assert is_agent_online(agent) is True

    agent.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=6)
    assert is_agent_online(agent) is False


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

    def __init__(
        self,
        agent_id: str,
        address: str,
        status: str = "online",
        capabilities: str | None = None,
    ):
        self.id = agent_id
        self.address = address
        self.status = status
        self.last_heartbeat = datetime.now(timezone.utc)
        self.name = f"agent-{agent_id}"
        self.capabilities = capabilities or '{"providers": ["docker"], "max_concurrent_jobs": 4}'
        self.version = "0.1.0"
        self.created_at = datetime.now(timezone.utc)


class MockLab:
    """Helper class for creating mock labs in tests."""

    def __init__(self, lab_id: str, agent_id: str | None = None):
        self.id = lab_id
        self.name = f"lab-{lab_id}"
        self.agent_id = agent_id
        self.state = "stopped"
        self.owner_id = "user1"


# --- Unit Tests for Capability Parsing ---

def test_parse_capabilities_valid():
    """Test parsing valid capabilities JSON."""
    agent = MockAgent("agent1", "localhost:8001", capabilities='{"providers": ["docker", "libvirt"], "max_concurrent_jobs": 8}')

    caps = agent_client.parse_capabilities(agent)

    assert caps["providers"] == ["docker", "libvirt"]
    assert caps["max_concurrent_jobs"] == 8


def test_parse_capabilities_empty():
    """Test parsing empty capabilities."""
    # Create agent directly, bypassing MockAgent default
    agent = MagicMock()
    agent.capabilities = ""

    caps = agent_client.parse_capabilities(agent)

    assert caps == {}


def test_parse_capabilities_invalid_json():
    """Test parsing invalid JSON returns empty dict."""
    agent = MockAgent("agent1", "localhost:8001", capabilities="not valid json")

    caps = agent_client.parse_capabilities(agent)

    assert caps == {}


def test_get_agent_providers():
    """Test extracting provider list from agent."""
    agent = MockAgent("agent1", "localhost:8001", capabilities='{"providers": ["docker", "libvirt"]}')

    providers = agent_client.get_agent_providers(agent)

    assert providers == ["docker", "libvirt"]


def test_get_agent_providers_missing():
    """Test extracting providers when not present."""
    agent = MockAgent("agent1", "localhost:8001", capabilities='{}')

    providers = agent_client.get_agent_providers(agent)

    assert providers == []


def test_get_agent_max_jobs():
    """Test extracting max concurrent jobs from agent."""
    agent = MockAgent("agent1", "localhost:8001", capabilities='{"max_concurrent_jobs": 8}')

    max_jobs = agent_client.get_agent_max_jobs(agent)

    assert max_jobs == 8


def test_get_agent_max_jobs_default():
    """Test default max concurrent jobs when not specified."""
    agent = MockAgent("agent1", "localhost:8001", capabilities='{}')

    max_jobs = agent_client.get_agent_max_jobs(agent)

    assert max_jobs == 4  # Default value


# --- Unit Tests for Active Job Counting ---

def test_count_active_jobs():
    """Test counting active jobs for an agent."""
    mock_db = MagicMock()
    mock_query = MagicMock()
    mock_query.filter.return_value.count.return_value = 2
    mock_db.query.return_value = mock_query

    count = agent_client.count_active_jobs(mock_db, "agent1")

    assert count == 2


# --- Unit Tests for Multi-Agent Selection ---

@pytest.mark.asyncio
async def test_get_healthy_agent_no_agents():
    """Test get_healthy_agent returns None when no agents exist."""
    mock_db = MagicMock()
    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = []
    mock_db.query.return_value = mock_query

    result = await agent_client.get_healthy_agent(mock_db)

    assert result is None


@pytest.mark.asyncio
async def test_get_healthy_agent_capability_filtering():
    """Test that agents are filtered by required provider."""
    mock_db = MagicMock()

    # Create agents with different capabilities
    agent_docker = MockAgent("agent1", "localhost:8001", capabilities='{"providers": ["docker"], "max_concurrent_jobs": 4}')
    agent_libvirt = MockAgent("agent2", "localhost:8002", capabilities='{"providers": ["libvirt"], "max_concurrent_jobs": 4}')
    agent_both = MockAgent("agent3", "localhost:8003", capabilities='{"providers": ["docker", "libvirt"], "max_concurrent_jobs": 4}')

    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [agent_docker, agent_libvirt, agent_both]
    mock_db.query.return_value = mock_query

    # Mock count_active_jobs_by_agent to return 0 for all agents
    with patch.object(agent_client, "count_active_jobs_by_agent", return_value={}):
        # Request libvirt provider
        result = await agent_client.get_healthy_agent(mock_db, required_provider="libvirt")

    # Should only return agent2 or agent3 (both support libvirt)
    assert result in [agent_libvirt, agent_both]


@pytest.mark.asyncio
async def test_get_healthy_agent_no_matching_provider():
    """Test that returns None when no agent supports required provider."""
    mock_db = MagicMock()

    agent = MockAgent("agent1", "localhost:8001", capabilities='{"providers": ["docker"]}')

    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [agent]
    mock_db.query.return_value = mock_query

    result = await agent_client.get_healthy_agent(mock_db, required_provider="libvirt")

    assert result is None


@pytest.mark.asyncio
async def test_get_healthy_agent_exclude_agents():
    """Test that excluded agents are not selected."""
    mock_db = MagicMock()

    MockAgent("agent1", "localhost:8001")
    agent2 = MockAgent("agent2", "localhost:8002")

    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [agent2]  # agent1 excluded by query
    mock_db.query.return_value = mock_query

    with patch.object(agent_client, "count_active_jobs_by_agent", return_value={}):
        result = await agent_client.get_healthy_agent(mock_db, exclude_agents=["agent1"])

    assert result == agent2


@pytest.mark.asyncio
async def test_get_healthy_agent_affinity():
    """Test that preferred agent is selected when available."""
    mock_db = MagicMock()

    agent1 = MockAgent("agent1", "localhost:8001")
    agent2 = MockAgent("agent2", "localhost:8002")

    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [agent1, agent2]
    mock_db.query.return_value = mock_query

    with patch.object(agent_client, "count_active_jobs_by_agent", return_value={}):
        result = await agent_client.get_healthy_agent(mock_db, prefer_agent_id="agent2")

    assert result == agent2


@pytest.mark.asyncio
async def test_get_healthy_agent_affinity_unavailable():
    """Test that fallback to other agents when preferred is unavailable."""
    mock_db = MagicMock()

    agent1 = MockAgent("agent1", "localhost:8001")
    # agent2 is not in the list (unavailable)

    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [agent1]
    mock_db.query.return_value = mock_query

    with patch.object(agent_client, 'count_active_jobs_by_agent', return_value={}):
        result = await agent_client.get_healthy_agent(mock_db, prefer_agent_id="agent2")

    # Should fall back to agent1
    assert result == agent1


@pytest.mark.asyncio
async def test_get_healthy_agent_load_balancing():
    """Test that least loaded agent is selected."""
    mock_db = MagicMock()

    # Both agents have same max_concurrent_jobs
    agent1 = MockAgent("agent1", "localhost:8001", capabilities='{"providers": ["docker"], "max_concurrent_jobs": 4}')
    agent2 = MockAgent("agent2", "localhost:8002", capabilities='{"providers": ["docker"], "max_concurrent_jobs": 4}')

    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [agent1, agent2]
    mock_db.query.return_value = mock_query

    # agent1 has 3 jobs, agent2 has 1 job
    with patch.object(agent_client, 'count_active_jobs_by_agent', return_value={"agent1": 3, "agent2": 1}):
        result = await agent_client.get_healthy_agent(mock_db)

    # Should select agent2 (less loaded)
    assert result == agent2


@pytest.mark.asyncio
async def test_get_healthy_agent_capacity_check():
    """Test that agents at capacity are skipped."""
    mock_db = MagicMock()

    agent1 = MockAgent("agent1", "localhost:8001", capabilities='{"providers": ["docker"], "max_concurrent_jobs": 2}')
    agent2 = MockAgent("agent2", "localhost:8002", capabilities='{"providers": ["docker"], "max_concurrent_jobs": 4}')

    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [agent1, agent2]
    mock_db.query.return_value = mock_query

    # agent1 is at capacity (2/2), agent2 has room (1/4)
    with patch.object(agent_client, 'count_active_jobs_by_agent', return_value={"agent1": 2, "agent2": 1}):
        result = await agent_client.get_healthy_agent(mock_db)

    # Should select agent2 (agent1 at capacity)
    assert result == agent2


@pytest.mark.asyncio
async def test_get_healthy_agent_all_at_capacity():
    """Test that returns None when all agents at capacity."""
    mock_db = MagicMock()

    agent1 = MockAgent("agent1", "localhost:8001", capabilities='{"providers": ["docker"], "max_concurrent_jobs": 2}')
    agent2 = MockAgent("agent2", "localhost:8002", capabilities='{"providers": ["docker"], "max_concurrent_jobs": 2}')

    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = [agent1, agent2]
    mock_db.query.return_value = mock_query

    # Both at capacity
    with patch.object(agent_client, 'count_active_jobs_by_agent', return_value={"agent1": 2, "agent2": 2}):
        result = await agent_client.get_healthy_agent(mock_db)

    assert result is None


# --- Unit Tests for Lab Affinity ---

class MockNodePlacement:
    """Helper class for creating mock node placements in tests."""

    def __init__(self, lab_id: str, node_name: str, host_id: str):
        self.lab_id = lab_id
        self.node_name = node_name
        self.host_id = host_id


@pytest.mark.asyncio
async def test_get_agent_for_lab_with_existing_agent():
    """Test that lab's existing agent is preferred via NodePlacement."""
    mock_db = MagicMock()
    lab = MockLab("lab1", agent_id="agent2")

    agent1 = MockAgent("agent1", "localhost:8001")
    agent2 = MockAgent("agent2", "localhost:8002")

    # Mock NodePlacement query - lab has nodes on agent2
    placement_query = MagicMock()
    placement_query.filter.return_value = placement_query
    placement_query.all.return_value = [
        MockNodePlacement("lab1", "node1", "agent2"),
        MockNodePlacement("lab1", "node2", "agent2"),
    ]

    # Mock Host query - for get_healthy_agent
    host_query = MagicMock()
    host_query.filter.return_value = host_query
    host_query.all.return_value = [agent1, agent2]

    def query_side_effect(model):
        # Return different query based on model
        if hasattr(model, '__tablename__') and model.__tablename__ == 'node_placements':
            return placement_query
        return host_query

    mock_db.query.side_effect = query_side_effect

    with patch.object(agent_client, 'count_active_jobs_by_agent', return_value={}):
        result = await agent_client.get_agent_for_lab(mock_db, lab, required_provider="docker")

    # Should select agent2 (agent with existing node placements)
    assert result == agent2


@pytest.mark.asyncio
async def test_get_agent_for_lab_without_existing_agent():
    """Test that new lab gets load-balanced agent."""
    mock_db = MagicMock()
    lab = MockLab("lab1", agent_id=None)

    agent1 = MockAgent("agent1", "localhost:8001")
    agent2 = MockAgent("agent2", "localhost:8002")

    # Mock NodePlacement query - no placements (new lab)
    placement_query = MagicMock()
    placement_query.filter.return_value = placement_query
    placement_query.all.return_value = []  # No existing placements

    # Mock Host query - for get_healthy_agent
    host_query = MagicMock()
    host_query.filter.return_value = host_query
    host_query.all.return_value = [agent1, agent2]

    def query_side_effect(model):
        # Return different query based on model
        if hasattr(model, '__tablename__') and model.__tablename__ == 'node_placements':
            return placement_query
        return host_query

    mock_db.query.side_effect = query_side_effect

    # agent1 less loaded
    with patch.object(agent_client, 'count_active_jobs_by_agent', return_value={"agent1": 1, "agent2": 3}):
        result = await agent_client.get_agent_for_lab(mock_db, lab, required_provider="docker")

    # Should select agent1 (least loaded)
    assert result == agent1


# --- Unit Tests for destroy_container_on_agent ---

@pytest.mark.asyncio
async def test_destroy_container_on_agent_success():
    """Test successful container destruction on agent."""
    mock_agent = MockAgent("agent1", "localhost:8001")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"success": True, "message": "Container removed"}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.delete.return_value = mock_response

    with patch("app.agent_client.get_http_client", return_value=mock_client):
        result = await agent_client.destroy_container_on_agent(
            mock_agent, "test-lab", "test-container"
        )

    assert result["success"] is True
    mock_client.delete.assert_called_once()

    # Verify URL construction
    call_args = mock_client.delete.call_args
    url = call_args[0][0]
    assert "test-lab" in url
    assert "test-container" in url


@pytest.mark.asyncio
async def test_destroy_container_on_agent_http_error():
    """Test container destruction handles HTTP errors."""
    mock_agent = MockAgent("agent1", "localhost:8001")

    mock_client = AsyncMock()
    mock_client.delete.side_effect = httpx.HTTPStatusError(
        "Server error",
        request=MagicMock(),
        response=MagicMock(status_code=500),
    )

    with patch("app.agent_client.get_http_client", return_value=mock_client):
        result = await agent_client.destroy_container_on_agent(
            mock_agent, "test-lab", "test-container"
        )

    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_destroy_container_on_agent_timeout():
    """Test container destruction handles timeout."""
    mock_agent = MockAgent("agent1", "localhost:8001")

    mock_client = AsyncMock()
    mock_client.delete.side_effect = httpx.ReadTimeout("Request timed out")

    with patch("app.agent_client.get_http_client", return_value=mock_client):
        result = await agent_client.destroy_container_on_agent(
            mock_agent, "test-lab", "test-container"
        )

    assert result["success"] is False
    assert "error" in result
    assert "timed out" in result["error"].lower()


@pytest.mark.asyncio
async def test_destroy_container_on_agent_already_removed():
    """Test that 404 from agent is handled (container already gone)."""
    mock_agent = MockAgent("agent1", "localhost:8001")

    # Agent returns 200 with success message for not found (idempotent)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "success": True,
        "message": "Container not found (already removed)"
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.delete.return_value = mock_response

    with patch("app.agent_client.get_http_client", return_value=mock_client):
        result = await agent_client.destroy_container_on_agent(
            mock_agent, "test-lab", "already-gone"
        )

    # Should still be success (idempotent operation)
    assert result["success"] is True


@pytest.mark.asyncio
async def test_destroy_container_on_agent_connection_error():
    """Test container destruction handles connection errors."""
    mock_agent = MockAgent("agent1", "localhost:8001")

    mock_client = AsyncMock()
    mock_client.delete.side_effect = httpx.ConnectError("Connection refused")

    with patch("app.agent_client.get_http_client", return_value=mock_client):
        result = await agent_client.destroy_container_on_agent(
            mock_agent, "test-lab", "test-container"
        )

    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_destroy_container_on_agent_url_construction():
    """Test URL is correctly constructed with lab_id and container_name."""
    mock_agent = MockAgent("agent1", "http://192.168.1.10:8080")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"success": True}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.delete.return_value = mock_response

    with patch("app.agent_client.get_http_client", return_value=mock_client):
        await agent_client.destroy_container_on_agent(
            mock_agent, "my-lab-123", "archetype-my-lab-123-router1"
        )

    call_args = mock_client.delete.call_args
    url = call_args[0][0]
    assert url == "http://192.168.1.10:8080/containers/my-lab-123/archetype-my-lab-123-router1"


# To run these tests:
# cd api && pytest tests/test_agent_client.py -v
