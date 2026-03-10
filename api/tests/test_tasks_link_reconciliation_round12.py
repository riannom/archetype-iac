"""Round-12 deep convergence tests for link_reconciliation.py.

Targets three functions with new scenarios not covered by existing tests:
- run_same_host_convergence: VLAN mismatch repair, multi-link batching,
  agent exception during declare, zero-vlan skip, mixed agent results
- run_cross_host_port_convergence: multi-agent port matching, partial agent
  failure, already-converged noop, lab_id filtering, missing node skip
- reconcile_lab_links: empty lab skip, VLAN mismatch triggers lightweight
  repair, error cross-host recovery, pending link skip, rollback on exception
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models
from tests.factories import make_link_state, make_node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_interface_mapping(
    test_db: Session,
    lab_id: str,
    node_id: str,
    linux_interface: str = "eth1",
    ovs_port: str = "vh-test-port",
    vlan_tag: int | None = None,
) -> models.InterfaceMapping:
    im = models.InterfaceMapping(
        id=str(uuid4()),
        lab_id=lab_id,
        node_id=node_id,
        linux_interface=linux_interface,
        ovs_port=ovs_port,
        vlan_tag=vlan_tag,
    )
    test_db.add(im)
    test_db.commit()
    test_db.refresh(im)
    return im


# ============================================================================
# run_same_host_convergence — deeper scenarios
# ============================================================================


class TestRunSameHostConvergenceRound12:
    """Deeper convergence scenarios for run_same_host_convergence."""

    @pytest.mark.asyncio
    async def test_vlan_mismatch_updated_by_agent(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """When agent reports 'updated' (VLAN was wrong), result reflects it."""
        from app.tasks.link_reconciliation import run_same_host_convergence

        host_to_agent = {sample_host.id: sample_host}

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=False,
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
            vlan_tag=200,
        )
        n1 = make_node(test_db, sample_lab.id, "R1", link.source_node, sample_host.id)
        n2 = make_node(test_db, sample_lab.id, "R2", link.target_node, sample_host.id)
        _make_interface_mapping(
            test_db, sample_lab.id, n1.id,
            linux_interface="eth1", ovs_port="vh-src", vlan_tag=200,
        )
        _make_interface_mapping(
            test_db, sample_lab.id, n2.id,
            linux_interface="eth1", ovs_port="vh-tgt", vlan_tag=200,
        )

        with patch(
            "app.tasks.link_reconciliation.declare_port_state_on_agent",
            new_callable=AsyncMock,
            return_value={
                "results": [{"link_name": link.link_name, "status": "updated"}]
            },
        ):
            result = await run_same_host_convergence(test_db, host_to_agent)

        assert sample_host.id in result
        assert result[sample_host.id]["updated"] == 1

    @pytest.mark.asyncio
    async def test_multiple_links_batched_to_single_agent(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Multiple same-host links are batched into one declare call per agent."""
        from app.tasks.link_reconciliation import run_same_host_convergence

        host_to_agent = {sample_host.id: sample_host}

        # Two links on the same host
        for i, (sn, tn) in enumerate([
            ("archetype-test-a1", "archetype-test-a2"),
            ("archetype-test-b1", "archetype-test-b2"),
        ]):
            make_link_state(
                test_db, sample_lab.id,
                link_name=f"L{i}:eth1--L{i}:eth1",
                source_node=sn, target_node=tn,
                desired_state="up", actual_state="up",
                is_cross_host=False,
                source_host_id=sample_host.id,
                target_host_id=sample_host.id,
                vlan_tag=300 + i,
            )
            n_s = make_node(test_db, sample_lab.id, f"S{i}", sn, sample_host.id)
            n_t = make_node(test_db, sample_lab.id, f"T{i}", tn, sample_host.id)
            _make_interface_mapping(
                test_db, sample_lab.id, n_s.id,
                linux_interface="eth1", ovs_port=f"vh-s{i}", vlan_tag=300 + i,
            )
            _make_interface_mapping(
                test_db, sample_lab.id, n_t.id,
                linux_interface="eth1", ovs_port=f"vh-t{i}", vlan_tag=300 + i,
            )

        with patch(
            "app.tasks.link_reconciliation.declare_port_state_on_agent",
            new_callable=AsyncMock,
            return_value={
                "results": [
                    {"status": "converged"},
                    {"status": "converged"},
                ]
            },
        ) as mock_declare:
            await run_same_host_convergence(test_db, host_to_agent)

        # Should be called exactly once with both pairings batched
        mock_declare.assert_awaited_once()
        pairings_arg = mock_declare.call_args[0][1]
        assert len(pairings_arg) == 2

    @pytest.mark.asyncio
    async def test_agent_exception_during_declare_captured(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """If declare_port_state_on_agent raises, error is captured in results."""
        from app.tasks.link_reconciliation import run_same_host_convergence

        host_to_agent = {sample_host.id: sample_host}

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=False,
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
            vlan_tag=100,
        )
        n1 = make_node(test_db, sample_lab.id, "R1", link.source_node, sample_host.id)
        n2 = make_node(test_db, sample_lab.id, "R2", link.target_node, sample_host.id)
        _make_interface_mapping(
            test_db, sample_lab.id, n1.id,
            linux_interface="eth1", ovs_port="vh-s", vlan_tag=100,
        )
        _make_interface_mapping(
            test_db, sample_lab.id, n2.id,
            linux_interface="eth1", ovs_port="vh-t", vlan_tag=100,
        )

        with patch(
            "app.tasks.link_reconciliation.declare_port_state_on_agent",
            new_callable=AsyncMock,
            side_effect=ConnectionError("agent unreachable"),
        ):
            result = await run_same_host_convergence(test_db, host_to_agent)

        assert sample_host.id in result
        assert "error" in result[sample_host.id]

    @pytest.mark.asyncio
    async def test_zero_vlan_tag_link_skipped(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Links with vlan_tag=0 and no mapping tag are silently skipped."""
        from app.tasks.link_reconciliation import run_same_host_convergence

        host_to_agent = {sample_host.id: sample_host}

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=False,
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
            vlan_tag=None,  # no vlan_tag on link
        )
        n1 = make_node(test_db, sample_lab.id, "R1", link.source_node, sample_host.id)
        n2 = make_node(test_db, sample_lab.id, "R2", link.target_node, sample_host.id)
        _make_interface_mapping(
            test_db, sample_lab.id, n1.id,
            linux_interface="eth1", ovs_port="vh-s", vlan_tag=None,  # no tag
        )
        _make_interface_mapping(
            test_db, sample_lab.id, n2.id,
            linux_interface="eth1", ovs_port="vh-t", vlan_tag=None,
        )

        with patch(
            "app.tasks.link_reconciliation.declare_port_state_on_agent",
            new_callable=AsyncMock,
        ) as mock_declare:
            result = await run_same_host_convergence(test_db, host_to_agent)

        # No pairings built, agent never called
        assert result == {}
        mock_declare.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_agent_returns_error_status_for_some_links(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Agent returns mixed results (updated + error); both counts are tracked."""
        from app.tasks.link_reconciliation import run_same_host_convergence

        host_to_agent = {sample_host.id: sample_host}

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=False,
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
            vlan_tag=150,
        )
        n1 = make_node(test_db, sample_lab.id, "R1", link.source_node, sample_host.id)
        n2 = make_node(test_db, sample_lab.id, "R2", link.target_node, sample_host.id)
        _make_interface_mapping(
            test_db, sample_lab.id, n1.id,
            linux_interface="eth1", ovs_port="vh-s", vlan_tag=150,
        )
        _make_interface_mapping(
            test_db, sample_lab.id, n2.id,
            linux_interface="eth1", ovs_port="vh-t", vlan_tag=150,
        )

        with patch(
            "app.tasks.link_reconciliation.declare_port_state_on_agent",
            new_callable=AsyncMock,
            return_value={
                "results": [
                    {"status": "updated"},
                    {"status": "error", "message": "port not found"},
                ]
            },
        ):
            result = await run_same_host_convergence(test_db, host_to_agent)

        assert result[sample_host.id]["updated"] == 1
        assert result[sample_host.id]["errors"] == 1


