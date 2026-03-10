"""Tests for app/tasks/link_reconciliation.py - Link state reconciliation.

This module tests:
- reconcile_link_states: desired vs actual state enforcement
- create_link_if_ready delegation for pending/down links
- teardown_link delegation for links that should be down
- Agent offline handling and error recovery
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import models
from tests.factories import make_link_state, make_node_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ============================================================================
# reconcile_link_states
# ============================================================================


class TestReconcileLinkStates:
    """Tests for the reconcile_link_states orchestrator function."""

    @pytest.mark.asyncio
    async def test_down_to_up_creates_link(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Links with desired=up and actual=down should be created."""
        from app.tasks.link_reconciliation import reconcile_link_states

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="down",
            source_host_id=sample_host.id, target_host_id=sample_host.id,
        )

        with patch("app.tasks.link_reconciliation.create_link_if_ready", new_callable=AsyncMock, return_value=True) as mock_create, \
             patch("app.tasks.link_reconciliation._cleanup_deleted_links", new_callable=AsyncMock, return_value=0), \
             patch("app.tasks.link_reconciliation.verify_link_connected", new_callable=AsyncMock, return_value=(True, None)):
            results = await reconcile_link_states(test_db)

        assert results["created"] >= 1
        mock_create.assert_awaited()

    @pytest.mark.asyncio
    async def test_up_to_down_tears_down_link(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Links with desired=down and actual=up should be torn down."""
        from app.tasks.link_reconciliation import reconcile_link_states

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="down", actual_state="up",
            source_host_id=sample_host.id, target_host_id=sample_host.id,
        )

        with patch("app.tasks.link_reconciliation.teardown_link", new_callable=AsyncMock, return_value=True) as mock_teardown, \
             patch("app.tasks.link_reconciliation._cleanup_deleted_links", new_callable=AsyncMock, return_value=0), \
             patch("app.tasks.link_reconciliation.create_link_if_ready", new_callable=AsyncMock):
            results = await reconcile_link_states(test_db)

        assert results["torn_down"] >= 1
        mock_teardown.assert_awaited()
        args, _kwargs = mock_teardown.await_args
        assert args[2]["link_state_id"] == link.id

    @pytest.mark.asyncio
    async def test_offline_agent_skipped(
        self, test_db: Session, sample_lab: models.Lab, offline_host: models.Host,
    ):
        """Links with offline agent hosts are skipped."""
        from app.tasks.link_reconciliation import reconcile_link_states

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="down",
            source_host_id=offline_host.id, target_host_id=offline_host.id,
        )

        with patch("app.tasks.link_reconciliation._cleanup_deleted_links", new_callable=AsyncMock, return_value=0):
            results = await reconcile_link_states(test_db)

        assert results["skipped"] >= 1

    @pytest.mark.asyncio
    async def test_error_cross_host_attempts_recovery(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host],
    ):
        """Cross-host links in error state should attempt partial recovery."""
        from app.tasks.link_reconciliation import reconcile_link_states

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="error",
            source_host_id=multiple_hosts[0].id,
            target_host_id=multiple_hosts[1].id,
            is_cross_host=True,
        )

        with patch("app.tasks.link_reconciliation.attempt_partial_recovery", new_callable=AsyncMock, return_value=True) as mock_recovery, \
             patch("app.tasks.link_reconciliation._cleanup_deleted_links", new_callable=AsyncMock, return_value=0), \
             patch("app.tasks.link_reconciliation.create_link_if_ready", new_callable=AsyncMock), \
             patch("app.tasks.link_reconciliation.verify_link_connected", new_callable=AsyncMock, return_value=(True, None)):
            results = await reconcile_link_states(test_db)

        assert results["recovered"] >= 1
        mock_recovery.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_links_returns_empty_results(
        self, test_db: Session, sample_lab: models.Lab,
    ):
        """Empty link table returns zeroed results dict."""
        from app.tasks.link_reconciliation import reconcile_link_states

        with patch("app.tasks.link_reconciliation._cleanup_deleted_links", new_callable=AsyncMock, return_value=0):
            results = await reconcile_link_states(test_db)

        assert results["checked"] == 0
        assert results["created"] == 0
        assert results["torn_down"] == 0
        assert results["errors"] == 0


# ============================================================================
# create_link_if_ready (via live_links)
# ============================================================================


class TestCreateLinkIfReady:
    """Tests for the create_link_if_ready function used by reconciliation."""

    @pytest.mark.asyncio
    async def test_both_nodes_running_same_host(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Link is created when both endpoint nodes are running on the same host."""
        from app.tasks.live_links import create_link_if_ready

        make_node_state(test_db, sample_lab.id, "archetype-test-r1", actual_state="running", is_ready=True)
        make_node_state(test_db, sample_lab.id, "archetype-test-r2", actual_state="running", is_ready=True)

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="down",
            source_host_id=sample_host.id, target_host_id=sample_host.id,
        )

        # Need Node definitions for host lookup
        node1 = models.Node(
            id=str(uuid4()), lab_id=sample_lab.id, gui_id="n1",
            display_name="R1", container_name="archetype-test-r1",
            device="linux", host_id=sample_host.id,
        )
        node2 = models.Node(
            id=str(uuid4()), lab_id=sample_lab.id, gui_id="n2",
            display_name="R2", container_name="archetype-test-r2",
            device="linux", host_id=sample_host.id,
        )
        test_db.add_all([node1, node2])
        test_db.commit()

        # Create placements
        for node_def in [node1, node2]:
            test_db.add(models.NodePlacement(
                id=str(uuid4()),
                lab_id=sample_lab.id,
                host_id=sample_host.id,
                node_name=node_def.container_name,
                node_definition_id=node_def.id,
            ))
        test_db.commit()

        host_to_agent = {sample_host.id: sample_host}

        with patch(
            "app.tasks.live_links.create_same_host_link",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.tasks.live_links.claim_link_endpoints",
            return_value=(True, []),
        ):
            result = await create_link_if_ready(
                test_db, sample_lab.id, link, host_to_agent,
            )

        assert result is True


