"""Tests for app.agent_client.maintenance — wrapper functions for agent HTTP calls."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(
    test_db: Session | None = None,
    *,
    host_id: str = "agent-1",
    name: str = "Agent 1",
    address: str = "agent1.local:8080",
) -> models.Host:
    """Create a Host model (may or may not be persisted)."""
    host = models.Host(
        id=host_id,
        name=name,
        address=address,
        status="online",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
        resource_usage=json.dumps({}),
        last_heartbeat=datetime.now(timezone.utc),
    )
    if test_db:
        test_db.add(host)
        test_db.commit()
        test_db.refresh(host)
    return host


# ---------------------------------------------------------------------------
# prune_docker_on_agent
# ---------------------------------------------------------------------------

class TestPruneDockerOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agent_client.maintenance import prune_docker_on_agent

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._safe_agent_request",
            new_callable=AsyncMock,
            return_value={"success": True, "images_removed": 3, "space_reclaimed": 1024},
        ):
            result = await prune_docker_on_agent(agent, ["lab-1"])
        assert result["success"] is True
        assert result["images_removed"] == 3

    @pytest.mark.asyncio
    async def test_agent_unreachable_returns_fallback(self):
        from app.agent_client.maintenance import prune_docker_on_agent

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._safe_agent_request",
            new_callable=AsyncMock,
            return_value={"success": False, "images_removed": 0, "space_reclaimed": 0},
        ):
            result = await prune_docker_on_agent(agent, [])
        assert result["success"] is False


# ---------------------------------------------------------------------------
# cleanup_agent_workspace
# ---------------------------------------------------------------------------

class TestCleanupAgentWorkspace:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agent_client.maintenance import cleanup_agent_workspace

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._safe_agent_request",
            new_callable=AsyncMock,
            return_value={"success": True},
        ):
            result = await cleanup_agent_workspace(agent, "lab-1")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_error(self):
        from app.agent_client.maintenance import cleanup_agent_workspace

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._safe_agent_request",
            new_callable=AsyncMock,
            return_value={"success": False},
        ):
            result = await cleanup_agent_workspace(agent, "lab-1")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# cleanup_workspaces_on_agent
# ---------------------------------------------------------------------------

class TestCleanupWorkspacesOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agent_client.maintenance import cleanup_workspaces_on_agent

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._safe_agent_request",
            new_callable=AsyncMock,
            return_value={"success": True, "removed": ["lab-old"], "errors": []},
        ):
            result = await cleanup_workspaces_on_agent(agent, ["lab-1"])
        assert result["success"] is True
        assert "lab-old" in result["removed"]

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        from app.agent_client.maintenance import cleanup_workspaces_on_agent

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._safe_agent_request",
            new_callable=AsyncMock,
            return_value={"success": False, "removed": [], "errors": ["permission denied"]},
        ):
            result = await cleanup_workspaces_on_agent(agent, [])
        assert len(result["errors"]) == 1


# ---------------------------------------------------------------------------
# test_mtu_on_agent
# ---------------------------------------------------------------------------

class TestMtuOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agent_client.maintenance import test_mtu_on_agent

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._agent_request",
            new_callable=AsyncMock,
            return_value={"success": True, "tested_mtu": 9000, "latency_ms": 0.5},
        ):
            result = await test_mtu_on_agent(agent, "10.0.0.2", 9000)
        assert result["success"] is True
        assert result["tested_mtu"] == 9000

    @pytest.mark.asyncio
    async def test_with_source_ip(self):
        from app.agent_client.maintenance import test_mtu_on_agent

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._agent_request",
            new_callable=AsyncMock,
            return_value={"success": True},
        ) as mock_req:
            await test_mtu_on_agent(agent, "10.0.0.2", 9000, source_ip="10.0.0.1")
        # Verify source_ip was included in the payload
        call_kwargs = mock_req.call_args
        payload = call_kwargs.kwargs.get("json_body") or call_kwargs[1].get("json_body")
        assert payload["source_ip"] == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_agent_error(self):
        from app.agent_client.maintenance import test_mtu_on_agent

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._agent_request",
            new_callable=AsyncMock,
            side_effect=Exception("connection refused"),
        ):
            result = await test_mtu_on_agent(agent, "10.0.0.2", 1500)
        assert result["success"] is False
        assert "connection refused" in result["error"]


# ---------------------------------------------------------------------------
# get_agent_interface_details
# ---------------------------------------------------------------------------

class TestGetAgentInterfaceDetails:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agent_client.maintenance import get_agent_interface_details

        agent = _make_agent()
        expected = {"interfaces": [{"name": "eth0", "mtu": 1500}], "default_route_interface": "eth0"}
        with patch(
            "app.agent_client.maintenance._agent_request",
            new_callable=AsyncMock,
            return_value=expected,
        ):
            result = await get_agent_interface_details(agent)
        assert result["interfaces"][0]["name"] == "eth0"

    @pytest.mark.asyncio
    async def test_error_raises(self):
        from app.agent_client.maintenance import get_agent_interface_details

        agent = _make_agent()
        with (
            patch(
                "app.agent_client.maintenance._agent_request",
                new_callable=AsyncMock,
                side_effect=Exception("timeout"),
            ),
            pytest.raises(Exception, match="timeout"),
        ):
            await get_agent_interface_details(agent)


# ---------------------------------------------------------------------------
# set_agent_interface_mtu
# ---------------------------------------------------------------------------

class TestSetAgentInterfaceMtu:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agent_client.maintenance import set_agent_interface_mtu

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._agent_request",
            new_callable=AsyncMock,
            return_value={"success": True, "interface": "eth0", "previous_mtu": 1500, "new_mtu": 9000},
        ):
            result = await set_agent_interface_mtu(agent, "eth0", 9000)
        assert result["success"] is True
        assert result["new_mtu"] == 9000

    @pytest.mark.asyncio
    async def test_error_returns_failure(self):
        from app.agent_client.maintenance import set_agent_interface_mtu

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._agent_request",
            new_callable=AsyncMock,
            side_effect=Exception("permission denied"),
        ):
            result = await set_agent_interface_mtu(agent, "eth0", 9000)
        assert result["success"] is False
        assert "permission denied" in result["error"]


# ---------------------------------------------------------------------------
# provision_interface_on_agent
# ---------------------------------------------------------------------------

class TestProvisionInterfaceOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agent_client.maintenance import provision_interface_on_agent

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._agent_request",
            new_callable=AsyncMock,
            return_value={"success": True, "interface_name": "eth0.100"},
        ):
            result = await provision_interface_on_agent(
                agent, action="create_subinterface",
                parent_interface="eth0", vlan_id=100,
            )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_none_values_stripped(self):
        from app.agent_client.maintenance import provision_interface_on_agent

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._agent_request",
            new_callable=AsyncMock,
            return_value={"success": True},
        ) as mock_req:
            await provision_interface_on_agent(agent, action="configure", name="eth0")
        call_kwargs = mock_req.call_args
        payload = call_kwargs.kwargs.get("json_body") or call_kwargs[1].get("json_body")
        # None values should be stripped
        assert "parent_interface" not in payload
        assert "vlan_id" not in payload

    @pytest.mark.asyncio
    async def test_error(self):
        from app.agent_client.maintenance import provision_interface_on_agent

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._agent_request",
            new_callable=AsyncMock,
            side_effect=Exception("failed"),
        ):
            result = await provision_interface_on_agent(agent, action="delete", name="eth0.100")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# get_ovs_status_from_agent
# ---------------------------------------------------------------------------

class TestGetOvsStatusFromAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agent_client.maintenance import get_ovs_status_from_agent

        agent = _make_agent()
        expected = {"bridge_name": "arch-ovs", "initialized": True, "ports": [], "links": []}
        with patch(
            "app.agent_client.maintenance._agent_request",
            new_callable=AsyncMock,
            return_value=expected,
        ):
            result = await get_ovs_status_from_agent(agent)
        assert result["bridge_name"] == "arch-ovs"
        assert result["initialized"] is True

    @pytest.mark.asyncio
    async def test_error_returns_empty(self):
        from app.agent_client.maintenance import get_ovs_status_from_agent

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._agent_request",
            new_callable=AsyncMock,
            side_effect=Exception("OVS not running"),
        ):
            result = await get_ovs_status_from_agent(agent)
        assert result["bridge_name"] == ""
        assert result["initialized"] is False


# ---------------------------------------------------------------------------
# get_agent_boot_logs
# ---------------------------------------------------------------------------

class TestGetAgentBootLogs:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agent_client.maintenance import get_agent_boot_logs

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._agent_request",
            new_callable=AsyncMock,
            return_value={"lab_id": "lab-1", "boot_logs": {"R1": "booting..."}},
        ):
            result = await get_agent_boot_logs(agent, lab_id="lab-1")
        assert result["boot_logs"]["R1"] == "booting..."

    @pytest.mark.asyncio
    async def test_no_lab_id(self):
        from app.agent_client.maintenance import get_agent_boot_logs

        agent = _make_agent()
        result = await get_agent_boot_logs(agent, lab_id=None)
        assert result["boot_logs"] == {}
        assert "error" in result

    @pytest.mark.asyncio
    async def test_not_found(self):
        from app.agent_client.maintenance import get_agent_boot_logs

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._agent_request",
            new_callable=AsyncMock,
            side_effect=Exception("404 Not Found"),
        ):
            result = await get_agent_boot_logs(agent, lab_id="lab-missing")
        assert result["boot_logs"] == {}
        assert "error" in result


# ---------------------------------------------------------------------------
# get_agent_ovs_flows
# ---------------------------------------------------------------------------

class TestGetAgentOvsFlows:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agent_client.maintenance import get_agent_ovs_flows

        agent = _make_agent()
        expected = {"bridge": "arch-ovs", "flows": "NXST_FLOW reply..."}
        with patch(
            "app.agent_client.maintenance._agent_request",
            new_callable=AsyncMock,
            return_value=expected,
        ):
            result = await get_agent_ovs_flows(agent)
        assert result["bridge"] == "arch-ovs"

    @pytest.mark.asyncio
    async def test_error(self):
        from app.agent_client.maintenance import get_agent_ovs_flows

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._agent_request",
            new_callable=AsyncMock,
            side_effect=Exception("OVS error"),
        ):
            result = await get_agent_ovs_flows(agent)
        assert result["bridge"] == ""
        assert "error" in result


# ---------------------------------------------------------------------------
# exec_node_on_agent
# ---------------------------------------------------------------------------

class TestExecNodeOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agent_client.maintenance import exec_node_on_agent

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._agent_request",
            new_callable=AsyncMock,
            return_value={"exit_code": 0, "output": "hello"},
        ):
            result = await exec_node_on_agent(agent, "lab-1", "R1", "echo hello")
        assert result["exit_code"] == 0
        assert result["output"] == "hello"

    @pytest.mark.asyncio
    async def test_timeout(self):
        from app.agent_client.maintenance import exec_node_on_agent

        agent = _make_agent()
        with (
            patch(
                "app.agent_client.maintenance._agent_request",
                new_callable=AsyncMock,
                side_effect=TimeoutError("command timed out"),
            ),
            pytest.raises(TimeoutError),
        ):
            await exec_node_on_agent(agent, "lab-1", "R1", "sleep 999", timeout=5.0)

    @pytest.mark.asyncio
    async def test_error(self):
        from app.agent_client.maintenance import exec_node_on_agent

        agent = _make_agent()
        with (
            patch(
                "app.agent_client.maintenance._agent_request",
                new_callable=AsyncMock,
                side_effect=Exception("agent down"),
            ),
            pytest.raises(Exception, match="agent down"),
        ):
            await exec_node_on_agent(agent, "lab-1", "R1", "ls")


# ---------------------------------------------------------------------------
# extract_configs_on_agent
# ---------------------------------------------------------------------------

class TestExtractConfigsOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agent_client.maintenance import extract_configs_on_agent

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._safe_agent_request",
            new_callable=AsyncMock,
            return_value={"success": True, "extracted_count": 3},
        ):
            result = await extract_configs_on_agent(agent, "lab-1")
        assert result["success"] is True
        assert result["extracted_count"] == 3

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.agent_client.maintenance import extract_configs_on_agent

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._safe_agent_request",
            new_callable=AsyncMock,
            return_value={"success": False, "extracted_count": 0},
        ):
            result = await extract_configs_on_agent(agent, "lab-1")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# extract_node_config_on_agent
# ---------------------------------------------------------------------------

class TestExtractNodeConfigOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agent_client.maintenance import extract_node_config_on_agent

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._safe_agent_request",
            new_callable=AsyncMock,
            return_value={"success": True, "node_name": "R1", "config": "hostname R1"},
        ):
            result = await extract_node_config_on_agent(agent, "lab-1", "R1")
        assert result["success"] is True


# ---------------------------------------------------------------------------
# update_config_on_agent
# ---------------------------------------------------------------------------

class TestUpdateConfigOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agent_client.maintenance import update_config_on_agent

        agent = _make_agent()
        with patch(
            "app.agent_client.maintenance._safe_agent_request",
            new_callable=AsyncMock,
            return_value={"success": True},
        ):
            result = await update_config_on_agent(agent, "lab-1", "R1", "hostname R1\n")
        assert result["success"] is True
