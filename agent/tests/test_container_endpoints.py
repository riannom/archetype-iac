"""Tests for agent container management endpoints.

Tests the DELETE /containers/{lab_id}/{container_name} endpoint used for
live node removal when users delete nodes from the canvas.
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import HTTPException

from agent.main import _sync_prune_docker, prune_docker, remove_container, remove_container_for_lab
from agent.schemas import DockerPruneRequest, DockerPruneResponse


def _run(coro):
    return asyncio.run(coro)


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

                result = _run(remove_container_for_lab("test-lab", "archetype-test-container"))

        assert result["success"] is True
        assert result["message"] == "Container removed"

    def test_delete_container_for_lab_not_found(
        self,
        mock_docker_client: MagicMock,
    ):
        """Container not found should return success (idempotent operation)."""
        import docker.errors

        with patch("agent.main.docker.from_env", return_value=mock_docker_client):
            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = docker.errors.NotFound("Container not found")

                result = _run(remove_container_for_lab("test-lab", "archetype-missing-container"))

        assert result["success"] is True
        assert "not found" in result["message"].lower() or "already removed" in result["message"].lower()

    def test_delete_container_stops_running_container_first(
        self,
        mock_docker_client: MagicMock,
        mock_container: MagicMock,
    ):
        """Running container should be stopped before removal."""
        mock_container.status = "running"
        mock_docker_client.containers.get.return_value = mock_container

        with patch("agent.main.docker.from_env", return_value=mock_docker_client):
            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                # Sequence: get container -> stop -> remove
                mock_thread.side_effect = [mock_container, None, None]

                result = _run(remove_container_for_lab("test-lab", "archetype-running-container"))

        assert result["success"] is True
        assert result["message"] == "Container removed"

    def test_delete_container_lab_id_mismatch(
        self,
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
                    result = _run(remove_container_for_lab("test-lab", "archetype-mismatched-container"))

                    # Should still succeed
                    assert result["success"] is True

                    # Should log warning (check warning was called)
                    mock_logger.warning.assert_called()

    def test_delete_container_force_flag(
        self,
        mock_docker_client: MagicMock,
        mock_container: MagicMock,
    ):
        """Force flag should be passed to container.remove()."""
        mock_container.status = "exited"
        mock_docker_client.containers.get.return_value = mock_container

        with patch("agent.main.docker.from_env", return_value=mock_docker_client):
            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = [mock_container, None]

                result = _run(remove_container_for_lab(
                    "test-lab",
                    "archetype-test-container",
                    force=True,
                ))

        assert result["success"] is True

    def test_delete_container_docker_api_error(
        self,
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

                result = _run(remove_container_for_lab("test-lab", "archetype-test-container"))

        assert result["success"] is False
        assert "error" in result

    def test_delete_container_no_labels(
        self,
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

                result = _run(remove_container_for_lab("test-lab", "archetype-unlabeled-container"))

        assert result["success"] is True


class TestRemoveContainerGeneric:
    """Tests for the generic DELETE /containers/{container_name} endpoint."""

    def test_delete_container_success(
        self,
        mock_docker_client: MagicMock,
        mock_container: MagicMock,
    ):
        """Should remove a container by name."""
        mock_container.status = "exited"
        mock_docker_client.containers.get.return_value = mock_container

        with patch("agent.main.docker.from_env", return_value=mock_docker_client):
            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = [mock_container, None]

                result = _run(remove_container("archetype-test-container"))

        assert result["success"] is True

    def test_delete_container_not_found_returns_404(
        self,
        mock_docker_client: MagicMock,
    ):
        """Generic endpoint returns 404 for missing container."""
        import docker.errors

        with patch("agent.main.docker.from_env", return_value=mock_docker_client):
            with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                mock_thread.side_effect = docker.errors.NotFound("not found")

                with pytest.raises(HTTPException) as exc:
                    _run(remove_container("archetype-missing"))

        assert exc.value.status_code == 404

    def test_delete_container_invalid_name_returns_400(self):
        """Container name without valid prefix should return 400."""
        with pytest.raises(HTTPException) as exc:
            _run(remove_container("bad-name"))

        assert exc.value.status_code == 400


class TestPruneDockerEndpoint:
    """Tests for POST /prune-docker endpoint behavior."""

    def test_prune_docker_offloads_to_thread(self):
        """Prune should run in a worker thread so health endpoints stay responsive."""
        request = DockerPruneRequest(valid_lab_ids=["lab-1"])
        expected = DockerPruneResponse(success=True, images_removed=1)

        with patch("agent.main.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.return_value = expected
            result = _run(prune_docker(request))

        assert result == expected
        mock_thread.assert_awaited_once()
        assert mock_thread.await_args.args[0] is _sync_prune_docker
        assert mock_thread.await_args.args[1] == request
