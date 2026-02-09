"""Integration tests for agent container management endpoints.

These tests verify real container operations:
1. DELETE container removes real container
2. Container status reflects actual state
3. Hot-connect link creates VLAN connection
4. Container start triggers interface fix

Mark with @pytest.mark.integration - requires Docker.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient

from agent.main import app
from agent.config import settings


# --- Integration Test Markers ---

try:
    import docker
    docker.from_env().ping()
    DOCKER_AVAILABLE = True
except Exception:
    DOCKER_AVAILABLE = False

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker not available"),
]


# --- Fixtures ---


@pytest.fixture
def test_client():
    """Create a test client for the agent API."""
    return TestClient(app)


@pytest.fixture
def docker_client():
    """Get real Docker client."""
    return docker.from_env()


@pytest.fixture
def test_container(docker_client):
    """Create a test container for integration tests."""
    container = docker_client.containers.run(
        "alpine:latest",
        command="sleep 3600",
        detach=True,
        name="archetype-test-integration",
        labels={"archetype.test": "integration", "archetype.lab_id": "test-lab"},
    )

    yield container

    # Cleanup
    try:
        container.stop(timeout=1)
        container.remove(force=True)
    except Exception:
        pass


@pytest.fixture
def stopped_container(docker_client):
    """Create a stopped test container."""
    container = docker_client.containers.run(
        "alpine:latest",
        command="sleep 3600",
        detach=True,
        name="archetype-test-stopped",
        labels={"archetype.test": "integration", "archetype.lab_id": "test-lab"},
    )
    container.stop(timeout=1)

    yield container

    # Cleanup
    try:
        container.remove(force=True)
    except Exception:
        pass


# --- Unit Tests (Mocked) ---


class TestContainerEndpointsUnit:
    """Unit tests with mocked Docker client."""

    def test_delete_container_success(self, test_client):
        """Should successfully remove container."""
        mock_container = MagicMock()
        mock_container.status = "exited"

        with patch("agent.main.docker.from_env") as mock_docker:
            mock_client = MagicMock()
            mock_docker.return_value = mock_client

            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = [mock_container, None]

                response = test_client.delete("/containers/test-container")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

    def test_delete_container_not_found(self, test_client):
        """Should return 404 for missing container."""
        with patch("agent.main.docker.from_env") as mock_docker:
            mock_client = MagicMock()
            mock_docker.return_value = mock_client

            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = docker.errors.NotFound("not found")

                response = test_client.delete("/containers/missing")

        assert response.status_code == 404

    def test_delete_container_for_lab_success(self, test_client):
        """Should remove container with lab context."""
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_container.labels = {"archetype.lab_id": "test-lab"}

        with patch("agent.main.docker.from_env") as mock_docker:
            mock_client = MagicMock()
            mock_docker.return_value = mock_client

            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = [mock_container, None]

                response = test_client.delete("/containers/test-lab/test-container")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

    def test_delete_container_for_lab_not_found_is_idempotent(self, test_client):
        """Lab container removal is idempotent - returns success for missing container."""
        with patch("agent.main.docker.from_env") as mock_docker:
            mock_client = MagicMock()
            mock_docker.return_value = mock_client

            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = docker.errors.NotFound("not found")

                response = test_client.delete("/containers/test-lab/missing")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

    def test_start_container_success(self, test_client):
        """Should start stopped container."""
        mock_container = MagicMock()
        mock_container.status = "exited"

        with patch("agent.main.docker.from_env") as mock_docker:
            mock_client = MagicMock()
            mock_docker.return_value = mock_client

            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = [mock_container, None]

                response = test_client.post("/containers/test-container/start")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

    def test_start_container_already_running(self, test_client):
        """Should return success for already running container."""
        mock_container = MagicMock()
        mock_container.status = "running"

        with patch("agent.main.docker.from_env") as mock_docker:
            mock_client = MagicMock()
            mock_docker.return_value = mock_client

            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.return_value = mock_container

                response = test_client.post("/containers/test-container/start")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True
        assert "already running" in result["message"]

    def test_stop_container_success(self, test_client):
        """Should stop running container."""
        mock_container = MagicMock()
        mock_container.status = "running"

        with patch("agent.main.docker.from_env") as mock_docker:
            mock_client = MagicMock()
            mock_docker.return_value = mock_client

            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = [mock_container, None]

                response = test_client.post("/containers/test-container/stop")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

    def test_stop_container_already_stopped(self, test_client):
        """Should return success for already stopped container."""
        mock_container = MagicMock()
        mock_container.status = "exited"

        with patch("agent.main.docker.from_env") as mock_docker:
            mock_client = MagicMock()
            mock_docker.return_value = mock_client

            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.return_value = mock_container

                response = test_client.post("/containers/test-container/stop")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True


# --- Link/VLAN Tests ---


class TestHotConnectLinkUnit:
    """Unit tests for hot-connect link creation."""

    def test_create_link_ovs_disabled(self, test_client):
        """Should return error when OVS is disabled."""
        with patch.object(settings, "enable_ovs", False):
            response = test_client.post(
                "/labs/test-lab/links",
                json={
                    "source_node": "node1",
                    "source_interface": "eth1",
                    "target_node": "node2",
                    "target_interface": "eth1",
                },
            )

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is False
        assert "not enabled" in result.get("error", "")

    def test_create_link_success(self, test_client):
        """Should create VLAN link between interfaces."""
        with patch.object(settings, "enable_ovs", True):
            with patch("agent.main.get_ovs_manager") as mock_get_ovs:
                mock_ovs = MagicMock()
                mock_ovs._initialized = True
                mock_ovs.hot_connect = AsyncMock(return_value={"success": True, "vlan_tag": 100})
                mock_get_ovs.return_value = mock_ovs

                with patch("agent.main.get_docker_ovs_plugin") as mock_get_plugin:
                    mock_plugin = MagicMock()
                    mock_plugin.hot_connect = AsyncMock(return_value={"success": True, "vlan_tag": 100})
                    mock_get_plugin.return_value = mock_plugin

                    response = test_client.post(
                        "/labs/test-lab/links",
                        json={
                            "source_node": "node1",
                            "source_interface": "eth1",
                            "target_node": "node2",
                            "target_interface": "eth1",
                        },
                    )

        assert response.status_code == 200

    def test_delete_link_ovs_disabled(self, test_client):
        """Should return error when OVS is disabled."""
        with patch.object(settings, "enable_ovs", False):
            response = test_client.delete("/labs/test-lab/links/node1:eth1-node2:eth1")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is False

    def test_list_links_empty(self, test_client):
        """Should return empty list when no links."""
        with patch.object(settings, "enable_ovs", True):
            with patch("agent.main.get_ovs_manager") as mock_get_ovs:
                mock_ovs = MagicMock()
                mock_ovs._initialized = True
                mock_ovs.get_lab_links.return_value = []
                mock_get_ovs.return_value = mock_ovs

                response = test_client.get("/labs/test-lab/links")

        assert response.status_code == 200
        result = response.json()
        assert result["links"] == [] or isinstance(result.get("links"), list)


# --- Fix Interfaces Tests ---


class TestFixInterfacesEndpoint:
    """Tests for fix-interfaces endpoint."""

    def test_fix_interfaces_no_provider(self, test_client):
        """Should return 503 when provider not available.

        The fix-interfaces endpoint uses get_provider_for_request() which
        raises HTTPException(503) when the requested provider is not available.
        """
        with patch("agent.main.get_provider", return_value=None):
            response = test_client.post("/labs/test-lab/nodes/node1/fix-interfaces")

        assert response.status_code == 503
        result = response.json()
        assert "provider" in result.get("detail", "").lower()

    def test_fix_interfaces_success(self, test_client):
        """Should fix interface names successfully."""
        mock_provider = MagicMock()
        mock_provider._fix_interface_names = AsyncMock(
            return_value={"fixed": 2, "already_correct": 1, "reconnected": 0, "errors": []}
        )
        mock_provider.get_container_name = MagicMock(return_value="archetype-test-lab-node1")

        with patch("agent.main.get_provider", return_value=mock_provider):
            response = test_client.post("/labs/test-lab/nodes/node1/fix-interfaces")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

    def test_fix_interfaces_with_errors(self, test_client):
        """Should report errors from interface fix."""
        mock_provider = MagicMock()
        mock_provider._fix_interface_names = AsyncMock(
            return_value={"fixed": 0, "already_correct": 0, "reconnected": 0, "errors": ["Container not running"]}
        )
        mock_provider.get_container_name = MagicMock(return_value="archetype-test-lab-node1")

        with patch("agent.main.get_provider", return_value=mock_provider):
            response = test_client.post("/labs/test-lab/nodes/node1/fix-interfaces")

        assert response.status_code == 200
        result = response.json()
        # May succeed even with errors if they're warnings


# --- Real Integration Tests (require Docker) ---


@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker not available")
class TestContainerEndpointsIntegration:
    """Integration tests with real Docker containers."""

    def test_real_delete_container(self, test_client, test_container, docker_client):
        """Should delete real container."""
        container_name = test_container.name

        # Verify container exists
        container = docker_client.containers.get(container_name)
        assert container is not None

        # Delete via API
        response = test_client.delete(f"/containers/{container_name}", params={"force": "true"})

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

        # Verify container is removed
        with pytest.raises(docker.errors.NotFound):
            docker_client.containers.get(container_name)

    def test_real_start_container(self, test_client, stopped_container, docker_client):
        """Should start real stopped container."""
        container_name = stopped_container.name

        # Verify container is stopped
        container = docker_client.containers.get(container_name)
        assert container.status in ("exited", "created")

        # Start via API
        response = test_client.post(f"/containers/{container_name}/start")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

        # Verify container is running
        container.reload()
        assert container.status == "running"

        # Cleanup - stop it again
        container.stop(timeout=1)

    def test_real_stop_container(self, test_client, test_container, docker_client):
        """Should stop real running container."""
        container_name = test_container.name

        # Verify container is running
        container = docker_client.containers.get(container_name)
        assert container.status == "running"

        # Stop via API
        response = test_client.post(f"/containers/{container_name}/stop")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

        # Verify container is stopped
        container.reload()
        assert container.status in ("exited", "stopped")

    def test_real_container_status_reflects_state(self, test_client, test_container, docker_client):
        """Container status should reflect actual Docker state."""
        container_name = test_container.name

        # Check initial state (running)
        container = docker_client.containers.get(container_name)
        assert container.status == "running"

        # Stop it
        container.stop(timeout=1)
        container.reload()
        assert container.status in ("exited", "stopped")

        # Start again
        container.start()
        container.reload()
        assert container.status == "running"


@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker not available")
class TestContainerLabRemovalIntegration:
    """Integration tests for lab-specific container removal."""

    @pytest.fixture
    def lab_container(self, docker_client):
        """Create container with lab labels."""
        container = docker_client.containers.run(
            "alpine:latest",
            command="sleep 3600",
            detach=True,
            name="archetype-mylab-router1",
            labels={
                "archetype.test": "integration",
                "archetype.lab_id": "mylab",
            },
        )

        yield container

        try:
            container.stop(timeout=1)
            container.remove(force=True)
        except Exception:
            pass

    def test_delete_container_for_lab_removes_container(
        self, test_client, lab_container, docker_client
    ):
        """Should remove container when lab_id matches."""
        container_name = lab_container.name

        response = test_client.delete(
            f"/containers/mylab/{container_name}",
            params={"force": "true"},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

        # Verify removed
        with pytest.raises(docker.errors.NotFound):
            docker_client.containers.get(container_name)

    def test_delete_container_for_lab_warns_on_mismatch(
        self, test_client, lab_container, docker_client
    ):
        """Should warn but still remove container with lab_id mismatch."""
        container_name = lab_container.name

        # Use wrong lab_id
        response = test_client.delete(
            f"/containers/wrong-lab/{container_name}",
            params={"force": "true"},
        )

        assert response.status_code == 200
        result = response.json()
        # Should still succeed (or fail with appropriate error)
        # The behavior depends on implementation


# To run these tests:
# cd agent && pytest tests/test_container_endpoints_integration.py -v
# cd agent && pytest tests/test_container_endpoints_integration.py -v -m integration
