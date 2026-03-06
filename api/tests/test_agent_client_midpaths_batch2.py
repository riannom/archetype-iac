"""Additional branch coverage for mid-path app.agent_client flows."""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from app import agent_client, models
from app.agent_client import AgentError, AgentUnavailableError
from app.agent_client import http as _ac_http


def _agent(
    agent_id: str = "agent-1",
    *,
    name: str | None = None,
    address: str = "10.0.0.10:8001",
    status: str = "online",
    last_heartbeat: datetime | None = None,
    data_plane_address: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=agent_id,
        name=name or agent_id,
        address=address,
        status=status,
        last_heartbeat=last_heartbeat or datetime.now(timezone.utc),
        data_plane_address=data_plane_address,
        agent_id=agent_id,
        get_capabilities=lambda: {},
    )


@pytest.mark.asyncio
async def test_resolve_agent_ip_paths():
    assert await agent_client.resolve_agent_ip("192.168.1.10:8001") == "192.168.1.10"

    fake_loop = SimpleNamespace(getaddrinfo=AsyncMock(return_value=[(None, None, None, None, ("10.2.3.4", 0))]))
    with patch("app.agent_client.selection.asyncio.get_running_loop", return_value=fake_loop):
        assert await agent_client.resolve_agent_ip("http://my-agent.example:8001") == "10.2.3.4"

    fake_loop_empty = SimpleNamespace(getaddrinfo=AsyncMock(return_value=[]))
    with patch("app.agent_client.selection.asyncio.get_running_loop", return_value=fake_loop_empty):
        assert await agent_client.resolve_agent_ip("agent.local:8001") == "agent.local"

    fake_loop_err = SimpleNamespace(getaddrinfo=AsyncMock(side_effect=socket.gaierror("dns err")))
    with patch("app.agent_client.selection.asyncio.get_running_loop", return_value=fake_loop_err):
        assert await agent_client.resolve_agent_ip("agent.local:8001") == "agent.local"


@pytest.mark.asyncio
async def test_resolve_data_plane_ip_paths():
    db = MagicMock()
    host = _agent(data_plane_address="172.16.0.10")
    assert await agent_client.resolve_data_plane_ip(db, host) == "172.16.0.10"

    db = MagicMock()
    host = _agent(data_plane_address=None)
    iface = SimpleNamespace(ip_address="10.10.10.20/24")
    db.query.return_value.filter.return_value.first.return_value = iface
    resolved = await agent_client.resolve_data_plane_ip(db, host)
    assert resolved == "10.10.10.20"
    assert host.data_plane_address == "10.10.10.20"
    db.commit.assert_called_once()

    db = MagicMock()
    host = _agent(data_plane_address=None)
    db.query.return_value.filter.return_value.first.return_value = iface
    db.commit.side_effect = RuntimeError("commit failed")
    with patch("app.agent_client.selection.resolve_agent_ip", new_callable=AsyncMock, return_value="192.0.2.1"):
        resolved_rollback = await agent_client.resolve_data_plane_ip(db, host)
    assert resolved_rollback == "10.10.10.20"
    db.rollback.assert_called_once()

    db = MagicMock()
    host = _agent(data_plane_address=None, address="agent.lab:8001")
    db.query.return_value.filter.return_value.first.return_value = None
    with patch("app.agent_client.selection.resolve_agent_ip", new_callable=AsyncMock, return_value="198.51.100.5"):
        fallback = await agent_client.resolve_data_plane_ip(db, host)
    assert fallback == "198.51.100.5"


def test_data_plane_mtu_ok():
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [
        SimpleNamespace(source_agent_id="a", target_agent_id="b", tested_mtu=1600),
        SimpleNamespace(source_agent_id="b", target_agent_id="a", tested_mtu=1550),
    ]
    assert agent_client._data_plane_mtu_ok(db, "a", "b", 1500) is True
    assert agent_client._data_plane_mtu_ok(db, "a", "b", 1700) is False