class TestReconcileLabLinks:
    """Tests for reconcile_lab_links error recovery."""

    @pytest.mark.asyncio
    async def test_db_error_on_one_link_does_not_break_entire_reconcile(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        from app.tasks.link_reconciliation import reconcile_lab_links

        make_link_state(
            test_db,
            sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            desired_state="up",
            actual_state="up",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )
        make_link_state(
            test_db,
            sample_lab.id,
            link_name="R2:eth2-R3:eth2",
            source_node="archetype-test-r2",
            target_node="archetype-test-r3",
            desired_state="up",
            actual_state="up",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )

        calls = {"count": 0}

        async def _verify_with_first_call_db_error(session, _link, _host_to_agent):
            calls["count"] += 1
            if calls["count"] == 1:
                session.execute(text("SELECT * FROM __definitely_missing_table__"))
            return True, None

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.tasks.link_reconciliation.verify_link_connected",
            side_effect=_verify_with_first_call_db_error,
        ):
            results = await reconcile_lab_links(test_db, sample_lab.id)

        assert calls["count"] == 2
        assert results["checked"] == 2
        assert results["errors"] >= 1
        assert results["valid"] >= 1


class TestCreateLinkIfReadyAdditional:
    """Additional create_link_if_ready behaviors."""

    @pytest.mark.asyncio
    async def test_source_not_running_skips(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Link creation is skipped when source node is not running."""
        from app.tasks.live_links import create_link_if_ready

        make_node_state(test_db, sample_lab.id, "archetype-test-r1", actual_state="stopped")
        make_node_state(test_db, sample_lab.id, "archetype-test-r2", actual_state="running")

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="down",
            source_host_id=sample_host.id, target_host_id=sample_host.id,
        )

        host_to_agent = {sample_host.id: sample_host}

        with patch("app.tasks.live_links.claim_link_endpoints", return_value=(True, [])):
            result = await create_link_if_ready(
                test_db, sample_lab.id, link, host_to_agent,
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_row_lock_skip(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """When skip_locked=True and row is locked, returns False silently."""
        from app.tasks.live_links import create_link_if_ready

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="down",
        )

        host_to_agent = {sample_host.id: sample_host}

        # Simulate row lock by making get_link_state_for_update return None
        with patch("app.tasks.live_links.get_link_state_for_update", return_value=None):
            result = await create_link_if_ready(
                test_db, sample_lab.id, link, host_to_agent, skip_locked=True,
            )

        assert result is False


# ============================================================================
# teardown_link (via live_links)
# ============================================================================


class TestTeardownLink:
    """Tests for the teardown_link function used by reconciliation."""

    @pytest.mark.asyncio
    async def test_same_host_disconnect(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Same-host link teardown calls agent to disconnect."""
        from app.tasks.live_links import teardown_link

        link_info = {
            "link_name": "R1:eth1-R2:eth1",
            "is_cross_host": False,
            "actual_state": "up",
            "source_host_id": sample_host.id,
            "target_host_id": sample_host.id,
            "source_node": "archetype-test-r1",
            "target_node": "archetype-test-r2",
            "source_interface": "eth1",
            "target_interface": "eth1",
        }

        # Create a LinkState for the link
        make_link_state(
            test_db, sample_lab.id,
            desired_state="down", actual_state="up",
            source_host_id=sample_host.id, target_host_id=sample_host.id,
        )

        host_to_agent = {sample_host.id: sample_host}

        with patch("app.tasks.live_links.agent_client") as mock_client:
            mock_client.delete_link_on_agent = AsyncMock(return_value={
                "success": True,
            })

            result = await teardown_link(
                test_db, sample_lab.id, link_info, host_to_agent,
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_cross_host_overlay_teardown(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host],
    ):
        """Cross-host link teardown cleans up VXLAN overlay."""
        from app.tasks.live_links import teardown_link

        host_a, host_b = multiple_hosts[0], multiple_hosts[1]

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="down", actual_state="up",
            source_host_id=host_a.id, target_host_id=host_b.id,
            is_cross_host=True,
        )

        # Create a VxlanTunnel record
        tunnel = models.VxlanTunnel(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_state_id=link.id,
            vni=50000,
            vlan_tag=200,
            agent_a_id=host_a.id,
            agent_a_ip="192.168.1.1",
            agent_b_id=host_b.id,
            agent_b_ip="192.168.1.2",
            status="active",
        )
        test_db.add(tunnel)
        test_db.commit()

        link_info = {
            "link_name": link.link_name,
            "is_cross_host": True,
            "actual_state": "up",
            "source_host_id": host_a.id,
            "target_host_id": host_b.id,
            "source_node": link.source_node,
            "target_node": link.target_node,
            "source_interface": link.source_interface,
            "target_interface": link.target_interface,
        }

        host_to_agent = {host_a.id: host_a, host_b.id: host_b}

        with patch("app.tasks.live_links.agent_client") as mock_client:
            mock_client.detach_overlay_interface_on_agent = AsyncMock(
                return_value={"success": True},
            )
            mock_client.resolve_agent_ip = AsyncMock(
                side_effect=lambda addr: addr.split(":")[0],
            )
            mock_client.cleanup_overlay_link_on_agent = AsyncMock(
                return_value={"success": True},
            )

            result = await teardown_link(
                test_db, sample_lab.id, link_info, host_to_agent,
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_teardown_skips_non_active_link(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Teardown of link with non-active actual_state returns True (no-op)."""
        from app.tasks.live_links import teardown_link

        link_info = {
            "link_name": "R1:eth1-R2:eth1",
            "is_cross_host": False,
            "actual_state": "down",
            "source_host_id": sample_host.id,
            "target_host_id": sample_host.id,
            "source_node": "archetype-test-r1",
            "target_node": "archetype-test-r2",
            "source_interface": "eth1",
            "target_interface": "eth1",
        }

        host_to_agent = {sample_host.id: sample_host}

        result = await teardown_link(
            test_db, sample_lab.id, link_info, host_to_agent,
        )

        # "down" is not in (up, error, pending) so it's skipped
        assert result is True

    @pytest.mark.asyncio
    async def test_cross_host_teardown_missing_agent_deferred(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host],
    ):
        """Cross-host teardown with missing agent marks link as error and returns False."""
        from app.tasks.live_links import teardown_link

        host_a = multiple_hosts[0]

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="down", actual_state="up",
            source_host_id=host_a.id, target_host_id="missing-agent-id",
            is_cross_host=True,
        )

        link_info = {
            "link_name": link.link_name,
            "is_cross_host": True,
            "actual_state": "up",
            "source_host_id": host_a.id,
            "target_host_id": "missing-agent-id",
            "source_node": link.source_node,
            "target_node": link.target_node,
            "source_interface": link.source_interface,
            "target_interface": link.target_interface,
        }

        # Only include host_a, not the missing target
        host_to_agent = {host_a.id: host_a}

        result = await teardown_link(
            test_db, sample_lab.id, link_info, host_to_agent,
        )

        assert result is False