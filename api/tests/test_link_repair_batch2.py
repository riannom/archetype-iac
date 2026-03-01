"""Additional branch coverage for app.tasks.link_repair."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.tasks.link_repair as lr


def _host(host_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=host_id,
        name=name,
        data_plane_address=None,
        address=f"{name}.local:8001",
    )


def _link(*, cross_host: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        id="ls-1",
        link_name="r1:eth1-r2:eth1",
        is_cross_host=cross_host,
        source_host_id="h1",
        target_host_id="h2",
        lab_id="lab-1",
        source_node="r1",
        target_node="r2",
        source_interface="eth1",
        target_interface="eth1",
        vni=None,
        source_vxlan_attached=False,
        target_vxlan_attached=False,
        source_vlan_tag=2101,
        target_vlan_tag=2102,
        vlan_tag=100,
        actual_state="error",
        error_message="old",
        source_carrier_state="off",
        target_carrier_state="off",
    )


@pytest.mark.asyncio
async def test_attempt_partial_recovery_guardrails(monkeypatch):
    session = object()
    link = _link(cross_host=False)

    monkeypatch.setattr(lr, "get_link_state_by_id_for_update", lambda *_args, **_kwargs: link)
    out = await lr.attempt_partial_recovery(session, link, {})
    assert out is False

    link = _link(cross_host=True)
    monkeypatch.setattr(lr, "get_link_state_by_id_for_update", lambda *_args, **_kwargs: link)
    out = await lr.attempt_partial_recovery(session, link, {"h1": _host("h1", "a")})
    assert out is False


@pytest.mark.asyncio
async def test_attempt_partial_recovery_attach_and_failure_paths(monkeypatch):
    session = object()
    link = _link(cross_host=True)

    monkeypatch.setattr(lr, "get_link_state_by_id_for_update", lambda *_args, **_kwargs: link)
    monkeypatch.setattr("app.agent_client.resolve_data_plane_ip", AsyncMock(return_value="10.0.0.1"))
    monkeypatch.setattr("app.services.link_manager.allocate_vni", lambda *_args, **_kwargs: 4242)
    monkeypatch.setattr("app.routers.infrastructure.get_or_create_settings", lambda _session: SimpleNamespace(overlay_mtu=1450))
    monkeypatch.setattr(lr, "normalize_for_node", lambda *_args, **_kwargs: "Ethernet1")
    monkeypatch.setattr(lr, "_sync_oper_state", lambda *_args, **_kwargs: None)

    # Happy path: both sides attached.
    monkeypatch.setattr(
        lr.agent_client,
        "attach_overlay_interface_on_agent",
        AsyncMock(side_effect=[{"success": True, "local_vlan": 3001}, {"success": True, "local_vlan": 3002}]),
    )
    host_to_agent = {"h1": _host("h1", "agent-a"), "h2": _host("h2", "agent-b")}
    ok = await lr.attempt_partial_recovery(session, link, host_to_agent)
    assert ok is True
    assert link.actual_state == "up"
    assert link.source_vxlan_attached is True
    assert link.target_vxlan_attached is True
    assert link.source_vlan_tag == 3001
    assert link.target_vlan_tag == 3002

    # Failure path: one side fails attach.
    link2 = _link(cross_host=True)
    monkeypatch.setattr(lr, "get_link_state_by_id_for_update", lambda *_args, **_kwargs: link2)
    monkeypatch.setattr(
        lr.agent_client,
        "attach_overlay_interface_on_agent",
        AsyncMock(side_effect=[{"success": False, "error": "src fail"}, {"success": True, "local_vlan": 3002}]),
    )
    failed = await lr.attempt_partial_recovery(session, link2, host_to_agent)
    assert failed is False
    assert "source=failed" in (link2.error_message or "")

    # Exception path is swallowed and treated as failure.
    link3 = _link(cross_host=True)
    monkeypatch.setattr(lr, "get_link_state_by_id_for_update", lambda *_args, **_kwargs: link3)
    monkeypatch.setattr(
        lr.agent_client,
        "attach_overlay_interface_on_agent",
        AsyncMock(side_effect=[RuntimeError("boom"), {"success": True, "local_vlan": 3002}]),
    )
    failed_exc = await lr.attempt_partial_recovery(session, link3, host_to_agent)
    assert failed_exc is False
    assert "source=failed" in (link3.error_message or "")


@pytest.mark.asyncio
async def test_attempt_partial_recovery_attached_validation_paths(monkeypatch):
    session = object()
    link = _link(cross_host=True)
    link.source_vxlan_attached = True
    link.target_vxlan_attached = True

    monkeypatch.setattr(lr, "get_link_state_by_id_for_update", lambda *_args, **_kwargs: link)
    monkeypatch.setattr("app.agent_client.resolve_data_plane_ip", AsyncMock(return_value="10.0.0.1"))
    monkeypatch.setattr("app.services.link_manager.allocate_vni", lambda *_args, **_kwargs: 4242)
    monkeypatch.setattr("app.routers.infrastructure.get_or_create_settings", lambda _session: SimpleNamespace(overlay_mtu=1450))
    monkeypatch.setattr(lr, "normalize_for_node", lambda *_args, **_kwargs: "Ethernet1")
    monkeypatch.setattr(lr, "_sync_oper_state", lambda *_args, **_kwargs: None)

    monkeypatch.setattr(lr, "verify_link_connected", AsyncMock(return_value=(True, None)))
    ok = await lr.attempt_partial_recovery(session, link, {"h1": _host("h1", "a"), "h2": _host("h2", "b")})
    assert ok is True
    assert link.actual_state == "up"

    link.actual_state = "error"
    monkeypatch.setattr(lr, "verify_link_connected", AsyncMock(return_value=(False, "missing tunnel")))
    bad = await lr.attempt_partial_recovery(session, link, {"h1": _host("h1", "a"), "h2": _host("h2", "b")})
    assert bad is False
    assert "validation failed" in (link.error_message or "").lower()


@pytest.mark.asyncio
async def test_attempt_vlan_repair_dispatch_and_exception(monkeypatch):
    session = object()
    link = _link(cross_host=False)

    monkeypatch.setattr(lr, "get_link_state_by_id_for_update", lambda *_args, **_kwargs: link)
    monkeypatch.setattr(lr, "_repair_same_host_vlan", AsyncMock(return_value=True))
    monkeypatch.setattr(lr, "_repair_cross_host_vlan", AsyncMock(return_value=False))

    assert await lr.attempt_vlan_repair(session, link, {}) is True

    link_x = _link(cross_host=True)
    monkeypatch.setattr(lr, "get_link_state_by_id_for_update", lambda *_args, **_kwargs: link_x)
    assert await lr.attempt_vlan_repair(session, link_x, {}) is False

    monkeypatch.setattr(lr, "_repair_cross_host_vlan", AsyncMock(side_effect=RuntimeError("repair crash")))
    assert await lr.attempt_vlan_repair(session, link_x, {}) is False

    monkeypatch.setattr(lr, "get_link_state_by_id_for_update", lambda *_args, **_kwargs: None)
    assert await lr.attempt_vlan_repair(session, link_x, {}) is False


@pytest.mark.asyncio
async def test_repair_same_host_vlan_paths(monkeypatch):
    session = object()
    link = _link(cross_host=False)

    # Missing agent.
    assert await lr._repair_same_host_vlan(session, link, {}) is False

    monkeypatch.setattr(lr, "normalize_for_node", lambda *_args, **_kwargs: "eth1")

    # Missing VLAN reads.
    monkeypatch.setattr(lr.agent_client, "get_interface_vlan_from_agent", AsyncMock(side_effect=[None, 100]))
    assert await lr._repair_same_host_vlan(session, link, {"h1": _host("h1", "agent")}) is False

    # Equal VLAN updates DB fields.
    monkeypatch.setattr(lr.agent_client, "get_interface_vlan_from_agent", AsyncMock(side_effect=[2222, 2222]))
    assert await lr._repair_same_host_vlan(session, link, {"h1": _host("h1", "agent")}) is True
    assert link.vlan_tag == 2222
    assert link.source_vlan_tag == 2222
    assert link.target_vlan_tag == 2222

    # Drifted VLAN repaired via create_link_on_agent.
    monkeypatch.setattr(lr.agent_client, "get_interface_vlan_from_agent", AsyncMock(side_effect=[1111, 2222]))
    monkeypatch.setattr(lr.agent_client, "create_link_on_agent", AsyncMock(return_value={"success": True, "link": {"vlan_tag": 3333}}))
    assert await lr._repair_same_host_vlan(session, link, {"h1": _host("h1", "agent")}) is True
    assert link.vlan_tag == 3333

    monkeypatch.setattr(lr.agent_client, "get_interface_vlan_from_agent", AsyncMock(side_effect=[1111, 2222]))
    monkeypatch.setattr(lr.agent_client, "create_link_on_agent", AsyncMock(return_value={"success": False}))
    assert await lr._repair_same_host_vlan(session, link, {"h1": _host("h1", "agent")}) is False


@pytest.mark.asyncio
async def test_repair_cross_host_vlan_paths(monkeypatch):
    session = object()
    link = _link(cross_host=True)
    source = _host("h1", "agent-a")
    target = _host("h2", "agent-b")

    # Missing agents.
    assert await lr._repair_cross_host_vlan(session, link, {"h1": source}) is False

    monkeypatch.setattr(lr.agent_client, "compute_vxlan_port_name", lambda *_args, **_kwargs: "vxlan-4242")
    monkeypatch.setattr(lr, "normalize_for_node", lambda *_args, **_kwargs: "eth1")

    # Missing DB VLAN for one side -> false, but still processes loop.
    link_missing = _link(cross_host=True)
    link_missing.source_vlan_tag = None
    monkeypatch.setattr(lr.agent_client, "get_lab_port_state", AsyncMock(return_value=[]))
    monkeypatch.setattr(lr.agent_client, "set_port_vlan_on_agent", AsyncMock(return_value=True))
    assert await lr._repair_cross_host_vlan(session, link_missing, {"h1": source, "h2": target}) is False

    # No container port discovered -> false.
    monkeypatch.setattr(lr.agent_client, "get_lab_port_state", AsyncMock(return_value=[]))
    assert await lr._repair_cross_host_vlan(session, link, {"h1": source, "h2": target}) is False

    # Container/tunnel set failures -> false.
    ports = [{"node_name": "r1", "interface_name": "eth1", "ovs_port_name": "vh-r1-eth1"}]
    monkeypatch.setattr(lr.agent_client, "get_lab_port_state", AsyncMock(return_value=ports))
    monkeypatch.setattr(lr.agent_client, "set_port_vlan_on_agent", AsyncMock(side_effect=[False, True, True, False]))
    assert await lr._repair_cross_host_vlan(session, link, {"h1": source, "h2": target}) is False

    # Success on both sides.
    ports_src = [{"node_name": "r1", "interface_name": "eth1", "ovs_port_name": "vh-r1-eth1"}]
    ports_tgt = [{"node_name": "r2", "interface_name": "eth1", "ovs_port_name": "vh-r2-eth1"}]
    monkeypatch.setattr(lr.agent_client, "get_lab_port_state", AsyncMock(side_effect=[ports_src, ports_tgt]))
    monkeypatch.setattr(lr.agent_client, "set_port_vlan_on_agent", AsyncMock(return_value=True))
    assert await lr._repair_cross_host_vlan(session, link, {"h1": source, "h2": target}) is True


@pytest.mark.asyncio
async def test_attempt_link_repair_paths(monkeypatch):
    session = object()
    link = _link(cross_host=True)

    monkeypatch.setattr(lr, "get_link_state_by_id_for_update", lambda *_args, **_kwargs: None)
    assert await lr.attempt_link_repair(session, link, {}) is False

    monkeypatch.setattr(lr, "get_link_state_by_id_for_update", lambda *_args, **_kwargs: link)
    monkeypatch.setattr(lr, "create_cross_host_link", AsyncMock(return_value=True))
    assert await lr.attempt_link_repair(session, link, {"h1": _host("h1", "a"), "h2": _host("h2", "b")}) is True

    link_same = _link(cross_host=False)
    monkeypatch.setattr(lr, "get_link_state_by_id_for_update", lambda *_args, **_kwargs: link_same)
    monkeypatch.setattr(lr, "create_same_host_link", AsyncMock(return_value=False))
    assert await lr.attempt_link_repair(session, link_same, {"h1": _host("h1", "a")}) is False

    monkeypatch.setattr(lr, "create_same_host_link", AsyncMock(side_effect=RuntimeError("repair failed")))
    assert await lr.attempt_link_repair(session, link_same, {"h1": _host("h1", "a")}) is False
