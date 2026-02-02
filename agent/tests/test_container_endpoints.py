"""Tests for agent container management endpoints.

Tests the DELETE /containers/{lab_id}/{container_name} endpoint used for
live node removal when users delete nodes from the canvas.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient

# Import the FastAPI app from agent main
from agent.main import app


@pytest.fixture
def test_client():
    """Create a test client for the agent API."""
    return TestClient(app)


@pytest.fixture
def mock_docker_client():
    """Create a mock Docker client with common setup."""
    mock_client = MagicMock()
    return mock_client


@pytest.fixture
def mock_container():
    """Create a mock container object."""
    container = MagicMock()
    container.status = "running"
    container.labels = {"archetype.lab_id": "test-lab"}
    container.stop = MagicMock()
    container.remove = MagicMock()
    return container


class TestRemoveContainerForLab:
    """Tests for DELETE /containers/{lab_id}/{container_name} endpoint."""

    def test_delete_container_for_lab_success(
        self,
        test_client: TestClient,
        mock_docker_client: MagicMock,
        mock_container: MagicMock,
    ):
        """Should successfully remove a container for a lab."""
        mock_container.status = "exited"  # Not running, no need to stop
        mock_docker_client.containers.get.return_value = mock_container

        with patch("agent.main.docker.from_env", return_value=mock_docker_client):
            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                # First call: get container, second call: remove
                mock_thread.side_effect = [mock_container, None]

                response = test_client.delete("/containers/test-lab/test-container")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True
        assert result["message"] == "Container removed"

    def test_delete_container_for_lab_not_found(
        self,
        test_client: TestClient,
        mock_docker_client: MagicMock,
    ):
        """Container not found should return success (idempotent operation)."""
        import docker.errors

        with patch("agent.main.docker.from_env", return_value=mock_docker_client):
            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = docker.errors.NotFound("Container not found")

                response = test_client.delete("/containers/test-lab/missing-container")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True
        assert "not found" in result["message"].lower() or "already removed" in result["message"].lower()

    def test_delete_container_stops_running_container_first(
        self,
        test_client: TestClient,
        mock_docker_client: MagicMock,
        mock_container: MagicMock,
    ):
        """Running container should be stopped before removal."""
        mock_container.status = "running"
        mock_docker_client.containers.get.return_value = mock_container

        call_sequence = []

        async def track_calls(func, *args, **kwargs):
            call_sequence.append(func.__name__ if hasattr(func, '__name__') else str(func))
            if "containers.get" in str(func) or func == mock_docker_client.containers.get:
                return mock_container
            return None

        with patch("agent.main.docker.from_env", return_value=mock_docker_client):
            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                # Sequence: get container -> stop -> remove
                mock_thread.side_effect = [mock_container, None, None]

                response = test_client.delete("/containers/test-lab/running-container")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

    def test_delete_container_lab_id_mismatch_logs_warning(
        self,
        test_client: TestClient,
        mock_docker_client: MagicMock,
        mock_container: MagicMock,
    ):
        """Lab ID mismatch should log warning but still proceed."""
        # Container belongs to different lab
        mock_container.status = "exited"
        mock_container.labels = {"archetype.lab_id": "different-lab"}
        mock_docker_client.containers.get.return_value = mock_container

        with patch("agent.main.docker.from_env", return_value=mock_docker_client):
            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = [mock_container, None]

                with patch("agent.main.logger") as mock_logger:
                    response = test_client.delete("/containers/test-lab/mismatched-container")

                    # Should still succeed
                    assert response.status_code == 200
                    result = response.json()
                    assert result["success"] is True

                    # Should log warning (check warning was called)
                    mock_logger.warning.assert_called()

    def test_delete_container_force_flag(
        self,
        test_client: TestClient,
        mock_docker_client: MagicMock,
        mock_container: MagicMock,
    ):
        """Force flag should be passed to container.remove()."""
        mock_container.status = "exited"
        mock_docker_client.containers.get.return_value = mock_container

        with patch("agent.main.docker.from_env", return_value=mock_docker_client):
            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = [mock_container, None]

                response = test_client.delete(
                    "/containers/test-lab/test-container",
                    params={"force": "true"}
                )

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

    def test_delete_container_docker_api_error(
        self,
        test_client: TestClient,
        mock_docker_client: MagicMock,
        mock_container: MagicMock,
    ):
        """Docker API error should return failure status."""
        import docker.errors

        mock_container.status = "exited"

        with patch("agent.main.docker.from_env", return_value=mock_docker_client):
            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = [
                    mock_container,
                    docker.errors.APIError("Permission denied"),
                ]

                response = test_client.delete("/containers/test-lab/test-container")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is False
        assert "error" in result

    def test_delete_container_no_labels(
        self,
        test_client: TestClient,
        mock_docker_client: MagicMock,
        mock_container: MagicMock,
    ):
        """Container without labels should still be removable."""
        mock_container.status = "exited"
        mock_container.labels = {}
        mock_docker_client.containers.get.return_value = mock_container

        with patch("agent.main.docker.from_env", return_value=mock_docker_client):
            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = [mock_container, None]

                response = test_client.delete("/containers/test-lab/unlabeled-container")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True


class TestRemoveContainerGeneric:
    """Tests for the generic DELETE /containers/{container_name} endpoint."""

    def test_delete_container_success(
        self,
        test_client: TestClient,
        mock_docker_client: MagicMock,
        mock_container: MagicMock,
    ):
        """Should remove a container by name."""
        mock_container.status = "exited"
        mock_docker_client.containers.get.return_value = mock_container

        with patch("agent.main.docker.from_env", return_value=mock_docker_client):
            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = [mock_container, None]

                response = test_client.delete("/containers/test-container")

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

    def test_delete_container_not_found_returns_404(
        self,
        test_client: TestClient,
        mock_docker_client: MagicMock,
    ):
        """Generic endpoint returns 404 for missing container."""
        import docker.errors

        with patch("agent.main.docker.from_env", return_value=mock_docker_client):
            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = docker.errors.NotFound("not found")

                response = test_client.delete("/containers/missing")

        assert response.status_code == 404
