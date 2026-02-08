"""Tests for config extraction functionality.

This module tests:
- extract_configs endpoint (multi-host support)
- _auto_extract_configs_before_destroy function
- Config snapshot deduplication
- Error handling for partial/complete agent failures
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models
from app.tasks.jobs import _auto_extract_configs_before_destroy


# --- Fixtures ---


@pytest.fixture(autouse=True)
def _mock_workspace_write(monkeypatch):
    """Prevent workspace filesystem writes that fail in CI."""
    monkeypatch.setattr(
        "app.services.config_service._save_config_to_workspace",
        lambda workspace, node_name, content: None,
    )


@pytest.fixture
def sample_host_2(test_db: Session) -> models.Host:
    """Create a second agent host for multi-host testing."""
    host = models.Host(
        id="test-agent-2",
        name="Test Agent 2",
        address="localhost:8081",
        status="online",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
        last_heartbeat=datetime.now(timezone.utc),
        resource_usage=json.dumps({
            "cpu_percent": 30.0,
            "memory_percent": 50.0,
        }),
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


@pytest.fixture
def lab_with_multi_host_nodes(
    test_db: Session,
    test_user: models.User,
    sample_host: models.Host,
    sample_host_2: models.Host,
) -> tuple[models.Lab, list[models.Node], list[models.NodePlacement]]:
    """Create a lab with nodes placed on different hosts."""
    lab = models.Lab(
        name="Multi-Host Lab",
        owner_id=test_user.id,
        provider="docker",
        state="running",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)

    # Create nodes (using correct model fields)
    nodes = [
        models.Node(
            lab_id=lab.id,
            gui_id="node-1",
            display_name="EOS-1",
            container_name="eos_1",
            device="ceos",
            image="ceos:latest",
            host_id=sample_host.id,
        ),
        models.Node(
            lab_id=lab.id,
            gui_id="node-2",
            display_name="EOS-2",
            container_name="eos_2",
            device="ceos",
            image="ceos:latest",
            host_id=sample_host_2.id,
        ),
        models.Node(
            lab_id=lab.id,
            gui_id="node-3",
            display_name="EOS-3",
            container_name="eos_3",
            device="ceos",
            image="ceos:latest",
            host_id=sample_host_2.id,
        ),
    ]
    for node in nodes:
        test_db.add(node)
    test_db.commit()

    # Create node placements (mapping nodes to hosts)
    placements = [
        models.NodePlacement(
            lab_id=lab.id,
            node_name="eos_1",
            host_id=sample_host.id,
        ),
        models.NodePlacement(
            lab_id=lab.id,
            node_name="eos_2",
            host_id=sample_host_2.id,
        ),
        models.NodePlacement(
            lab_id=lab.id,
            node_name="eos_3",
            host_id=sample_host_2.id,
        ),
    ]
    for placement in placements:
        test_db.add(placement)
    test_db.commit()

    for node in nodes:
        test_db.refresh(node)

    return lab, nodes, placements


@pytest.fixture
def lab_single_host(
    test_db: Session,
    test_user: models.User,
    sample_host: models.Host,
) -> tuple[models.Lab, list[models.Node], list[models.NodePlacement]]:
    """Create a lab with all nodes on a single host."""
    lab = models.Lab(
        name="Single-Host Lab",
        owner_id=test_user.id,
        provider="docker",
        state="running",
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)

    nodes = [
        models.Node(
            lab_id=lab.id,
            gui_id="node-1",
            display_name="EOS-1",
            container_name="eos_1",
            device="ceos",
            image="ceos:latest",
            host_id=sample_host.id,
        ),
        models.Node(
            lab_id=lab.id,
            gui_id="node-2",
            display_name="EOS-2",
            container_name="eos_2",
            device="ceos",
            image="ceos:latest",
            host_id=sample_host.id,
        ),
    ]
    for node in nodes:
        test_db.add(node)
    test_db.commit()

    placements = [
        models.NodePlacement(
            lab_id=lab.id,
            node_name="eos_1",
            host_id=sample_host.id,
        ),
        models.NodePlacement(
            lab_id=lab.id,
            node_name="eos_2",
            host_id=sample_host.id,
        ),
    ]
    for placement in placements:
        test_db.add(placement)
    test_db.commit()

    return lab, nodes, placements


@pytest.fixture
def lab_no_placements(
    test_db: Session,
    test_user: models.User,
    sample_host: models.Host,
) -> models.Lab:
    """Create a lab with no node placements (fallback scenario)."""
    lab = models.Lab(
        name="No Placements Lab",
        owner_id=test_user.id,
        provider="docker",
        state="running",
        agent_id=sample_host.id,  # Lab-level agent assignment
    )
    test_db.add(lab)
    test_db.commit()
    test_db.refresh(lab)
    return lab


# --- Extract Configs Endpoint Tests ---


class TestExtractConfigsEndpoint:
    """Tests for POST /labs/{lab_id}/extract-configs endpoint."""

    def test_extract_configs_unauthenticated(
        self,
        test_client: TestClient,
        lab_single_host: tuple,
    ):
        """Unauthenticated request should return 401."""
        lab, _, _ = lab_single_host
        response = test_client.post(f"/labs/{lab.id}/extract-configs")
        assert response.status_code == 401

    def test_extract_configs_single_agent(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        lab_single_host: tuple,
        sample_host: models.Host,
    ):
        """Extract configs with single agent returns configs and creates snapshots."""
        lab, nodes, _ = lab_single_host

        mock_result = {
            "success": True,
            "extracted_count": 2,
            "configs": [
                {"node_name": "eos_1", "content": "! config for eos_1\nhostname eos_1"},
                {"node_name": "eos_2", "content": "! config for eos_2\nhostname eos_2"},
            ],
        }

        with patch("app.routers.labs.agent_client") as mock_agent_client, \
             patch("app.routers.labs._save_config_to_workspace"):
            mock_agent_client.is_agent_online.return_value = True
            mock_agent_client.extract_configs_on_agent = AsyncMock(return_value=mock_result)

            response = test_client.post(
                f"/labs/{lab.id}/extract-configs",
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["extracted_count"] == 2
        assert data["snapshots_created"] == 2

        # Verify snapshots were created
        snapshots = test_db.query(models.ConfigSnapshot).filter(
            models.ConfigSnapshot.lab_id == lab.id
        ).all()
        assert len(snapshots) == 2
        node_names = {s.node_name for s in snapshots}
        assert node_names == {"eos_1", "eos_2"}

    def test_extract_configs_multi_host(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        lab_with_multi_host_nodes: tuple,
        sample_host: models.Host,
        sample_host_2: models.Host,
    ):
        """Extract configs from multi-host lab calls all agents and merges results."""
        lab, nodes, placements = lab_with_multi_host_nodes

        # Mock responses from each agent
        agent1_result = {
            "success": True,
            "extracted_count": 1,
            "configs": [
                {"node_name": "eos_1", "content": "! config for eos_1\nhostname eos_1"},
            ],
        }
        agent2_result = {
            "success": True,
            "extracted_count": 2,
            "configs": [
                {"node_name": "eos_2", "content": "! config for eos_2\nhostname eos_2"},
                {"node_name": "eos_3", "content": "! config for eos_3\nhostname eos_3"},
            ],
        }

        async def mock_extract(agent, lab_id):
            if agent.id == sample_host.id:
                return agent1_result
            else:
                return agent2_result

        with patch("app.routers.labs.agent_client") as mock_agent_client, \
             patch("app.routers.labs._save_config_to_workspace"):
            mock_agent_client.is_agent_online.return_value = True
            mock_agent_client.extract_configs_on_agent = AsyncMock(side_effect=mock_extract)

            response = test_client.post(
                f"/labs/{lab.id}/extract-configs",
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["extracted_count"] == 3  # 1 + 2 from both agents
        assert data["snapshots_created"] == 3

        # Verify all snapshots were created
        snapshots = test_db.query(models.ConfigSnapshot).filter(
            models.ConfigSnapshot.lab_id == lab.id
        ).all()
        assert len(snapshots) == 3
        node_names = {s.node_name for s in snapshots}
        assert node_names == {"eos_1", "eos_2", "eos_3"}

        # Verify extract was called for both agents
        assert mock_agent_client.extract_configs_on_agent.call_count == 2

    def test_extract_configs_partial_agent_failure(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        lab_with_multi_host_nodes: tuple,
        sample_host: models.Host,
        sample_host_2: models.Host,
    ):
        """Partial agent failure still returns configs from successful agents."""
        lab, nodes, placements = lab_with_multi_host_nodes

        agent1_result = {
            "success": True,
            "extracted_count": 1,
            "configs": [
                {"node_name": "eos_1", "content": "! config for eos_1"},
            ],
        }

        async def mock_extract(agent, lab_id):
            if agent.id == sample_host.id:
                return agent1_result
            else:
                # Second agent fails
                return {"success": False, "error": "Agent unreachable"}

        with patch("app.routers.labs.agent_client") as mock_agent_client, \
             patch("app.routers.labs._save_config_to_workspace"):
            mock_agent_client.is_agent_online.return_value = True
            mock_agent_client.extract_configs_on_agent = AsyncMock(side_effect=mock_extract)

            response = test_client.post(
                f"/labs/{lab.id}/extract-configs",
                headers=auth_headers,
            )

        # Should succeed with partial results
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["extracted_count"] == 1
        assert data["snapshots_created"] == 1

    def test_extract_configs_all_agents_fail(
        self,
        test_client: TestClient,
        auth_headers: dict,
        lab_with_multi_host_nodes: tuple,
    ):
        """All agents failing returns 500 error."""
        lab, nodes, placements = lab_with_multi_host_nodes

        with patch("app.routers.labs.agent_client") as mock_agent_client:
            mock_agent_client.is_agent_online.return_value = True
            mock_agent_client.extract_configs_on_agent = AsyncMock(
                return_value={"success": False, "error": "Agent error"}
            )

            response = test_client.post(
                f"/labs/{lab.id}/extract-configs",
                headers=auth_headers,
            )

        assert response.status_code == 500
        assert "failed on all agents" in response.json()["detail"]

    def test_extract_configs_no_healthy_agents(
        self,
        test_client: TestClient,
        auth_headers: dict,
        lab_with_multi_host_nodes: tuple,
    ):
        """No healthy agents returns 503 error."""
        lab, nodes, placements = lab_with_multi_host_nodes

        with patch("app.routers.labs.agent_client") as mock_agent_client:
            mock_agent_client.is_agent_online.return_value = False
            mock_agent_client.get_agent_for_lab = AsyncMock(return_value=None)

            response = test_client.post(
                f"/labs/{lab.id}/extract-configs",
                headers=auth_headers,
            )

        assert response.status_code == 503
        assert "No healthy agents" in response.json()["detail"]

    def test_extract_configs_no_placements_fallback(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        lab_no_placements: models.Lab,
        sample_host: models.Host,
    ):
        """Lab without placements falls back to get_agent_for_lab."""
        lab = lab_no_placements

        mock_result = {
            "success": True,
            "extracted_count": 1,
            "configs": [
                {"node_name": "node1", "content": "! config"},
            ],
        }

        with patch("app.routers.labs.agent_client") as mock_agent_client, \
             patch("app.routers.labs._save_config_to_workspace"):
            mock_agent_client.get_agent_for_lab = AsyncMock(return_value=sample_host)
            mock_agent_client.is_agent_online.return_value = True
            mock_agent_client.extract_configs_on_agent = AsyncMock(return_value=mock_result)

            response = test_client.post(
                f"/labs/{lab.id}/extract-configs",
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["extracted_count"] == 1

        # Verify fallback was used
        mock_agent_client.get_agent_for_lab.assert_called_once()

    def test_extract_configs_deduplication(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        lab_single_host: tuple,
        sample_host: models.Host,
    ):
        """Duplicate configs are not saved as new snapshots."""
        lab, nodes, _ = lab_single_host

        config_content = "! config for eos_1\nhostname eos_1"
        mock_result = {
            "success": True,
            "extracted_count": 1,
            "configs": [
                {"node_name": "eos_1", "content": config_content},
            ],
        }

        with patch("app.routers.labs.agent_client") as mock_agent_client, \
             patch("app.routers.labs._save_config_to_workspace"):
            mock_agent_client.is_agent_online.return_value = True
            mock_agent_client.extract_configs_on_agent = AsyncMock(return_value=mock_result)

            # First extraction
            response1 = test_client.post(
                f"/labs/{lab.id}/extract-configs",
                headers=auth_headers,
            )
            assert response1.status_code == 200
            assert response1.json()["snapshots_created"] == 1

            # Second extraction with same content
            response2 = test_client.post(
                f"/labs/{lab.id}/extract-configs",
                headers=auth_headers,
            )
            assert response2.status_code == 200
            assert response2.json()["snapshots_created"] == 0  # No new snapshot

        # Verify only one snapshot exists
        snapshots = test_db.query(models.ConfigSnapshot).filter(
            models.ConfigSnapshot.lab_id == lab.id,
            models.ConfigSnapshot.node_name == "eos_1",
        ).all()
        assert len(snapshots) == 1

    def test_extract_configs_agent_exception(
        self,
        test_client: TestClient,
        test_db: Session,
        auth_headers: dict,
        lab_with_multi_host_nodes: tuple,
        sample_host: models.Host,
        sample_host_2: models.Host,
    ):
        """Agent throwing exception is handled gracefully."""
        lab, nodes, placements = lab_with_multi_host_nodes

        agent1_result = {
            "success": True,
            "extracted_count": 1,
            "configs": [
                {"node_name": "eos_1", "content": "! config for eos_1"},
            ],
        }

        async def mock_extract(agent, lab_id):
            if agent.id == sample_host.id:
                return agent1_result
            else:
                raise Exception("Connection timeout")

        with patch("app.routers.labs.agent_client") as mock_agent_client, \
             patch("app.routers.labs._save_config_to_workspace"):
            mock_agent_client.is_agent_online.return_value = True
            mock_agent_client.extract_configs_on_agent = AsyncMock(side_effect=mock_extract)

            response = test_client.post(
                f"/labs/{lab.id}/extract-configs",
                headers=auth_headers,
            )

        # Should succeed with partial results from agent1
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["extracted_count"] == 1


# --- Auto Extract Before Destroy Tests ---


class TestAutoExtractBeforeDestroy:
    """Tests for _auto_extract_configs_before_destroy function."""

    @pytest.mark.asyncio
    async def test_auto_extract_disabled(
        self,
        test_db: Session,
        sample_host: models.Host,
        lab_single_host: tuple,
    ):
        """Auto-extract is skipped when feature is disabled."""
        lab, _, _ = lab_single_host

        with patch("app.tasks.jobs.settings") as mock_settings:
            mock_settings.feature_auto_extract_on_destroy = False

            with patch("app.tasks.jobs.agent_client") as mock_agent_client:
                await _auto_extract_configs_before_destroy(test_db, lab, sample_host)

                # Should not call extract
                mock_agent_client.extract_configs_on_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_extract_multi_host(
        self,
        test_db: Session,
        sample_host: models.Host,
        sample_host_2: models.Host,
        lab_with_multi_host_nodes: tuple,
    ):
        """Auto-extract before destroy calls all agents in multi-host lab."""
        lab, nodes, placements = lab_with_multi_host_nodes

        agent1_result = {
            "success": True,
            "configs": [{"node_name": "eos_1", "content": "! config"}],
        }
        agent2_result = {
            "success": True,
            "configs": [
                {"node_name": "eos_2", "content": "! config"},
                {"node_name": "eos_3", "content": "! config"},
            ],
        }

        async def mock_extract(agent, lab_id):
            if agent.id == sample_host.id:
                return agent1_result
            else:
                return agent2_result

        with patch("app.tasks.jobs.settings") as mock_settings:
            mock_settings.feature_auto_extract_on_destroy = True

            with patch("app.tasks.jobs.agent_client") as mock_agent_client:
                mock_agent_client.is_agent_online.return_value = True
                mock_agent_client.extract_configs_on_agent = AsyncMock(side_effect=mock_extract)

                await _auto_extract_configs_before_destroy(test_db, lab, sample_host)

                # Both agents should be called
                assert mock_agent_client.extract_configs_on_agent.call_count == 2

        # Verify snapshots were created for all nodes
        snapshots = test_db.query(models.ConfigSnapshot).filter(
            models.ConfigSnapshot.lab_id == lab.id
        ).all()
        assert len(snapshots) == 3

    @pytest.mark.asyncio
    async def test_auto_extract_agent_offline(
        self,
        test_db: Session,
        sample_host: models.Host,
        sample_host_2: models.Host,
        lab_with_multi_host_nodes: tuple,
    ):
        """Offline agents are skipped gracefully."""
        lab, nodes, placements = lab_with_multi_host_nodes

        agent1_result = {
            "success": True,
            "configs": [{"node_name": "eos_1", "content": "! config"}],
        }

        def mock_is_online(agent):
            # Only first agent is online
            return agent.id == sample_host.id

        with patch("app.tasks.jobs.settings") as mock_settings:
            mock_settings.feature_auto_extract_on_destroy = True

            with patch("app.tasks.jobs.agent_client") as mock_agent_client:
                mock_agent_client.is_agent_online.side_effect = mock_is_online
                mock_agent_client.extract_configs_on_agent = AsyncMock(return_value=agent1_result)

                await _auto_extract_configs_before_destroy(test_db, lab, sample_host)

                # Only online agent should be called
                assert mock_agent_client.extract_configs_on_agent.call_count == 1

    @pytest.mark.asyncio
    async def test_auto_extract_no_healthy_agents(
        self,
        test_db: Session,
        sample_host: models.Host,
        lab_with_multi_host_nodes: tuple,
    ):
        """No healthy agents logs warning and returns without error."""
        lab, nodes, placements = lab_with_multi_host_nodes

        with patch("app.tasks.jobs.settings") as mock_settings:
            mock_settings.feature_auto_extract_on_destroy = True

            with patch("app.tasks.jobs.agent_client") as mock_agent_client:
                mock_agent_client.is_agent_online.return_value = False

                # Should not raise, just log warning and return
                await _auto_extract_configs_before_destroy(test_db, lab, sample_host)

                mock_agent_client.extract_configs_on_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_extract_fallback_to_provided_agent(
        self,
        test_db: Session,
        sample_host: models.Host,
        lab_no_placements: models.Lab,
    ):
        """Falls back to provided agent when no placements exist."""
        lab = lab_no_placements

        mock_result = {
            "success": True,
            "configs": [{"node_name": "node1", "content": "! config"}],
        }

        with patch("app.tasks.jobs.settings") as mock_settings:
            mock_settings.feature_auto_extract_on_destroy = True

            with patch("app.tasks.jobs.agent_client") as mock_agent_client:
                mock_agent_client.is_agent_online.return_value = True
                mock_agent_client.extract_configs_on_agent = AsyncMock(return_value=mock_result)

                await _auto_extract_configs_before_destroy(test_db, lab, sample_host)

                # Provided agent should be used
                mock_agent_client.extract_configs_on_agent.assert_called_once()
                call_args = mock_agent_client.extract_configs_on_agent.call_args
                assert call_args[0][0].id == sample_host.id

    @pytest.mark.asyncio
    async def test_auto_extract_partial_failure(
        self,
        test_db: Session,
        sample_host: models.Host,
        sample_host_2: models.Host,
        lab_with_multi_host_nodes: tuple,
    ):
        """Partial agent failure still saves configs from successful agents."""
        lab, nodes, placements = lab_with_multi_host_nodes

        async def mock_extract(agent, lab_id):
            if agent.id == sample_host.id:
                return {
                    "success": True,
                    "configs": [{"node_name": "eos_1", "content": "! config"}],
                }
            else:
                return {"success": False, "error": "Agent error"}

        with patch("app.tasks.jobs.settings") as mock_settings:
            mock_settings.feature_auto_extract_on_destroy = True

            with patch("app.tasks.jobs.agent_client") as mock_agent_client:
                mock_agent_client.is_agent_online.return_value = True
                mock_agent_client.extract_configs_on_agent = AsyncMock(side_effect=mock_extract)

                await _auto_extract_configs_before_destroy(test_db, lab, sample_host)

        # Should have saved config from successful agent
        snapshots = test_db.query(models.ConfigSnapshot).filter(
            models.ConfigSnapshot.lab_id == lab.id
        ).all()
        assert len(snapshots) == 1
        assert snapshots[0].node_name == "eos_1"

    @pytest.mark.asyncio
    async def test_auto_extract_snapshot_type_auto_stop(
        self,
        test_db: Session,
        sample_host: models.Host,
        lab_single_host: tuple,
    ):
        """Auto-extract creates snapshots with 'auto_stop' type."""
        lab, _, _ = lab_single_host

        mock_result = {
            "success": True,
            "configs": [{"node_name": "eos_1", "content": "! config"}],
        }

        with patch("app.tasks.jobs.settings") as mock_settings:
            mock_settings.feature_auto_extract_on_destroy = True

            with patch("app.tasks.jobs.agent_client") as mock_agent_client:
                mock_agent_client.is_agent_online.return_value = True
                mock_agent_client.extract_configs_on_agent = AsyncMock(return_value=mock_result)

                await _auto_extract_configs_before_destroy(test_db, lab, sample_host)

        snapshots = test_db.query(models.ConfigSnapshot).filter(
            models.ConfigSnapshot.lab_id == lab.id
        ).all()
        assert len(snapshots) == 1
        assert snapshots[0].snapshot_type == "auto_stop"


# --- Concurrent Agent Calls Test ---


class TestConcurrentAgentCalls:
    """Tests for concurrent agent call behavior."""

    @pytest.mark.asyncio
    async def test_extract_configs_concurrent_agents(
        self,
        test_db: Session,
        sample_host: models.Host,
        sample_host_2: models.Host,
        lab_with_multi_host_nodes: tuple,
    ):
        """Verify asyncio.gather properly parallelizes agent calls."""
        lab, nodes, placements = lab_with_multi_host_nodes

        call_times = []

        async def mock_extract(agent, lab_id):
            call_times.append((agent.id, asyncio.get_event_loop().time()))
            await asyncio.sleep(0.1)  # Simulate network delay
            return {
                "success": True,
                "configs": [{"node_name": f"node_{agent.id}", "content": "! config"}],
            }

        with patch("app.tasks.jobs.settings") as mock_settings:
            mock_settings.feature_auto_extract_on_destroy = True

            with patch("app.tasks.jobs.agent_client") as mock_agent_client:
                mock_agent_client.is_agent_online.return_value = True
                mock_agent_client.extract_configs_on_agent = AsyncMock(side_effect=mock_extract)

                start = asyncio.get_event_loop().time()
                await _auto_extract_configs_before_destroy(test_db, lab, sample_host)
                elapsed = asyncio.get_event_loop().time() - start

        # Both agents should be called
        assert len(call_times) == 2

        # If truly parallel, elapsed time should be ~0.1s, not ~0.2s
        # Allow some margin for test overhead
        assert elapsed < 0.2, f"Expected parallel execution, but took {elapsed}s"

        # Verify calls started at approximately the same time
        time_diff = abs(call_times[0][1] - call_times[1][1])
        assert time_diff < 0.05, f"Calls should start together, but diff was {time_diff}s"
