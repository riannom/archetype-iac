"""Tests for agent per-node lifecycle endpoints in agent/routers/nodes.py.

Covers create, start, stop, destroy, exec, and linux-interfaces endpoints
using TestClient with mocked providers.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from agent.main import app
from agent.providers.base import NodeActionResult, NodeStatus


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# TestCreateNode
# ---------------------------------------------------------------------------


class TestCreateNode:
    """Tests for POST /labs/{lab_id}/nodes/{node_name}/create."""

    def test_create_node_success(self, tmp_path) -> None:
        """Successful node creation returns success and container name."""
        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"
        provider.create_node = AsyncMock(
            return_value=NodeActionResult(
                success=True,
                node_name="r1",
                new_status=NodeStatus.STOPPED,
            )
        )

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                resp = client.post(
                    "/labs/lab1/nodes/r1/create?provider=docker",
                    json={"node_name": "r1", "kind": "ceos"},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["container_name"] == "archetype-lab1-r1"
        assert body["status"] == "stopped"
        client.close()

    def test_create_node_provider_error(self, tmp_path) -> None:
        """Provider returning success=False surfaces the error."""
        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"
        provider.create_node = AsyncMock(
            return_value=NodeActionResult(
                success=False,
                node_name="r1",
                error="image not found",
            )
        )

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                resp = client.post(
                    "/labs/lab1/nodes/r1/create?provider=docker",
                    json={"node_name": "r1", "kind": "linux"},
                )

        body = resp.json()
        assert body["success"] is False
        assert "image not found" in body["error"]
        client.close()

    def test_create_node_provider_unavailable_503(self) -> None:
        """Requesting an unavailable provider returns 503."""
        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.routers.nodes.get_provider_for_request") as mock_gp:
            from fastapi import HTTPException
            mock_gp.side_effect = HTTPException(
                status_code=503,
                detail="Provider 'libvirt' not available. Available: ['docker']",
            )
            resp = client.post(
                "/labs/lab1/nodes/r1/create?provider=libvirt",
                json={"node_name": "r1", "kind": "cisco_n9kv"},
            )

        assert resp.status_code == 503
        client.close()

    def test_runtime_conflict_probe_returns_classification(self) -> None:
        """Runtime-conflict probe returns the provider classification payload."""
        provider = MagicMock()
        provider.probe_runtime_conflict = AsyncMock(
            return_value=MagicMock(
                available=False,
                classification="foreign",
                runtime_name="archetype-lab1-r1",
                status="exited",
                runtime_id="abc123def456",
                error="Container archetype-lab1-r1 is not managed by Archetype",
            )
        )

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            resp = client.post(
                "/labs/lab1/nodes/r1/runtime-conflict?provider=docker",
                json={"node_name": "r1"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["available"] is False
        assert body["classification"] == "foreign"
        assert body["runtime_name"] == "archetype-lab1-r1"
        client.close()


# ---------------------------------------------------------------------------
# TestStartNode
# ---------------------------------------------------------------------------


class TestStartNode:
    """Tests for POST /labs/{lab_id}/nodes/{node_name}/start."""

    def test_start_node_success(self, tmp_path) -> None:
        """Successful start returns running status."""
        provider = MagicMock()
        provider.start_node = AsyncMock(
            return_value=NodeActionResult(
                success=True,
                node_name="r1",
                new_status=NodeStatus.RUNNING,
                stdout="repaired 2 endpoints, fixed 1 interfaces",
            )
        )

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                resp = client.post("/labs/lab1/nodes/r1/start?provider=docker")

        body = resp.json()
        assert body["success"] is True
        assert body["status"] == "running"
        assert body["endpoints_repaired"] == 2
        assert body["interfaces_fixed"] == 1
        client.close()

    def test_start_node_repair_flags_forwarded(self, tmp_path) -> None:
        """repair_endpoints and fix_interfaces flags are passed to provider."""
        provider = MagicMock()
        provider.start_node = AsyncMock(
            return_value=NodeActionResult(
                success=True, node_name="r1", new_status=NodeStatus.RUNNING,
            )
        )

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                resp = client.post(
                    "/labs/lab1/nodes/r1/start?provider=docker",
                    json={"repair_endpoints": False, "fix_interfaces": False},
                )

        assert resp.status_code == 200
        kwargs = provider.start_node.await_args.kwargs
        assert kwargs["repair_endpoints"] is False
        assert kwargs["fix_interfaces"] is False
        client.close()


# ---------------------------------------------------------------------------
# TestStopNode
# ---------------------------------------------------------------------------


class TestStopNode:
    """Tests for POST /labs/{lab_id}/nodes/{node_name}/stop."""

    def test_stop_node_returns_new_status(self, tmp_path) -> None:
        """Stop returns stopped status and duration."""
        provider = MagicMock()
        provider.stop_node = AsyncMock(
            return_value=NodeActionResult(
                success=True, node_name="r1", new_status=NodeStatus.STOPPED,
            )
        )

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                resp = client.post("/labs/lab1/nodes/r1/stop?provider=docker")

        body = resp.json()
        assert body["success"] is True
        assert body["status"] == "stopped"
        assert "duration_ms" in body
        client.close()

    def test_stop_node_records_duration_ms(self, tmp_path) -> None:
        """duration_ms is present in the stop response."""
        provider = MagicMock()
        provider.stop_node = AsyncMock(
            return_value=NodeActionResult(
                success=True, node_name="r1", new_status=NodeStatus.STOPPED,
            )
        )

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                resp = client.post("/labs/lab1/nodes/r1/stop?provider=docker")

        body = resp.json()
        assert body["duration_ms"] is not None
        assert body["duration_ms"] >= 0
        client.close()


# ---------------------------------------------------------------------------
# TestDestroyNode
# ---------------------------------------------------------------------------


class TestDestroyNode:
    """Tests for DELETE /labs/{lab_id}/nodes/{node_name}."""

    def test_destroy_node_success(self, tmp_path) -> None:
        """Successful destruction returns container_removed=True."""
        provider = MagicMock()
        provider.destroy_node = AsyncMock(
            return_value=NodeActionResult(
                success=True, node_name="r1",
            )
        )

        client = TestClient(app, raise_server_exceptions=False)
        # Patch auto-detection to skip libvirt lookup
        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                with patch("agent.routers.nodes.get_provider", return_value=None):
                    resp = client.delete("/labs/lab1/nodes/r1?provider=auto")

        body = resp.json()
        assert body["success"] is True
        assert body["container_removed"] is True
        client.close()

    def test_destroy_node_auto_provider_fallback(self, tmp_path) -> None:
        """provider=auto falls back to docker when libvirt is unavailable."""
        docker_provider = MagicMock()
        docker_provider.destroy_node = AsyncMock(
            return_value=NodeActionResult(success=True, node_name="r1")
        )

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.routers.nodes.get_provider", return_value=None):
            with patch("agent.routers.nodes.get_provider_for_request", return_value=docker_provider):
                with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                    resp = client.delete("/labs/lab1/nodes/r1?provider=auto")

        body = resp.json()
        assert body["success"] is True
        docker_provider.destroy_node.assert_awaited_once()
        client.close()

    def test_destroy_node_failure_returns_error(self, tmp_path) -> None:
        """Provider error surfaces in the response."""
        provider = MagicMock()
        provider.destroy_node = AsyncMock(
            return_value=NodeActionResult(
                success=False, node_name="r1", error="container stuck",
            )
        )

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                with patch("agent.routers.nodes.get_provider", return_value=None):
                    resp = client.delete("/labs/lab1/nodes/r1?provider=auto")

        body = resp.json()
        assert body["success"] is False
        assert "container stuck" in body["error"]
        client.close()


# ---------------------------------------------------------------------------
# TestExec
# ---------------------------------------------------------------------------


class TestExec:
    """Tests for POST /labs/{lab_id}/nodes/{node_name}/exec."""

    def test_exec_success_with_stdout(self) -> None:
        """Successful exec returns exit code and output."""
        mock_container = MagicMock()
        mock_container.exec_run.return_value = (0, b"hello world\n")

        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        docker_provider = MagicMock()
        docker_provider.get_container_name.return_value = "archetype-lab1-r1"

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.routers.nodes.get_provider", return_value=docker_provider):
            with patch("agent.routers.nodes.get_docker_client", return_value=mock_docker):
                resp = client.post(
                    "/labs/lab1/nodes/r1/exec",
                    json={"cmd": "echo hello world"},
                )

        body = resp.json()
        assert body["exit_code"] == 0
        assert "hello world" in body["output"]
        client.close()


# ---------------------------------------------------------------------------
# TestLinuxInterfaces
# ---------------------------------------------------------------------------


class TestLinuxInterfaces:
    """Tests for GET /labs/{lab_id}/nodes/{node_name}/linux-interfaces."""

    def test_container_not_running(self) -> None:
        """When PID is unavailable, returns an error message."""
        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"
        provider.docker = MagicMock()
        container = MagicMock()
        container.attrs = {"State": {"Pid": 0}}
        provider.docker.containers.get.return_value = container

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            resp = client.get("/labs/lab1/nodes/r1/linux-interfaces")

        body = resp.json()
        assert body["error"] is not None
        assert "not running" in body["error"].lower() or body["interfaces"] == []
        client.close()

    def test_parses_ip_link_output(self) -> None:
        """Correctly parses nsenter ip link show output into interface names."""
        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"
        provider.docker = MagicMock()
        container = MagicMock()
        container.attrs = {"State": {"Pid": 42}}
        provider.docker.containers.get.return_value = container

        ip_output = (
            "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536\n"
            "2: eth0@if10: <BROADCAST,MULTICAST,UP> mtu 1500\n"
            "3: eth1@if12: <BROADCAST,MULTICAST,UP> mtu 9000\n"
            "4: eth2@if14: <BROADCAST,MULTICAST,UP> mtu 9000\n"
        )

        async def _fake_subprocess(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(
                return_value=(ip_output.encode(), b"")
            )
            return proc

        client = TestClient(app, raise_server_exceptions=False)
        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.asyncio.create_subprocess_exec", side_effect=_fake_subprocess):
                resp = client.get("/labs/lab1/nodes/r1/linux-interfaces")

        body = resp.json()
        assert body["error"] is None
        assert "lo" in body["interfaces"]
        assert "eth0" in body["interfaces"]
        assert "eth1" in body["interfaces"]
        assert "eth2" in body["interfaces"]
        client.close()
