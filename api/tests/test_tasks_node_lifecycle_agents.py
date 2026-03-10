"""Tests for app/tasks/node_lifecycle_agents.py — Agent resolution mixin."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models
from app.agent_client import AgentUnavailableError
from app.state import JobStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_host(
    test_db: Session,
    *,
    host_id: str | None = None,
    name: str = "agent",
    status: str = "online",
    heartbeat_offset: timedelta = timedelta(seconds=0),
    providers: list[str] | None = None,
) -> models.Host:
    """Create and persist a Host row for testing."""
    hid = host_id or str(uuid4())
    host = models.Host(
        id=hid,
        name=name,
        address=f"{name}.local:8001",
        status=status,
        capabilities=json.dumps({"providers": providers or ["docker"]}),
        version="1.0.0",
        last_heartbeat=datetime.now(timezone.utc) - heartbeat_offset,
        resource_usage=json.dumps({}),
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


def _make_mixin(
    session: Session,
    lab: models.Lab,
    job: models.Job,
    node_states: list[models.NodeState],
    *,
    db_nodes_map: dict | None = None,
    db_nodes_by_gui_id: dict | None = None,
    placements_map: dict | None = None,
    provider: str | None = "docker",
    target_agent_id: str | None = None,
):
    """Build a minimal AgentResolutionMixin instance with injected attributes."""
    from app.tasks.node_lifecycle_agents import AgentResolutionMixin

    mixin = AgentResolutionMixin.__new__(AgentResolutionMixin)
    mixin.session = session
    mixin.lab = lab
    mixin.job = job
    mixin.node_states = list(node_states)
    mixin.db_nodes_map = db_nodes_map or {}
    mixin.db_nodes_by_gui_id = db_nodes_by_gui_id or {}
    mixin.placements_map = placements_map or {}
    mixin.provider = provider
    mixin.target_agent_id = target_agent_id
    mixin.agent = None
    mixin.log_parts = []
    return mixin


def _make_node_state(
    test_db: Session,
    lab: models.Lab,
    name: str,
    *,
    desired: str = "running",
    actual: str = "undeployed",
) -> models.NodeState:
    """Create and persist a NodeState row."""
    ns = models.NodeState(
        lab_id=lab.id,
        node_id=name.lower(),
        node_name=name,
        desired_state=desired,
        actual_state=actual,
    )
    test_db.add(ns)
    test_db.commit()
    test_db.refresh(ns)
    return ns


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def agent_lab(test_db: Session, test_user: models.User) -> models.Lab:
    """Create a lab for agent resolution tests."""
    lab = models.Lab(
        name="Agent Lab",
        owner_id=test_user.id,
        provider="docker",
        state="starting",
        workspace_path="/tmp/agent-lab",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


@pytest.fixture()
def agent_job(
    test_db: Session, agent_lab: models.Lab, test_user: models.User,
) -> models.Job:
    """Create a queued sync job."""
    job = models.Job(
        id=str(uuid4()),
        lab_id=agent_lab.id,
        user_id=test_user.id,
        action="sync:lab",
        status="queued",
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)
    return job


# ---------------------------------------------------------------------------
# TestGetCandidateAgents
# ---------------------------------------------------------------------------

class TestGetCandidateAgents:
    """Tests for _get_candidate_agents."""

    @pytest.mark.asyncio
    async def test_online_agents_included(
        self, test_db: Session, agent_lab: models.Lab, agent_job: models.Job,
    ):
        """Online agents with fresh heartbeat should be returned."""
        host = _make_host(test_db, name="online-agent")
        ns = _make_node_state(test_db, agent_lab, "R1")
        mixin = _make_mixin(test_db, agent_lab, agent_job, [ns])

        with patch(
            "app.agent_client.get_agent_providers",
            return_value=["docker"],
        ):
            candidates = await mixin._get_candidate_agents()

        assert any(c.id == host.id for c in candidates)

    @pytest.mark.asyncio
    async def test_offline_agents_excluded(
        self, test_db: Session, agent_lab: models.Lab, agent_job: models.Job,
    ):
        """Offline agents should not be returned."""
        _make_host(test_db, name="offline-agent", status="offline")
        ns = _make_node_state(test_db, agent_lab, "R1")
        mixin = _make_mixin(test_db, agent_lab, agent_job, [ns])

        with patch(
            "app.agent_client.get_agent_providers",
            return_value=["docker"],
        ):
            candidates = await mixin._get_candidate_agents()

        assert len(candidates) == 0

    @pytest.mark.asyncio
    async def test_stale_heartbeat_excluded(
        self, test_db: Session, agent_lab: models.Lab, agent_job: models.Job,
    ):
        """Agents with stale heartbeats should not be returned."""
        _make_host(
            test_db,
            name="stale-agent",
            status="online",
            heartbeat_offset=timedelta(minutes=10),
        )
        ns = _make_node_state(test_db, agent_lab, "R1")
        mixin = _make_mixin(test_db, agent_lab, agent_job, [ns])

        with patch(
            "app.agent_client.get_agent_providers",
            return_value=["docker"],
        ):
            candidates = await mixin._get_candidate_agents()

        assert len(candidates) == 0


# ---------------------------------------------------------------------------
# TestResolveExplicitPlacements
# ---------------------------------------------------------------------------

class TestResolveExplicitPlacements:
    """Tests for _resolve_explicit_placements."""

    @pytest.mark.asyncio
    async def test_online_host_assigned(
        self, test_db: Session, agent_lab: models.Lab, agent_job: models.Job,
    ):
        """Node with explicit host_id pointing to an online host should be assigned."""
        host = _make_host(test_db, name="explicit-host")
        ns = _make_node_state(test_db, agent_lab, "R1")

        node_def = models.Node(
            lab_id=agent_lab.id,
            gui_id="r1",
            display_name="R1",
            container_name="R1",
            device="linux",
            host_id=host.id,
        )
        test_db.add(node_def)
        test_db.commit()
        test_db.refresh(node_def)

        mixin = _make_mixin(
            test_db,
            agent_lab,
            agent_job,
            [ns],
            db_nodes_map={"R1": node_def},
        )

        all_node_agents: dict[str, str] = {}
        with patch(
            "app.agent_client.is_agent_online",
            return_value=True,
        ), patch(
            "app.agent_client.ping_agent",
            new_callable=AsyncMock,
        ):
            ok = await mixin._resolve_explicit_placements(all_node_agents)

        assert ok is True
        assert all_node_agents["R1"] == host.id

    @pytest.mark.asyncio
    async def test_offline_host_fails_job(
        self, test_db: Session, agent_lab: models.Lab, agent_job: models.Job,
    ):
        """Node with explicit host_id pointing to an offline host should fail the job."""
        host = _make_host(test_db, name="down-host", status="online")
        ns = _make_node_state(test_db, agent_lab, "R1")

        node_def = models.Node(
            lab_id=agent_lab.id,
            gui_id="r1",
            display_name="R1",
            container_name="R1",
            device="linux",
            host_id=host.id,
        )
        test_db.add(node_def)
        test_db.commit()

        mixin = _make_mixin(
            test_db,
            agent_lab,
            agent_job,
            [ns],
            db_nodes_map={"R1": node_def},
        )

        all_node_agents: dict[str, str] = {}
        with patch(
            "app.agent_client.is_agent_online",
            return_value=False,
        ):
            ok = await mixin._resolve_explicit_placements(all_node_agents)

        assert ok is False
        test_db.refresh(agent_job)
        assert agent_job.status == JobStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_unreachable_host_fails_job(
        self, test_db: Session, agent_lab: models.Lab, agent_job: models.Job,
    ):
        """Node with explicit host that is unreachable (ping fails) should fail the job."""
        host = _make_host(test_db, name="unreachable-host")
        ns = _make_node_state(test_db, agent_lab, "R1")

        node_def = models.Node(
            lab_id=agent_lab.id,
            gui_id="r1",
            display_name="R1",
            container_name="R1",
            device="linux",
            host_id=host.id,
        )
        test_db.add(node_def)
        test_db.commit()

        mixin = _make_mixin(
            test_db,
            agent_lab,
            agent_job,
            [ns],
            db_nodes_map={"R1": node_def},
        )

        all_node_agents: dict[str, str] = {}
        with patch(
            "app.agent_client.is_agent_online",
            return_value=True,
        ), patch(
            "app.agent_client.ping_agent",
            new_callable=AsyncMock,
            side_effect=AgentUnavailableError("unreachable"),
        ):
            ok = await mixin._resolve_explicit_placements(all_node_agents)

        assert ok is False
        test_db.refresh(agent_job)
        assert agent_job.status == JobStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_no_explicit_hosts_returns_true(
        self, test_db: Session, agent_lab: models.Lab, agent_job: models.Job,
    ):
        """When no nodes have explicit host_id the method should succeed."""
        ns = _make_node_state(test_db, agent_lab, "R1")

        mixin = _make_mixin(test_db, agent_lab, agent_job, [ns])

        all_node_agents: dict[str, str] = {}
        ok = await mixin._resolve_explicit_placements(all_node_agents)

        assert ok is True
        assert len(all_node_agents) == 0


# ---------------------------------------------------------------------------
# TestResolveAutoPlacements
# ---------------------------------------------------------------------------

class TestResolveAutoPlacements:
    """Tests for _resolve_auto_placements."""

    @pytest.mark.asyncio
    async def test_sticky_used_if_online(
        self, test_db: Session, agent_lab: models.Lab, agent_job: models.Job,
    ):
        """Node with a valid sticky placement on an online host should reuse it."""
        host = _make_host(test_db, name="sticky-host")
        ns = _make_node_state(test_db, agent_lab, "R1")

        placement = models.NodePlacement(
            lab_id=agent_lab.id,
            node_name="R1",
            host_id=host.id,
            status="active",
        )
        test_db.add(placement)
        test_db.commit()
        test_db.refresh(placement)

        mixin = _make_mixin(
            test_db,
            agent_lab,
            agent_job,
            [ns],
            placements_map={"R1": placement},
        )

        all_node_agents: dict[str, str] = {}
        with patch(
            "app.agent_client.is_agent_online",
            return_value=True,
        ), patch(
            "app.agent_client.ping_agent",
            new_callable=AsyncMock,
        ):
            ok = await mixin._resolve_auto_placements(all_node_agents)

        assert ok is True
        assert all_node_agents["R1"] == host.id

    @pytest.mark.asyncio
    async def test_sticky_evicted_if_offline(
        self, test_db: Session, agent_lab: models.Lab, agent_job: models.Job,
    ):
        """Sticky placement on offline host should be evicted (status=failed)."""
        host = _make_host(test_db, name="dead-host", status="online")
        ns = _make_node_state(test_db, agent_lab, "R1")

        placement = models.NodePlacement(
            lab_id=agent_lab.id,
            node_name="R1",
            host_id=host.id,
            status="active",
        )
        test_db.add(placement)
        test_db.commit()
        test_db.refresh(placement)

        # Lab has a fallback agent
        fallback = _make_host(test_db, name="fallback-host")
        agent_lab.agent_id = fallback.id
        test_db.commit()

        mixin = _make_mixin(
            test_db,
            agent_lab,
            agent_job,
            [ns],
            placements_map={"R1": placement},
        )

        all_node_agents: dict[str, str] = {}
        with patch(
            "app.agent_client.is_agent_online",
            side_effect=lambda h: h.id == fallback.id,
        ), patch(
            "app.agent_client.ping_agent",
            new_callable=AsyncMock,
            side_effect=AgentUnavailableError("offline"),
        ), patch(
            "app.agent_client.get_agent_for_node",
            new_callable=AsyncMock,
            return_value=fallback,
        ), patch(
            "app.tasks.node_lifecycle_agents.settings",
        ) as mock_settings:
            mock_settings.agent_stale_timeout = 90
            mock_settings.placement_scoring_enabled = False

            ok = await mixin._resolve_auto_placements(all_node_agents)

        assert ok is True
        test_db.refresh(placement)
        assert placement.status == "failed"
        # Node should fall back to lab.agent_id
        assert all_node_agents.get("R1") == fallback.id

    @pytest.mark.asyncio
    async def test_fallback_to_lab_agent_id(
        self, test_db: Session, agent_lab: models.Lab, agent_job: models.Job,
    ):
        """Nodes with no placement should fall back to lab.agent_id."""
        fallback = _make_host(test_db, name="fallback")
        agent_lab.agent_id = fallback.id
        test_db.commit()

        ns = _make_node_state(test_db, agent_lab, "R1")

        mixin = _make_mixin(test_db, agent_lab, agent_job, [ns])

        all_node_agents: dict[str, str] = {}
        with patch(
            "app.agent_client.is_agent_online",
            return_value=True,
        ), patch(
            "app.tasks.node_lifecycle_agents.settings",
        ) as mock_settings:
            mock_settings.agent_stale_timeout = 90
            mock_settings.placement_scoring_enabled = False

            ok = await mixin._resolve_auto_placements(all_node_agents)

        assert ok is True
        assert all_node_agents["R1"] == fallback.id
