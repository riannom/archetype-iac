"""Extended tests for app.services.link_validator.

Covers edge cases not in test_link_validator.py: VLAN mismatch detection,
overlay status errors, null VLAN reads, backfill behavior, and
update_interface_mappings.
"""
from __future__ import annotations

import pytest

from app import models
import app.services.link_validator as link_validator


# ---------------------------------------------------------------------------
# Tests: is_vlan_mismatch helper
# ---------------------------------------------------------------------------

class TestIsVlanMismatch:
    def test_vlan_mismatch_prefix(self) -> None:
        assert link_validator.is_vlan_mismatch("VLAN_MISMATCH: tags differ") is True

    def test_tunnel_missing_not_vlan_mismatch(self) -> None:
        assert link_validator.is_vlan_mismatch("TUNNEL_MISSING: on agent-1") is False

    def test_none_not_vlan_mismatch(self) -> None:
        assert link_validator.is_vlan_mismatch(None) is False

    def test_empty_not_vlan_mismatch(self) -> None:
        assert link_validator.is_vlan_mismatch("") is False


# ---------------------------------------------------------------------------
# Tests: verify_same_host_link — edge cases
# ---------------------------------------------------------------------------

class TestVerifySameHostLinkEdge:
    @pytest.mark.asyncio
    async def test_source_vlan_none_fails(
        self, test_db, sample_lab, sample_host, monkeypatch
    ) -> None:
        link_state = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            is_cross_host=False,
            source_host_id=sample_host.id,
        )
        test_db.add(link_state)
        test_db.commit()

        call_count = [0]
        async def fake_vlan(*args, **kwargs):
            call_count[0] += 1
            return None  # Both sides return None

        monkeypatch.setattr(
            "app.services.link_validator.agent_client.get_interface_vlan_from_agent",
            fake_vlan,
        )

        ok, error = await link_validator.verify_same_host_link(
            test_db, link_state, {sample_host.id: sample_host}
        )
        assert not ok
        assert "Could not read VLAN tag" in error

    @pytest.mark.asyncio
    async def test_target_vlan_none_fails(
        self, test_db, sample_lab, sample_host, monkeypatch
    ) -> None:
        link_state = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            is_cross_host=False,
            source_host_id=sample_host.id,
        )
        test_db.add(link_state)
        test_db.commit()

        call_count = [0]
        async def fake_vlan(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return 100  # source OK
            return None  # target fails

        monkeypatch.setattr(
            "app.services.link_validator.agent_client.get_interface_vlan_from_agent",
            fake_vlan,
        )

        ok, error = await link_validator.verify_same_host_link(
            test_db, link_state, {sample_host.id: sample_host}
        )
        assert not ok
        assert "r2" in error

    @pytest.mark.asyncio
    async def test_vlan_mismatch_detected(
        self, test_db, sample_lab, sample_host, monkeypatch
    ) -> None:
        link_state = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            is_cross_host=False,
            source_host_id=sample_host.id,
        )
        test_db.add(link_state)
        test_db.commit()

        call_count = [0]
        async def fake_vlan(*args, **kwargs):
            call_count[0] += 1
            return 100 if call_count[0] == 1 else 200

        monkeypatch.setattr(
            "app.services.link_validator.agent_client.get_interface_vlan_from_agent",
            fake_vlan,
        )

        ok, error = await link_validator.verify_same_host_link(
            test_db, link_state, {sample_host.id: sample_host}
        )
        assert not ok
        assert link_validator.is_vlan_mismatch(error)

    @pytest.mark.asyncio
    async def test_backfills_per_side_vlan_tags(
        self, test_db, sample_lab, sample_host, monkeypatch
    ) -> None:
        link_state = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            is_cross_host=False,
            source_host_id=sample_host.id,
            source_vlan_tag=None,
            target_vlan_tag=None,
        )
        test_db.add(link_state)
        test_db.commit()

        async def fake_vlan(*args, **kwargs):
            return 300

        monkeypatch.setattr(
            "app.services.link_validator.agent_client.get_interface_vlan_from_agent",
            fake_vlan,
        )

        ok, error = await link_validator.verify_same_host_link(
            test_db, link_state, {sample_host.id: sample_host}
        )
        assert ok
        assert link_state.source_vlan_tag == 300
        assert link_state.target_vlan_tag == 300


# ---------------------------------------------------------------------------
# Tests: verify_cross_host_link — edge cases
# ---------------------------------------------------------------------------

