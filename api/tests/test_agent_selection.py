"""Tests for agent selection and health management.

This module tests the agent selection algorithms including:
- get_healthy_agent: Load-balanced agent selection
- get_agent_for_lab: Affinity-aware agent selection
- get_agent_by_name: Name-based lookup
- update_stale_agents: Heartbeat-based offline detection
- Agent capability filtering
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app import agent_client, models
from app.config import settings


class TestGetHealthyAgent:
    """Tests for get_healthy_agent function."""

    @pytest.mark.asyncio
    async def test_no_agents(self, test_db: Session):
        """Returns None when no agents are registered."""
        result = await agent_client.get_healthy_agent(test_db)
        assert result is None

    @pytest.mark.asyncio
    async def test_all_agents_offline(self, test_db: Session):
        """Returns None when all agents are offline."""
        host = models.Host(
            id="offline-agent",
            name="Offline Agent",
            address="localhost:8080",
            status="offline",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.commit()

        result = await agent_client.get_healthy_agent(test_db)
        assert result is None

    @pytest.mark.asyncio
    async def test_stale_heartbeat_excluded(self, test_db: Session):
        """Agents with stale heartbeats are excluded."""
        # Agent with stale heartbeat
        host = models.Host(
            id="stale-agent",
            name="Stale Agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc) - timedelta(
                seconds=settings.agent_stale_timeout + 10
            ),
        )
        test_db.add(host)
        test_db.commit()

        result = await agent_client.get_healthy_agent(test_db)
        assert result is None

    @pytest.mark.asyncio
    async def test_healthy_agent_selected(self, test_db: Session):
        """Healthy agent with recent heartbeat is selected."""
        host = models.Host(
            id="healthy-agent",
            name="Healthy Agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.commit()

        result = await agent_client.get_healthy_agent(test_db)
        assert result is not None
        assert result.id == "healthy-agent"

    @pytest.mark.asyncio
    async def test_provider_filtering(self, test_db: Session):
        """Only agents supporting required provider are returned."""
        # Agent with docker only
        docker_agent = models.Host(
            id="docker-agent",
            name="Docker Agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        # Agent with libvirt only
        libvirt_agent = models.Host(
            id="libvirt-agent",
            name="Libvirt Agent",
            address="localhost:8081",
            status="online",
            capabilities=json.dumps({"providers": ["libvirt"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add_all([docker_agent, libvirt_agent])
        test_db.commit()

        # Request docker provider
        result = await agent_client.get_healthy_agent(test_db, required_provider="docker")
        assert result is not None
        assert result.id == "docker-agent"

        # Request libvirt provider
        result = await agent_client.get_healthy_agent(test_db, required_provider="libvirt")
        assert result is not None
        assert result.id == "libvirt-agent"

        # Request unsupported provider
        result = await agent_client.get_healthy_agent(test_db, required_provider="vmware")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_balancing(self, test_db: Session):
        """Least-loaded agent is preferred."""
        # Create two agents
        agent1 = models.Host(
            id="agent-1",
            name="Agent 1",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"], "max_concurrent_jobs": 4}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        agent2 = models.Host(
            id="agent-2",
            name="Agent 2",
            address="localhost:8081",
            status="online",
            capabilities=json.dumps({"providers": ["docker"], "max_concurrent_jobs": 4}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add_all([agent1, agent2])
        test_db.commit()

        # Add jobs to agent1
        for i in range(3):
            job = models.Job(
                agent_id="agent-1",
                action="up",
                status="running",
            )
            test_db.add(job)
        test_db.commit()

        # Should select agent2 (less loaded)
        result = await agent_client.get_healthy_agent(test_db, required_provider="docker")
        assert result is not None
        assert result.id == "agent-2"

    @pytest.mark.asyncio
    async def test_capacity_limit(self, test_db: Session):
        """Agents at capacity are excluded."""
        agent = models.Host(
            id="busy-agent",
            name="Busy Agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"], "max_concurrent_jobs": 2}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(agent)
        test_db.commit()

        # Fill capacity
        for i in range(2):
            job = models.Job(
                agent_id="busy-agent",
                action="up",
                status="running",
            )
            test_db.add(job)
        test_db.commit()

        result = await agent_client.get_healthy_agent(test_db, required_provider="docker")
        assert result is None  # No agent has capacity

    @pytest.mark.asyncio
    async def test_affinity_preference(self, test_db: Session):
        """Preferred agent is selected if healthy and has capacity."""
        agent1 = models.Host(
            id="agent-1",
            name="Agent 1",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        agent2 = models.Host(
            id="agent-2",
            name="Agent 2",
            address="localhost:8081",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add_all([agent1, agent2])
        test_db.commit()

        # Request with affinity to agent-2
        result = await agent_client.get_healthy_agent(
            test_db,
            required_provider="docker",
            prefer_agent_id="agent-2"
        )
        assert result is not None
        assert result.id == "agent-2"

    @pytest.mark.asyncio
    async def test_exclude_agents(self, test_db: Session):
        """Excluded agents are not selected."""
        agent1 = models.Host(
            id="agent-1",
            name="Agent 1",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        agent2 = models.Host(
            id="agent-2",
            name="Agent 2",
            address="localhost:8081",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add_all([agent1, agent2])
        test_db.commit()

        # Exclude agent-1
        result = await agent_client.get_healthy_agent(
            test_db,
            required_provider="docker",
            exclude_agents=["agent-1"]
        )
        assert result is not None
        assert result.id == "agent-2"


class TestGetAgentForLab:
    """Tests for get_agent_for_lab function."""

    @pytest.mark.asyncio
    async def test_respects_lab_affinity(self, test_db: Session, test_user: models.User):
        """Uses lab's assigned agent if healthy."""
        agent = models.Host(
            id="lab-agent",
            name="Lab Agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(agent)
        test_db.commit()

        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            provider="docker",
            agent_id="lab-agent",
        )
        test_db.add(lab)
        test_db.commit()

        result = await agent_client.get_agent_for_lab(test_db, lab)
        assert result is not None
        assert result.id == "lab-agent"

    @pytest.mark.asyncio
    async def test_finds_new_agent_if_assigned_offline(self, test_db: Session, test_user: models.User):
        """Falls back to another agent if assigned agent is offline."""
        offline_agent = models.Host(
            id="offline-agent",
            name="Offline Agent",
            address="localhost:8080",
            status="offline",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=300),
        )
        healthy_agent = models.Host(
            id="healthy-agent",
            name="Healthy Agent",
            address="localhost:8081",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add_all([offline_agent, healthy_agent])
        test_db.commit()

        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            provider="docker",
            agent_id="offline-agent",  # Assigned to offline agent
        )
        test_db.add(lab)
        test_db.commit()

        result = await agent_client.get_agent_for_lab(test_db, lab)
        assert result is not None
        assert result.id == "healthy-agent"


