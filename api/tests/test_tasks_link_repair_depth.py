"""Deep branch-coverage tests for app.tasks.link_repair.

Supplements test_link_repair.py and test_link_repair_batch2.py with
scenarios NOT already covered: VLAN mismatch nuances, carrier state
propagation, cross-host partial-recovery edge cases, agent unreachable
paths, idempotency, and the _sync_oper_state helper.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.tasks.link_repair as lr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _host(host_id: str, name: str = "agent") -> SimpleNamespace:
    return SimpleNamespace(
        id=host_id,
        name=name,
        data_plane_address=None,
        address=f"{name}.local:8001",
    )


def _link(*, cross_host: bool = True, **overrides) -> SimpleNamespace:
    defaults = dict(
        id="ls-1",
        link_name="r1:eth1-r2:eth1",
        is_cross_host=cross_host,
        source_host_id="h1",
        target_host_id="h2" if cross_host else "h1",
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
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _common_monkeypatches(monkeypatch):
    """Wire up the common stubs needed by most repair functions."""
    monkeypatch.setattr(lr, "normalize_for_node", lambda *_a, **_kw: "eth1")
    monkeypatch.setattr(lr, "_sync_oper_state", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        "app.agent_client.resolve_data_plane_ip",
        AsyncMock(return_value="10.0.0.1"),
    )
    monkeypatch.setattr(
        "app.services.link_manager.allocate_vni",
        lambda *_a, **_kw: 5000,
    )
    monkeypatch.setattr(
        "app.routers.infrastructure.get_or_create_settings",
        lambda _s: SimpleNamespace(overlay_mtu=1450),
    )


# ===========================================================================
# 1. _sync_oper_state delegation
# ===========================================================================

class TestSyncOperState:
    def test_delegates_to_recompute(self, monkeypatch):
        """_sync_oper_state must forward to recompute_link_oper_state."""
        mock_recompute = MagicMock()
        monkeypatch.setattr(lr, "recompute_link_oper_state", mock_recompute)

        session = object()
        link = _link()
        lr._sync_oper_state(session, link)
        mock_recompute.assert_called_once_with(session, link)


# ===========================================================================
# 2. VLAN mismatch repair — same-host
# ===========================================================================

class TestSameHostVlanRepair:

    @pytest.mark.asyncio
    async def test_create_link_on_agent_returns_no_link_key(self, monkeypatch):
        """When agent response has no 'link' sub-object the vlan_tag
        is recorded as None rather than raising."""
        session = object()
        link = _link(cross_host=False)

        monkeypatch.setattr(lr, "normalize_for_node", lambda *_a, **_kw: "eth1")
        monkeypatch.setattr(
            lr.agent_client,
            "get_interface_vlan_from_agent",
            AsyncMock(side_effect=[111, 222]),
        )
        monkeypatch.setattr(
            lr.agent_client,
            "create_link_on_agent",
            AsyncMock(return_value={"success": True}),
        )

        ok = await lr._repair_same_host_vlan(
            session, link, {"h1": _host("h1")}
        )
        assert ok is True
        assert link.vlan_tag is None
        assert link.source_vlan_tag is None
        assert link.target_vlan_tag is None

    @pytest.mark.asyncio
    async def test_target_vlan_read_returns_none(self, monkeypatch):
        """If the target VLAN read returns None the repair must fail."""
        session = object()
        link = _link(cross_host=False)

        monkeypatch.setattr(lr, "normalize_for_node", lambda *_a, **_kw: "eth1")
        monkeypatch.setattr(
            lr.agent_client,
            "get_interface_vlan_from_agent",
            AsyncMock(side_effect=[100, None]),
        )

        ok = await lr._repair_same_host_vlan(
            session, link, {"h1": _host("h1")}
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_vlans_already_matching_updates_all_three_fields(self, monkeypatch):
        """When OVS already has matching VLANs the repair updates all
        three DB fields (vlan_tag, source_vlan_tag, target_vlan_tag)."""
        session = object()
        link = _link(cross_host=False, vlan_tag=999, source_vlan_tag=888, target_vlan_tag=777)

        monkeypatch.setattr(lr, "normalize_for_node", lambda *_a, **_kw: "eth1")
        monkeypatch.setattr(
            lr.agent_client,
            "get_interface_vlan_from_agent",
            AsyncMock(side_effect=[555, 555]),
        )

        ok = await lr._repair_same_host_vlan(
            session, link, {"h1": _host("h1")}
        )
        assert ok is True
        assert link.vlan_tag == 555
        assert link.source_vlan_tag == 555
        assert link.target_vlan_tag == 555


# ===========================================================================
# 3. VLAN mismatch repair — cross-host
# ===========================================================================

class TestCrossHostVlanRepair:

    @pytest.mark.asyncio
    async def test_both_sides_repaired_successfully(self, monkeypatch):
        """Full success: container port found on both sides,
        set_port_vlan succeeds for container AND tunnel on both sides."""
        session = object()
        link = _link(cross_host=True, source_vlan_tag=300, target_vlan_tag=400)
        src = _host("h1", "agent-a")
        tgt = _host("h2", "agent-b")

        monkeypatch.setattr(lr, "normalize_for_node", lambda *_a, **_kw: "eth1")
        monkeypatch.setattr(
            lr.agent_client, "compute_vxlan_port_name",
            lambda *_a, **_kw: "vxlan-5k",
        )

        # Side effects for get_lab_port_state: source ports then target ports
        src_ports = [{"node_name": "r1", "interface_name": "eth1", "ovs_port_name": "vh-r1-e1"}]
        tgt_ports = [{"node_name": "r2", "interface_name": "eth1", "ovs_port_name": "vh-r2-e1"}]
        monkeypatch.setattr(
            lr.agent_client, "get_lab_port_state",
            AsyncMock(side_effect=[src_ports, tgt_ports]),
        )
        monkeypatch.setattr(
            lr.agent_client, "set_port_vlan_on_agent",
            AsyncMock(return_value=True),
        )

        ok = await lr._repair_cross_host_vlan(
            session, link, {"h1": src, "h2": tgt}
        )
        assert ok is True

    @pytest.mark.asyncio
    async def test_tunnel_port_set_fails_one_side(self, monkeypatch):
        """If the VXLAN tunnel port set fails on one side, repair returns False."""
        session = object()
        link = _link(cross_host=True, source_vlan_tag=300, target_vlan_tag=400)
        src = _host("h1", "agent-a")
        tgt = _host("h2", "agent-b")

        monkeypatch.setattr(lr, "normalize_for_node", lambda *_a, **_kw: "eth1")
        monkeypatch.setattr(
            lr.agent_client, "compute_vxlan_port_name",
            lambda *_a, **_kw: "vxlan-5k",
        )

        src_ports = [{"node_name": "r1", "interface_name": "eth1", "ovs_port_name": "vh-r1-e1"}]
        tgt_ports = [{"node_name": "r2", "interface_name": "eth1", "ovs_port_name": "vh-r2-e1"}]
        monkeypatch.setattr(
            lr.agent_client, "get_lab_port_state",
            AsyncMock(side_effect=[src_ports, tgt_ports]),
        )

        # container port OK, tunnel port FAIL for source; both OK for target
        monkeypatch.setattr(
            lr.agent_client, "set_port_vlan_on_agent",
            AsyncMock(side_effect=[True, False, True, True]),
        )

        ok = await lr._repair_cross_host_vlan(
            session, link, {"h1": src, "h2": tgt}
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_no_db_vlan_for_both_sides(self, monkeypatch):
        """When both sides have None DB VLAN tags, repair must fail."""
        session = object()
        link = _link(cross_host=True, source_vlan_tag=None, target_vlan_tag=None)
        src = _host("h1", "agent-a")
        tgt = _host("h2", "agent-b")

        monkeypatch.setattr(lr, "normalize_for_node", lambda *_a, **_kw: "eth1")
        monkeypatch.setattr(
            lr.agent_client, "compute_vxlan_port_name",
            lambda *_a, **_kw: "vxlan-5k",
        )
        # Should not even reach port state queries for missing-vlan sides
        monkeypatch.setattr(
            lr.agent_client, "get_lab_port_state",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            lr.agent_client, "set_port_vlan_on_agent",
            AsyncMock(return_value=True),
        )

        ok = await lr._repair_cross_host_vlan(
            session, link, {"h1": src, "h2": tgt}
        )
        assert ok is False


# ===========================================================================
# 4. Carrier state propagation through partial recovery
# ===========================================================================

class TestCarrierStatePropagation:

    @pytest.mark.asyncio
    async def test_carrier_set_on_full_recovery(self, monkeypatch):
        """After successful partial recovery both carrier states must be 'on'."""
        session = object()
        link = _link(
            cross_host=True,
            source_carrier_state="off",
            target_carrier_state="off",
        )

        monkeypatch.setattr(
            lr, "get_link_state_by_id_for_update",
            lambda *_a, **_kw: link,
        )
        _common_monkeypatches(monkeypatch)
        monkeypatch.setattr(
            lr.agent_client,
            "attach_overlay_interface_on_agent",
            AsyncMock(side_effect=[
                {"success": True, "local_vlan": 3001},
                {"success": True, "local_vlan": 3002},
            ]),
        )

        ok = await lr.attempt_partial_recovery(
            session, link, {"h1": _host("h1"), "h2": _host("h2")}
        )
        assert ok is True
        assert link.source_carrier_state == "on"
        assert link.target_carrier_state == "on"

    @pytest.mark.asyncio
    async def test_carrier_unchanged_on_partial_failure(self, monkeypatch):
        """If only one side recovers, carrier states must NOT be set to 'on'."""
        session = object()
        link = _link(
            cross_host=True,
            source_carrier_state="off",
            target_carrier_state="off",
        )

        monkeypatch.setattr(
            lr, "get_link_state_by_id_for_update",
            lambda *_a, **_kw: link,
        )
        _common_monkeypatches(monkeypatch)
        monkeypatch.setattr(
            lr.agent_client,
            "attach_overlay_interface_on_agent",
            AsyncMock(side_effect=[
                {"success": True, "local_vlan": 3001},
                {"success": False, "error": "agent-b down"},
            ]),
        )

        ok = await lr.attempt_partial_recovery(
            session, link, {"h1": _host("h1"), "h2": _host("h2")}
        )
        assert ok is False
        # The carrier states should remain untouched since we didn't
        # enter the "both sides OK" branch
        assert link.source_carrier_state == "off"
        assert link.target_carrier_state == "off"


# ===========================================================================
# 5. Agent unreachable / exception paths
# ===========================================================================

class TestAgentUnreachable:

    @pytest.mark.asyncio
    async def test_partial_recovery_target_exception(self, monkeypatch):
        """If the target agent raises an exception, source attachment is
        still recorded but overall recovery fails."""
        session = object()
        link = _link(cross_host=True)

        monkeypatch.setattr(
            lr, "get_link_state_by_id_for_update",
            lambda *_a, **_kw: link,
        )
        _common_monkeypatches(monkeypatch)

        monkeypatch.setattr(
            lr.agent_client,
            "attach_overlay_interface_on_agent",
            AsyncMock(side_effect=[
                {"success": True, "local_vlan": 3001},
                ConnectionError("agent-b unreachable"),
            ]),
        )

        ok = await lr.attempt_partial_recovery(
            session, link, {"h1": _host("h1"), "h2": _host("h2")}
        )
        assert ok is False
        # Source should be recorded as attached
        assert link.source_vxlan_attached is True
        assert link.source_vlan_tag == 3001
        # Target should remain unattached
        assert link.target_vxlan_attached is False
        assert "source=ok" in link.error_message
        assert "target=failed" in link.error_message

    @pytest.mark.asyncio
    async def test_vlan_repair_cross_host_missing_source_agent(self, monkeypatch):
        """Cross-host VLAN repair fails when the source agent is missing."""
        session = object()
        link = _link(cross_host=True)

        # Only target agent available
        ok = await lr._repair_cross_host_vlan(
            session, link, {"h2": _host("h2")}
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_link_repair_cross_host_exception_sets_return_false(self, monkeypatch):
        """attempt_link_repair swallows exceptions from create_cross_host_link."""
        session = object()
        link = _link(cross_host=True)

        monkeypatch.setattr(
            lr, "get_link_state_by_id_for_update",
            lambda *_a, **_kw: link,
        )
        monkeypatch.setattr(
            lr, "create_cross_host_link",
            AsyncMock(side_effect=ConnectionError("agent down")),
        )

        ok = await lr.attempt_link_repair(
            session, link, {"h1": _host("h1"), "h2": _host("h2")}
        )
        assert ok is False


# ===========================================================================
# 6. Repair idempotency
# ===========================================================================

class TestRepairIdempotency:

    @pytest.mark.asyncio
    async def test_same_host_vlan_repair_idempotent_when_already_matching(self, monkeypatch):
        """Running same-host VLAN repair twice when VLANs already match
        produces the same result without calling create_link_on_agent."""
        session = object()
        link = _link(cross_host=False)

        monkeypatch.setattr(lr, "normalize_for_node", lambda *_a, **_kw: "eth1")
        create_mock = AsyncMock()
        monkeypatch.setattr(lr.agent_client, "create_link_on_agent", create_mock)

        # Both reads return matching VLANs
        monkeypatch.setattr(
            lr.agent_client,
            "get_interface_vlan_from_agent",
            AsyncMock(return_value=444),
        )

        ok1 = await lr._repair_same_host_vlan(
            session, link, {"h1": _host("h1")}
        )
        assert ok1 is True
        assert link.vlan_tag == 444
        create_mock.assert_not_awaited()

        # Run again — same result
        monkeypatch.setattr(
            lr.agent_client,
            "get_interface_vlan_from_agent",
            AsyncMock(return_value=444),
        )
        ok2 = await lr._repair_same_host_vlan(
            session, link, {"h1": _host("h1")}
        )
        assert ok2 is True
        assert link.vlan_tag == 444

    @pytest.mark.asyncio
    async def test_attempt_link_repair_idempotent_success(self, monkeypatch):
        """Running attempt_link_repair twice with a success mock yields True both times."""
        session = object()
        link = _link(cross_host=False)

        monkeypatch.setattr(
            lr, "get_link_state_by_id_for_update",
            lambda *_a, **_kw: link,
        )
        monkeypatch.setattr(
            lr, "create_same_host_link",
            AsyncMock(return_value=True),
        )

        ok1 = await lr.attempt_link_repair(
            session, link, {"h1": _host("h1")}
        )
        ok2 = await lr.attempt_link_repair(
            session, link, {"h1": _host("h1")}
        )
        assert ok1 is True
        assert ok2 is True


# ===========================================================================
# 7. Partial recovery: VNI allocation
# ===========================================================================

class TestVniAllocation:

    @pytest.mark.asyncio
    async def test_vni_allocated_when_none(self, monkeypatch):
        """If link.vni is None, partial recovery allocates one."""
        session = object()
        link = _link(cross_host=True, vni=None)

        monkeypatch.setattr(
            lr, "get_link_state_by_id_for_update",
            lambda *_a, **_kw: link,
        )
        _common_monkeypatches(monkeypatch)
        monkeypatch.setattr(
            lr.agent_client,
            "attach_overlay_interface_on_agent",
            AsyncMock(side_effect=[
                {"success": True, "local_vlan": 3001},
                {"success": True, "local_vlan": 3002},
            ]),
        )

        ok = await lr.attempt_partial_recovery(
            session, link, {"h1": _host("h1"), "h2": _host("h2")}
        )
        assert ok is True
        assert link.vni == 5000  # from allocate_vni stub

    @pytest.mark.asyncio
    async def test_existing_vni_preserved(self, monkeypatch):
        """If link.vni is already set, it must not be overwritten."""
        session = object()
        link = _link(cross_host=True, vni=9999)

        monkeypatch.setattr(
            lr, "get_link_state_by_id_for_update",
            lambda *_a, **_kw: link,
        )
        _common_monkeypatches(monkeypatch)
        monkeypatch.setattr(
            lr.agent_client,
            "attach_overlay_interface_on_agent",
            AsyncMock(side_effect=[
                {"success": True, "local_vlan": 3001},
                {"success": True, "local_vlan": 3002},
            ]),
        )

        ok = await lr.attempt_partial_recovery(
            session, link, {"h1": _host("h1"), "h2": _host("h2")}
        )
        assert ok is True
        assert link.vni == 9999


# ===========================================================================
# 8. attempt_vlan_repair row-lock guard
# ===========================================================================

class TestVlanRepairRowLock:

    @pytest.mark.asyncio
    async def test_returns_false_when_link_locked(self, monkeypatch):
        """If get_link_state_by_id_for_update returns None (row locked),
        attempt_vlan_repair returns False without calling repair helpers."""
        session = object()
        link = _link(cross_host=False)

        monkeypatch.setattr(
            lr, "get_link_state_by_id_for_update",
            lambda *_a, **_kw: None,
        )
        same_mock = AsyncMock()
        cross_mock = AsyncMock()
        monkeypatch.setattr(lr, "_repair_same_host_vlan", same_mock)
        monkeypatch.setattr(lr, "_repair_cross_host_vlan", cross_mock)

        ok = await lr.attempt_vlan_repair(session, link, {})
        assert ok is False
        same_mock.assert_not_awaited()
        cross_mock.assert_not_awaited()


# ===========================================================================
# 9. Cross-host partial recovery: only source missing
# ===========================================================================

class TestPartialRecoveryOneSideMissing:

    @pytest.mark.asyncio
    async def test_only_source_reattached(self, monkeypatch):
        """When only source is detached and target is already attached,
        only one attach call is made."""
        session = object()
        link = _link(
            cross_host=True,
            source_vxlan_attached=False,
            target_vxlan_attached=True,
        )

        monkeypatch.setattr(
            lr, "get_link_state_by_id_for_update",
            lambda *_a, **_kw: link,
        )
        _common_monkeypatches(monkeypatch)

        attach_mock = AsyncMock(return_value={"success": True, "local_vlan": 3001})
        monkeypatch.setattr(
            lr.agent_client,
            "attach_overlay_interface_on_agent",
            attach_mock,
        )

        ok = await lr.attempt_partial_recovery(
            session, link, {"h1": _host("h1"), "h2": _host("h2")}
        )
        assert ok is True
        # Only one attach call (source only)
        assert attach_mock.await_count == 1
        assert link.source_vxlan_attached is True
        assert link.source_vlan_tag == 3001
        assert link.actual_state == "up"