@pytest.mark.asyncio
async def test_close_http_client_and_agent_request_paths(monkeypatch):
    fake_client = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(_ac_http, "_http_client", fake_client)
    await agent_client.close_http_client()
    fake_client.aclose.assert_awaited_once()
    assert _ac_http._http_client is None

    response = MagicMock()
    response.status_code = 204
    response.raise_for_status.return_value = None
    client = SimpleNamespace(request=AsyncMock(return_value=response))
    labels = MagicMock()

    async def _run_once(func, max_retries=None):  # noqa: ARG001
        return await func()

    with patch("app.agent_client.http.get_http_client", return_value=client), patch(
        "app.agent_client.http.with_retry", side_effect=_run_once
    ), patch("app.agent_client.http.agent_operation_duration.labels", return_value=labels):
        result = await agent_client._agent_request(
            "GET",
            "http://agent/health",
            metric_operation="probe",
            metric_host_id="agent-1",
        )
    assert result == {}
    labels.observe.assert_called_once()

    with patch("app.agent_client.http.get_http_client", return_value=client), patch(
        "app.agent_client.http.with_retry", side_effect=RuntimeError("boom")
    ), patch("app.agent_client.http.agent_operation_duration.labels", side_effect=RuntimeError("metrics down")):
        with pytest.raises(RuntimeError):
            await agent_client._agent_request(
                "GET",
                "http://agent/health",
                metric_operation="probe",
                metric_host_id="agent-1",
            )


@pytest.mark.asyncio
async def test_get_agent_for_node_priorities_and_mark_offline():
    host = _agent("h1")
    db = MagicMock()

    node_q = MagicMock()
    node_q.filter.return_value.first.return_value = SimpleNamespace(host_id="h1")
    placement_q = MagicMock()
    placement_q.filter.return_value.first.return_value = None

    def _query_side_effect(model):
        return node_q if model is models.Node else placement_q

    db.query.side_effect = _query_side_effect
    db.get.side_effect = lambda model, key: host if model is models.Host and key == "h1" else None

    with patch("app.agent_client.selection.is_agent_online", return_value=True):
        selected = await agent_client.get_agent_for_node(db, "lab-1", "n1")
    assert selected == host

    with patch("app.agent_client.selection.is_agent_online", return_value=False):
        none_selected = await agent_client.get_agent_for_node(db, "lab-1", "n1")
    assert none_selected is None

    db = MagicMock()
    node_q = MagicMock()
    node_q.filter.return_value.first.return_value = None
    placement_q = MagicMock()
    placement_q.filter.return_value.first.return_value = None
    db.query.side_effect = lambda model: node_q if model is models.Node else placement_q
    db.get.return_value = None
    with patch("app.agent_client.selection.get_healthy_agent", new_callable=AsyncMock, return_value=host):
        fallback = await agent_client.get_agent_for_node(db, "lab-1", "n1")
    assert fallback == host

    online = _agent("a1", status="online")
    db = MagicMock()
    db.get.return_value = online
    with patch("app.agent_client.selection.asyncio.create_task") as create_task, patch(
        "app.agent_client.selection.emit_agent_offline", new=Mock(return_value=None)
    ):
        await agent_client.mark_agent_offline(db, "a1")
    assert online.status == "offline"
    db.commit.assert_called_once()
    create_task.assert_called_once()


@pytest.mark.asyncio
async def test_deploy_destroy_status_and_reconcile_wrappers():
    host = _agent("h1", name="host-1")
    with patch("app.agent_client.node_ops.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client.node_ops._agent_request", new_callable=AsyncMock, return_value={"status": "ok"}
    ):
        deploy = await agent_client.deploy_to_agent(host, "job-1", "lab-1", topology={"nodes": []})
        destroy = await agent_client.destroy_on_agent(host, "job-1", "lab-1")
        status = await agent_client.get_lab_status_from_agent(host, "lab-1")
        reconcile = await agent_client.reconcile_nodes_on_agent(host, "lab-1", [{"container_name": "n1"}])

    assert deploy["status"] == "ok"
    assert destroy["status"] == "ok"
    assert status["status"] == "ok"
    assert reconcile["status"] == "ok"

    with pytest.raises(ValueError):
        await agent_client.deploy_to_agent(host, "job-1", "lab-1", topology=None)

    with patch("app.agent_client.node_ops.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client.node_ops._agent_request",
        new_callable=AsyncMock,
        side_effect=AgentError("deploy failed", agent_id=None),
    ) as req:
        with pytest.raises(AgentError) as deploy_err:
            await agent_client.deploy_to_agent(host, "job-1", "lab-1", topology={"nodes": []})
    assert deploy_err.value.agent_id == host.id
    assert req.await_count == 1

    with patch("app.agent_client.node_ops.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client.node_ops._agent_request",
        new_callable=AsyncMock,
        side_effect=AgentError("destroy failed", agent_id=None),
    ):
        with pytest.raises(AgentError):
            await agent_client.destroy_on_agent(host, "job-1", "lab-1")

    with patch("app.agent_client.node_ops.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client.node_ops._agent_request",
        new_callable=AsyncMock,
        side_effect=AgentError("status failed", agent_id=None),
    ):
        with pytest.raises(AgentError):
            await agent_client.get_lab_status_from_agent(host, "lab-1")

    with patch("app.agent_client.node_ops.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client.node_ops._agent_request",
        new_callable=AsyncMock,
        side_effect=AgentError("reconcile failed", agent_id="h1"),
    ):
        with pytest.raises(AgentError):
            await agent_client.reconcile_nodes_on_agent(host, "lab-1", [])


