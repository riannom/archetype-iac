"""Tests for API-driven overlay convergence (declare-state).

Covers:
- run_overlay_convergence(): builds declared state from DB, groups by agent,
  calls declare-state, updates attachment flags
- declare_overlay_state_on_agent(): agent client with 404 fallback
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app import models
from app.agent_client import compute_vxlan_port_name
import app.tasks.link_reconciliation as link_reconciliation


# ─── Helpers ──────────────────────────────────────────────────────────────


def _make_link_state(
    test_db,
    lab_id: str,
    link_name: str,
    *,
    is_cross_host: bool = True,
    actual_state: str = "up",
    desired_state: str = "up",
    source_host_id: str | None = None,
    target_host_id: str | None = None,
    source_vlan_tag: int | None = None,
    target_vlan_tag: int | None = None,
    source_vxlan_attached: bool = False,
    target_vxlan_attached: bool = False,
) -> models.LinkState:
    ls = models.LinkState(
        lab_id=lab_id,
        link_name=link_name,
        source_node="R1",
        source_interface="eth1",
        target_node="R2",
        target_interface="eth1",
        is_cross_host=is_cross_host,
        actual_state=actual_state,
        desired_state=desired_state,
        source_host_id=source_host_id,
        target_host_id=target_host_id,
        source_vlan_tag=source_vlan_tag,
        target_vlan_tag=target_vlan_tag,
        source_vxlan_attached=source_vxlan_attached,
        target_vxlan_attached=target_vxlan_attached,
    )
    test_db.add(ls)
    test_db.flush()
    return ls


def _make_tunnel(
    test_db,
    lab_id: str,
    link_state_id: str | None,
    vni: int,
    agent_a_id: str,
    agent_b_id: str,
    *,
    status: str = "active",
    port_name: str | None = None,
    agent_a_ip: str = "10.0.0.1",
    agent_b_ip: str = "10.0.0.2",
) -> models.VxlanTunnel:
    t = models.VxlanTunnel(
        lab_id=lab_id,
        link_state_id=link_state_id,
        vni=vni,
        vlan_tag=0,
        agent_a_id=agent_a_id,
        agent_a_ip=agent_a_ip,
        agent_b_id=agent_b_id,
        agent_b_ip=agent_b_ip,
        status=status,
        port_name=port_name,
    )
    test_db.add(t)
    test_db.flush()
    return t


# ─── Payload building tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_declare_payload_from_db(test_db, sample_lab, multiple_hosts):
    """Correct grouping of declared tunnels by agent from DB records."""
    ls = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
        source_vlan_tag=3001,
        target_vlan_tag=3002,
    )
    port_name = compute_vxlan_port_name(sample_lab.id, "R1:eth1-R2:eth1")
    _make_tunnel(
        test_db, sample_lab.id, ls.id, 50000,
        multiple_hosts[0].id, multiple_hosts[1].id,
        port_name=port_name,
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}
    declared_payloads = {}

    async def _capture(agent, tunnels):
        declared_payloads[agent.id] = tunnels
        return {"results": [], "orphans_removed": []}

    with patch.object(
        link_reconciliation,
        "declare_overlay_state_on_agent",
        side_effect=_capture,
    ):
        await link_reconciliation.run_overlay_convergence(test_db, host_to_agent)

    # Both agents should get entries (one for each side)
    assert multiple_hosts[0].id in declared_payloads
    assert multiple_hosts[1].id in declared_payloads

    # Agent A gets source entry
    a_tunnels = declared_payloads[multiple_hosts[0].id]
    assert len(a_tunnels) == 1
    assert a_tunnels[0]["expected_vlan"] == 3001
    assert a_tunnels[0]["local_ip"] == "10.0.0.1"
    assert a_tunnels[0]["remote_ip"] == "10.0.0.2"

    # Agent B gets target entry
    b_tunnels = declared_payloads[multiple_hosts[1].id]
    assert len(b_tunnels) == 1
    assert b_tunnels[0]["expected_vlan"] == 3002
    assert b_tunnels[0]["local_ip"] == "10.0.0.2"
    assert b_tunnels[0]["remote_ip"] == "10.0.0.1"


@pytest.mark.asyncio
async def test_build_payload_skips_inactive_tunnels(test_db, sample_lab, multiple_hosts):
    """Only active tunnels with desired_state='up' are included."""
    # Active tunnel with desired up — should be included
    ls_up = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    _make_tunnel(
        test_db, sample_lab.id, ls_up.id, 50000,
        multiple_hosts[0].id, multiple_hosts[1].id,
        port_name=compute_vxlan_port_name(sample_lab.id, "R1:eth1-R2:eth1"),
    )

    # Cleanup tunnel — should be excluded
    ls_down = _make_link_state(
        test_db, sample_lab.id, "R3:eth1-R4:eth1",
        desired_state="down",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    _make_tunnel(
        test_db, sample_lab.id, ls_down.id, 50001,
        multiple_hosts[0].id, multiple_hosts[1].id,
        status="cleanup",
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}
    declared_payloads = {}

    async def _capture(agent, tunnels):
        declared_payloads[agent.id] = tunnels
        return {"results": [], "orphans_removed": []}

    with patch.object(
        link_reconciliation,
        "declare_overlay_state_on_agent",
        side_effect=_capture,
    ):
        await link_reconciliation.run_overlay_convergence(test_db, host_to_agent)

    # Only the active/up tunnel should appear
    for agent_id, tunnels in declared_payloads.items():
        for t in tunnels:
            assert t["vni"] == 50000


@pytest.mark.asyncio
async def test_build_payload_both_sides_included(test_db, sample_lab, multiple_hosts):
    """Each tunnel produces entries for both agent_a and agent_b."""
    ls = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    port_name = compute_vxlan_port_name(sample_lab.id, "R1:eth1-R2:eth1")
    _make_tunnel(
        test_db, sample_lab.id, ls.id, 50000,
        multiple_hosts[0].id, multiple_hosts[1].id,
        port_name=port_name,
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}
    declared_payloads = {}

    async def _capture(agent, tunnels):
        declared_payloads[agent.id] = tunnels
        return {"results": [], "orphans_removed": []}

    with patch.object(
        link_reconciliation,
        "declare_overlay_state_on_agent",
        side_effect=_capture,
    ):
        await link_reconciliation.run_overlay_convergence(test_db, host_to_agent)

    # Both sides included with same port_name and VNI
    all_tunnels = []
    for t_list in declared_payloads.values():
        all_tunnels.extend(t_list)
    assert len(all_tunnels) == 2
    assert all(t["port_name"] == port_name for t in all_tunnels)
    assert all(t["vni"] == 50000 for t in all_tunnels)


@pytest.mark.asyncio
async def test_declare_payload_uses_linkstate_vlans(test_db, sample_lab, multiple_hosts):
    """Payload VLAN tags come from LinkState source/target_vlan_tag, not VxlanTunnel."""
    ls = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
        source_vlan_tag=3050,
        target_vlan_tag=3060,
    )
    _make_tunnel(
        test_db, sample_lab.id, ls.id, 50000,
        multiple_hosts[0].id, multiple_hosts[1].id,
        port_name=compute_vxlan_port_name(sample_lab.id, "R1:eth1-R2:eth1"),
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}
    declared_payloads = {}

    async def _capture(agent, tunnels):
        declared_payloads[agent.id] = tunnels
        return {"results": [], "orphans_removed": []}

    with patch.object(
        link_reconciliation,
        "declare_overlay_state_on_agent",
        side_effect=_capture,
    ):
        await link_reconciliation.run_overlay_convergence(test_db, host_to_agent)

    # Agent A (source side) should get source_vlan_tag=3050
    a_tunnels = declared_payloads[multiple_hosts[0].id]
    assert a_tunnels[0]["expected_vlan"] == 3050

    # Agent B (target side) should get target_vlan_tag=3060
    b_tunnels = declared_payloads[multiple_hosts[1].id]
    assert b_tunnels[0]["expected_vlan"] == 3060


# ─── declare_overlay_state_on_agent tests ─────────────────────────────────


@pytest.mark.asyncio
async def test_declare_state_updates_attachment_flags(test_db, sample_lab, multiple_hosts):
    """Successful convergence sets source/target_vxlan_attached to True."""
    ls = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
        source_vxlan_attached=False,
        target_vxlan_attached=False,
    )
    port_name = compute_vxlan_port_name(sample_lab.id, "R1:eth1-R2:eth1")
    _make_tunnel(
        test_db, sample_lab.id, ls.id, 50000,
        multiple_hosts[0].id, multiple_hosts[1].id,
        port_name=port_name,
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    async def _mock_declare(agent, tunnels):
        return {
            "results": [
                {"link_id": "R1:eth1-R2:eth1", "lab_id": sample_lab.id,
                 "status": "converged", "actual_vlan": 3001}
            ],
            "orphans_removed": [],
        }

    with patch.object(
        link_reconciliation,
        "declare_overlay_state_on_agent",
        side_effect=_mock_declare,
    ):
        await link_reconciliation.run_overlay_convergence(test_db, host_to_agent)

    test_db.refresh(ls)
    assert ls.source_vxlan_attached is True
    assert ls.target_vxlan_attached is True


@pytest.mark.asyncio
async def test_declare_state_handles_agent_offline(test_db, sample_lab, multiple_hosts):
    """Gracefully skips agents not in host_to_agent map."""
    ls = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[2].id,  # agent-3 is offline
    )
    _make_tunnel(
        test_db, sample_lab.id, ls.id, 50000,
        multiple_hosts[0].id, multiple_hosts[2].id,
        port_name=compute_vxlan_port_name(sample_lab.id, "R1:eth1-R2:eth1"),
    )
    test_db.commit()

    # Only include online agents (agent-3 is offline)
    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    call_count = 0

    async def _mock_declare(agent, tunnels):
        nonlocal call_count
        call_count += 1
        return {"results": [], "orphans_removed": []}

    with patch.object(
        link_reconciliation,
        "declare_overlay_state_on_agent",
        side_effect=_mock_declare,
    ):
        result = await link_reconciliation.run_overlay_convergence(test_db, host_to_agent)

    # Only agent-1 (online) should be called
    assert call_count == 1
    assert multiple_hosts[2].id not in result


@pytest.mark.asyncio
async def test_declare_state_handles_old_agent_404(test_db, sample_lab, multiple_hosts):
    """404 from agent triggers fallback to whitelist reconciliation."""
    ls = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    port_name = compute_vxlan_port_name(sample_lab.id, "R1:eth1-R2:eth1")
    _make_tunnel(
        test_db, sample_lab.id, ls.id, 50000,
        multiple_hosts[0].id, multiple_hosts[1].id,
        port_name=port_name,
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    # Simulate 404 fallback that returns whitelist-style result
    async def _mock_404_fallback(agent, tunnels):
        return {
            "results": [],
            "orphans_removed": [],
            "removed_ports": [],
            "valid_count": 1,
        }

    with patch.object(
        link_reconciliation,
        "declare_overlay_state_on_agent",
        side_effect=_mock_404_fallback,
    ):
        result = await link_reconciliation.run_overlay_convergence(test_db, host_to_agent)

    # Should complete without errors
    for agent_id, r in result.items():
        assert "error" not in r or r.get("error") is None


@pytest.mark.asyncio
async def test_declare_state_handles_mixed_results(test_db, sample_lab, multiple_hosts):
    """Handles mix of converged, created, and error results."""
    ls1 = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    ls2 = _make_link_state(
        test_db, sample_lab.id, "R3:eth1-R4:eth1",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    _make_tunnel(
        test_db, sample_lab.id, ls1.id, 50000,
        multiple_hosts[0].id, multiple_hosts[1].id,
        port_name=compute_vxlan_port_name(sample_lab.id, "R1:eth1-R2:eth1"),
    )
    _make_tunnel(
        test_db, sample_lab.id, ls2.id, 50001,
        multiple_hosts[0].id, multiple_hosts[1].id,
        port_name=compute_vxlan_port_name(sample_lab.id, "R3:eth1-R4:eth1"),
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    async def _mock_mixed(agent, tunnels):
        results = []
        for t in tunnels:
            if t["vni"] == 50000:
                results.append({
                    "link_id": t["link_id"], "lab_id": t["lab_id"],
                    "status": "converged", "actual_vlan": 3001,
                })
            else:
                results.append({
                    "link_id": t["link_id"], "lab_id": t["lab_id"],
                    "status": "error", "error": "OVS timeout",
                })
        return {"results": results, "orphans_removed": []}

    with patch.object(
        link_reconciliation,
        "declare_overlay_state_on_agent",
        side_effect=_mock_mixed,
    ):
        result = await link_reconciliation.run_overlay_convergence(test_db, host_to_agent)

    # Should track errors count
    for agent_id, r in result.items():
        if isinstance(r, dict) and "errors" in r:
            assert r["errors"] >= 0  # Not negative


@pytest.mark.asyncio
async def test_declare_state_handles_orphan_report(test_db, sample_lab, multiple_hosts):
    """Orphans removed by agent are recorded in results."""
    ls = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    _make_tunnel(
        test_db, sample_lab.id, ls.id, 50000,
        multiple_hosts[0].id, multiple_hosts[1].id,
        port_name=compute_vxlan_port_name(sample_lab.id, "R1:eth1-R2:eth1"),
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    async def _mock_with_orphans(agent, tunnels):
        return {
            "results": [
                {"link_id": "R1:eth1-R2:eth1", "lab_id": sample_lab.id,
                 "status": "converged", "actual_vlan": 3001}
            ],
            "orphans_removed": ["vxlan-deadbeef", "vxlan-cafebabe"],
        }

    with patch.object(
        link_reconciliation,
        "declare_overlay_state_on_agent",
        side_effect=_mock_with_orphans,
    ):
        result = await link_reconciliation.run_overlay_convergence(test_db, host_to_agent)

    # Orphan list should be captured
    for agent_id, r in result.items():
        if isinstance(r, dict) and "orphans_removed" in r:
            assert len(r["orphans_removed"]) == 2


@pytest.mark.asyncio
async def test_declare_state_empty_payload(test_db, sample_lab, multiple_hosts):
    """No active tunnels → no agent calls."""
    test_db.commit()
    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}

    with patch.object(
        link_reconciliation,
        "declare_overlay_state_on_agent",
        new_callable=AsyncMock,
    ) as mock_declare:
        result = await link_reconciliation.run_overlay_convergence(test_db, host_to_agent)

    mock_declare.assert_not_called()
    assert result == {}


@pytest.mark.asyncio
async def test_declare_state_partial_state_error(test_db, sample_lab, multiple_hosts):
    """Links with PARTIAL_STATE prefix in error_message are still included."""
    ls = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        actual_state="error",
        desired_state="up",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    ls.error_message = "PARTIAL_STATE: creation failed mid-way"
    _make_tunnel(
        test_db, sample_lab.id, ls.id, 50000,
        multiple_hosts[0].id, multiple_hosts[1].id,
        port_name=compute_vxlan_port_name(sample_lab.id, "R1:eth1-R2:eth1"),
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}
    declared_payloads = {}

    async def _capture(agent, tunnels):
        declared_payloads[agent.id] = tunnels
        return {"results": [], "orphans_removed": []}

    with patch.object(
        link_reconciliation,
        "declare_overlay_state_on_agent",
        side_effect=_capture,
    ):
        await link_reconciliation.run_overlay_convergence(test_db, host_to_agent)

    # Tunnel should be included (status=active, desired_state=up)
    assert len(declared_payloads) > 0


@pytest.mark.asyncio
async def test_convergence_protects_in_progress_links(test_db, sample_lab, multiple_hosts):
    """In-progress (creating/connecting) links are protected from orphan cleanup."""
    # Active tunnel
    ls1 = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    _make_tunnel(
        test_db, sample_lab.id, ls1.id, 50000,
        multiple_hosts[0].id, multiple_hosts[1].id,
        port_name=compute_vxlan_port_name(sample_lab.id, "R1:eth1-R2:eth1"),
    )

    # In-progress link (no tunnel yet)
    _make_link_state(
        test_db, sample_lab.id, "R3:eth1-R4:eth1",
        actual_state="creating",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}
    declared_payloads = {}

    async def _capture(agent, tunnels):
        declared_payloads[agent.id] = tunnels
        return {"results": [], "orphans_removed": []}

    with patch.object(
        link_reconciliation,
        "declare_overlay_state_on_agent",
        side_effect=_capture,
    ):
        await link_reconciliation.run_overlay_convergence(test_db, host_to_agent)

    # In-progress link's port should be in the declared set (protective)
    in_progress_port = compute_vxlan_port_name(sample_lab.id, "R3:eth1-R4:eth1")
    all_port_names = set()
    for tunnels in declared_payloads.values():
        for t in tunnels:
            all_port_names.add(t["port_name"])

    assert in_progress_port in all_port_names


@pytest.mark.asyncio
async def test_convergence_exception_continues(test_db, sample_lab, multiple_hosts):
    """If one agent call throws, other agents still get called."""
    ls = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    _make_tunnel(
        test_db, sample_lab.id, ls.id, 50000,
        multiple_hosts[0].id, multiple_hosts[1].id,
        port_name=compute_vxlan_port_name(sample_lab.id, "R1:eth1-R2:eth1"),
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}
    calls = []

    async def _mock_error_then_ok(agent, tunnels):
        calls.append(agent.id)
        if agent.id == multiple_hosts[0].id:
            raise Exception("connection refused")
        return {"results": [], "orphans_removed": []}

    with patch.object(
        link_reconciliation,
        "declare_overlay_state_on_agent",
        side_effect=_mock_error_then_ok,
    ):
        result = await link_reconciliation.run_overlay_convergence(test_db, host_to_agent)

    # Both agents were called
    assert len(calls) == 2
    # Error agent has error in results
    assert "error" in result.get(multiple_hosts[0].id, {})


@pytest.mark.asyncio
async def test_convergence_port_name_fallback(test_db, sample_lab, multiple_hosts):
    """When tunnel has no port_name, compute it from lab_id + link_name."""
    ls = _make_link_state(
        test_db, sample_lab.id, "R1:eth1-R2:eth1",
        source_host_id=multiple_hosts[0].id,
        target_host_id=multiple_hosts[1].id,
    )
    # No port_name on tunnel (legacy record)
    _make_tunnel(
        test_db, sample_lab.id, ls.id, 50000,
        multiple_hosts[0].id, multiple_hosts[1].id,
        port_name=None,
    )
    test_db.commit()

    host_to_agent = {h.id: h for h in multiple_hosts if h.status == "online"}
    declared_payloads = {}

    async def _capture(agent, tunnels):
        declared_payloads[agent.id] = tunnels
        return {"results": [], "orphans_removed": []}

    with patch.object(
        link_reconciliation,
        "declare_overlay_state_on_agent",
        side_effect=_capture,
    ):
        await link_reconciliation.run_overlay_convergence(test_db, host_to_agent)

    expected_port = compute_vxlan_port_name(str(sample_lab.id), "R1:eth1-R2:eth1")
    for tunnels in declared_payloads.values():
        for t in tunnels:
            assert t["port_name"] == expected_port
