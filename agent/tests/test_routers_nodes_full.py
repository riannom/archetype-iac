"""Tests for agent per-node lifecycle, readiness, exec, and repair endpoints.

Source: agent/routers/nodes.py
Covers: create, start, stop, destroy, exec, linux-interfaces, ready,
        run-post-boot, fix-interfaces, repair-endpoints, cli-verify, runtime.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent.main import app
from agent.config import settings
from agent.providers.base import NodeActionResult, NodeStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(settings, "controller_secret", "")


# ---------------------------------------------------------------------------
# TestCreateNode
# ---------------------------------------------------------------------------


class TestCreateNode:
    """Tests for POST /labs/{lab_id}/nodes/{node_name}/create."""

    def test_create_docker_success(self, client, tmp_path):
        """Successful Docker node creation."""
        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"
        provider.create_node = AsyncMock(
            return_value=NodeActionResult(
                success=True, node_name="r1", new_status=NodeStatus.STOPPED,
            )
        )

        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                resp = client.post(
                    "/labs/lab1/nodes/r1/create?provider=docker",
                    json={"node_name": "r1", "kind": "ceos"},
                )

        body = resp.json()
        assert body["success"] is True
        assert body["container_name"] == "archetype-lab1-r1"
        assert body["status"] == "stopped"
        assert "duration_ms" in body

    def test_create_with_all_fields(self, client, tmp_path):
        """Create node passes optional fields to provider."""
        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"
        provider.create_node = AsyncMock(
            return_value=NodeActionResult(
                success=True, node_name="r1", new_status=NodeStatus.STOPPED,
            )
        )

        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                resp = client.post(
                    "/labs/lab1/nodes/r1/create?provider=docker",
                    json={
                        "node_name": "r1",
                        "kind": "ceos",
                        "image": "ceos:4.28",
                        "display_name": "Router 1",
                        "interface_count": 4,
                        "memory": 2048,
                        "cpu": 1,
                    },
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        # Verify provider was called with image and display_name
        call_kwargs = provider.create_node.await_args.kwargs
        assert call_kwargs["image"] == "ceos:4.28"
        assert call_kwargs["display_name"] == "Router 1"

    def test_create_failure_surfaces_error(self, client, tmp_path):
        """Provider failure surfaces error."""
        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"
        provider.create_node = AsyncMock(
            return_value=NodeActionResult(
                success=False, node_name="r1", error="image not found",
            )
        )

        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                resp = client.post(
                    "/labs/lab1/nodes/r1/create?provider=docker",
                    json={"node_name": "r1", "kind": "linux"},
                )

        body = resp.json()
        assert body["success"] is False
        assert "image not found" in body["error"]


# ---------------------------------------------------------------------------
# TestStartNode
# ---------------------------------------------------------------------------


class TestStartNode:
    """Tests for POST /labs/{lab_id}/nodes/{node_name}/start."""

    def test_start_success_with_repair_counts(self, client, tmp_path):
        """Start parses repair and fix counts from stdout."""
        provider = MagicMock()
        provider.start_node = AsyncMock(
            return_value=NodeActionResult(
                success=True, node_name="r1", new_status=NodeStatus.RUNNING,
                stdout="repaired 3 endpoints, fixed 2 interfaces",
            )
        )

        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                resp = client.post("/labs/lab1/nodes/r1/start?provider=docker")

        body = resp.json()
        assert body["success"] is True
        assert body["endpoints_repaired"] == 3
        assert body["interfaces_fixed"] == 2

    def test_start_without_repair_body(self, client, tmp_path):
        """Start without request body uses defaults."""
        provider = MagicMock()
        provider.start_node = AsyncMock(
            return_value=NodeActionResult(
                success=True, node_name="r1", new_status=NodeStatus.RUNNING,
            )
        )

        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                resp = client.post("/labs/lab1/nodes/r1/start?provider=docker")

        body = resp.json()
        assert body["success"] is True
        # Default is True for both repair_endpoints and fix_interfaces
        kwargs = provider.start_node.await_args.kwargs
        assert kwargs["repair_endpoints"] is True
        assert kwargs["fix_interfaces"] is True

    def test_start_libvirt_skips_repair_flags(self, client, tmp_path):
        """Libvirt start does not pass Docker-specific kwargs."""
        provider = MagicMock()
        provider.start_node = AsyncMock(
            return_value=NodeActionResult(
                success=True, node_name="r1", new_status=NodeStatus.RUNNING,
            )
        )

        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                resp = client.post("/labs/lab1/nodes/r1/start?provider=libvirt")

        body = resp.json()
        assert body["success"] is True
        kwargs = provider.start_node.await_args.kwargs
        assert "repair_endpoints" not in kwargs
        assert "fix_interfaces" not in kwargs


# ---------------------------------------------------------------------------
# TestStopNode
# ---------------------------------------------------------------------------


class TestStopNode:
    """Tests for POST /labs/{lab_id}/nodes/{node_name}/stop."""

    def test_stop_success(self, client, tmp_path):
        """Successful stop returns stopped status."""
        provider = MagicMock()
        provider.stop_node = AsyncMock(
            return_value=NodeActionResult(
                success=True, node_name="r1", new_status=NodeStatus.STOPPED,
            )
        )

        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                resp = client.post("/labs/lab1/nodes/r1/stop?provider=docker")

        body = resp.json()
        assert body["success"] is True
        assert body["status"] == "stopped"

    def test_stop_failure(self, client, tmp_path):
        """Stop failure surfaces error."""
        provider = MagicMock()
        provider.stop_node = AsyncMock(
            return_value=NodeActionResult(
                success=False, node_name="r1", error="container stuck",
            )
        )

        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                resp = client.post("/labs/lab1/nodes/r1/stop?provider=docker")

        body = resp.json()
        assert body["success"] is False
        assert "container stuck" in body["error"]


# ---------------------------------------------------------------------------
# TestDestroyNode
# ---------------------------------------------------------------------------


class TestDestroyNode:
    """Tests for DELETE /labs/{lab_id}/nodes/{node_name}."""

    def test_destroy_auto_docker(self, client, tmp_path):
        """Auto provider falls back to docker when no libvirt."""
        provider = MagicMock()
        provider.destroy_node = AsyncMock(
            return_value=NodeActionResult(success=True, node_name="r1")
        )

        with patch("agent.routers.nodes.get_provider", return_value=None):
            with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
                with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                    resp = client.delete("/labs/lab1/nodes/r1?provider=auto")

        body = resp.json()
        assert body["success"] is True
        assert body["container_removed"] is True

    def test_destroy_auto_libvirt(self, client, tmp_path):
        """Auto provider detects libvirt domain."""
        lv_provider = MagicMock()
        lv_provider._domain_name.return_value = "arch-lab1-r1"
        lv_provider.conn.lookupByName.return_value = MagicMock()

        provider = MagicMock()
        provider.destroy_node = AsyncMock(
            return_value=NodeActionResult(success=True, node_name="r1")
        )

        with patch("agent.routers.nodes.get_provider", return_value=lv_provider):
            with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
                with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                    resp = client.delete("/labs/lab1/nodes/r1?provider=auto")

        body = resp.json()
        assert body["success"] is True

    def test_destroy_error(self, client, tmp_path):
        """Destroy failure surfaces error."""
        provider = MagicMock()
        provider.destroy_node = AsyncMock(
            return_value=NodeActionResult(
                success=False, node_name="r1", error="permission denied",
            )
        )

        with patch("agent.routers.nodes.get_provider", return_value=None):
            with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
                with patch("agent.routers.nodes.get_workspace", return_value=tmp_path):
                    resp = client.delete("/labs/lab1/nodes/r1?provider=auto")

        body = resp.json()
        assert body["success"] is False
        assert "permission denied" in body["error"]


# ---------------------------------------------------------------------------
# TestExecOnNode
# ---------------------------------------------------------------------------


class TestExecOnNode:
    """Tests for POST /labs/{lab_id}/nodes/{node_name}/exec."""

    def test_exec_success(self, client):
        """Successful command execution returns output."""
        mock_container = MagicMock()
        mock_container.exec_run.return_value = (0, b"hello\n")

        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        docker_provider = MagicMock()
        docker_provider.get_container_name.return_value = "archetype-lab1-r1"

        with patch("agent.routers.nodes.get_provider", return_value=docker_provider):
            with patch("agent.routers.nodes.get_docker_client", return_value=mock_docker):
                resp = client.post(
                    "/labs/lab1/nodes/r1/exec",
                    json={"cmd": "echo hello"},
                )

        body = resp.json()
        assert body["exit_code"] == 0
        assert "hello" in body["output"]

    def test_exec_missing_cmd(self, client):
        """Missing cmd field returns 400."""
        docker_provider = MagicMock()
        docker_provider.get_container_name.return_value = "archetype-lab1-r1"

        with patch("agent.routers.nodes.get_provider", return_value=docker_provider):
            resp = client.post(
                "/labs/lab1/nodes/r1/exec",
                json={},
            )

        assert resp.status_code == 400

    def test_exec_docker_unavailable(self, client):
        """Returns 400 when Docker provider not available."""
        with patch("agent.routers.nodes.get_provider", return_value=None):
            resp = client.post(
                "/labs/lab1/nodes/r1/exec",
                json={"cmd": "ls"},
            )

        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# TestCheckNodeReady
# ---------------------------------------------------------------------------


class TestCheckNodeReady:
    """Tests for GET /labs/{lab_id}/nodes/{node_name}/ready."""

    def test_docker_ready(self, client):
        """Returns ready status for Docker container."""
        docker_provider = MagicMock()
        docker_provider.get_container_name.return_value = "archetype-lab1-r1"

        mock_container = MagicMock()
        mock_container.labels = {
            "archetype.node_kind": "ceos",
            "archetype.readiness_probe": None,
            "archetype.readiness_pattern": None,
            "archetype.readiness_timeout": None,
        }

        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        probe_result = SimpleNamespace(
            is_ready=True, message="Ready", progress_percent=100, details=None,
        )
        mock_probe = MagicMock()
        mock_probe.check = AsyncMock(return_value=probe_result)

        with patch("agent.routers.nodes.get_provider",
                    side_effect=lambda x: docker_provider if x == "docker" else None):
            with patch("agent.routers.nodes.get_docker_client", return_value=mock_docker):
                with patch("agent.routers.nodes.get_probe_for_vendor", return_value=mock_probe):
                    with patch("agent.routers.nodes.run_post_boot_commands",
                               new_callable=AsyncMock, return_value=True):
                        resp = client.get("/labs/lab1/nodes/r1/ready")

        body = resp.json()
        assert body["is_ready"] is True
        assert body["provider"] == "docker"

    def test_libvirt_explicit(self, client):
        """Explicit libvirt provider check."""
        libvirt_provider = MagicMock()
        libvirt_provider.check_readiness = AsyncMock(
            return_value=SimpleNamespace(
                is_ready=False, message="Booting", progress_percent=40, details=None,
            )
        )
        libvirt_provider.get_readiness_timeout.return_value = 600

        with patch("agent.routers.nodes.get_provider",
                    side_effect=lambda x: libvirt_provider if x == "libvirt" else None):
            resp = client.get("/labs/lab1/nodes/r1/ready?provider_type=libvirt&kind=cisco_n9kv")

        body = resp.json()
        assert body["is_ready"] is False
        assert body["provider"] == "libvirt"
        assert body["timeout"] == 600

    def test_no_kind_for_libvirt(self, client):
        """Returns error when kind not provided for libvirt."""
        libvirt_provider = MagicMock()

        with patch("agent.routers.nodes.get_provider",
                    side_effect=lambda x: libvirt_provider if x == "libvirt" else None):
            resp = client.get("/labs/lab1/nodes/r1/ready?provider_type=libvirt")

        body = resp.json()
        assert body["is_ready"] is False
        assert "kind" in body["message"].lower()


# ---------------------------------------------------------------------------
# TestRunPostBoot
# ---------------------------------------------------------------------------


class TestRunPostBoot:
    """Tests for POST /labs/{lab_id}/nodes/{node_name}/run-post-boot."""

    def test_success(self, client):
        """Successfully re-runs post-boot commands."""
        docker_provider = MagicMock()
        docker_provider.get_container_name.return_value = "archetype-lab1-r1"

        mock_container = MagicMock()
        mock_container.labels = {"archetype.node_kind": "ceos"}
        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        with patch("agent.routers.nodes.get_provider",
                    side_effect=lambda x: docker_provider if x == "docker" else None):
            with patch("agent.routers.nodes.get_docker_client", return_value=mock_docker):
                with patch("agent.readiness.clear_post_boot_state"):
                    with patch("agent.routers.nodes.run_post_boot_commands",
                               new_callable=AsyncMock, return_value=True):
                        resp = client.post("/labs/lab1/nodes/r1/run-post-boot")

        body = resp.json()
        assert body["success"] is True
        assert body["kind"] == "ceos"

    def test_no_docker_provider(self, client):
        """Returns 400 when Docker provider unavailable."""
        with patch("agent.routers.nodes.get_provider", return_value=None):
            resp = client.post("/labs/lab1/nodes/r1/run-post-boot")

        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# TestFixInterfaces
# ---------------------------------------------------------------------------


class TestFixInterfaces:
    """Tests for POST /labs/{lab_id}/nodes/{node_name}/fix-interfaces."""

    def test_success(self, client):
        """Successfully fixes interface names."""
        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"
        provider._fix_interface_names = AsyncMock(
            return_value={"fixed": 2, "already_correct": 1, "errors": []}
        )

        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            resp = client.post("/labs/lab1/nodes/r1/fix-interfaces")

        body = resp.json()
        assert body["success"] is True
        assert body["fixed"] == 2
        assert body["already_correct"] == 1

    def test_exception_surfaces_error(self, client):
        """Exception surfaces in response."""
        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"
        provider._fix_interface_names = AsyncMock(side_effect=RuntimeError("nsenter failed"))

        with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
            resp = client.post("/labs/lab1/nodes/r1/fix-interfaces")

        body = resp.json()
        assert body["success"] is False
        assert "nsenter failed" in body["errors"][0]


# ---------------------------------------------------------------------------
# TestRepairEndpoints
# ---------------------------------------------------------------------------


class TestRepairEndpoints:
    """Tests for POST /labs/{lab_id}/repair-endpoints."""

    def test_repair_specific_nodes(self, client):
        """Repairs specified nodes."""
        plugin = MagicMock()
        plugin.repair_endpoints = AsyncMock(return_value=[
            {"interface": "eth1", "status": "repaired", "host_veth": "vh-abc", "vlan_tag": 100},
        ])

        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"

        with patch("agent.routers.nodes._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
                resp = client.post(
                    "/labs/lab1/repair-endpoints",
                    json={"nodes": ["r1"]},
                )

        body = resp.json()
        assert body["success"] is True
        assert body["total_endpoints_repaired"] == 1
        assert body["nodes_repaired"] == 1

    def test_repair_exception_per_node(self, client):
        """Exception for one node does not block others."""
        plugin = MagicMock()
        plugin.repair_endpoints = AsyncMock(side_effect=RuntimeError("ns gone"))

        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"

        with patch("agent.routers.nodes._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.routers.nodes.get_provider_for_request", return_value=provider):
                resp = client.post(
                    "/labs/lab1/repair-endpoints",
                    json={"nodes": ["r1"]},
                )

        body = resp.json()
        assert body["success"] is True
        assert body["results"]["r1"][0]["status"] == "error"


# ---------------------------------------------------------------------------
# TestGetNodeRuntime
# ---------------------------------------------------------------------------


class TestGetNodeRuntime:
    """Tests for GET /labs/{lab_id}/nodes/{node_name}/runtime."""

    def test_docker_runtime(self, client):
        """Returns Docker runtime profile."""
        docker_provider = MagicMock()
        docker_provider.get_container_name.return_value = "archetype-lab1-r1"

        mock_container = MagicMock()
        mock_container.name = "archetype-lab1-r1"
        mock_container.status = "running"
        mock_container.image = MagicMock()
        mock_container.image.tags = ["ceos:4.28"]
        mock_container.attrs = {
            "HostConfig": {"Memory": 2147483648, "CpuQuota": 100000, "CpuPeriod": 100000},
        }

        mock_docker = MagicMock()
        mock_docker.containers.get.return_value = mock_container

        with patch("agent.routers.nodes.get_provider",
                    side_effect=lambda x: docker_provider if x == "docker" else None):
            with patch("agent.routers.nodes.get_docker_client", return_value=mock_docker):
                resp = client.get("/labs/lab1/nodes/r1/runtime?provider_type=docker")

        body = resp.json()
        assert body["provider"] == "docker"
        assert body["state"] == "running"
        assert body["runtime"]["memory"] == 2048
        assert body["runtime"]["cpu"] == 1.0
