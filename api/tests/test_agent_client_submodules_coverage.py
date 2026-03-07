"""Tests for agent_client submodules: links, node_ops, overlay."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_agent():
    agent = MagicMock()
    agent.id = "agent-1"
    agent.name = "Agent 1"
    agent.address = "http://agent:8001"
    return agent


AGENT_URL = "http://agent:8001"


# ---------------------------------------------------------------------------
# links.py tests
# ---------------------------------------------------------------------------

class TestCreateLinkOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.links.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.links._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"success": True, "link": {"link_id": "l1"}}
            result = await self._call(agent)
            assert result["success"] is True
            mock_req.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_returns_fallback(self):
        agent = _make_agent()
        with patch("app.agent_client.links.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.links._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("timeout")):
            result = await self._call(agent)
            assert result["success"] is False
            assert "timeout" in result["error"]

    async def _call(self, agent):
        from app.agent_client.links import create_link_on_agent
        return await create_link_on_agent(agent, "lab1", "n1", "eth1", "n2", "eth1")


class TestDeleteLinkOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.links.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.links._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"success": True}
            from app.agent_client.links import delete_link_on_agent
            result = await delete_link_on_agent(agent, "lab1", "n1:eth1-n2:eth1")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exception(self):
        agent = _make_agent()
        with patch("app.agent_client.links.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.links._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("err")):
            from app.agent_client.links import delete_link_on_agent
            result = await delete_link_on_agent(agent, "lab1", "link-1")
            assert result["success"] is False


class TestListLinksOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.links.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.links._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"links": [{"id": "l1"}]}
            from app.agent_client.links import list_links_on_agent
            result = await list_links_on_agent(agent, "lab1")
            assert result["links"] == [{"id": "l1"}]

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        agent = _make_agent()
        with patch("app.agent_client.links.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.links._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("err")):
            from app.agent_client.links import list_links_on_agent
            result = await list_links_on_agent(agent, "lab1")
            assert result == {"links": []}


class TestGetLabPortState:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.links.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.links._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"ports": [{"name": "p1", "vlan": 100}]}
            from app.agent_client.links import get_lab_port_state
            result = await get_lab_port_state(agent, "lab1")
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_exception_returns_empty_list(self):
        agent = _make_agent()
        with patch("app.agent_client.links.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.links._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("err")):
            from app.agent_client.links import get_lab_port_state
            result = await get_lab_port_state(agent, "lab1")
            assert result == []


class TestSetPortVlanOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.links.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.links._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"success": True}
            from app.agent_client.links import set_port_vlan_on_agent
            result = await set_port_vlan_on_agent(agent, "vxlan-abc", 100)
            assert result is True

    @pytest.mark.asyncio
    async def test_exception_returns_false(self):
        agent = _make_agent()
        with patch("app.agent_client.links.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.links._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("err")):
            from app.agent_client.links import set_port_vlan_on_agent
            result = await set_port_vlan_on_agent(agent, "vxlan-abc", 100)
            assert result is False


class TestRepairEndpointsOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.links.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.links._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"success": True, "total_endpoints_repaired": 3}
            from app.agent_client.links import repair_endpoints_on_agent
            result = await repair_endpoints_on_agent(agent, "lab1", nodes=["n1"])
            assert result["success"] is True
            assert result["total_endpoints_repaired"] == 3

    @pytest.mark.asyncio
    async def test_exception(self):
        agent = _make_agent()
        with patch("app.agent_client.links.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.links._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("err")):
            from app.agent_client.links import repair_endpoints_on_agent
            result = await repair_endpoints_on_agent(agent, "lab1")
            assert result["success"] is False


class TestConnectExternalOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.links.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.links._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"success": True, "vlan_tag": 200}
            from app.agent_client.links import connect_external_on_agent
            result = await connect_external_on_agent(agent, "lab1", "n1", "eth1", "ens192")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exception(self):
        agent = _make_agent()
        with patch("app.agent_client.links.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.links._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("err")):
            from app.agent_client.links import connect_external_on_agent
            result = await connect_external_on_agent(agent, "lab1", "n1", "eth1", "ens192")
            assert result["success"] is False


class TestDetachExternalOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.links.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.links._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"success": True}
            from app.agent_client.links import detach_external_on_agent
            result = await detach_external_on_agent(agent, "ens192")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exception(self):
        agent = _make_agent()
        with patch("app.agent_client.links.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.links._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("err")):
            from app.agent_client.links import detach_external_on_agent
            result = await detach_external_on_agent(agent, "ens192")
            assert result["success"] is False


# ---------------------------------------------------------------------------
# node_ops.py tests
# ---------------------------------------------------------------------------

class TestDeployToAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.node_ops.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.node_ops._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"status": "success"}
            from app.agent_client.node_ops import deploy_to_agent
            result = await deploy_to_agent(agent, "j1", "lab1", topology={"nodes": []})
            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_topology_none_raises_valueerror(self):
        agent = _make_agent()
        from app.agent_client.node_ops import deploy_to_agent
        with pytest.raises(ValueError, match="Deploy requires topology JSON"):
            await deploy_to_agent(agent, "j1", "lab1", topology=None)


class TestDestroyOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.node_ops.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.node_ops._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"status": "success"}
            from app.agent_client.node_ops import destroy_on_agent
            result = await destroy_on_agent(agent, "j1", "lab1")
            assert result["status"] == "success"


class TestCheckNodeReadiness:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.node_ops.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.node_ops._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"is_ready": True, "message": "Boot complete"}
            from app.agent_client.node_ops import check_node_readiness
            result = await check_node_readiness(agent, "lab1", "r1", kind="cisco_iosv")
            assert result["is_ready"] is True

    @pytest.mark.asyncio
    async def test_catches_all_exceptions(self):
        """check_node_readiness catches ALL exceptions and returns is_ready:False."""
        agent = _make_agent()
        with patch("app.agent_client.node_ops.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.node_ops._agent_request", new_callable=AsyncMock, side_effect=Exception("any error")):
            from app.agent_client.node_ops import check_node_readiness
            result = await check_node_readiness(agent, "lab1", "r1")
            assert result["is_ready"] is False
            assert "Readiness check failed" in result["message"]
            assert result["progress_percent"] is None


class TestCreateNodeOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.node_ops.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.node_ops._timed_node_operation", new_callable=AsyncMock) as mock_op:
            mock_op.return_value = {"success": True}
            from app.agent_client.node_ops import create_node_on_agent
            result = await create_node_on_agent(
                agent,
                "lab1",
                "r1",
                "ceos",
                node_definition_id="node-def-1",
                image="ceos:latest",
            )
            assert result["success"] is True
            mock_op.assert_awaited_once()
            assert mock_op.await_args.kwargs["json_body"]["node_definition_id"] == "node-def-1"

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        agent = _make_agent()
        with patch("app.agent_client.node_ops.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.node_ops._timed_node_operation", new_callable=AsyncMock, side_effect=RuntimeError("fail")):
            from app.agent_client.node_ops import create_node_on_agent
            with pytest.raises(RuntimeError, match="fail"):
                await create_node_on_agent(agent, "lab1", "r1", "ceos")


class TestDiscoverLabsOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.node_ops._safe_agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"labs": ["lab1"]}
            from app.agent_client.node_ops import discover_labs_on_agent
            result = await discover_labs_on_agent(agent)
            assert result["labs"] == ["lab1"]


class TestCleanupOrphansOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.node_ops._safe_agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"removed_containers": ["c1"], "errors": []}
            from app.agent_client.node_ops import cleanup_orphans_on_agent
            result = await cleanup_orphans_on_agent(agent, ["lab1"])
            assert result["removed_containers"] == ["c1"]


class TestContainerAction:
    @pytest.mark.asyncio
    async def test_legacy_path_success(self):
        agent = _make_agent()
        with patch("app.agent_client.node_ops.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.node_ops._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"success": True}
            from app.agent_client.node_ops import container_action
            result = await container_action(agent, "arch-lab1-r1", "start")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_legacy_path_exception(self):
        agent = _make_agent()
        with patch("app.agent_client.node_ops.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.node_ops._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("err")):
            from app.agent_client.node_ops import container_action
            result = await container_action(agent, "arch-lab1-r1", "stop")
            assert result["success"] is False


class TestDestroyLabOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.node_ops.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.node_ops._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"status": "success"}
            from app.agent_client.node_ops import destroy_lab_on_agent
            result = await destroy_lab_on_agent(agent, "lab1")
            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_exception(self):
        agent = _make_agent()
        with patch("app.agent_client.node_ops.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.node_ops._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("err")):
            from app.agent_client.node_ops import destroy_lab_on_agent
            result = await destroy_lab_on_agent(agent, "lab1")
            assert result["status"] == "failed"
            assert "err" in result["error"]


# ---------------------------------------------------------------------------
# overlay.py tests
# ---------------------------------------------------------------------------

class TestComputeVxlanPortName:
    def test_deterministic_hash(self):
        from app.agent_client.overlay import compute_vxlan_port_name
        name1 = compute_vxlan_port_name("lab1", "n1:eth1-n2:eth1")
        name2 = compute_vxlan_port_name("lab1", "n1:eth1-n2:eth1")
        assert name1 == name2
        assert name1.startswith("vxlan-")
        assert len(name1) == 6 + 8  # "vxlan-" + 8 hex chars

    def test_different_inputs_different_names(self):
        from app.agent_client.overlay import compute_vxlan_port_name
        name1 = compute_vxlan_port_name("lab1", "n1:eth1-n2:eth1")
        name2 = compute_vxlan_port_name("lab2", "n1:eth1-n2:eth1")
        assert name1 != name2


class TestReconcileVxlanPortsOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.overlay.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.overlay._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"removed_ports": ["vxlan-old1"]}
            from app.agent_client.overlay import reconcile_vxlan_ports_on_agent
            result = await reconcile_vxlan_ports_on_agent(agent, ["vxlan-keep1"])
            assert result["removed_ports"] == ["vxlan-old1"]

    @pytest.mark.asyncio
    async def test_exception(self):
        agent = _make_agent()
        with patch("app.agent_client.overlay.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.overlay._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("err")):
            from app.agent_client.overlay import reconcile_vxlan_ports_on_agent
            result = await reconcile_vxlan_ports_on_agent(agent, [])
            assert result["removed_ports"] == []
            assert "err" in result["errors"][0]


class TestDeclareOverlayStateOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.overlay.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.overlay._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"results": [{"ok": True}], "orphans_removed": []}
            from app.agent_client.overlay import declare_overlay_state_on_agent
            result = await declare_overlay_state_on_agent(agent, [{"port_name": "vx1"}])
            assert result["results"] == [{"ok": True}]

    @pytest.mark.asyncio
    async def test_404_fallback_to_reconcile(self):
        agent = _make_agent()
        with patch("app.agent_client.overlay.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.overlay._agent_request", new_callable=AsyncMock) as mock_req, \
             patch("app.agent_client.overlay.reconcile_vxlan_ports_on_agent", new_callable=AsyncMock) as mock_reconcile:
            mock_req.side_effect = RuntimeError("404 Not Found")
            mock_reconcile.return_value = {"removed_ports": []}
            from app.agent_client.overlay import declare_overlay_state_on_agent
            result = await declare_overlay_state_on_agent(agent, [{"port_name": "vx1"}])
            mock_reconcile.assert_awaited_once()
            assert result == {"removed_ports": []}

    @pytest.mark.asyncio
    async def test_non_404_exception(self):
        agent = _make_agent()
        with patch("app.agent_client.overlay.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.overlay._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("connection refused")):
            from app.agent_client.overlay import declare_overlay_state_on_agent
            result = await declare_overlay_state_on_agent(agent, [])
            assert result["results"] == []
            assert "connection refused" in result["error"]


class TestGetOverlayStatusFromAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.overlay._safe_agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"tunnels": [{"vni": 100}], "bridges": []}
            from app.agent_client.overlay import get_overlay_status_from_agent
            result = await get_overlay_status_from_agent(agent)
            assert len(result["tunnels"]) == 1


class TestCleanupOverlayOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.overlay.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.overlay._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"tunnels_deleted": 2, "bridges_deleted": 1}
            from app.agent_client.overlay import cleanup_overlay_on_agent
            result = await cleanup_overlay_on_agent(agent, "lab1")
            assert result["tunnels_deleted"] == 2

    @pytest.mark.asyncio
    async def test_exception(self):
        agent = _make_agent()
        with patch("app.agent_client.overlay.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.overlay._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("err")):
            from app.agent_client.overlay import cleanup_overlay_on_agent
            result = await cleanup_overlay_on_agent(agent, "lab1")
            assert result["tunnels_deleted"] == 0
            assert result["bridges_deleted"] == 0


class TestAttachOverlayInterfaceOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.overlay.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.overlay._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"success": True, "local_vlan": 100, "vni": 5000}
            from app.agent_client.overlay import attach_overlay_interface_on_agent
            result = await attach_overlay_interface_on_agent(
                agent, "lab1", "arch-lab1-r1", "eth1", 5000, "10.0.0.1", "10.0.0.2", "link1"
            )
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exception(self):
        agent = _make_agent()
        with patch("app.agent_client.overlay.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.overlay._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("err")):
            from app.agent_client.overlay import attach_overlay_interface_on_agent
            result = await attach_overlay_interface_on_agent(
                agent, "lab1", "arch-lab1-r1", "eth1", 5000, "10.0.0.1", "10.0.0.2", "link1"
            )
            assert result["success"] is False


class TestDetachOverlayInterfaceOnAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.overlay.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.overlay._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"success": True, "interface_isolated": True, "new_vlan": 999, "tunnel_deleted": True}
            from app.agent_client.overlay import detach_overlay_interface_on_agent
            result = await detach_overlay_interface_on_agent(agent, "lab1", "r1", "eth1", "link1")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_exception(self):
        agent = _make_agent()
        with patch("app.agent_client.overlay.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.overlay._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("err")):
            from app.agent_client.overlay import detach_overlay_interface_on_agent
            result = await detach_overlay_interface_on_agent(agent, "lab1", "r1", "eth1", "link1")
            assert result["success"] is False


class TestGetCleanupAuditFromAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        agent = _make_agent()
        with patch("app.agent_client.overlay.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.overlay._agent_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"network": {"orphans": 1}, "ovs": None}
            from app.agent_client.overlay import get_cleanup_audit_from_agent
            result = await get_cleanup_audit_from_agent(agent, include_ovs=True)
            assert result["network"]["orphans"] == 1

    @pytest.mark.asyncio
    async def test_exception(self):
        agent = _make_agent()
        with patch("app.agent_client.overlay.get_agent_url", return_value=AGENT_URL), \
             patch("app.agent_client.overlay._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("err")):
            from app.agent_client.overlay import get_cleanup_audit_from_agent
            result = await get_cleanup_audit_from_agent(agent)
            assert result["network"] == {}
            assert "err" in result["errors"][0]
