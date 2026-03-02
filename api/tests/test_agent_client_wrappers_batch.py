"""Additional unit coverage for app.agent_client wrapper endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import agent_client
from app.agent_client import AgentError, AgentJobError


def _agent() -> SimpleNamespace:
    return SimpleNamespace(
        id="agent-1",
        name="agent-1",
        address="10.0.0.10:8001",
        get_capabilities=lambda: {"features": ["vxlan"]},
    )


@pytest.mark.asyncio
async def test_overlay_and_images_wrappers_use_safe_request():
    host = _agent()
    with patch("app.agent_client._safe_agent_request", new_callable=AsyncMock) as mock_req:
        mock_req.side_effect = [
            {"tunnels": [], "bridges": []},
            {"images": ["img:a"]},
        ]

        overlay = await agent_client.get_overlay_status_from_agent(host)
        images = await agent_client.get_agent_images(host)

    assert overlay["tunnels"] == []
    assert images["images"] == ["img:a"]
    assert mock_req.await_count == 2


@pytest.mark.asyncio
async def test_container_action_lab_reconcile_success_and_failures():
    host = _agent()

    with patch("app.agent_client.reconcile_nodes_on_agent", new_callable=AsyncMock) as mock_reconcile:
        mock_reconcile.return_value = {"results": [{"success": True, "action": "start"}]}
        ok = await agent_client.container_action(host, "arch-lab-node", "start", lab_id="lab-1")
    assert ok == {"success": True, "message": "Container start"}

    with patch("app.agent_client.reconcile_nodes_on_agent", new_callable=AsyncMock) as mock_reconcile:
        mock_reconcile.return_value = {"results": [{"success": False, "error": "denied"}]}
        denied = await agent_client.container_action(host, "arch-lab-node", "stop", lab_id="lab-1")
    assert denied == {"success": False, "error": "denied"}

    with patch("app.agent_client.reconcile_nodes_on_agent", new_callable=AsyncMock) as mock_reconcile:
        mock_reconcile.return_value = {"results": []}
        no_result = await agent_client.container_action(host, "arch-lab-node", "stop", lab_id="lab-1")
    assert no_result == {"success": False, "error": "No result from reconcile"}

    with patch(
        "app.agent_client.reconcile_nodes_on_agent",
        new_callable=AsyncMock,
        side_effect=AgentError("agent unavailable", agent_id=host.id),
    ):
        agent_err = await agent_client.container_action(host, "arch-lab-node", "start", lab_id="lab-1")
    assert agent_err["success"] is False
    assert "agent unavailable" in agent_err["error"]

    with patch(
        "app.agent_client.reconcile_nodes_on_agent",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    ):
        generic_err = await agent_client.container_action(host, "arch-lab-node", "start", lab_id="lab-1")
    assert generic_err == {"success": False, "error": "boom"}


@pytest.mark.asyncio
async def test_container_action_legacy_path_and_job_error_parsing():
    host = _agent()
    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._agent_request", new_callable=AsyncMock
    ) as mock_req:
        mock_req.return_value = {"success": True}
        result = await agent_client.container_action(host, "arch-lab-node", "start")
    assert result["success"] is True
    args, _ = mock_req.await_args
    assert args[0] == "POST"
    assert args[1].endswith("/containers/arch-lab-node/start")

    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._agent_request",
        new_callable=AsyncMock,
        side_effect=AgentJobError(
            "job failed",
            agent_id=host.id,
            stderr='Response: {"detail":"container missing"}',
        ),
    ):
        parsed = await agent_client.container_action(host, "arch-lab-node", "stop")
    assert parsed == {"success": False, "error": "container missing"}

    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._agent_request",
        new_callable=AsyncMock,
        side_effect=AgentJobError("job failed", agent_id=host.id, stderr="not-json"),
    ):
        unparsed = await agent_client.container_action(host, "arch-lab-node", "stop")
    assert unparsed == {"success": False, "error": "job failed"}

    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._agent_request",
        new_callable=AsyncMock,
        side_effect=RuntimeError("unexpected"),
    ):
        runtime = await agent_client.container_action(host, "arch-lab-node", "stop")
    assert runtime == {"success": False, "error": "unexpected"}


@pytest.mark.asyncio
async def test_create_node_payload_includes_optional_fields():
    host = _agent()
    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._timed_node_operation", new_callable=AsyncMock, return_value={"success": True}
    ) as mock_op:
        result = await agent_client.create_node_on_agent(
            host,
            "lab-1",
            "r1",
            "router",
            image="img:a",
            display_name="Router 1",
            interface_count=5,
            binds=["/tmp:/tmp"],
            env={"A": "B"},
            startup_config="hostname r1",
            provider="libvirt",
            memory=4096,
            cpu=2,
            cpu_limit=150,
            disk_driver="virtio",
            nic_driver="e1000",
            machine_type="q35",
            libvirt_driver="qemu",
            readiness_probe="cli",
            readiness_pattern="ready",
            readiness_timeout=45,
            efi_boot=False,
            efi_vars="/tmp/vars.fd",
            data_volume_gb=0,
            image_sha256="sha256:abc",
        )

    assert result["success"] is True
    _, kwargs = mock_op.await_args
    payload = kwargs["json_body"]
    assert payload["image"] == "img:a"
    assert payload["display_name"] == "Router 1"
    assert payload["interface_count"] == 5
    assert payload["binds"] == ["/tmp:/tmp"]
    assert payload["env"] == {"A": "B"}
    assert payload["startup_config"] == "hostname r1"
    assert payload["memory"] == 4096
    assert payload["cpu"] == 2
    assert payload["cpu_limit"] == 150
    assert payload["disk_driver"] == "virtio"
    assert payload["nic_driver"] == "e1000"
    assert payload["machine_type"] == "q35"
    assert payload["libvirt_driver"] == "qemu"
    assert payload["readiness_probe"] == "cli"
    assert payload["readiness_pattern"] == "ready"
    assert payload["readiness_timeout"] == 45
    assert payload["efi_boot"] is False
    assert payload["efi_vars"] == "/tmp/vars.fd"
    assert payload["data_volume_gb"] == 0
    assert payload["image_sha256"] == "sha256:abc"


@pytest.mark.asyncio
async def test_start_stop_destroy_node_wrappers_delegate_to_timed_operation():
    host = _agent()
    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._timed_node_operation", new_callable=AsyncMock, return_value={"success": True}
    ) as mock_op:
        await agent_client.start_node_on_agent(host, "lab-1", "r1", provider="docker")
        await agent_client.stop_node_on_agent(host, "lab-1", "r1", provider="docker")
        await agent_client.destroy_node_on_agent(host, "lab-1", "r1", provider="docker")

    assert mock_op.await_count == 3


@pytest.mark.asyncio
async def test_extract_and_update_config_wrappers_cover_success_branches():
    host = _agent()
    with patch("app.agent_client._safe_agent_request", new_callable=AsyncMock) as mock_req:
        mock_req.side_effect = [
            {"success": True, "extracted_count": 2},
            {"success": True, "node_name": "r1"},
            {"success": True},
        ]
        batch = await agent_client.extract_configs_on_agent(host, "lab-1")
        single = await agent_client.extract_node_config_on_agent(host, "lab-1", "r1")
        pushed = await agent_client.update_config_on_agent(host, "lab-1", "r1", "hostname r1")

    assert batch["extracted_count"] == 2
    assert single["node_name"] == "r1"
    assert pushed["success"] is True


@pytest.mark.asyncio
async def test_prune_and_workspace_cleanup_wrappers():
    host = _agent()
    with patch("app.agent_client._safe_agent_request", new_callable=AsyncMock) as mock_req:
        mock_req.side_effect = [
            {"success": True, "images_removed": 1},
            {"success": True},
            {"success": True, "removed": ["lab-2"]},
        ]
        prune = await agent_client.prune_docker_on_agent(
            host,
            ["lab-1"],
            prune_dangling_images=True,
            prune_build_cache=False,
            prune_unused_volumes=True,
            prune_stopped_containers=True,
            prune_unused_networks=True,
        )
        one = await agent_client.cleanup_agent_workspace(host, "lab-1")
        many = await agent_client.cleanup_workspaces_on_agent(host, ["lab-1"])

    assert prune["success"] is True
    assert one["success"] is True
    assert many["removed"] == ["lab-2"]


@pytest.mark.asyncio
async def test_mtu_and_interface_config_wrappers_success_and_error_paths():
    host = _agent()

    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._agent_request",
        new_callable=AsyncMock,
        return_value={"success": True, "tested_mtu": 1450},
    ) as mock_req:
        mtu_ok = await agent_client.test_mtu_on_agent(host, "10.10.10.2", 1450, source_ip="10.10.10.1")
    assert mtu_ok["success"] is True
    _, kwargs = mock_req.await_args
    assert kwargs["json_body"]["source_ip"] == "10.10.10.1"

    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._agent_request",
        new_callable=AsyncMock,
        side_effect=RuntimeError("mtu failed"),
    ):
        mtu_err = await agent_client.test_mtu_on_agent(host, "10.10.10.2", 1450)
    assert mtu_err == {"success": False, "error": "mtu failed"}

    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._agent_request",
        new_callable=AsyncMock,
        return_value={"interfaces": []},
    ):
        details = await agent_client.get_agent_interface_details(host)
    assert details["interfaces"] == []

    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._agent_request",
        new_callable=AsyncMock,
        side_effect=RuntimeError("details failed"),
    ):
        with pytest.raises(RuntimeError):
            await agent_client.get_agent_interface_details(host)

    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._agent_request",
        new_callable=AsyncMock,
        return_value={"success": True, "new_mtu": 1600},
    ):
        mtu_set = await agent_client.set_agent_interface_mtu(host, "eth1", 1600)
    assert mtu_set["success"] is True

    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._agent_request",
        new_callable=AsyncMock,
        side_effect=RuntimeError("set mtu failed"),
    ):
        mtu_set_err = await agent_client.set_agent_interface_mtu(host, "eth1", 1600, persist=False)
    assert mtu_set_err["success"] is False
    assert mtu_set_err["new_mtu"] == 1600

    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._agent_request",
        new_callable=AsyncMock,
        return_value={"success": True, "interface_name": "eth1.100"},
    ) as mock_req:
        provisioned = await agent_client.provision_interface_on_agent(
            host,
            action="create_subinterface",
            parent_interface="eth1",
            vlan_id=100,
            ip_cidr="10.0.0.1/24",
            attach_to_ovs=True,
        )
    assert provisioned["success"] is True
    _, kwargs = mock_req.await_args
    assert "name" not in kwargs["json_body"]
    assert kwargs["json_body"]["parent_interface"] == "eth1"

    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._agent_request",
        new_callable=AsyncMock,
        side_effect=RuntimeError("provision failed"),
    ):
        provision_err = await agent_client.provision_interface_on_agent(host, action="configure", name="eth2")
    assert provision_err == {"success": False, "error": "provision failed"}


@pytest.mark.asyncio
async def test_link_and_ovs_wrappers_success_false_and_exception_branches():
    host = _agent()

    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._agent_request", new_callable=AsyncMock
    ) as mock_req:
        mock_req.side_effect = [
            {"success": True, "link": {"link_id": "a-b"}},
            {"success": False, "error": "already disconnected"},
            {"links": [{"id": "a-b"}]},
            {"bridge_name": "arch-ovs", "initialized": True, "ports": [], "links": []},
            {"boot_logs": {"r1": "booting"}},
            {"bridge": "arch-ovs", "flows": "table=0"},
        ]
        created = await agent_client.create_link_on_agent(host, "lab-1", "r1", "eth1", "r2", "eth1")
        deleted = await agent_client.delete_link_on_agent(host, "lab-1", "r1:eth1-r2:eth1")
        listed = await agent_client.list_links_on_agent(host, "lab-1")
        ovs = await agent_client.get_ovs_status_from_agent(host)
        boots = await agent_client.get_agent_boot_logs(host, "lab-1")
        flows = await agent_client.get_agent_ovs_flows(host)

    assert created["success"] is True
    assert deleted["success"] is False
    assert listed["links"][0]["id"] == "a-b"
    assert ovs["initialized"] is True
    assert "r1" in boots["boot_logs"]
    assert "table=0" in flows["flows"]

    missing_lab = await agent_client.get_agent_boot_logs(host)
    assert missing_lab["error"] == "lab_id required"

    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("link failed")
    ):
        assert (await agent_client.create_link_on_agent(host, "lab-1", "r1", "eth1", "r2", "eth1"))["success"] is False
        assert (await agent_client.delete_link_on_agent(host, "lab-1", "r1:eth1-r2:eth1"))["success"] is False
        assert await agent_client.list_links_on_agent(host, "lab-1") == {"links": []}
        assert (await agent_client.get_ovs_status_from_agent(host))["initialized"] is False
        assert (await agent_client.get_agent_boot_logs(host, "lab-1"))["boot_logs"] == {}
        assert "error" in await agent_client.get_agent_ovs_flows(host)


@pytest.mark.asyncio
async def test_external_vlan_and_repair_wrappers():
    host = _agent()

    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._agent_request", new_callable=AsyncMock
    ) as mock_req:
        mock_req.side_effect = [
            {"success": True, "vlan_tag": 3111},
            {"success": False, "error": "in use"},
            {"success": True},
            {"success": False, "error": "busy"},
            {"ports": [{"port_name": "vh1"}]},
            {"vlan_tag": 2222},
            {"success": True},
            {"success": True, "total_endpoints_repaired": 2},
            {"exit_code": 0, "output": "ok"},
        ]
        connected = await agent_client.connect_external_on_agent(
            host,
            "lab-1",
            "r1",
            "eth2",
            "ens192",
            vlan_tag=3111,
        )
        connected_fail = await agent_client.connect_external_on_agent(
            host,
            "lab-1",
            "r1",
            "eth2",
            "ens192",
        )
        detached = await agent_client.detach_external_on_agent(host, "ens192")
        detached_fail = await agent_client.detach_external_on_agent(host, "ens193")
        ports = await agent_client.get_lab_ports_from_agent(host, "lab-1")
        vlan = await agent_client.get_interface_vlan_from_agent(
            host, "lab-1", "r1", "eth1", read_from_ovs=True
        )
        set_ok = await agent_client.set_port_vlan_on_agent(host, "vh1", 3333)
        repaired = await agent_client.repair_endpoints_on_agent(host, "lab-1", nodes=["r1"])
        cmd = await agent_client.exec_node_on_agent(host, "lab-1", "r1", "show ver")

    assert connected["success"] is True
    assert connected_fail["success"] is False
    assert detached["success"] is True
    assert detached_fail["success"] is False
    assert ports == [{"port_name": "vh1"}]
    assert vlan == 2222
    assert set_ok is True
    assert repaired["total_endpoints_repaired"] == 2
    assert cmd["exit_code"] == 0
    assert mock_req.await_count == 9

    with patch("app.agent_client.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("agent err")
    ):
        assert await agent_client.get_lab_ports_from_agent(host, "lab-1") == []
        assert await agent_client.get_interface_vlan_from_agent(host, "lab-1", "r1", "eth1") is None
        assert await agent_client.set_port_vlan_on_agent(host, "vh1", 2) is False
        assert (await agent_client.repair_endpoints_on_agent(host, "lab-1"))["success"] is False
        assert (await agent_client.connect_external_on_agent(host, "lab-1", "r1", "eth1", "ens10"))["success"] is False
        assert (await agent_client.detach_external_on_agent(host, "ens10"))["success"] is False
