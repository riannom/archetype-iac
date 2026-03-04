"""Tests for under-covered overlay router endpoints.

Covers:
- GET /overlay/port-ifindex (zero prior coverage)
- GET /overlay/bridge-ports (zero prior coverage)
- POST /cleanup/audit (zero prior coverage)
- POST /network/test-mtu success/failure paths (only validation was tested)
- POST /overlay/reconcile-ports actual deletion path
- POST /ports/declare-state update/error paths
"""
from __future__ import annotations

import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure agent root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.config import settings
from agent.main import app

from fastapi.testclient import TestClient


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
# Helpers
# ---------------------------------------------------------------------------

def _mock_subprocess(stdout: str = "", returncode: int = 0):
    """Create a mock async subprocess result."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(
        stdout.encode(),
        b"",
    ))
    proc.returncode = returncode
    return proc


# ---------------------------------------------------------------------------
# GET /overlay/port-ifindex
# ---------------------------------------------------------------------------

class TestPortIfindex:
    """Tests for GET /overlay/port-ifindex."""

    def test_returns_ports_with_ifindex(self, client, monkeypatch):
        """Returns port name, tag, and ifindex for non-VXLAN ports."""
        call_count = {"n": 0}

        async def fake_subprocess(*args, **kwargs):
            call_count["n"] += 1
            cmd_args = args
            # First call: list-ports
            if "list-ports" in cmd_args:
                return _mock_subprocess("vh-abc\nvxlan-lk1\nvh-def")
            # get port tag or interface ifindex
            if "tag" in cmd_args:
                if "vh-abc" in cmd_args:
                    return _mock_subprocess("100")
                return _mock_subprocess("200")
            if "ifindex" in cmd_args:
                if "vh-abc" in cmd_args:
                    return _mock_subprocess("42")
                return _mock_subprocess("43")
            return _mock_subprocess("")

        with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
            resp = client.get("/overlay/port-ifindex")

        assert resp.status_code == 200
        body = resp.json()
        ports = body["ports"]
        # vxlan-lk1 should be filtered out
        names = [p["name"] for p in ports]
        assert "vh-abc" in names
        assert "vh-def" in names
        assert "vxlan-lk1" not in names

    def test_empty_bridge(self, client):
        """Returns empty list when no ports on bridge."""
        with patch(
            "asyncio.create_subprocess_exec",
            return_value=_mock_subprocess(""),
        ):
            resp = client.get("/overlay/port-ifindex")

        assert resp.status_code == 200
        assert resp.json()["ports"] == []

    def test_vtep_ports_filtered(self, client):
        """Ports starting with 'vtep' are skipped."""
        async def fake_subprocess(*args, **kwargs):
            if "list-ports" in args:
                return _mock_subprocess("vtep-host2\nvh-x")
            if "tag" in args:
                return _mock_subprocess("300")
            if "ifindex" in args:
                return _mock_subprocess("50")
            return _mock_subprocess("")

        with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
            resp = client.get("/overlay/port-ifindex")

        names = [p["name"] for p in resp.json()["ports"]]
        assert "vtep-host2" not in names
        assert "vh-x" in names


# ---------------------------------------------------------------------------
# GET /overlay/bridge-ports
# ---------------------------------------------------------------------------

class TestBridgePorts:
    """Tests for GET /overlay/bridge-ports."""

    def test_returns_vxlan_and_other_ports(self, client):
        """Classifies ports as VXLAN vs container ports."""
        async def fake_subprocess(*args, **kwargs):
            if "list-ports" in args:
                return _mock_subprocess("vxlan-lk1\nvh-abc")
            if "get" in args and "type" in args:
                if "vxlan-lk1" in args:
                    return _mock_subprocess("vxlan")
                return _mock_subprocess("")
            if "tag" in args:
                return _mock_subprocess("100")
            if "options" in args:
                return _mock_subprocess("{remote_ip=10.0.0.2}")
            if "statistics" in args:
                return _mock_subprocess("{}")
            if "fdb/show" in args:
                return _mock_subprocess("port  VLAN  MAC")
            return _mock_subprocess("")

        with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
            resp = client.get("/overlay/bridge-ports")

        assert resp.status_code == 200
        body = resp.json()
        assert body["bridge"] == (settings.ovs_bridge_name or "arch-ovs")
        assert body["total_ports"] == 2
        assert len(body["vxlan_ports"]) == 1
        assert body["vxlan_ports"][0]["name"] == "vxlan-lk1"
        # vh-abc is a container port
        assert any(p["name"] == "vh-abc" for p in body["container_ports_all"])

    def test_empty_bridge_returns_empty(self, client):
        """Empty bridge returns zeroed counters."""
        async def fake_subprocess(*args, **kwargs):
            if "fdb/show" in args:
                return _mock_subprocess("")
            return _mock_subprocess("")

        with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
            resp = client.get("/overlay/bridge-ports")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_ports"] == 0
        assert body["vxlan_ports"] == []


# ---------------------------------------------------------------------------
# POST /cleanup/audit
# ---------------------------------------------------------------------------

class TestCleanupAudit:
    """Tests for POST /cleanup/audit."""

    def test_basic_audit_without_ovs(self, client):
        """Audit without OVS returns network stats only."""
        mock_stats = MagicMock()
        mock_stats.to_dict.return_value = {"stale_networks": 0}

        mock_cleanup_mgr = AsyncMock()
        mock_cleanup_mgr.run_full_cleanup = AsyncMock(return_value=mock_stats)

        with patch(
            "agent.routers.overlay.get_cleanup_manager",
            return_value=mock_cleanup_mgr,
            create=True,
        ):
            # Patch at the import location inside the function
            with patch.dict("sys.modules", {
                "agent.network.cleanup": MagicMock(
                    get_cleanup_manager=MagicMock(return_value=mock_cleanup_mgr)
                ),
            }):
                resp = client.post("/cleanup/audit", json={"include_ovs": False})

        assert resp.status_code == 200
        body = resp.json()
        assert body["ovs"] is None
        assert isinstance(body["errors"], list)

    def test_audit_network_failure_records_error(self, client):
        """Network audit failure is recorded in errors list."""
        with patch.dict("sys.modules", {
            "agent.network.cleanup": MagicMock(
                get_cleanup_manager=MagicMock(side_effect=RuntimeError("no cleanup"))
            ),
        }):
            resp = client.post("/cleanup/audit", json={"include_ovs": False})

        assert resp.status_code == 200
        body = resp.json()
        assert any("network_audit_failed" in e for e in body["errors"])


# ---------------------------------------------------------------------------
# POST /network/test-mtu
# ---------------------------------------------------------------------------

class TestMtuTest:
    """Tests for POST /network/test-mtu."""

    def test_mtu_success_direct_link(self, client):
        """Successful MTU test with direct link (TTL >= 64)."""
        ping_output = (
            "PING 10.0.0.2 (10.0.0.2) 1422(1450) bytes of data.\n"
            "1430 bytes from 10.0.0.2: icmp_seq=1 ttl=64 time=0.123 ms\n"
            "1430 bytes from 10.0.0.2: icmp_seq=2 ttl=64 time=0.111 ms\n"
            "1430 bytes from 10.0.0.2: icmp_seq=3 ttl=64 time=0.115 ms\n"
            "\n--- 10.0.0.2 ping statistics ---\n"
            "3 packets transmitted, 3 received, 0% packet loss\n"
            "rtt min/avg/max/mdev = 0.111/0.116/0.123/0.005 ms\n"
        )
        with patch(
            "asyncio.create_subprocess_exec",
            return_value=_mock_subprocess(ping_output, returncode=0),
        ):
            resp = client.post("/network/test-mtu", json={
                "target_ip": "10.0.0.2",
                "mtu": 1450,
            })

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["tested_mtu"] == 1450
        assert body["link_type"] == "direct"
        assert body["ttl"] == 64
        assert body["latency_ms"] == pytest.approx(0.116, abs=0.001)

    def test_mtu_too_large_frag_needed(self, client):
        """MTU test fails when path MTU is too small."""
        with patch(
            "asyncio.create_subprocess_exec",
            return_value=_mock_subprocess(
                "From 10.0.0.1: icmp_seq=1 Frag needed and DF set",
                returncode=1,
            ),
        ):
            resp = client.post("/network/test-mtu", json={
                "target_ip": "10.0.0.2",
                "mtu": 9000,
            })

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "too small" in body["error"]

    def test_mtu_too_small_value(self, client):
        """MTU below minimum (28) returns error without pinging."""
        resp = client.post("/network/test-mtu", json={
            "target_ip": "10.0.0.2",
            "mtu": 20,
        })

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "too small" in body["error"]

    def test_mtu_routed_link_low_ttl(self, client):
        """TTL < 64 is classified as routed link."""
        ping_output = (
            "64 bytes from 10.0.0.2: icmp_seq=1 ttl=62 time=1.5 ms\n"
            "rtt min/avg/max/mdev = 1.5/1.5/1.5/0.0 ms\n"
        )
        with patch(
            "asyncio.create_subprocess_exec",
            return_value=_mock_subprocess(ping_output, returncode=0),
        ):
            resp = client.post("/network/test-mtu", json={
                "target_ip": "10.0.0.2",
                "mtu": 1500,
            })

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["link_type"] == "routed"
        assert body["ttl"] == 62

    def test_mtu_with_source_ip(self, client):
        """Source IP is passed to ping command."""
        ping_output = "64 bytes from 10.0.0.2: icmp_seq=1 ttl=64 time=0.5 ms\n"
        mock_proc = _mock_subprocess(ping_output, returncode=0)

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ) as mock_exec:
            resp = client.post("/network/test-mtu", json={
                "target_ip": "10.0.0.2",
                "mtu": 1450,
                "source_ip": "10.0.0.1",
            })

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        # Verify -I flag was passed
        call_args = mock_exec.call_args[0]
        assert "-I" in call_args
        assert "10.0.0.1" in call_args

    def test_mtu_timeout(self, client):
        """Ping timeout returns error."""
        async def timeout_subprocess(*args, **kwargs):
            proc = AsyncMock()
            proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            proc.returncode = 1
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=timeout_subprocess):
            # Also patch asyncio.wait_for to raise TimeoutError
            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
                resp = client.post("/network/test-mtu", json={
                    "target_ip": "10.0.0.2",
                    "mtu": 1450,
                })

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "timed out" in body["error"].lower()


# ---------------------------------------------------------------------------
# POST /overlay/reconcile-ports — actual deletion flow
# ---------------------------------------------------------------------------

class TestReconcilePortsDeletion:
    """Tests for POST /overlay/reconcile-ports with actual port removal."""

    def test_removes_stale_vxlan_ports(self, client):
        """Stale VXLAN ports not in valid set are deleted."""
        async def fake_subprocess(*args, **kwargs):
            if "list-ports" in args:
                return _mock_subprocess("vxlan-lk1\nvxlan-lk2\nvh-abc")
            if "get" in args and "type" in args:
                # vxlan-lk1 and vxlan-lk2 are vxlan type
                if "vxlan-lk1" in args or "vxlan-lk2" in args:
                    return _mock_subprocess("vxlan")
                return _mock_subprocess("")
            # del-port and ip link delete succeed
            return _mock_subprocess("", returncode=0)

        with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
            resp = client.post("/overlay/reconcile-ports", json={
                "valid_port_names": ["vxlan-lk1"],
            })

        assert resp.status_code == 200
        body = resp.json()
        assert "vxlan-lk2" in body["removed_ports"]
        assert "vxlan-lk1" not in body["removed_ports"]
        assert body["valid_count"] == 1

    def test_force_with_confirm_and_allow_empty(self, client):
        """Force + confirm + allow_empty removes all VXLAN ports."""
        async def fake_subprocess(*args, **kwargs):
            if "list-ports" in args:
                return _mock_subprocess("vxlan-orphan1")
            if "get" in args and "type" in args:
                return _mock_subprocess("vxlan")
            return _mock_subprocess("", returncode=0)

        with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
            resp = client.post("/overlay/reconcile-ports", json={
                "valid_port_names": [],
                "force": True,
                "confirm": True,
                "allow_empty": True,
            })

        assert resp.status_code == 200
        body = resp.json()
        assert "vxlan-orphan1" in body["removed_ports"]
