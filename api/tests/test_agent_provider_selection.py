"""Tests for agent capability-based provider filtering.

Tests get_agent_providers() and get_healthy_agent() from agent_client.py,
verifying that agent selection correctly filters by provider capabilities.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app import models
from app.agent_client import get_agent_providers, get_healthy_agent


# ---------------------------------------------------------------------------
# TestGetAgentProviders
# ---------------------------------------------------------------------------


class TestGetAgentProviders:
    """Test parsing provider capabilities from agent Host model."""

    def test_both_providers(self, test_db: Session):
        host = models.Host(
            id="agent-both",
            name="Both Providers",
            address="localhost:8080",
            status="online",
            capabilities=json.dumps({"providers": ["docker", "libvirt"]}),
            version="1.0.0",
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.commit()
        test_db.refresh(host)

        providers = get_agent_providers(host)
        assert "docker" in providers
        assert "libvirt" in providers

    def test_docker_only(self, test_db: Session):
        host = models.Host(
            id="agent-docker",
            name="Docker Only",
            address="localhost:8081",
            status="online",
            capabilities=json.dumps({"providers": ["docker"]}),
            version="1.0.0",
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.commit()
        test_db.refresh(host)

        providers = get_agent_providers(host)
        assert providers == ["docker"]

    def test_no_capabilities(self, test_db: Session):
        host = models.Host(
            id="agent-nocaps",
            name="No Caps",
            address="localhost:8082",
            status="online",
            capabilities=None,
            version="1.0.0",
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.commit()
        test_db.refresh(host)

        providers = get_agent_providers(host)
        assert providers == []

    def test_empty_providers_list(self, test_db: Session):
        host = models.Host(
            id="agent-empty",
            name="Empty Providers",
            address="localhost:8083",
            status="online",
            capabilities=json.dumps({"providers": []}),
            version="1.0.0",
            last_heartbeat=datetime.now(timezone.utc),
        )
        test_db.add(host)
        test_db.commit()
        test_db.refresh(host)

        providers = get_agent_providers(host)
        assert providers == []


# ---------------------------------------------------------------------------
# TestHealthyAgentProviderFiltering
# ---------------------------------------------------------------------------


class TestHealthyAgentProviderFiltering:
    """Test get_healthy_agent with provider-based filtering."""

    def _make_agent(self, test_db, agent_id, providers, status="online"):
        host = models.Host(
            id=agent_id,
            name=f"Agent {agent_id}",
            address=f"{agent_id}.local:8080",
            status=status,
            capabilities=json.dumps({"providers": providers}),
            version="1.0.0",
            last_heartbeat=datetime.now(timezone.utc),
            resource_usage=json.dumps({
                "cpu_percent": 20.0,
                "memory_percent": 40.0,
                "disk_percent": 50.0,
                "disk_used_gb": 100.0,
                "disk_total_gb": 200.0,
            }),
        )
        test_db.add(host)
        test_db.commit()
        test_db.refresh(host)
        return host

    @pytest.mark.asyncio
    async def test_no_libvirt_agents_returns_none(self, test_db: Session):
        """When requiring libvirt but no agents support it, returns None."""
        self._make_agent(test_db, "docker-only-1", ["docker"])

        result = await get_healthy_agent(test_db, required_provider="libvirt")
        assert result is None

    @pytest.mark.asyncio
    async def test_libvirt_agent_returned(self, test_db: Session):
        """When requiring libvirt and one agent supports it, returns that agent."""
        self._make_agent(test_db, "docker-agent", ["docker"])
        libvirt_agent = self._make_agent(test_db, "libvirt-agent", ["docker", "libvirt"])

        result = await get_healthy_agent(test_db, required_provider="libvirt")
        assert result is not None
        assert result.id == libvirt_agent.id

    @pytest.mark.asyncio
    async def test_docker_required_skips_libvirt_only(self, test_db: Session):
        """When requiring docker, agents with only libvirt are skipped."""
        self._make_agent(test_db, "libvirt-only", ["libvirt"])
        docker_agent = self._make_agent(test_db, "docker-agent-2", ["docker"])

        result = await get_healthy_agent(test_db, required_provider="docker")
        assert result is not None
        assert result.id == docker_agent.id

    @pytest.mark.asyncio
    async def test_no_provider_filter_returns_any(self, test_db: Session):
        """Without provider filter, any healthy agent is returned."""
        self._make_agent(test_db, "any-agent", ["docker"])

        result = await get_healthy_agent(test_db, required_provider=None)
        assert result is not None

    @pytest.mark.asyncio
    async def test_mixed_capabilities_picks_matching(self, test_db: Session):
        """Agent with both providers matches either requirement."""
        mixed_agent = self._make_agent(test_db, "mixed-agent", ["docker", "libvirt"])

        result_docker = await get_healthy_agent(test_db, required_provider="docker")
        assert result_docker is not None
        assert result_docker.id == mixed_agent.id

        result_libvirt = await get_healthy_agent(test_db, required_provider="libvirt")
        assert result_libvirt is not None
        assert result_libvirt.id == mixed_agent.id