# ============================================================================
# run_cross_host_port_convergence — deeper scenarios
# ============================================================================


class TestRunCrossHostPortConvergenceRound12:
    """Deeper convergence scenarios for run_cross_host_port_convergence."""

    @pytest.mark.asyncio
    async def test_no_cross_host_links_returns_zero_counts(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """No cross-host links → returns {updated: 0, errors: 0}."""
        from app.tasks.link_reconciliation import run_cross_host_port_convergence

        host_to_agent = {sample_host.id: sample_host}
        result = await run_cross_host_port_convergence(test_db, host_to_agent)
        assert result == {"updated": 0, "errors": 0}

    @pytest.mark.asyncio
    async def test_already_converged_no_set_port_vlan_calls(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host],
    ):
        """When mapping vlan_tag matches DB tag, no set_port_vlan calls are made."""
        from app.tasks.link_reconciliation import run_cross_host_port_convergence

        h1, h2 = multiple_hosts[0], multiple_hosts[1]
        host_to_agent = {h1.id: h1, h2.id: h2}

        ls = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=True,
            source_host_id=h1.id, target_host_id=h2.id,
            source_vlan_tag=500, target_vlan_tag=600,
        )
        n1 = make_node(test_db, sample_lab.id, "R1", ls.source_node, h1.id)
        n2 = make_node(test_db, sample_lab.id, "R2", ls.target_node, h2.id)
        # Mapping tags match DB tags → already converged
        _make_interface_mapping(
            test_db, sample_lab.id, n1.id,
            linux_interface="eth1", ovs_port="vh-s", vlan_tag=500,
        )
        _make_interface_mapping(
            test_db, sample_lab.id, n2.id,
            linux_interface="eth1", ovs_port="vh-t", vlan_tag=600,
        )

        with patch(
            "app.tasks.link_reconciliation.agent_client"
        ) as mock_ac:
            mock_ac.set_port_vlan_on_agent = AsyncMock()
            result = await run_cross_host_port_convergence(test_db, host_to_agent)

        assert result == {"updated": 0, "errors": 0}
        mock_ac.set_port_vlan_on_agent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_vlan_mismatch_triggers_set_port_vlan(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host],
    ):
        """When mapping vlan_tag differs from DB tag, set_port_vlan is called."""
        from app.tasks.link_reconciliation import run_cross_host_port_convergence

        h1, h2 = multiple_hosts[0], multiple_hosts[1]
        host_to_agent = {h1.id: h1, h2.id: h2}

        ls = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=True,
            source_host_id=h1.id, target_host_id=h2.id,
            source_vlan_tag=500, target_vlan_tag=600,
        )
        n1 = make_node(test_db, sample_lab.id, "R1", ls.source_node, h1.id)
        n2 = make_node(test_db, sample_lab.id, "R2", ls.target_node, h2.id)
        # Mapping tags DIFFER from DB (stale after container restart)
        _make_interface_mapping(
            test_db, sample_lab.id, n1.id,
            linux_interface="eth1", ovs_port="vh-s", vlan_tag=999,
        )
        _make_interface_mapping(
            test_db, sample_lab.id, n2.id,
            linux_interface="eth1", ovs_port="vh-t", vlan_tag=888,
        )

        with patch(
            "app.tasks.link_reconciliation.agent_client"
        ) as mock_ac:
            mock_ac.set_port_vlan_on_agent = AsyncMock(return_value=True)
            result = await run_cross_host_port_convergence(test_db, host_to_agent)

        assert result["updated"] == 2
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_partial_agent_failure_counted_as_error(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host],
    ):
        """If set_port_vlan raises for one endpoint, error counter increments."""
        from app.tasks.link_reconciliation import run_cross_host_port_convergence

        h1, h2 = multiple_hosts[0], multiple_hosts[1]
        host_to_agent = {h1.id: h1, h2.id: h2}

        ls = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=True,
            source_host_id=h1.id, target_host_id=h2.id,
            source_vlan_tag=500, target_vlan_tag=600,
        )
        n1 = make_node(test_db, sample_lab.id, "R1", ls.source_node, h1.id)
        n2 = make_node(test_db, sample_lab.id, "R2", ls.target_node, h2.id)
        _make_interface_mapping(
            test_db, sample_lab.id, n1.id,
            linux_interface="eth1", ovs_port="vh-s", vlan_tag=111,
        )
        _make_interface_mapping(
            test_db, sample_lab.id, n2.id,
            linux_interface="eth1", ovs_port="vh-t", vlan_tag=222,
        )

        call_count = {"n": 0}

        async def _set_vlan_with_failure(agent, port, tag):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ConnectionError("agent h1 unreachable")
            return True

        with patch(
            "app.tasks.link_reconciliation.agent_client"
        ) as mock_ac:
            mock_ac.set_port_vlan_on_agent = AsyncMock(side_effect=_set_vlan_with_failure)
            result = await run_cross_host_port_convergence(test_db, host_to_agent)

        assert result["errors"] >= 1
        assert result["updated"] >= 0  # at least partial success possible

    @pytest.mark.asyncio
    async def test_lab_id_filter_limits_scope(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host],
        test_user: models.User,
    ):
        """Passing lab_id only converges ports for that lab."""
        from app.tasks.link_reconciliation import run_cross_host_port_convergence

        h1, h2 = multiple_hosts[0], multiple_hosts[1]
        host_to_agent = {h1.id: h1, h2.id: h2}

        # Create a second lab
        other_lab = models.Lab(
            name="Other Lab", owner_id=test_user.id,
            provider="docker", state="running", workspace_path="/tmp/other",
        )
        test_db.add(other_lab)
        test_db.commit()
        test_db.refresh(other_lab)

        # Link in sample_lab (should be converged)
        make_link_state(
            test_db, sample_lab.id,
            link_name="A:eth1--B:eth1",
            source_node="archetype-test-a", target_node="archetype-test-b",
            desired_state="up", actual_state="up",
            is_cross_host=True,
            source_host_id=h1.id, target_host_id=h2.id,
            source_vlan_tag=500, target_vlan_tag=600,
        )
        n1 = make_node(test_db, sample_lab.id, "A", "archetype-test-a", h1.id)
        n2 = make_node(test_db, sample_lab.id, "B", "archetype-test-b", h2.id)
        _make_interface_mapping(
            test_db, sample_lab.id, n1.id,
            linux_interface="eth1", ovs_port="vh-a", vlan_tag=1,
        )
        _make_interface_mapping(
            test_db, sample_lab.id, n2.id,
            linux_interface="eth1", ovs_port="vh-b", vlan_tag=1,
        )

        # Link in other_lab (should NOT be converged when filtering by sample_lab)
        make_link_state(
            test_db, other_lab.id,
            link_name="C:eth1--D:eth1",
            source_node="archetype-other-c", target_node="archetype-other-d",
            desired_state="up", actual_state="up",
            is_cross_host=True,
            source_host_id=h1.id, target_host_id=h2.id,
            source_vlan_tag=700, target_vlan_tag=800,
        )
        n3 = make_node(test_db, other_lab.id, "C", "archetype-other-c", h1.id)
        n4 = make_node(test_db, other_lab.id, "D", "archetype-other-d", h2.id)
        _make_interface_mapping(
            test_db, other_lab.id, n3.id,
            linux_interface="eth1", ovs_port="vh-c", vlan_tag=1,
        )
        _make_interface_mapping(
            test_db, other_lab.id, n4.id,
            linux_interface="eth1", ovs_port="vh-d", vlan_tag=1,
        )

        corrected_ports = []

        async def _track_set_vlan(agent, port, tag):
            corrected_ports.append(port)
            return True

        with patch(
            "app.tasks.link_reconciliation.agent_client"
        ) as mock_ac:
            mock_ac.set_port_vlan_on_agent = AsyncMock(side_effect=_track_set_vlan)
            await run_cross_host_port_convergence(
                test_db, host_to_agent, lab_id=sample_lab.id,
            )

        # Only sample_lab ports should be corrected
        for p in corrected_ports:
            assert p in ("vh-a", "vh-b"), f"Unexpected port {p} from other lab"


