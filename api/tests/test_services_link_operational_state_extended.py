"""Extended tests for app.services.link_operational_state.

Covers multi-endpoint scenarios, transport state derivation, batch recompute,
and edge cases not covered by test_link_operational_state.py.
"""
from __future__ import annotations

from types import SimpleNamespace


from app.services.link_operational_state import (
    REASON_ADMIN_DOWN,
    REASON_LOCAL_INTERFACE_DOWN,
    REASON_LOCAL_NODE_DOWN,
    REASON_PEER_HOST_OFFLINE,
    REASON_PEER_NODE_DOWN,
    REASON_TRANSPORT_DOWN,
    TRANSPORT_DEGRADED,
    TRANSPORT_DOWN,
    TRANSPORT_UP,
    EndpointOperationalInput,
    _carrier_up,
    _is_external_endpoint,
    _transport_state,
    compute_endpoint_oper_state,
    recompute_link_oper_state,
)


# ---------------------------------------------------------------------------
# Tests: _is_external_endpoint
# ---------------------------------------------------------------------------

class TestIsExternalEndpoint:
    def test_external_prefix(self) -> None:
        assert _is_external_endpoint("_ext:bridge0") is True

    def test_regular_node(self) -> None:
        assert _is_external_endpoint("router1") is False

    def test_none(self) -> None:
        assert _is_external_endpoint(None) is False

    def test_empty_string(self) -> None:
        assert _is_external_endpoint("") is False

    def test_ext_prefix_case_sensitive(self) -> None:
        assert _is_external_endpoint("_EXT:bridge") is False


# ---------------------------------------------------------------------------
# Tests: _carrier_up
# ---------------------------------------------------------------------------

class TestCarrierUp:
    def test_on_is_up(self) -> None:
        assert _carrier_up("on") is True

    def test_On_case_insensitive(self) -> None:
        assert _carrier_up("ON") is True

    def test_off_is_down(self) -> None:
        assert _carrier_up("off") is False

    def test_none_is_down(self) -> None:
        assert _carrier_up(None) is False

    def test_empty_is_down(self) -> None:
        assert _carrier_up("") is False


# ---------------------------------------------------------------------------
# Tests: _transport_state — cross-host scenarios
# ---------------------------------------------------------------------------

