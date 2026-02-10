"""Tests for POST /overlay/reconcile-ports endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from agent.main import app
from agent.config import settings


@pytest.fixture
def test_client():
    original_enable_docker = settings.enable_docker
    original_enable_ovs_plugin = settings.enable_ovs_plugin
    original_enable_vxlan = settings.enable_vxlan
    settings.enable_docker = False
    settings.enable_ovs_plugin = False
    settings.enable_vxlan = True
    client = TestClient(app)
    yield client
    client.close()
    settings.enable_docker = original_enable_docker
    settings.enable_ovs_plugin = original_enable_ovs_plugin
    settings.enable_vxlan = original_enable_vxlan


def _mock_subprocess(outputs: dict[str, tuple[int, str]]):
    """Create a mock for asyncio.create_subprocess_shell.

    Args:
        outputs: Mapping from command substring to (returncode, stdout).
    """

    async def fake_create_subprocess_shell(cmd, **kwargs):
        for key, (code, out) in outputs.items():
            if key in cmd:
                proc = AsyncMock()
                proc.returncode = code
                proc.communicate = AsyncMock(
                    return_value=(out.encode(), b"")
                )
                return proc
        # Default: empty success
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    return fake_create_subprocess_shell


def test_reconcile_removes_stale_ports(test_client):
    """Stale VXLAN ports not in valid_port_names are deleted."""
    outputs = {
        "list-ports": (0, "vxlan-aabb1122\nvxlan-ccdd3344\neth1-ovs"),
        "get interface vxlan-aabb1122 type": (0, "vxlan"),
        "get interface vxlan-ccdd3344 type": (0, "vxlan"),
        "get interface eth1-ovs type": (0, "internal"),
        "del-port": (0, ""),
    }

    with patch("asyncio.create_subprocess_shell", side_effect=_mock_subprocess(outputs)):
        resp = test_client.post(
            "/overlay/reconcile-ports",
            json={"valid_port_names": ["vxlan-aabb1122"]},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "vxlan-ccdd3344" in data["removed_ports"]
    assert "vxlan-aabb1122" not in data["removed_ports"]
    assert data["valid_count"] == 1


def test_reconcile_keeps_all_valid_ports(test_client):
    """When all VXLAN ports are valid, nothing is removed."""
    outputs = {
        "list-ports": (0, "vxlan-aabb1122\nvxlan-ccdd3344"),
        "get interface vxlan-aabb1122 type": (0, "vxlan"),
        "get interface vxlan-ccdd3344 type": (0, "vxlan"),
    }

    with patch("asyncio.create_subprocess_shell", side_effect=_mock_subprocess(outputs)):
        resp = test_client.post(
            "/overlay/reconcile-ports",
            json={"valid_port_names": ["vxlan-aabb1122", "vxlan-ccdd3344"]},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["removed_ports"] == []
    assert data["valid_count"] == 2


def test_reconcile_empty_valid_list_removes_all_vxlan(test_client):
    """When valid list is empty, all VXLAN ports are removed."""
    outputs = {
        "list-ports": (0, "vxlan-aabb1122\nvxlan-ccdd3344"),
        "get interface vxlan-aabb1122 type": (0, "vxlan"),
        "get interface vxlan-ccdd3344 type": (0, "vxlan"),
        "del-port": (0, ""),
    }

    with patch("asyncio.create_subprocess_shell", side_effect=_mock_subprocess(outputs)):
        resp = test_client.post(
            "/overlay/reconcile-ports",
            json={"valid_port_names": [], "force": True, "confirm": True, "allow_empty": True},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["removed_ports"]) == 2
    assert set(data["removed_ports"]) == {"vxlan-aabb1122", "vxlan-ccdd3344"}


def test_reconcile_ignores_non_vxlan_ports(test_client):
    """Non-VXLAN ports (containers, internal) are never deleted."""
    outputs = {
        "list-ports": (0, "eth1-ovs\narch-br0\nvxlan-stale1"),
        "get interface eth1-ovs type": (0, "internal"),
        "get interface arch-br0 type": (0, "internal"),
        "get interface vxlan-stale1 type": (0, "vxlan"),
        "del-port": (0, ""),
    }

    with patch("asyncio.create_subprocess_shell", side_effect=_mock_subprocess(outputs)):
        resp = test_client.post(
            "/overlay/reconcile-ports",
            json={"valid_port_names": [], "force": True, "confirm": True, "allow_empty": True},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["removed_ports"] == ["vxlan-stale1"]


def test_reconcile_no_ports_on_bridge(test_client):
    """No ports on bridge → nothing to reconcile."""
    outputs = {
        "list-ports": (0, ""),
    }

    with patch("asyncio.create_subprocess_shell", side_effect=_mock_subprocess(outputs)):
        resp = test_client.post(
            "/overlay/reconcile-ports",
            json={"valid_port_names": ["vxlan-aabb1122"]},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["removed_ports"] == []


def test_reconcile_handles_del_port_failure(test_client):
    """If del-port fails, the port is not listed as removed."""
    outputs = {
        "list-ports": (0, "vxlan-stale1"),
        "get interface vxlan-stale1 type": (0, "vxlan"),
        "del-port": (1, "Error: no port named vxlan-stale1"),
    }

    with patch("asyncio.create_subprocess_shell", side_effect=_mock_subprocess(outputs)):
        resp = test_client.post(
            "/overlay/reconcile-ports",
            json={"valid_port_names": [], "force": True, "confirm": True, "allow_empty": True},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["removed_ports"] == []
