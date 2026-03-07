"""Tests for the agent registration module.

Covers agent registration, heartbeat, heartbeat loop,
and bootstrap transport configuration.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import agent.agent_state as _state
from agent.config import settings
from agent.registration import (
    _bootstrap_transport_config,
    heartbeat_loop,
    register_with_controller,
    send_heartbeat,
)
from agent.schemas import (
    AgentStatus,
    HeartbeatResponse,
)
from agent.schemas.common import AgentCapabilities, AgentInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(self, status_code: int = 200, data: dict | None = None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data


class FakeHttpClient:
    """Controllable httpx.AsyncClient stand-in."""

    def __init__(self):
        self.post = AsyncMock(return_value=FakeResponse())
        self.get = AsyncMock(return_value=FakeResponse())


def _make_agent_info(agent_id: str = "test-agent-id") -> AgentInfo:
    """Build a real AgentInfo for registration tests."""
    return AgentInfo(
        agent_id=agent_id,
        name="test-agent",
        address="127.0.0.1:8001",
        capabilities=AgentCapabilities(providers=[], features=[]),
    )


@pytest.fixture(autouse=True)
def _reset_registration_state():
    """Ensure registration state is clean for each test."""
    original_registered = _state._registered
    original_agent_id = _state.AGENT_ID
    _state.set_registered(False)
    _state.set_agent_id("test-agent-id")
    yield
    _state.set_registered(original_registered)
    _state.set_agent_id(original_agent_id)


# ---------------------------------------------------------------------------
# 1. register_with_controller()
# ---------------------------------------------------------------------------


class TestRegisterWithController:
    """Tests for register_with_controller()."""

    @pytest.mark.asyncio
    async def test_register_success_sets_flag(self, monkeypatch):
        """Successful registration should mark agent as registered."""
        client = FakeHttpClient()
        client.post = AsyncMock(return_value=FakeResponse(200, {
            "success": True,
            "message": "Registered",
            "assigned_id": None,
        }))
        monkeypatch.setattr("agent.registration.get_http_client", lambda: client)
        monkeypatch.setattr("agent.registration.get_controller_auth_headers", lambda: {})
        monkeypatch.setattr(
            "agent.registration.get_agent_info",
            _make_agent_info,
        )

        result = await register_with_controller()

        assert result is True
        assert _state._registered is True

    @pytest.mark.asyncio
    async def test_register_uses_assigned_id(self, monkeypatch):
        """When controller assigns a different ID, agent should adopt it."""
        client = FakeHttpClient()
        client.post = AsyncMock(return_value=FakeResponse(200, {
            "success": True,
            "message": "Re-registered",
            "assigned_id": "controller-assigned-id",
        }))
        monkeypatch.setattr("agent.registration.get_http_client", lambda: client)
        monkeypatch.setattr("agent.registration.get_controller_auth_headers", lambda: {})
        monkeypatch.setattr(
            "agent.registration.get_agent_info",
            _make_agent_info,
        )

        result = await register_with_controller()

        assert result is True
        assert _state.AGENT_ID == "controller-assigned-id"

    @pytest.mark.asyncio
    async def test_register_failure_leaves_unregistered(self, monkeypatch):
        """When controller rejects registration, agent should remain unregistered."""
        client = FakeHttpClient()
        client.post = AsyncMock(return_value=FakeResponse(200, {
            "success": False,
            "message": "Token invalid",
            "assigned_id": None,
        }))
        monkeypatch.setattr("agent.registration.get_http_client", lambda: client)
        monkeypatch.setattr("agent.registration.get_controller_auth_headers", lambda: {})
        monkeypatch.setattr(
            "agent.registration.get_agent_info",
            _make_agent_info,
        )

        result = await register_with_controller()

        assert result is False
        assert _state._registered is False

    @pytest.mark.asyncio
    async def test_register_http_error_returns_false(self, monkeypatch):
        """Non-200 HTTP response should return False."""
        client = FakeHttpClient()
        client.post = AsyncMock(return_value=FakeResponse(500, {}))
        monkeypatch.setattr("agent.registration.get_http_client", lambda: client)
        monkeypatch.setattr("agent.registration.get_controller_auth_headers", lambda: {})
        monkeypatch.setattr(
            "agent.registration.get_agent_info",
            _make_agent_info,
        )

        result = await register_with_controller()

        assert result is False
        assert _state._registered is False

    @pytest.mark.asyncio
    async def test_register_connect_error_returns_false(self, monkeypatch):
        """Network connect error should return False gracefully."""
        import httpx

        client = FakeHttpClient()
        client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        monkeypatch.setattr("agent.registration.get_http_client", lambda: client)
        monkeypatch.setattr("agent.registration.get_controller_auth_headers", lambda: {})
        monkeypatch.setattr(
            "agent.registration.get_agent_info",
            _make_agent_info,
        )

        result = await register_with_controller()

        assert result is False


# ---------------------------------------------------------------------------
# 2. send_heartbeat()
# ---------------------------------------------------------------------------


class TestSendHeartbeat:
    """Tests for send_heartbeat()."""

    @pytest.mark.asyncio
    async def test_heartbeat_builds_correct_payload(self, monkeypatch):
        """Heartbeat should include agent_id, status, active_jobs, resource_usage."""
        captured_kwargs = {}
        client = FakeHttpClient()

        async def capture_post(url, **kwargs):
            captured_kwargs.update(kwargs)
            return FakeResponse(200, {
                "acknowledged": True,
                "pending_jobs": [],
            })

        client.post = capture_post
        monkeypatch.setattr("agent.registration.get_http_client", lambda: client)
        monkeypatch.setattr("agent.registration.get_controller_auth_headers", lambda: {})
        monkeypatch.setattr(
            "agent.registration.get_resource_usage",
            AsyncMock(return_value={"cpu_percent": 10.5}),
        )
        monkeypatch.setattr(
            "agent.network.transport.get_data_plane_ip",
            lambda: "10.0.0.5",
        )
        _state.set_agent_id("hb-test-agent")

        response = await send_heartbeat()

        assert response is not None
        assert response.acknowledged is True
        payload = captured_kwargs["json"]
        assert payload["agent_id"] == "hb-test-agent"
        assert payload["status"] == AgentStatus.ONLINE

    @pytest.mark.asyncio
    async def test_heartbeat_network_error_returns_none(self, monkeypatch):
        """Network error during heartbeat should return None."""
        client = FakeHttpClient()
        client.post = AsyncMock(side_effect=Exception("connection reset"))
        monkeypatch.setattr("agent.registration.get_http_client", lambda: client)
        monkeypatch.setattr("agent.registration.get_controller_auth_headers", lambda: {})
        monkeypatch.setattr(
            "agent.registration.get_resource_usage",
            AsyncMock(return_value={"cpu_percent": 5.0}),
        )
        monkeypatch.setattr(
            "agent.network.transport.get_data_plane_ip",
            lambda: None,
        )

        response = await send_heartbeat()

        assert response is None

    @pytest.mark.asyncio
    async def test_heartbeat_success_returns_response(self, monkeypatch):
        """Successful heartbeat should return HeartbeatResponse."""
        client = FakeHttpClient()
        client.post = AsyncMock(return_value=FakeResponse(200, {
            "acknowledged": True,
            "pending_jobs": ["job-1", "job-2"],
        }))
        monkeypatch.setattr("agent.registration.get_http_client", lambda: client)
        monkeypatch.setattr("agent.registration.get_controller_auth_headers", lambda: {})
        monkeypatch.setattr(
            "agent.registration.get_resource_usage",
            AsyncMock(return_value={}),
        )
        monkeypatch.setattr(
            "agent.network.transport.get_data_plane_ip",
            lambda: None,
        )

        response = await send_heartbeat()

        assert isinstance(response, HeartbeatResponse)
        assert response.acknowledged is True
        assert response.pending_jobs == ["job-1", "job-2"]


# ---------------------------------------------------------------------------
# 3. heartbeat_loop()
# ---------------------------------------------------------------------------


class TestHeartbeatLoop:
    """Tests for heartbeat_loop()."""

    @pytest.mark.asyncio
    async def test_retries_registration_when_unregistered(self, monkeypatch):
        """When unregistered, heartbeat loop should attempt registration."""
        _state.set_registered(False)
        register_calls = {"count": 0}

        async def fake_register():
            register_calls["count"] += 1
            _state.set_registered(True)
            return True

        monkeypatch.setattr("agent.registration.register_with_controller", fake_register)
        monkeypatch.setattr(settings, "heartbeat_interval", 0)

        iteration = 0

        async def fake_sleep(seconds):
            nonlocal iteration
            iteration += 1
            if iteration > 1:
                raise asyncio.CancelledError()

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await heartbeat_loop()

        assert register_calls["count"] == 1

    @pytest.mark.asyncio
    async def test_marks_unregistered_on_heartbeat_failure(self, monkeypatch):
        """When heartbeat returns None, agent should be marked unregistered."""
        _state.set_registered(True)

        async def fake_heartbeat():
            return None

        monkeypatch.setattr("agent.registration.send_heartbeat", fake_heartbeat)
        monkeypatch.setattr(settings, "heartbeat_interval", 0)

        iteration = 0

        async def fake_sleep(seconds):
            nonlocal iteration
            iteration += 1
            if iteration > 1:
                raise asyncio.CancelledError()

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await heartbeat_loop()

        assert _state._registered is False


# ---------------------------------------------------------------------------
# 4. _bootstrap_transport_config()
# ---------------------------------------------------------------------------


class TestBootstrapTransportConfig:
    """Tests for _bootstrap_transport_config()."""

    @pytest.mark.asyncio
    async def test_skipped_when_not_registered(self, monkeypatch):
        """Bootstrap should be a no-op when agent is not registered."""
        _state.set_registered(False)
        client = FakeHttpClient()
        monkeypatch.setattr("agent.registration.get_http_client", lambda: client)

        await _bootstrap_transport_config()

        # No HTTP calls should have been made
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_management_mode_noop(self, monkeypatch):
        """Management transport mode should not run any ip commands."""
        _state.set_registered(True)
        client = FakeHttpClient()
        client.get = AsyncMock(return_value=FakeResponse(200, {
            "transport_mode": "management",
        }))
        monkeypatch.setattr("agent.registration.get_http_client", lambda: client)
        monkeypatch.setattr("agent.registration.get_controller_auth_headers", lambda: {})

        ip_calls = []

        async def fake_run_cmd(cmd):
            ip_calls.append(cmd)
            return (0, "", "")

        monkeypatch.setattr("agent.network.cmd.run_cmd", fake_run_cmd)

        await _bootstrap_transport_config()

        # No ip commands should have been run for management mode
        assert len(ip_calls) == 0

    @pytest.mark.asyncio
    async def test_subinterface_mode_calls_ip_commands(self, monkeypatch):
        """Subinterface mode should create VLAN interface and set IP."""
        _state.set_registered(True)
        client = FakeHttpClient()
        client.get = AsyncMock(return_value=FakeResponse(200, {
            "transport_mode": "subinterface",
            "parent_interface": "eth0",
            "vlan_id": 100,
            "transport_ip": "10.0.0.5/24",
            "desired_mtu": 9000,
        }))
        monkeypatch.setattr("agent.registration.get_http_client", lambda: client)
        monkeypatch.setattr("agent.registration.get_controller_auth_headers", lambda: {})

        ip_calls = []
        dp_ip_set = {}

        async def fake_run_cmd(cmd):
            ip_calls.append(cmd)
            # Subinterface does not exist yet (ip link show fails)
            if cmd == ["ip", "link", "show", "eth0.100"]:
                return (1, "", "Device not found")
            return (0, "", "")

        # Patch at module level — the function imports run_cmd inside its body
        monkeypatch.setattr("agent.network.cmd.run_cmd", fake_run_cmd)
        monkeypatch.setattr(
            "agent.network.transport.set_data_plane_ip",
            lambda ip: dp_ip_set.update({"ip": ip}),
        )

        await _bootstrap_transport_config()

        # Should have created the subinterface
        assert ["ip", "link", "show", "eth0.100"] in ip_calls
        assert ["ip", "link", "add", "link", "eth0",
                "name", "eth0.100", "type", "vlan", "id", "100"] in ip_calls
        assert ["ip", "link", "set", "eth0.100", "mtu", "9000"] in ip_calls
        assert ["ip", "addr", "flush", "dev", "eth0.100"] in ip_calls
        assert ["ip", "addr", "add", "10.0.0.5/24", "dev", "eth0.100"] in ip_calls
        assert ["ip", "link", "set", "eth0.100", "up"] in ip_calls
        assert dp_ip_set.get("ip") == "10.0.0.5"

    @pytest.mark.asyncio
    async def test_subinterface_mode_existing_interface_updates_parent_mtu(self, monkeypatch):
        """Existing subinterface should be reused and parent MTU raised when needed."""
        _state.set_registered(True)
        client = FakeHttpClient()
        client.get = AsyncMock(return_value=FakeResponse(200, {
            "transport_mode": "subinterface",
            "parent_interface": "eth0",
            "vlan_id": 200,
            "transport_ip": "10.0.1.5/24",
            "desired_mtu": 9000,
        }))
        monkeypatch.setattr("agent.registration.get_http_client", lambda: client)
        monkeypatch.setattr("agent.registration.get_controller_auth_headers", lambda: {})

        ip_calls = []
        dp_ip_set = {}

        async def fake_run_cmd(cmd):
            ip_calls.append(cmd)
            if cmd == ["ip", "link", "show", "eth0.200"]:
                return (0, "", "")
            return (0, "", "")

        monkeypatch.setattr("agent.network.cmd.run_cmd", fake_run_cmd)
        monkeypatch.setattr(
            "agent.network.transport.set_data_plane_ip",
            lambda ip: dp_ip_set.update({"ip": ip}),
        )

        class _FakeFile:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return "1500"

        monkeypatch.setattr("builtins.open", lambda *_args, **_kwargs: _FakeFile())

        await _bootstrap_transport_config()

        assert ["ip", "link", "show", "eth0.200"] in ip_calls
        assert ["ip", "link", "add", "link", "eth0", "name", "eth0.200", "type", "vlan", "id", "200"] not in ip_calls
        assert ["ip", "link", "set", "eth0", "mtu", "9000"] in ip_calls
        assert ["ip", "link", "set", "eth0.200", "mtu", "9000"] in ip_calls
        assert dp_ip_set.get("ip") == "10.0.1.5"

    @pytest.mark.asyncio
    async def test_subinterface_mode_missing_parent_or_vlan_is_noop(self, monkeypatch):
        """Incomplete subinterface config should not attempt interface provisioning."""
        _state.set_registered(True)
        client = FakeHttpClient()
        client.get = AsyncMock(return_value=FakeResponse(200, {
            "transport_mode": "subinterface",
            "parent_interface": "eth0",
        }))
        monkeypatch.setattr("agent.registration.get_http_client", lambda: client)
        monkeypatch.setattr("agent.registration.get_controller_auth_headers", lambda: {})

        ip_calls = []

        async def fake_run_cmd(cmd):
            ip_calls.append(cmd)
            return (0, "", "")

        monkeypatch.setattr("agent.network.cmd.run_cmd", fake_run_cmd)

        await _bootstrap_transport_config()

        assert ip_calls == []

    @pytest.mark.asyncio
    async def test_dedicated_mode_configures_interface_and_sets_data_plane_ip(self, monkeypatch):
        """Dedicated transport mode should configure the data plane interface directly."""
        _state.set_registered(True)
        client = FakeHttpClient()
        client.get = AsyncMock(return_value=FakeResponse(200, {
            "transport_mode": "dedicated",
            "data_plane_interface": "ens224",
            "transport_ip": "10.0.2.5/24",
            "desired_mtu": 9100,
        }))
        monkeypatch.setattr("agent.registration.get_http_client", lambda: client)
        monkeypatch.setattr("agent.registration.get_controller_auth_headers", lambda: {})

        ip_calls = []
        dp_ip_set = {}

        async def fake_run_cmd(cmd):
            ip_calls.append(cmd)
            return (0, "", "")

        monkeypatch.setattr("agent.network.cmd.run_cmd", fake_run_cmd)
        monkeypatch.setattr(
            "agent.network.transport.set_data_plane_ip",
            lambda ip: dp_ip_set.update({"ip": ip}),
        )

        await _bootstrap_transport_config()

        assert ["ip", "link", "set", "ens224", "mtu", "9100"] in ip_calls
        assert ["ip", "addr", "flush", "dev", "ens224"] in ip_calls
        assert ["ip", "addr", "add", "10.0.2.5/24", "dev", "ens224"] in ip_calls
        assert ["ip", "link", "set", "ens224", "up"] in ip_calls
        assert dp_ip_set.get("ip") == "10.0.2.5"

    @pytest.mark.asyncio
    async def test_dedicated_mode_missing_interface_is_noop(self, monkeypatch):
        """Incomplete dedicated config should not attempt interface provisioning."""
        _state.set_registered(True)
        client = FakeHttpClient()
        client.get = AsyncMock(return_value=FakeResponse(200, {
            "transport_mode": "dedicated",
            "transport_ip": "10.0.3.5/24",
        }))
        monkeypatch.setattr("agent.registration.get_http_client", lambda: client)
        monkeypatch.setattr("agent.registration.get_controller_auth_headers", lambda: {})

        ip_calls = []

        async def fake_run_cmd(cmd):
            ip_calls.append(cmd)
            return (0, "", "")

        monkeypatch.setattr("agent.network.cmd.run_cmd", fake_run_cmd)

        await _bootstrap_transport_config()

        assert ip_calls == []