@pytest.mark.asyncio
async def test_readiness_runtime_and_agent_lookup_paths():
    host = _agent("h1")
    with patch("app.agent_client.node_ops.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client.node_ops._agent_request",
        new_callable=AsyncMock,
        side_effect=[{"is_ready": True}, {"runtime": {"provider": "docker"}}],
    ):
        readiness = await agent_client.check_node_readiness(
            host, "lab-1", "n1", kind="ios", provider_type="libvirt"
        )
        runtime = await agent_client.get_node_runtime_profile(host, "lab-1", "n1", provider_type="docker")

    assert readiness["is_ready"] is True
    assert runtime["runtime"]["provider"] == "docker"

    with patch("app.agent_client.node_ops.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client.node_ops._agent_request",
        new_callable=AsyncMock,
        side_effect=RuntimeError("down"),
    ):
        failed = await agent_client.check_node_readiness(host, "lab-1", "n1")
    assert failed["is_ready"] is False
    assert "Readiness check failed" in failed["message"]

    db = MagicMock()
    db.query.return_value.all.return_value = [host]
    assert await agent_client.get_all_agents(db) == [host]

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    assert await agent_client.get_agent_by_name(db, "missing") is None

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = host
    with patch("app.agent_client.selection.get_agent_providers", return_value=["docker"]):
        assert await agent_client.get_agent_by_name(db, "h1", required_provider="libvirt") is None
        assert await agent_client.get_agent_by_name(db, "h1", required_provider="docker") == host


@pytest.mark.asyncio
async def test_update_stale_and_agent_discovery_wrappers():
    stale = _agent("stale-1", name="stale", status="online")
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [stale]
    with patch("app.agent_client.selection.asyncio.create_task") as create_task, patch(
        "app.agent_client.selection.emit_agent_offline", new=Mock(return_value=None)
    ):
        marked = await agent_client.update_stale_agents(db)
    assert marked == ["stale-1"]
    assert stale.status == "offline"
    db.commit.assert_called_once()
    create_task.assert_called_once()

    host = _agent("h1")
    with patch("app.agent_client.node_ops._safe_agent_request", new_callable=AsyncMock) as node_ops_req, \
         patch("app.agent_client.selection._safe_agent_request", new_callable=AsyncMock) as sel_req:
        node_ops_req.side_effect = [
            {"locks": []},
            {"status": "cleared"},
            {"labs": [{"id": "lab-1"}]},
            {"removed_containers": []},
        ]
        sel_req.return_value = {"capacity_ok": True}
        lock_status = await agent_client.get_agent_lock_status(host)
        lock_release = await agent_client.release_agent_lock(host, "lab-1")
        capacity = await agent_client.query_agent_capacity(host)
        discovered = await agent_client.discover_labs_on_agent(host)
        cleaned = await agent_client.cleanup_orphans_on_agent(host, ["lab-1"])

    assert lock_status["locks"] == []
    assert lock_release["status"] == "cleared"
    assert capacity["capacity_ok"] is True
    assert discovered["labs"][0]["id"] == "lab-1"
    assert cleaned["removed_containers"] == []

    with patch("app.agent_client.selection._agent_request", new_callable=AsyncMock, return_value={"ok": True}):
        assert await agent_client.ping_agent(host) is True
    with patch("app.agent_client.selection._agent_request", new_callable=AsyncMock, side_effect=RuntimeError("unreachable")):
        with pytest.raises(AgentUnavailableError):
            await agent_client.ping_agent(host)


