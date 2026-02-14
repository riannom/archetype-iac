"""Tests for agent input validation: path traversal, IP addresses, port names."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent.config import settings
from agent.main import _validate_port_name, app, get_workspace


@pytest.fixture()
def client(monkeypatch):
    """TestClient with auth disabled."""
    monkeypatch.setattr(settings, "controller_secret", "")
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# 2a. Path traversal in get_workspace()
# ---------------------------------------------------------------------------

class TestGetWorkspace:
    """Verify get_workspace rejects path traversal and shell metacharacters."""

    def test_valid_lab_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path))
        ws = get_workspace("valid-lab-123")
        assert ws.exists()
        assert ws.is_relative_to(tmp_path)

    def test_dotdot_path_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path))
        with pytest.raises(ValueError, match="Invalid lab_id"):
            get_workspace("../etc/passwd")

    def test_nested_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path))
        with pytest.raises(ValueError, match="Invalid lab_id"):
            get_workspace("foo/../../etc")

    def test_empty_lab_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path))
        with pytest.raises(ValueError, match="Invalid lab_id"):
            get_workspace("")

    def test_shell_metacharacters(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "workspace_path", str(tmp_path))
        with pytest.raises(ValueError, match="Invalid lab_id"):
            get_workspace("abc;rm -rf /")


# ---------------------------------------------------------------------------
# 2b. IP address validation (/network/test-mtu)
# ---------------------------------------------------------------------------

class TestMtuIpValidation:
    """Verify IP address validation on the test-mtu endpoint."""

    def test_valid_ip(self, client: TestClient):
        resp = client.post(
            "/network/test-mtu",
            json={"target_ip": "192.168.1.1", "mtu": 1500},
        )
        # Should pass validation (may fail at ping level, but not 400)
        assert resp.status_code != 422

    def test_invalid_ip_string(self, client: TestClient):
        resp = client.post(
            "/network/test-mtu",
            json={"target_ip": "not-an-ip", "mtu": 1500},
        )
        assert resp.status_code == 400
        assert "Invalid IP address" in resp.json()["detail"]

    def test_command_injection_in_ip(self, client: TestClient):
        resp = client.post(
            "/network/test-mtu",
            json={"target_ip": "; rm -rf /", "mtu": 1500},
        )
        assert resp.status_code == 400
        assert "Invalid IP address" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 2c. Port name validation
# ---------------------------------------------------------------------------

class TestPortNameValidation:
    """Verify _validate_port_name rejects unsafe names."""

    def test_valid_vxlan_name(self):
        assert _validate_port_name("vxlan-aabb1122") is True

    def test_valid_alphanumeric(self):
        assert _validate_port_name("eth0") is True

    def test_valid_dots_and_dashes(self):
        assert _validate_port_name("port-1.0") is True

    def test_command_injection(self):
        assert _validate_port_name("port; rm -rf /") is False

    def test_too_long(self):
        assert _validate_port_name("a" * 65) is False

    def test_empty_string(self):
        assert _validate_port_name("") is False

    def test_spaces(self):
        assert _validate_port_name("port name") is False
