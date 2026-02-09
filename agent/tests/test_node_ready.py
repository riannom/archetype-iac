"""CI-friendly tests for node readiness endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from agent.main import app


def test_node_ready_docker_runs_post_boot():
    client = TestClient(app)

    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    mock_container = MagicMock()
    mock_container.labels = {"archetype.node_kind": "ceos"}

    mock_docker = MagicMock()
    mock_docker.containers.get.return_value = mock_container

    probe = MagicMock()
    probe.check = AsyncMock(return_value=MagicMock(is_ready=True, message="ok", progress_percent=100))

    with patch("agent.main.get_provider", return_value=provider):
        with patch("docker.from_env", return_value=mock_docker):
            with patch("agent.readiness.get_probe_for_vendor", return_value=probe):
                with patch("agent.readiness.run_post_boot_commands", new_callable=AsyncMock) as mock_post:
                    with patch("agent.readiness.get_readiness_timeout", return_value=120):
                        response = client.get("/labs/lab1/nodes/r1/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["is_ready"] is True
    assert body["provider"] == "docker"
    mock_post.assert_awaited_once_with("archetype-lab1-r1", "ceos")

    client.close()


def test_node_ready_libvirt_requested_uses_libvirt():
    client = TestClient(app)

    with patch("agent.main._check_libvirt_readiness", new_callable=AsyncMock) as mock_libvirt:
        mock_libvirt.return_value = {
            "is_ready": True,
            "message": "ok",
            "progress_percent": 100,
            "timeout": 120,
            "provider": "libvirt",
        }
        response = client.get("/labs/lab1/nodes/r1/ready?provider_type=libvirt&kind=vsrx")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "libvirt"
    mock_libvirt.assert_awaited_once()

    client.close()


def test_node_ready_docker_missing_falls_back_to_libvirt():
    client = TestClient(app)

    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    mock_docker = MagicMock()
    mock_docker.containers.get.side_effect = Exception("missing")

    with patch("agent.main.get_provider", return_value=provider):
        with patch("docker.from_env", return_value=mock_docker):
            with patch("agent.main._check_libvirt_readiness", new_callable=AsyncMock) as mock_libvirt:
                mock_libvirt.return_value = {
                    "is_ready": False,
                    "message": "libvirt fallback",
                    "progress_percent": None,
                    "timeout": 120,
                    "provider": "libvirt",
                }
                response = client.get("/labs/lab1/nodes/r1/ready?kind=vsrx")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "libvirt"
    mock_libvirt.assert_awaited_once()

    client.close()


def test_node_ready_docker_not_ready_skips_post_boot():
    client = TestClient(app)

    provider = MagicMock()
    provider.get_container_name.return_value = "archetype-lab1-r1"

    mock_container = MagicMock()
    mock_container.labels = {"archetype.node_kind": "ceos"}

    mock_docker = MagicMock()
    mock_docker.containers.get.return_value = mock_container

    probe = MagicMock()
    probe.check = AsyncMock(return_value=MagicMock(is_ready=False, message="booting", progress_percent=10))

    with patch("agent.main.get_provider", return_value=provider):
        with patch("docker.from_env", return_value=mock_docker):
            with patch("agent.readiness.get_probe_for_vendor", return_value=probe):
                with patch("agent.readiness.run_post_boot_commands", new_callable=AsyncMock) as mock_post:
                    with patch("agent.readiness.get_readiness_timeout", return_value=120):
                        response = client.get("/labs/lab1/nodes/r1/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["is_ready"] is False
    assert body["provider"] == "docker"
    mock_post.assert_not_awaited()

    client.close()