class TestTransportStateCrossHost:
    def _make_link_state(self, **kwargs) -> SimpleNamespace:
        defaults = {
            "is_cross_host": False,
            "actual_state": "up",
            "source_vxlan_attached": False,
            "target_vxlan_attached": False,
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_same_host_up(self) -> None:
        ls = self._make_link_state(is_cross_host=False, actual_state="up")
        assert _transport_state(ls) == TRANSPORT_UP

    def test_same_host_error_is_degraded(self) -> None:
        ls = self._make_link_state(is_cross_host=False, actual_state="error")
        assert _transport_state(ls) == TRANSPORT_DEGRADED

    def test_same_host_down(self) -> None:
        ls = self._make_link_state(is_cross_host=False, actual_state="down")
        assert _transport_state(ls) == TRANSPORT_DOWN

    def test_cross_host_both_attached_and_up(self) -> None:
        ls = self._make_link_state(
            is_cross_host=True,
            source_vxlan_attached=True,
            target_vxlan_attached=True,
            actual_state="up",
        )
        assert _transport_state(ls) == TRANSPORT_UP

    def test_cross_host_both_attached_but_not_up(self) -> None:
        ls = self._make_link_state(
            is_cross_host=True,
            source_vxlan_attached=True,
            target_vxlan_attached=True,
            actual_state="pending",
        )
        assert _transport_state(ls) == TRANSPORT_DEGRADED

    def test_cross_host_one_attached(self) -> None:
        ls = self._make_link_state(
            is_cross_host=True,
            source_vxlan_attached=True,
            target_vxlan_attached=False,
            actual_state="pending",
        )
        assert _transport_state(ls) == TRANSPORT_DEGRADED

    def test_cross_host_none_attached_error(self) -> None:
        ls = self._make_link_state(
            is_cross_host=True,
            source_vxlan_attached=False,
            target_vxlan_attached=False,
            actual_state="error",
        )
        assert _transport_state(ls) == TRANSPORT_DEGRADED

    def test_cross_host_none_attached_down(self) -> None:
        ls = self._make_link_state(
            is_cross_host=True,
            source_vxlan_attached=False,
            target_vxlan_attached=False,
            actual_state="down",
        )
        assert _transport_state(ls) == TRANSPORT_DOWN


# ---------------------------------------------------------------------------
# Tests: compute_endpoint_oper_state — priority ordering
# ---------------------------------------------------------------------------

class TestEndpointOperStatePriority:
    """Verify the priority chain: admin_down > local_node > local_iface > peer checks."""

    def test_admin_down_overrides_local_node_down(self) -> None:
        ep = EndpointOperationalInput(
            admin_state="down",
            local_node_running=False,
            local_interface_up=False,
            peer_host_online=False,
            peer_node_running=False,
            peer_interface_up=False,
        )
        result = compute_endpoint_oper_state(ep, TRANSPORT_UP)
        assert result.reason == REASON_ADMIN_DOWN

    def test_local_node_down_before_local_interface(self) -> None:
        ep = EndpointOperationalInput(
            admin_state="up",
            local_node_running=False,
            local_interface_up=False,
            peer_host_online=True,
            peer_node_running=True,
            peer_interface_up=True,
        )
        result = compute_endpoint_oper_state(ep, TRANSPORT_UP)
        assert result.reason == REASON_LOCAL_NODE_DOWN

    def test_local_interface_down_before_peer_host(self) -> None:
        ep = EndpointOperationalInput(
            admin_state="up",
            local_node_running=True,
            local_interface_up=False,
            peer_host_online=False,
            peer_node_running=True,
            peer_interface_up=True,
        )
        result = compute_endpoint_oper_state(ep, TRANSPORT_UP)
        assert result.reason == REASON_LOCAL_INTERFACE_DOWN

    def test_peer_host_offline_before_peer_node(self) -> None:
        ep = EndpointOperationalInput(
            admin_state="up",
            local_node_running=True,
            local_interface_up=True,
            peer_host_online=False,
            peer_node_running=False,
            peer_interface_up=True,
        )
        result = compute_endpoint_oper_state(ep, TRANSPORT_UP)
        assert result.reason == REASON_PEER_HOST_OFFLINE

    def test_peer_node_down_before_peer_interface(self) -> None:
        ep = EndpointOperationalInput(
            admin_state="up",
            local_node_running=True,
            local_interface_up=True,
            peer_host_online=True,
            peer_node_running=False,
            peer_interface_up=False,
        )
        result = compute_endpoint_oper_state(ep, TRANSPORT_UP)
        assert result.reason == REASON_PEER_NODE_DOWN

    def test_transport_down_after_peer_interface_ok(self) -> None:
        ep = EndpointOperationalInput(
            admin_state="up",
            local_node_running=True,
            local_interface_up=True,
            peer_host_online=True,
            peer_node_running=True,
            peer_interface_up=True,
        )
        result = compute_endpoint_oper_state(ep, TRANSPORT_DOWN)
        assert result.reason == REASON_TRANSPORT_DOWN


# ---------------------------------------------------------------------------
# Tests: recompute_link_oper_state — DB-integrated
# ---------------------------------------------------------------------------

class TestRecomputeLinkOperState:
    def test_no_change_when_already_correct(
        self, test_db, sample_lab, monkeypatch
    ) -> None:
        from app import agent_client, models

        monkeypatch.setattr(agent_client, "is_agent_online", lambda host: False)

        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="a:eth1-b:eth1",
            source_node="a",
            source_interface="eth1",
            target_node="b",
            target_interface="eth1",
            desired_state="down",
            actual_state="down",
            source_oper_state="down",
            source_oper_reason="admin_down",
            target_oper_state="down",
            target_oper_reason="admin_down",
        )
        test_db.add(link)
        test_db.commit()

        changed = recompute_link_oper_state(test_db, link)
        assert changed is False

    def test_external_endpoint_treated_as_running(
        self, test_db, sample_lab, sample_host, monkeypatch
    ) -> None:
        from app import agent_client, models

        monkeypatch.setattr(
            agent_client, "is_agent_online",
            lambda host: bool(host and host.status == "online" and host.last_heartbeat),
        )

        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="_ext:br0:eth0-r1:eth1",
            source_node="_ext:br0",
            source_interface="eth0",
            target_node="r1",
            target_interface="eth1",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
            desired_state="up",
            actual_state="up",
            source_carrier_state="on",
            target_carrier_state="on",
        )
        test_db.add(link)

        # r1 needs to be running for source oper to be up
        test_db.add(models.NodeState(
            lab_id=sample_lab.id,
            node_id="r1",
            node_name="r1",
            desired_state="running",
            actual_state="running",
        ))
        test_db.commit()

        recompute_link_oper_state(test_db, link)
        # External endpoint is always "running" — so source side sees peer running
        assert link.source_oper_state == "up"

    def test_oper_epoch_increments_on_change(
        self, test_db, sample_lab, monkeypatch
    ) -> None:
        from app import agent_client, models

        monkeypatch.setattr(agent_client, "is_agent_online", lambda host: False)

        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="x:eth1-y:eth1",
            source_node="x",
            source_interface="eth1",
            target_node="y",
            target_interface="eth1",
            desired_state="up",
            actual_state="up",
            source_carrier_state="on",
            target_carrier_state="on",
            oper_epoch=5,
        )
        test_db.add(link)
        test_db.commit()

        changed = recompute_link_oper_state(test_db, link)
        assert changed is True
        assert link.oper_epoch == 6

    def test_both_endpoints_down_when_desired_down(
        self, test_db, sample_lab, monkeypatch
    ) -> None:
        from app import agent_client, models

        monkeypatch.setattr(agent_client, "is_agent_online", lambda host: True)

        link = models.LinkState(
            lab_id=sample_lab.id,
            link_name="p:eth1-q:eth1",
            source_node="p",
            source_interface="eth1",
            target_node="q",
            target_interface="eth1",
            desired_state="down",
            actual_state="up",
            source_carrier_state="on",
            target_carrier_state="on",
        )
        test_db.add(link)
        test_db.commit()

        recompute_link_oper_state(test_db, link)
        assert link.source_oper_state == "down"
        assert link.source_oper_reason == "admin_down"
        assert link.target_oper_state == "down"
        assert link.target_oper_reason == "admin_down"
