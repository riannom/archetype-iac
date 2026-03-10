"""Tests for api/app/tasks/link_orchestration.py — external links, cross-host, teardown (round 11)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.orm import Session

from app import models
from app.tasks.link_orchestration import (
    create_external_network_links,
    teardown_deployment_links,
)
from tests.factories import make_host, make_link_state


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# create_external_network_links
# ---------------------------------------------------------------------------


class TestCreateExternalLinks:

    def test_empty_links_returns_zero(self, test_db: Session, sample_lab):
        result = _run(create_external_network_links(
            test_db, sample_lab.id, "ext1", [], {}, [],
        ))
        assert result == (0, 0)

    def test_no_managed_interface_error(self, test_db: Session, sample_lab):
        ext_node = MagicMock()
        ext_node.managed_interface_id = None
        ext_node.display_name = "EXT"

        ls = make_link_state(test_db, sample_lab.id, "R1:eth1-EXT:eth0")
        link = MagicMock()

        log_parts = []
        with patch("app.tasks.link_orchestration._sync_oper_state"):
            ok, fail = _run(create_external_network_links(
                test_db, sample_lab.id, "ext1",
                [(link, ls, MagicMock(), ext_node, "eth1")],
                {}, log_parts,
            ))
        assert fail == 1
        assert ls.actual_state == "error"

    def test_managed_interface_deleted_error(self, test_db: Session, sample_lab):
        ext_node = MagicMock()
        ext_node.managed_interface_id = "mi-nonexistent"
        ext_node.display_name = "EXT"

        ls = make_link_state(test_db, sample_lab.id, "R1:eth1-EXT:eth0")
        link = MagicMock()
        log_parts = []

        with patch("app.tasks.link_orchestration._sync_oper_state"):
            ok, fail = _run(create_external_network_links(
                test_db, sample_lab.id, "ext1",
                [(link, ls, MagicMock(), ext_node, "eth1")],
                {}, log_parts,
            ))
        assert fail == 1
        assert "not found" in ls.error_message

    def test_no_host_placement_error(self, test_db: Session, sample_lab):
        mi = models.AgentManagedInterface(
            id="mi-1", host_id="h-missing", name="enp0s3",
            interface_type="external",
        )
        test_db.add(mi)
        test_db.flush()

        ext_node = MagicMock()
        ext_node.managed_interface_id = "mi-1"
        ext_node.display_name = "EXT"

        ls = make_link_state(test_db, sample_lab.id, "R1:eth1-EXT:eth0")
        log_parts = []

        with patch("app.tasks.link_orchestration._sync_oper_state"):
            ok, fail = _run(create_external_network_links(
                test_db, sample_lab.id, "ext1",
                [(MagicMock(), ls, MagicMock(), ext_node, "eth1")],
                {},  # host_to_agent has no entry for h-missing
                log_parts,
            ))
        assert fail == 1


# ---------------------------------------------------------------------------
# teardown_deployment_links
# ---------------------------------------------------------------------------


class TestTeardownLinks:

    def test_no_tunnels_early_return(self, test_db: Session, sample_lab):
        log_parts = []
        ok, fail = _run(teardown_deployment_links(
            test_db, sample_lab.id, {}, log_parts,
        ))
        assert (ok, fail) == (0, 0)

    def test_agent_failure_continues(self, test_db: Session, sample_lab, multiple_hosts):
        tunnel = models.VxlanTunnel(
            lab_id=sample_lab.id, link_state_id=None,
            vni=10000, vlan_tag=200,
            agent_a_id=multiple_hosts[0].id, agent_a_ip="10.0.0.1",
            agent_b_id=multiple_hosts[1].id, agent_b_ip="10.0.0.2",
            status="active",
        )
        test_db.add(tunnel)
        test_db.commit()

        host_to_agent = {h.id: h for h in multiple_hosts[:2]}
        log_parts = []

        with patch("app.tasks.link_orchestration.agent_client") as mock_ac:
            mock_ac.cleanup_overlay_on_agent = AsyncMock(side_effect=RuntimeError("unreachable"))
            ok, fail = _run(teardown_deployment_links(
                test_db, sample_lab.id, host_to_agent, log_parts,
            ))
        assert fail >= 1

    def test_success_marks_cleanup(self, test_db: Session, sample_lab, multiple_hosts):
        tunnel = models.VxlanTunnel(
            lab_id=sample_lab.id, link_state_id=None,
            vni=10001, vlan_tag=201,
            agent_a_id=multiple_hosts[0].id, agent_a_ip="10.0.0.1",
            agent_b_id=multiple_hosts[1].id, agent_b_ip="10.0.0.2",
            status="active",
        )
        test_db.add(tunnel)
        test_db.commit()

        host_to_agent = {h.id: h for h in multiple_hosts[:2]}
        log_parts = []

        with patch("app.tasks.link_orchestration.agent_client") as mock_ac:
            mock_ac.cleanup_overlay_on_agent = AsyncMock(return_value={
                "tunnels_deleted": 1, "bridges_deleted": 0,
            })
            ok, fail = _run(teardown_deployment_links(
                test_db, sample_lab.id, host_to_agent, log_parts,
            ))
        assert ok >= 1

    def test_missing_agent_fails(self, test_db: Session, sample_lab, multiple_hosts):
        tunnel = models.VxlanTunnel(
            lab_id=sample_lab.id, link_state_id=None,
            vni=10002, vlan_tag=202,
            agent_a_id=multiple_hosts[0].id, agent_a_ip="10.0.0.1",
            agent_b_id="nonexistent-agent", agent_b_ip="10.0.0.2",
            status="active",
        )
        test_db.add(tunnel)
        test_db.commit()

        host_to_agent = {multiple_hosts[0].id: multiple_hosts[0]}
        log_parts = []

        with patch("app.tasks.link_orchestration.agent_client") as mock_ac:
            mock_ac.cleanup_overlay_on_agent = AsyncMock(return_value={
                "tunnels_deleted": 1, "bridges_deleted": 0,
            })
            ok, fail = _run(teardown_deployment_links(
                test_db, sample_lab.id, host_to_agent, log_parts,
            ))
        assert fail >= 1