class TestVerifyCrossHostLinkEdge:
    @pytest.mark.asyncio
    async def test_missing_source_agent(
        self, test_db, sample_lab, multiple_hosts
    ) -> None:
        host_a, host_b = multiple_hosts[:2]
        link_state = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            is_cross_host=True,
            source_host_id="nonexistent",
            target_host_id=host_b.id,
        )
        ok, error = await link_validator.verify_cross_host_link(
            test_db, link_state, {host_b.id: host_b}
        )
        assert not ok
        assert "Source agent not found" in error

    @pytest.mark.asyncio
    async def test_missing_target_agent(
        self, test_db, sample_lab, multiple_hosts
    ) -> None:
        host_a, host_b = multiple_hosts[:2]
        link_state = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            is_cross_host=True,
            source_host_id=host_a.id,
            target_host_id="nonexistent",
        )
        ok, error = await link_validator.verify_cross_host_link(
            test_db, link_state, {host_a.id: host_a}
        )
        assert not ok
        assert "Target agent not found" in error

    @pytest.mark.asyncio
    async def test_overlay_status_error(
        self, test_db, sample_lab, multiple_hosts, monkeypatch
    ) -> None:
        host_a, host_b = multiple_hosts[:2]
        link_state = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            is_cross_host=True,
            source_host_id=host_a.id,
            target_host_id=host_b.id,
        )
        test_db.add(link_state)
        test_db.commit()

        async def fake_vlan(*args, **kwargs):
            return 100

        async def fake_overlay_status(agent):
            return {"error": "OVS not running"}

        monkeypatch.setattr(
            "app.services.link_validator.agent_client.get_interface_vlan_from_agent",
            fake_vlan,
        )
        monkeypatch.setattr(
            "app.services.link_validator.agent_client.get_overlay_status_from_agent",
            fake_overlay_status,
        )

        ok, error = await link_validator.verify_cross_host_link(
            test_db, link_state, {host_a.id: host_a, host_b.id: host_b}
        )
        assert not ok
        assert "Overlay status unavailable" in error

    @pytest.mark.asyncio
    async def test_overlay_status_exception(
        self, test_db, sample_lab, multiple_hosts, monkeypatch
    ) -> None:
        host_a, host_b = multiple_hosts[:2]
        link_state = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            is_cross_host=True,
            source_host_id=host_a.id,
            target_host_id=host_b.id,
        )
        test_db.add(link_state)
        test_db.commit()

        async def fake_vlan(*args, **kwargs):
            return 100

        async def fake_overlay_status(agent):
            raise ConnectionError("agent unreachable")

        monkeypatch.setattr(
            "app.services.link_validator.agent_client.get_interface_vlan_from_agent",
            fake_vlan,
        )
        monkeypatch.setattr(
            "app.services.link_validator.agent_client.get_overlay_status_from_agent",
            fake_overlay_status,
        )

        ok, error = await link_validator.verify_cross_host_link(
            test_db, link_state, {host_a.id: host_a, host_b.id: host_b}
        )
        assert not ok
        assert "Could not check overlay status" in error


# ---------------------------------------------------------------------------
# Tests: verify_link_connected — dispatch
# ---------------------------------------------------------------------------

class TestVerifyLinkConnectedDispatch:
    @pytest.mark.asyncio
    async def test_dispatches_to_same_host(
        self, test_db, sample_lab, sample_host, monkeypatch
    ) -> None:
        link_state = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            is_cross_host=False,
            source_host_id=sample_host.id,
        )

        async def fake_vlan(*args, **kwargs):
            return 100

        monkeypatch.setattr(
            "app.services.link_validator.agent_client.get_interface_vlan_from_agent",
            fake_vlan,
        )

        ok, error = await link_validator.verify_link_connected(
            test_db, link_state, {sample_host.id: sample_host}
        )
        assert ok


# ---------------------------------------------------------------------------
# Tests: update_interface_mappings
# ---------------------------------------------------------------------------

class TestUpdateInterfaceMappings:
    @pytest.mark.asyncio
    async def test_skips_when_no_host_ids(
        self, test_db, sample_lab, monkeypatch
    ) -> None:
        link_state = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            source_host_id=None,
            target_host_id=None,
        )
        # Should complete without error
        await link_validator.update_interface_mappings(
            test_db, link_state, {}
        )

    @pytest.mark.asyncio
    async def test_handles_agent_error_gracefully(
        self, test_db, sample_lab, sample_host, monkeypatch
    ) -> None:
        link_state = models.LinkState(
            lab_id=sample_lab.id,
            link_name="r1:eth1-r2:eth1",
            source_node="r1",
            source_interface="eth1",
            target_node="r2",
            target_interface="eth1",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )

        async def fake_populate(*args, **kwargs):
            raise ConnectionError("agent down")

        monkeypatch.setattr(
            "app.services.link_validator.mapping_service.populate_from_agent",
            fake_populate,
        )

        # Should not raise
        await link_validator.update_interface_mappings(
            test_db, link_state, {sample_host.id: sample_host}
        )