@pytest.mark.asyncio
async def test_overlay_state_and_overlay_attach_detach_wrappers():
    host = _agent("h1", name="host-1")

    with patch("app.agent_client.overlay.get_agent_url", return_value="http://agent"), patch(
        "app.agent_client.overlay._agent_request",
        new_callable=AsyncMock,
        side_effect=[
            # declare_overlay_state_on_agent #1 (success)
            {"results": [{"ok": True}]},
            # declare_overlay_state_on_agent #2 (404 -> fallback to reconcile mock)
            RuntimeError("404 Not Found"),
            # declare_overlay_state_on_agent #3 (generic error)
            RuntimeError("boom"),
            # attach_container_on_agent #1 (success)
            {"success": True},
            # attach_container_on_agent #2 (failure)
            {"success": False, "error": "failed"},
            # attach_container_on_agent #3 (exception)
            RuntimeError("attach down"),
            # cleanup_overlay_on_agent #1 (success)
            {"tunnels_deleted": 2, "bridges_deleted": 1},
            # cleanup_overlay_on_agent #2 (exception)
            RuntimeError("overlay down"),
            # get_cleanup_audit_from_agent #1 (success)
            {"network": {"containers": []}},
            # get_cleanup_audit_from_agent #2 (exception)
            RuntimeError("audit down"),
            # attach_overlay_interface_on_agent #1 (success)
            {"success": True, "local_vlan": 110},
            # attach_overlay_interface_on_agent #2 (failure)
            {"success": False, "error": "bad attach"},
            # attach_overlay_interface_on_agent #3 (exception)
            RuntimeError("attach-link down"),
            # detach_overlay_interface_on_agent #1 (success)
            {"success": True, "interface_isolated": True, "new_vlan": 200, "tunnel_deleted": True},
            # detach_overlay_interface_on_agent #2 (failure)
            {"success": False, "error": "bad detach"},
            # detach_overlay_interface_on_agent #3 (exception)
            RuntimeError("detach-link down"),
        ],
    ), patch(
        "app.agent_client.overlay.reconcile_vxlan_ports_on_agent",
        new_callable=AsyncMock,
        return_value={"fallback": True},
    ), patch(
        "app.agent_client.links.get_agent_url", return_value="http://agent"
    ), patch(
        "app.agent_client.links._agent_request",
        new_callable=AsyncMock,
        side_effect=[
            {"ports": [{"name": "vh1"}]},
            RuntimeError("state down"),
            {"results": [{"link_name": "l1"}]},
            RuntimeError("declare down"),
        ],
    ), patch(
        "app.agent_client.node_ops.get_agent_url", return_value="http://agent"
    ), patch(
        "app.agent_client.node_ops._agent_request",
        new_callable=AsyncMock,
        side_effect=[
            {"removed_containers": ["c1"], "kept_containers": []},
            RuntimeError("cleanup down"),
            {"status": "ok"},
            RuntimeError("destroy down"),
        ],
    ):
        declared = await agent_client.declare_overlay_state_on_agent(host, [{"port_name": "vxlan-a"}], ["lab-1"])
        fallback = await agent_client.declare_overlay_state_on_agent(host, [{"port_name": "vxlan-b"}])
        failed_decl = await agent_client.declare_overlay_state_on_agent(host, [{"port_name": "vxlan-c"}])
        port_state = await agent_client.get_lab_port_state(host, "lab-1")
        port_state_fail = await agent_client.get_lab_port_state(host, "lab-1")
        declared_ports = await agent_client.declare_port_state_on_agent(host, [{"port_a": "vh1"}])
        declared_ports_fail = await agent_client.declare_port_state_on_agent(host, [])
        orphan_ok = await agent_client.cleanup_lab_orphans(host, "lab-1", ["n1"])
        orphan_fail = await agent_client.cleanup_lab_orphans(host, "lab-1", ["n1"])
        attached_ok = await agent_client.attach_container_on_agent(host, "lab-1", "link-1", "n1", "eth1", "10.0.0.1/24")
        attached_fail = await agent_client.attach_container_on_agent(host, "lab-1", "link-1", "n1", "eth1")
        attached_exc = await agent_client.attach_container_on_agent(host, "lab-1", "link-1", "n1", "eth1")
        cleanup_ok = await agent_client.cleanup_overlay_on_agent(host, "lab-1")
        cleanup_fail = await agent_client.cleanup_overlay_on_agent(host, "lab-1")
        audit_ok = await agent_client.get_cleanup_audit_from_agent(host, include_ovs=True)
        audit_fail = await agent_client.get_cleanup_audit_from_agent(host)
        attach_link_ok = await agent_client.attach_overlay_interface_on_agent(
            host, "lab-1", "n1", "eth1", 7001, "10.0.0.1", "10.0.0.2", "link-1"
        )
        attach_link_fail = await agent_client.attach_overlay_interface_on_agent(
            host, "lab-1", "n1", "eth1", 7001, "10.0.0.1", "10.0.0.2", "link-1"
        )
        attach_link_exc = await agent_client.attach_overlay_interface_on_agent(
            host, "lab-1", "n1", "eth1", 7001, "10.0.0.1", "10.0.0.2", "link-1"
        )
        detach_link_ok = await agent_client.detach_overlay_interface_on_agent(host, "lab-1", "n1", "eth1", "link-1")
        detach_link_fail = await agent_client.detach_overlay_interface_on_agent(host, "lab-1", "n1", "eth1", "link-1")
        detach_link_exc = await agent_client.detach_overlay_interface_on_agent(host, "lab-1", "n1", "eth1", "link-1")
        destroy_lab = await agent_client.destroy_lab_on_agent(host, "lab-1")
        destroy_lab_fail = await agent_client.destroy_lab_on_agent(host, "lab-1")

    assert declared["results"][0]["ok"] is True
    assert fallback["fallback"] is True
    assert failed_decl["results"] == []
    assert port_state == [{"name": "vh1"}]
    assert port_state_fail == []
    assert declared_ports["results"][0]["link_name"] == "l1"
    assert "error" in declared_ports_fail
    assert orphan_ok["removed_containers"] == ["c1"]
    assert orphan_fail["removed_containers"] == []
    assert attached_ok["success"] is True
    assert attached_fail["success"] is False
    assert attached_exc["success"] is False
    assert cleanup_ok["tunnels_deleted"] == 2
    assert cleanup_fail["tunnels_deleted"] == 0
    assert "network" in audit_ok
    assert "errors" in audit_fail
    assert attach_link_ok["success"] is True
    assert attach_link_fail["success"] is False
    assert attach_link_exc["success"] is False
    assert detach_link_ok["success"] is True
    assert detach_link_fail["success"] is False
    assert detach_link_exc["success"] is False
    assert destroy_lab["status"] == "ok"
    assert destroy_lab_fail["status"] == "failed"