class TestGetAgentByName:
    """Tests for get_agent_by_name function."""

    @pytest.mark.asyncio
    async def test_finds_agent_by_name(self, test_db: Session):
        """Returns agent with matching name."""
        agent = models.Host(
            id="agent-1",
            name="my-agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(agent)
        test_db.commit()

        result = await agent_client.get_agent_by_name(test_db, "my-agent")
        assert result is not None
        assert result.id == "agent-1"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_name(self, test_db: Session):
        """Returns None if agent name not found."""
        result = await agent_client.get_agent_by_name(test_db, "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_offline_agent(self, test_db: Session):
        """Returns None if agent is offline."""
        agent = models.Host(
            id="agent-1",
            name="offline-agent",
            address="localhost:8080",
            status="offline",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(agent)
        test_db.commit()

        result = await agent_client.get_agent_by_name(test_db, "offline-agent")
        assert result is None

    @pytest.mark.asyncio
    async def test_provider_capability_check(self, test_db: Session):
        """Returns None if agent lacks required provider."""
        agent = models.Host(
            id="agent-1",
            name="docker-only",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(agent)
        test_db.commit()

        result = await agent_client.get_agent_by_name(test_db, "docker-only", required_provider="libvirt")
        assert result is None


class TestUpdateStaleAgents:
    """Tests for update_stale_agents function."""

    @pytest.mark.asyncio
    async def test_marks_stale_agents_offline(self, test_db: Session):
        """Agents with old heartbeats are marked offline."""
        stale_agent = models.Host(
            id="stale-1",
            name="Stale Agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({}),
            last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=120),
        )
        test_db.add(stale_agent)
        test_db.commit()

        marked = await agent_client.update_stale_agents(test_db, timeout_seconds=60)
        assert "stale-1" in marked

        test_db.refresh(stale_agent)
        assert stale_agent.status == "offline"

    @pytest.mark.asyncio
    async def test_keeps_healthy_agents_online(self, test_db: Session):
        """Agents with recent heartbeats stay online."""
        healthy_agent = models.Host(
            id="healthy-1",
            name="Healthy Agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({}),
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(healthy_agent)
        test_db.commit()

        marked = await agent_client.update_stale_agents(test_db, timeout_seconds=60)
        assert "healthy-1" not in marked

        test_db.refresh(healthy_agent)
        assert healthy_agent.status == "online"

    @pytest.mark.asyncio
    async def test_marks_null_heartbeat_offline(self, test_db: Session):
        """Agents with NULL heartbeat are marked offline."""
        no_heartbeat = models.Host(
            id="no-heartbeat",
            name="No Heartbeat Agent",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({}),
            last_heartbeat=None,
        )
        test_db.add(no_heartbeat)
        test_db.commit()

        marked = await agent_client.update_stale_agents(test_db, timeout_seconds=60)
        assert "no-heartbeat" in marked


class TestAgentCapabilities:
    """Tests for capability parsing functions."""

    def test_parse_capabilities_valid_json(self):
        """Parses valid JSON capabilities."""
        agent = models.Host(
            id="test",
            name="Test",
            address="localhost:8080",
            status="online",
            capabilities='{"providers": ["docker"], "features": ["vxlan"]}',
        )
        caps = agent_client.parse_capabilities(agent)
        assert caps["providers"] == ["docker"]
        assert caps["features"] == ["vxlan"]

    def test_parse_capabilities_invalid_json(self):
        """Returns empty dict for invalid JSON."""
        agent = models.Host(
            id="test",
            name="Test",
            address="localhost:8080",
            status="online",
            capabilities="not valid json",
        )
        caps = agent_client.parse_capabilities(agent)
        assert caps == {}

    def test_parse_capabilities_empty(self):
        """Returns empty dict for empty capabilities."""
        agent = models.Host(
            id="test",
            name="Test",
            address="localhost:8080",
            status="online",
            capabilities="",
        )
        caps = agent_client.parse_capabilities(agent)
        assert caps == {}

    def test_get_agent_providers(self):
        """Extracts providers list from capabilities."""
        agent = models.Host(
            id="test",
            name="Test",
            address="localhost:8080",
            status="online",
            capabilities='{"providers": ["docker", "libvirt"]}',
        )
        providers = agent_client.get_agent_providers(agent)
        assert "docker" in providers
        assert "libvirt" in providers

    def test_get_agent_max_jobs_default(self):
        """Returns default max jobs if not specified."""
        agent = models.Host(
            id="test",
            name="Test",
            address="localhost:8080",
            status="online",
            capabilities='{"providers": ["docker"]}',
        )
        max_jobs = agent_client.get_agent_max_jobs(agent)
        assert max_jobs == 4  # Default

    def test_get_agent_max_jobs_custom(self):
        """Returns custom max jobs from capabilities."""
        agent = models.Host(
            id="test",
            name="Test",
            address="localhost:8080",
            status="online",
            capabilities='{"providers": ["docker"], "max_concurrent_jobs": 8}',
        )
        max_jobs = agent_client.get_agent_max_jobs(agent)
        assert max_jobs == 8

    def test_agent_supports_vxlan(self):
        """Checks VXLAN support in capabilities."""
        vxlan_agent = models.Host(
            id="test",
            name="Test",
            address="localhost:8080",
            status="online",
            capabilities='{"features": ["vxlan"]}',
        )
        no_vxlan_agent = models.Host(
            id="test2",
            name="Test 2",
            address="localhost:8081",
            status="online",
            capabilities='{"features": []}',
        )
        assert agent_client.agent_supports_vxlan(vxlan_agent) is True
        assert agent_client.agent_supports_vxlan(no_vxlan_agent) is False


class TestAgentUrlBuilding:
    """Tests for URL building functions."""

    def test_get_agent_url_with_host_port(self):
        """Builds URL from host:port format."""
        agent = models.Host(
            id="test",
            name="Test",
            address="192.168.1.10:8080",
            status="online",
            capabilities="{}",
        )
        url = agent_client.get_agent_url(agent)
        assert url == "http://192.168.1.10:8080"

    def test_get_agent_url_with_http(self):
        """Returns URL unchanged if already has http."""
        agent = models.Host(
            id="test",
            name="Test",
            address="http://192.168.1.10:8080",
            status="online",
            capabilities="{}",
        )
        url = agent_client.get_agent_url(agent)
        assert url == "http://192.168.1.10:8080"

    def test_get_agent_console_url(self):
        """Builds WebSocket console URL."""
        agent = models.Host(
            id="test",
            name="Test",
            address="192.168.1.10:8080",
            status="online",
            capabilities="{}",
        )
        url = agent_client.get_agent_console_url(agent, "lab-123", "router1")
        assert url == "ws://192.168.1.10:8080/console/lab-123/router1"


class TestCountActiveJobs:
    """Tests for count_active_jobs function."""

    def test_counts_queued_and_running(self, test_db: Session):
        """Counts both queued and running jobs."""
        agent_id = "test-agent"

        # Add various jobs
        jobs = [
            models.Job(agent_id=agent_id, action="up", status="queued"),
            models.Job(agent_id=agent_id, action="up", status="running"),
            models.Job(agent_id=agent_id, action="down", status="completed"),
            models.Job(agent_id=agent_id, action="down", status="failed"),
        ]
        for job in jobs:
            test_db.add(job)
        test_db.commit()

        count = agent_client.count_active_jobs(test_db, agent_id)
        assert count == 2  # Only queued and running

    def test_returns_zero_for_no_jobs(self, test_db: Session):
        """Returns 0 when no active jobs."""
        count = agent_client.count_active_jobs(test_db, "nonexistent-agent")
        assert count == 0

    def test_count_active_jobs_by_agent(self, test_db: Session):
        """Counts active jobs for multiple agents in one query."""
        jobs = [
            models.Job(agent_id="agent-1", action="up", status="queued"),
            models.Job(agent_id="agent-1", action="up", status="running"),
            models.Job(agent_id="agent-2", action="down", status="running"),
            models.Job(agent_id="agent-2", action="down", status="completed"),
        ]
        for job in jobs:
            test_db.add(job)
        test_db.commit()

        counts = agent_client.count_active_jobs_by_agent(test_db, ["agent-1", "agent-2", "agent-3"])
        assert counts["agent-1"] == 2
        assert counts["agent-2"] == 1
        assert counts.get("agent-3", 0) == 0