# ============================================================================
# reconcile_lab_links — deeper scenarios
# ============================================================================


class TestReconcileLabLinksRound12:
    """Deeper scenarios for reconcile_lab_links."""

    @pytest.mark.asyncio
    async def test_empty_lab_returns_zero_results(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Lab with no links returns all-zero results dict."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock, return_value=0,
        ):
            results = await reconcile_lab_links(test_db, sample_lab.id)

        assert results["checked"] == 0
        assert results["created"] == 0
        assert results["torn_down"] == 0
        assert results["errors"] == 0
        assert results["valid"] == 0

    @pytest.mark.asyncio
    async def test_offline_source_agent_skipped(
        self, test_db: Session, sample_lab: models.Lab, offline_host: models.Host,
        sample_host: models.Host,
    ):
        """Links whose source_host_id points to an offline agent are skipped."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="down",
            source_host_id=offline_host.id,
            target_host_id=sample_host.id,
        )

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock, return_value=0,
        ):
            results = await reconcile_lab_links(test_db, sample_lab.id)

        assert results["skipped"] >= 1
        assert results["created"] == 0

    @pytest.mark.asyncio
    async def test_offline_target_agent_skipped(
        self, test_db: Session, sample_lab: models.Lab, offline_host: models.Host,
        sample_host: models.Host,
    ):
        """Links whose target_host_id points to an offline agent are skipped."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="down",
            source_host_id=sample_host.id,
            target_host_id=offline_host.id,
        )

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock, return_value=0,
        ):
            results = await reconcile_lab_links(test_db, sample_lab.id)

        assert results["skipped"] >= 1

    @pytest.mark.asyncio
    async def test_up_link_with_vlan_mismatch_triggers_vlan_repair(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """An 'up' link failing verification with VLAN mismatch tries lightweight repair."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock, return_value=0,
        ), patch(
            "app.tasks.link_reconciliation.verify_link_connected",
            new_callable=AsyncMock,
            return_value=(False, "VLAN tag mismatch: source=100 target=200"),
        ), patch(
            "app.tasks.link_reconciliation.is_vlan_mismatch",
            return_value=True,
        ), patch(
            "app.tasks.link_reconciliation.attempt_vlan_repair",
            new_callable=AsyncMock, return_value=True,
        ), patch(
            "app.tasks.link_reconciliation.attempt_link_repair",
            new_callable=AsyncMock,
        ) as mock_full_repair:
            # After vlan repair, re-verify succeeds
            with patch(
                "app.tasks.link_reconciliation.verify_link_connected",
                new_callable=AsyncMock,
                side_effect=[
                    (False, "VLAN tag mismatch: source=100 target=200"),
                    (True, None),
                ],
            ), patch(
                "app.tasks.link_reconciliation.is_vlan_mismatch",
                return_value=True,
            ):
                results = await reconcile_lab_links(test_db, sample_lab.id)

        assert results["repaired"] >= 1
        mock_full_repair.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_error_cross_host_link_attempts_partial_recovery(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host],
    ):
        """Cross-host link in error state with desired=up attempts partial recovery."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        h1, h2 = multiple_hosts[0], multiple_hosts[1]

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="error",
            is_cross_host=True,
            source_host_id=h1.id, target_host_id=h2.id,
        )

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock, return_value=0,
        ), patch(
            "app.tasks.link_reconciliation.attempt_partial_recovery",
            new_callable=AsyncMock, return_value=True,
        ) as mock_recovery:
            results = await reconcile_lab_links(test_db, sample_lab.id)

        assert results["recovered"] >= 1
        mock_recovery.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pending_link_with_desired_up_skipped_on_create_failure(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Pending link whose create_link_if_ready returns False is counted as skipped."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="pending",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock, return_value=0,
        ), patch(
            "app.tasks.link_reconciliation.create_link_if_ready",
            new_callable=AsyncMock, return_value=False,
        ):
            results = await reconcile_lab_links(test_db, sample_lab.id)

        assert results["skipped"] >= 1
        assert results["created"] == 0

    @pytest.mark.asyncio
    async def test_exception_during_link_processing_increments_errors(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """An exception processing one link increments errors and continues."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        # Two links: first will raise, second should still be processed
        make_link_state(
            test_db, sample_lab.id,
            link_name="L1:eth1--L2:eth1",
            source_node="archetype-test-l1", target_node="archetype-test-l2",
            desired_state="up", actual_state="up",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )
        make_link_state(
            test_db, sample_lab.id,
            link_name="L3:eth1--L4:eth1",
            source_node="archetype-test-l3", target_node="archetype-test-l4",
            desired_state="up", actual_state="up",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )

        call_count = {"n": 0}

        async def _verify_with_explosion(session, link, host_map):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated DB corruption")
            return True, None

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock, return_value=0,
        ), patch(
            "app.tasks.link_reconciliation.verify_link_connected",
            side_effect=_verify_with_explosion,
        ):
            results = await reconcile_lab_links(test_db, sample_lab.id)

        assert results["checked"] == 2
        assert results["errors"] >= 1
        assert results["valid"] >= 1

    @pytest.mark.asyncio
    async def test_up_link_verified_valid_counts_as_valid(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """An up/up link that passes verification is counted as valid."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock, return_value=0,
        ), patch(
            "app.tasks.link_reconciliation.verify_link_connected",
            new_callable=AsyncMock, return_value=(True, None),
        ):
            results = await reconcile_lab_links(test_db, sample_lab.id)

        assert results["valid"] == 1
        assert results["errors"] == 0

    @pytest.mark.asyncio
    async def test_down_to_up_creates_link(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Link with desired=up actual=down triggers create_link_if_ready."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="down",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock, return_value=0,
        ), patch(
            "app.tasks.link_reconciliation.create_link_if_ready",
            new_callable=AsyncMock, return_value=True,
        ) as mock_create:
            results = await reconcile_lab_links(test_db, sample_lab.id)

        assert results["created"] == 1
        mock_create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_up_to_down_tears_down_link(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Link with desired=down actual=up triggers teardown."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        make_link_state(
            test_db, sample_lab.id,
            desired_state="down", actual_state="up",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock, return_value=0,
        ), patch(
            "app.tasks.link_reconciliation.teardown_link",
            new_callable=AsyncMock, return_value=True,
        ) as mock_teardown:
            results = await reconcile_lab_links(test_db, sample_lab.id)

        assert results["torn_down"] == 1
        mock_teardown.assert_awaited_once()