@pytest.mark.asyncio
async def test_setup_cross_host_link_v2_success_and_partial_rollback():
    db = MagicMock()
    a = _agent("a", name="agent-a", address="a.example:8001")
    b = _agent("b", name="agent-b", address="b.example:8001")

    with patch("app.services.link_manager.allocate_vni", return_value=7010), patch(
        "app.routers.infrastructure.get_or_create_settings",
        return_value=SimpleNamespace(overlay_mtu=1500),
    ), patch("app.agent_client.overlay._data_plane_mtu_ok", return_value=True), patch(
        "app.agent_client.overlay.resolve_data_plane_ip",
        new_callable=AsyncMock,
        side_effect=["10.0.0.1", "10.0.0.2"],
    ), patch(
        "app.agent_client.overlay.attach_overlay_interface_on_agent",
        new_callable=AsyncMock,
        side_effect=[
            {"success": True, "local_vlan": 110},
            {"success": True, "local_vlan": 120},
        ],
    ):
        ok = await agent_client.setup_cross_host_link_v2(
            db, "lab-1", "link-1", a, b, "n1", "eth1", "n2", "eth1"
        )
    assert ok["success"] is True
    assert ok["local_vlans"] == {"a": 110, "b": 120}

    attach_calls = {"n1": 0, "n2": 0}

    async def _attach_with_one_retry(agent, **kwargs):
        _ = agent
        node = kwargs["container_name"]
        attach_calls[node] += 1
        if node == "n1":
            return {"success": True, "local_vlan": 210}
        if attach_calls[node] == 1:
            return {"success": False, "error": "container not running yet"}
        return {"success": False, "error": "attach failed"}

    with patch("app.services.link_manager.allocate_vni", return_value=7020), patch(
        "app.routers.infrastructure.get_or_create_settings",
        return_value=SimpleNamespace(overlay_mtu=1500),
    ), patch("app.agent_client.overlay._data_plane_mtu_ok", return_value=False), patch(
        "app.agent_client.overlay.resolve_agent_ip",
        new_callable=AsyncMock,
        side_effect=["192.0.2.1", "192.0.2.2"],
    ), patch(
        "app.agent_client.overlay.attach_overlay_interface_on_agent",
        new_callable=AsyncMock,
        side_effect=_attach_with_one_retry,
    ), patch(
        "app.agent_client.overlay.detach_overlay_interface_on_agent",
        new_callable=AsyncMock,
        side_effect=RuntimeError("rollback failed"),
    ), patch("app.agent_client.overlay.asyncio.sleep", new_callable=AsyncMock):
        partial = await agent_client.setup_cross_host_link_v2(
            db, "lab-1", "link-1", a, b, "n1", "eth1", "n2", "eth1"
        )

    assert partial["success"] is False
    assert partial["partial_state"] is True
    assert partial["agents_with_state"] == ["a"]
