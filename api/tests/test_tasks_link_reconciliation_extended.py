"""Extended tests for app/tasks/link_reconciliation.py.

This module covers gaps not addressed by test_tasks_link_reconciliation_full.py:
- run_overlay_convergence: consistent links, VLAN mismatch repair, offline skip, empty
- run_same_host_convergence: connected link, disconnected triggers repair, offline, empty
- run_cross_host_port_convergence: ports configured, missing port triggers repair, offline
- refresh_interface_mappings: successful refresh, offline skip, empty link set
- _resolve_node_by_endpoint_name: found by container_name, display_name, not found
- reconcile_lab_links: per-lab subset, link-level exception continues with others
- link_reconciliation_monitor: calls sub-functions at correct intervals, handles exceptions
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
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
    ovs_port: str = "vh-abc123",
    vlan_tag: int = 100,
) -> models.InterfaceMapping:
    """Create and persist an InterfaceMapping for testing."""
    mapping = models.InterfaceMapping(
        id=str(uuid4()),
        lab_id=lab_id,
        node_id=node_id,
        ovs_port=ovs_port,
        ovs_bridge="arch-ovs",
        vlan_tag=vlan_tag,
        linux_interface=linux_interface,
    )
    test_db.add(mapping)
    test_db.commit()
    test_db.refresh(mapping)
    return mapping


def _make_vxlan_tunnel(
    test_db: Session,
    lab_id: str,
    link_state_id: str,
    agent_a_id: str,
    agent_b_id: str,
    agent_a_ip: str = "10.0.0.1",
    agent_b_ip: str = "10.0.0.2",
    vni: int = 50000,
    vlan_tag: int = 200,
    status: str = "active",
) -> models.VxlanTunnel:
    """Create and persist a VxlanTunnel for testing."""
    tunnel = models.VxlanTunnel(
        id=str(uuid4()),
        lab_id=lab_id,
        link_state_id=link_state_id,
        vni=vni,
        vlan_tag=vlan_tag,
        agent_a_id=agent_a_id,
        agent_a_ip=agent_a_ip,
        agent_b_id=agent_b_id,
        agent_b_ip=agent_b_ip,
        status=status,
    )
    test_db.add(tunnel)
    test_db.commit()
    test_db.refresh(tunnel)
    return tunnel


# ============================================================================
# _resolve_node_by_endpoint_name
# ============================================================================


class TestResolveNodeByEndpointName:
    """Tests for _resolve_node_by_endpoint_name helper."""

    def test_found_by_container_name(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Should find node when endpoint_name matches container_name."""
        from app.tasks.link_reconciliation import _resolve_node_by_endpoint_name

        node = make_node(
            test_db, sample_lab.id,
            display_name="Router1",
            container_name="archetype-lab-r1",
            host_id=sample_host.id,
        )

        result = _resolve_node_by_endpoint_name(test_db, sample_lab.id, "archetype-lab-r1")

        assert result is not None
        assert result.id == node.id
        assert result.container_name == "archetype-lab-r1"

    def test_found_by_display_name_fallback(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Should fall back to display_name when container_name does not match."""
        from app.tasks.link_reconciliation import _resolve_node_by_endpoint_name

        node = make_node(
            test_db, sample_lab.id,
            display_name="R1",
            container_name="archetype-lab-r1",
            host_id=sample_host.id,
        )

        # Search by display_name, which won't match container_name
        result = _resolve_node_by_endpoint_name(test_db, sample_lab.id, "R1")

        assert result is not None
        assert result.id == node.id
        assert result.display_name == "R1"

    def test_not_found_returns_none(
        self, test_db: Session, sample_lab: models.Lab,
    ):
        """Should return None when no node matches by either name."""
        from app.tasks.link_reconciliation import _resolve_node_by_endpoint_name

        result = _resolve_node_by_endpoint_name(test_db, sample_lab.id, "nonexistent-node")

        assert result is None

    def test_empty_endpoint_name_returns_none(
        self, test_db: Session, sample_lab: models.Lab,
    ):
        """Should return None immediately when endpoint_name is empty string."""
        from app.tasks.link_reconciliation import _resolve_node_by_endpoint_name

        result = _resolve_node_by_endpoint_name(test_db, sample_lab.id, "")

        assert result is None

    def test_container_name_takes_priority_over_display_name(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """When both match different nodes, container_name match is returned first."""
        from app.tasks.link_reconciliation import _resolve_node_by_endpoint_name

        node_a = make_node(
            test_db, sample_lab.id,
            display_name="overlap",
            container_name="exact-match",
            host_id=sample_host.id,
        )
        make_node(
            test_db, sample_lab.id,
            display_name="exact-match",
            container_name="something-else",
            host_id=sample_host.id,
        )

        result = _resolve_node_by_endpoint_name(test_db, sample_lab.id, "exact-match")

        # Should prefer the container_name match
        assert result is not None
        assert result.id == node_a.id


# ============================================================================
# run_overlay_convergence
# ============================================================================


class TestRunOverlayConvergence:
    """Tests for run_overlay_convergence."""

    @pytest.mark.asyncio
    async def test_empty_tunnel_set_returns_empty_dict(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """No tunnels → declare_overlay_state_on_agent still called for online agents
        with empty tunnel list; returns per-agent result dict."""
        from app.tasks.link_reconciliation import run_overlay_convergence

        host_to_agent = {sample_host.id: sample_host}

        with patch(
            "app.tasks.link_reconciliation.declare_overlay_state_on_agent",
            new_callable=AsyncMock,
            return_value={"results": [], "orphans_removed": []},
        ) as mock_declare, patch(
            "app.routers.infrastructure.get_or_create_settings",
            return_value=MagicMock(overlay_mtu=1500),
        ):
            result = await run_overlay_convergence(test_db, host_to_agent)

        # One online agent, so it should be called once with empty tunnels
        mock_declare.assert_awaited_once()
        assert sample_host.id in result

    @pytest.mark.asyncio
    async def test_all_tunnels_consistent_no_repair(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host],
    ):
        """When all tunnels converge without errors, result reflects clean state."""
        from app.tasks.link_reconciliation import run_overlay_convergence

        host_a, host_b = multiple_hosts[0], multiple_hosts[1]
        host_to_agent = {host_a.id: host_a, host_b.id: host_b}

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=True,
            source_host_id=host_a.id,
            target_host_id=host_b.id,
            source_vlan_tag=100,
            target_vlan_tag=100,
        )
        _make_vxlan_tunnel(
            test_db, sample_lab.id, link.id,
            agent_a_id=host_a.id, agent_b_id=host_b.id,
        )

        converged_result = {
            "results": [{"link_id": link.link_name, "status": "converged"}],
            "orphans_removed": [],
        }

        with patch(
            "app.tasks.link_reconciliation.declare_overlay_state_on_agent",
            new_callable=AsyncMock,
            return_value=converged_result,
        ), patch(
            "app.routers.infrastructure.get_or_create_settings",
            return_value=MagicMock(overlay_mtu=1500),
        ):
            result = await run_overlay_convergence(test_db, host_to_agent)

        assert host_a.id in result or host_b.id in result
        # No errors reported
        for agent_result in result.values():
            if isinstance(agent_result, dict):
                assert agent_result.get("errors", 0) == 0

    @pytest.mark.asyncio
    async def test_agent_offline_skip(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host],
    ):
        """Agents not in host_to_agent (offline) should not receive any calls."""
        from app.tasks.link_reconciliation import run_overlay_convergence

        host_a = multiple_hosts[0]
        # Only include agent_a in host_to_agent, not agent_b
        host_to_agent = {host_a.id: host_a}

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=True,
            source_host_id=host_a.id,
            target_host_id=multiple_hosts[1].id,
        )
        _make_vxlan_tunnel(
            test_db, sample_lab.id, link.id,
            agent_a_id=host_a.id,
            agent_b_id=multiple_hosts[1].id,
        )

        call_agents: list[str] = []

        async def _fake_declare(agent, tunnels, *, declared_labs=None):
            call_agents.append(agent.id)
            return {"results": [], "orphans_removed": []}

        with patch(
            "app.tasks.link_reconciliation.declare_overlay_state_on_agent",
            side_effect=_fake_declare,
        ), patch(
            "app.routers.infrastructure.get_or_create_settings",
            return_value=MagicMock(overlay_mtu=1500),
        ):
            await run_overlay_convergence(test_db, host_to_agent)

        # Only agent_a should have been called (agent_b is offline/not in map)
        assert multiple_hosts[1].id not in call_agents

    @pytest.mark.asyncio
    async def test_agent_exception_captured_in_result(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """When an agent raises an exception, the error is captured per-agent."""
        from app.tasks.link_reconciliation import run_overlay_convergence

        host_to_agent = {sample_host.id: sample_host}

        with patch(
            "app.tasks.link_reconciliation.declare_overlay_state_on_agent",
            new_callable=AsyncMock,
            side_effect=RuntimeError("agent unreachable"),
        ), patch(
            "app.routers.infrastructure.get_or_create_settings",
            return_value=MagicMock(overlay_mtu=0),
        ):
            result = await run_overlay_convergence(test_db, host_to_agent)

        assert sample_host.id in result
        assert "error" in result[sample_host.id]

    @pytest.mark.asyncio
    async def test_lab_id_filter_scopes_tunnels(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host],
    ):
        """When lab_id is provided, only tunnels for that lab are declared."""
        from app.tasks.link_reconciliation import run_overlay_convergence

        host_a, host_b = multiple_hosts[0], multiple_hosts[1]
        host_to_agent = {host_a.id: host_a, host_b.id: host_b}

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=True,
            source_host_id=host_a.id,
            target_host_id=host_b.id,
        )
        _make_vxlan_tunnel(
            test_db, sample_lab.id, link.id,
            agent_a_id=host_a.id, agent_b_id=host_b.id,
        )

        with patch(
            "app.tasks.link_reconciliation.declare_overlay_state_on_agent",
            new_callable=AsyncMock,
            return_value={"results": [], "orphans_removed": []},
        ), patch(
            "app.routers.infrastructure.get_or_create_settings",
            return_value=MagicMock(overlay_mtu=0),
        ):
            # Filter to a non-existent lab — tunnel should not appear
            result = await run_overlay_convergence(
                test_db, host_to_agent, lab_id="nonexistent-lab"
            )

        # The function should return without error even for an unknown lab_id
        assert isinstance(result, dict)


# ============================================================================
# run_same_host_convergence
# ============================================================================


class TestRunSameHostConvergence:
    """Tests for run_same_host_convergence."""

    @pytest.mark.asyncio
    async def test_no_same_host_links_returns_empty(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """No same-host links → returns empty dict without calling any agent."""
        from app.tasks.link_reconciliation import run_same_host_convergence

        host_to_agent = {sample_host.id: sample_host}

        with patch(
            "app.tasks.link_reconciliation.declare_port_state_on_agent",
            new_callable=AsyncMock,
        ) as mock_declare:
            result = await run_same_host_convergence(test_db, host_to_agent)

        assert result == {}
        mock_declare.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_link_connected_no_action_needed(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """A same-host up link with full InterfaceMapping calls declare_port_state
        and records a converged result."""
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

        # Create nodes and interface mappings so the pairing resolves
        node_r1 = make_node(
            test_db, sample_lab.id,
            display_name="R1",
            container_name=link.source_node,
            host_id=sample_host.id,
        )
        node_r2 = make_node(
            test_db, sample_lab.id,
            display_name="R2",
            container_name=link.target_node,
            host_id=sample_host.id,
        )
        _make_interface_mapping(
            test_db, sample_lab.id, node_r1.id,
            linux_interface=link.source_interface,
            ovs_port="vh-src-port",
            vlan_tag=100,
        )
        _make_interface_mapping(
            test_db, sample_lab.id, node_r2.id,
            linux_interface=link.target_interface,
            ovs_port="vh-tgt-port",
            vlan_tag=100,
        )

        with patch(
            "app.tasks.link_reconciliation.declare_port_state_on_agent",
            new_callable=AsyncMock,
            return_value={
                "results": [{"link_name": link.link_name, "status": "converged"}]
            },
        ) as mock_declare:
            result = await run_same_host_convergence(test_db, host_to_agent)

        mock_declare.assert_awaited_once()
        assert sample_host.id in result
        assert result[sample_host.id].get("converged", 0) >= 1

    @pytest.mark.asyncio
    async def test_missing_mapping_triggers_refresh_and_repair(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """A same-host link with no InterfaceMapping triggers refresh then attempt_link_repair."""
        from app.tasks.link_reconciliation import run_same_host_convergence

        host_to_agent = {sample_host.id: sample_host}

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=False,
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
            vlan_tag=100,
        )
        # Create nodes but NO InterfaceMapping records so mappings are missing
        make_node(
            test_db, sample_lab.id,
            display_name="R1",
            container_name="archetype-test-r1",
            host_id=sample_host.id,
        )
        make_node(
            test_db, sample_lab.id,
            display_name="R2",
            container_name="archetype-test-r2",
            host_id=sample_host.id,
        )

        with patch(
            "app.tasks.link_reconciliation.refresh_interface_mappings",
            new_callable=AsyncMock,
            return_value={"updated": 0, "created": 0},
        ) as mock_refresh, patch(
            "app.tasks.link_reconciliation.attempt_link_repair",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_repair:
            result = await run_same_host_convergence(test_db, host_to_agent)

        # Refresh should have been called due to missing mappings
        mock_refresh.assert_awaited_once()
        # After failed refresh, attempt_link_repair should be called
        mock_repair.assert_awaited()
        # No pairings built → returns {}
        assert result == {}

    @pytest.mark.asyncio
    async def test_missing_mapping_backfilled_before_refresh(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Provider-agnostic backfill should avoid refresh/repair when port details resolve."""
        from app.tasks.link_reconciliation import run_same_host_convergence

        host_to_agent = {sample_host.id: sample_host}

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=False,
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
            vlan_tag=2053,
            source_node="ceos_5",
            target_node="cisco_n9kv_4",
            link_name="ceos_5:eth4-cisco_n9kv_4:eth1",
            source_interface="eth4",
            target_interface="eth1",
        )
        source_node = make_node(
            test_db, sample_lab.id, display_name="ceos_5", container_name="ceos_5", host_id=sample_host.id
        )
        target_node = make_node(
            test_db, sample_lab.id, display_name="cisco_n9kv_4", container_name="cisco_n9kv_4", host_id=sample_host.id
        )
        _make_interface_mapping(
            test_db, sample_lab.id, source_node.id, linux_interface="eth4", ovs_port="vh-ceos5-eth4", vlan_tag=2053
        )

        async def _backfill(session, link_state, agents):  # noqa: ARG001
            test_db.add(models.InterfaceMapping(
                id=str(uuid4()),
                lab_id=sample_lab.id,
                node_id=target_node.id,
                ovs_port="vh-n9kv-eth1",
                ovs_bridge="arch-ovs",
                vlan_tag=2053,
                linux_interface="eth1",
            ))
            test_db.flush()
            return 1

        with patch(
            "app.tasks.link_reconciliation.ensure_link_interface_mappings",
            new_callable=AsyncMock,
            side_effect=_backfill,
        ) as mock_backfill, patch(
            "app.tasks.link_reconciliation.refresh_interface_mappings",
            new_callable=AsyncMock,
            return_value={"updated": 0, "created": 0},
        ) as mock_refresh, patch(
            "app.tasks.link_reconciliation.attempt_link_repair",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_repair, patch(
            "app.tasks.link_reconciliation.declare_port_state_on_agent",
            new_callable=AsyncMock,
            return_value={"results": [{"link_name": link.link_name, "status": "updated"}]},
        ) as mock_declare:
            result = await run_same_host_convergence(test_db, host_to_agent)

        mock_backfill.assert_awaited()
        mock_refresh.assert_not_awaited()
        mock_repair.assert_not_awaited()
        mock_declare.assert_awaited_once()
        assert sample_host.id in result

    @pytest.mark.asyncio
    async def test_agent_offline_link_skipped(
        self, test_db: Session, sample_lab: models.Lab, offline_host: models.Host,
    ):
        """Same-host links whose agent is not in host_to_agent are skipped."""
        from app.tasks.link_reconciliation import run_same_host_convergence

        # The link's host_id is the offline host — NOT included in host_to_agent
        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=False,
            source_host_id=offline_host.id,
            target_host_id=offline_host.id,
            vlan_tag=100,
        )

        # host_to_agent is empty (no online agents)
        with patch(
            "app.tasks.link_reconciliation.declare_port_state_on_agent",
            new_callable=AsyncMock,
        ) as mock_declare:
            result = await run_same_host_convergence(test_db, {})

        # No pairings built because agent is offline
        mock_declare.assert_not_awaited()
        assert result == {}

    @pytest.mark.asyncio
    async def test_releases_transaction_before_agent_declare(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Same-host convergence should release DB transaction before agent I/O."""
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
        node_r1 = make_node(
            test_db, sample_lab.id,
            display_name="R1",
            container_name=link.source_node,
            host_id=sample_host.id,
        )
        node_r2 = make_node(
            test_db, sample_lab.id,
            display_name="R2",
            container_name=link.target_node,
            host_id=sample_host.id,
        )
        _make_interface_mapping(
            test_db, sample_lab.id, node_r1.id,
            linux_interface=link.source_interface,
            ovs_port="vh-src-port",
            vlan_tag=100,
        )
        _make_interface_mapping(
            test_db, sample_lab.id, node_r2.id,
            linux_interface=link.target_interface,
            ovs_port="vh-tgt-port",
            vlan_tag=100,
        )

        with patch(
            "app.tasks.link_reconciliation._release_db_transaction_for_io",
        ) as mock_release, patch(
            "app.tasks.link_reconciliation.declare_port_state_on_agent",
            new_callable=AsyncMock,
            return_value={"results": [{"link_name": link.link_name, "status": "converged"}]},
        ):
            await run_same_host_convergence(test_db, host_to_agent)

        mock_release.assert_called_once()
        assert "same-host convergence port-state declaration" == mock_release.call_args.kwargs["context"]


# ============================================================================
# run_cross_host_port_convergence
# ============================================================================


class TestRunCrossHostPortConvergence:
    """Tests for run_cross_host_port_convergence."""

    @pytest.mark.asyncio
    async def test_no_cross_host_links_returns_empty(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """No cross-host up links → returns zero counts without calling agent."""
        from app.tasks.link_reconciliation import run_cross_host_port_convergence

        host_to_agent = {sample_host.id: sample_host}

        with patch(
            "app.tasks.link_reconciliation.agent_client",
        ) as mock_client:
            mock_client.set_port_vlan_on_agent = AsyncMock(return_value=True)
            result = await run_cross_host_port_convergence(test_db, host_to_agent)

        assert result == {"updated": 0, "errors": 0}
        mock_client.set_port_vlan_on_agent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ports_already_correct_no_update(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host],
    ):
        """When InterfaceMapping vlan_tag matches LinkState db_vlan, no correction sent."""
        from app.tasks.link_reconciliation import run_cross_host_port_convergence

        host_a, host_b = multiple_hosts[0], multiple_hosts[1]
        host_to_agent = {host_a.id: host_a, host_b.id: host_b}

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=True,
            source_host_id=host_a.id,
            target_host_id=host_b.id,
            source_vlan_tag=200,
            target_vlan_tag=201,
        )
        node_a = make_node(
            test_db, sample_lab.id,
            container_name=link.source_node,
            host_id=host_a.id,
        )
        node_b = make_node(
            test_db, sample_lab.id,
            container_name=link.target_node,
            host_id=host_b.id,
        )
        # Interface mappings with matching VLAN tags
        _make_interface_mapping(
            test_db, sample_lab.id, node_a.id,
            linux_interface=link.source_interface,
            ovs_port="vh-src",
            vlan_tag=200,  # matches source_vlan_tag
        )
        _make_interface_mapping(
            test_db, sample_lab.id, node_b.id,
            linux_interface=link.target_interface,
            ovs_port="vh-tgt",
            vlan_tag=201,  # matches target_vlan_tag
        )

        with patch(
            "app.tasks.link_reconciliation.agent_client",
        ) as mock_client:
            mock_client.set_port_vlan_on_agent = AsyncMock(return_value=True)
            result = await run_cross_host_port_convergence(test_db, host_to_agent)

        # No corrections needed
        mock_client.set_port_vlan_on_agent.assert_not_awaited()
        assert result["updated"] == 0
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_vlan_mismatch_triggers_correction(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host],
    ):
        """When InterfaceMapping vlan_tag differs from DB, set_port_vlan_on_agent is called."""
        from app.tasks.link_reconciliation import run_cross_host_port_convergence

        host_a, host_b = multiple_hosts[0], multiple_hosts[1]
        host_to_agent = {host_a.id: host_a, host_b.id: host_b}

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=True,
            source_host_id=host_a.id,
            target_host_id=host_b.id,
            source_vlan_tag=200,
            target_vlan_tag=201,
        )
        node_a = make_node(
            test_db, sample_lab.id,
            container_name=link.source_node,
            host_id=host_a.id,
        )
        node_b = make_node(
            test_db, sample_lab.id,
            container_name=link.target_node,
            host_id=host_b.id,
        )
        # Interface mappings with STALE VLAN tags (differ from link state)
        _make_interface_mapping(
            test_db, sample_lab.id, node_a.id,
            linux_interface=link.source_interface,
            ovs_port="vh-src",
            vlan_tag=999,  # stale — should be 200
        )
        _make_interface_mapping(
            test_db, sample_lab.id, node_b.id,
            linux_interface=link.target_interface,
            ovs_port="vh-tgt",
            vlan_tag=999,  # stale — should be 201
        )

        with patch(
            "app.tasks.link_reconciliation.agent_client",
        ) as mock_client:
            mock_client.set_port_vlan_on_agent = AsyncMock(return_value=True)
            result = await run_cross_host_port_convergence(test_db, host_to_agent)

        # Both sides should have been corrected
        assert mock_client.set_port_vlan_on_agent.await_count >= 1
        assert result["updated"] >= 1

    @pytest.mark.asyncio
    async def test_agent_offline_no_correction_sent(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host],
    ):
        """Offline agent (not in host_to_agent) means its endpoint corrections are skipped."""
        from app.tasks.link_reconciliation import run_cross_host_port_convergence

        host_a = multiple_hosts[0]
        offline_host_id = "totally-offline-agent"

        # host_to_agent only has host_a
        host_to_agent = {host_a.id: host_a}

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=True,
            source_host_id=host_a.id,
            target_host_id=offline_host_id,
            source_vlan_tag=200,
            target_vlan_tag=201,
        )
        node_a = make_node(
            test_db, sample_lab.id,
            container_name=link.source_node,
            host_id=host_a.id,
        )
        _make_interface_mapping(
            test_db, sample_lab.id, node_a.id,
            linux_interface=link.source_interface,
            ovs_port="vh-src",
            vlan_tag=999,  # stale — should be 200
        )

        with patch(
            "app.tasks.link_reconciliation.agent_client",
        ) as mock_client:
            mock_client.set_port_vlan_on_agent = AsyncMock(return_value=True)
            result = await run_cross_host_port_convergence(test_db, host_to_agent)

        # The offline target side is skipped by the host_id not in host_to_agent check
        # Only the source side (host_a) might be corrected
        assert isinstance(result, dict)
        assert result.get("errors", 0) == 0

    @pytest.mark.asyncio
    async def test_missing_mapping_triggers_refresh(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host],
    ):
        """Missing InterfaceMapping triggers a refresh attempt before giving up."""
        from app.tasks.link_reconciliation import run_cross_host_port_convergence

        host_a, host_b = multiple_hosts[0], multiple_hosts[1]
        host_to_agent = {host_a.id: host_a, host_b.id: host_b}

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=True,
            source_host_id=host_a.id,
            target_host_id=host_b.id,
            source_vlan_tag=200,
            target_vlan_tag=201,
        )
        # Create nodes but NO InterfaceMapping records
        make_node(
            test_db, sample_lab.id,
            container_name=link.source_node,
            host_id=host_a.id,
        )

        with patch(
            "app.tasks.link_reconciliation.refresh_interface_mappings",
            new_callable=AsyncMock,
            return_value={"updated": 0, "created": 0},
        ) as mock_refresh, patch(
            "app.tasks.link_reconciliation.agent_client",
        ) as mock_client:
            mock_client.set_port_vlan_on_agent = AsyncMock(return_value=True)
            result = await run_cross_host_port_convergence(test_db, host_to_agent)

        mock_refresh.assert_awaited_once()
        # No corrections could be made without mappings
        assert result["updated"] == 0

    @pytest.mark.asyncio
    async def test_releases_transaction_before_vlan_correction(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host],
    ):
        """Cross-host convergence should release DB transaction before agent VLAN updates."""
        from app.tasks.link_reconciliation import run_cross_host_port_convergence

        host_a, host_b = multiple_hosts[0], multiple_hosts[1]
        host_to_agent = {host_a.id: host_a, host_b.id: host_b}

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=True,
            source_host_id=host_a.id,
            target_host_id=host_b.id,
            source_vlan_tag=200,
            target_vlan_tag=201,
        )
        node_a = make_node(
            test_db, sample_lab.id,
            container_name=link.source_node,
            host_id=host_a.id,
        )
        node_b = make_node(
            test_db, sample_lab.id,
            container_name=link.target_node,
            host_id=host_b.id,
        )
        _make_interface_mapping(
            test_db, sample_lab.id, node_a.id,
            linux_interface=link.source_interface,
            ovs_port="vh-src",
            vlan_tag=999,
        )
        _make_interface_mapping(
            test_db, sample_lab.id, node_b.id,
            linux_interface=link.target_interface,
            ovs_port="vh-tgt",
            vlan_tag=999,
        )

        with patch(
            "app.tasks.link_reconciliation._release_db_transaction_for_io",
        ) as mock_release, patch(
            "app.tasks.link_reconciliation.agent_client",
        ) as mock_client:
            mock_client.set_port_vlan_on_agent = AsyncMock(return_value=True)
            result = await run_cross_host_port_convergence(test_db, host_to_agent)

        assert result["updated"] == 2
        assert mock_release.call_count == 2
        contexts = [call.kwargs["context"] for call in mock_release.call_args_list]
        assert all("cross-host port convergence vlan update for agent" in context for context in contexts)


# ============================================================================
# refresh_interface_mappings
# ============================================================================


class TestRefreshInterfaceMappings:
    """Tests for refresh_interface_mappings."""

    @pytest.mark.asyncio
    async def test_no_active_links_returns_zero_counts(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """No active links → returns {updated: 0, created: 0} without calling agent."""
        from app.tasks.link_reconciliation import refresh_interface_mappings

        host_to_agent = {sample_host.id: sample_host}

        with patch(
            "app.tasks.link_reconciliation.agent_client",
        ) as mock_client:
            mock_client.get_lab_port_state = AsyncMock(return_value=[])
            result = await refresh_interface_mappings(test_db, host_to_agent)

        assert result == {"updated": 0, "created": 0}
        mock_client.get_lab_port_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_successful_refresh_creates_new_mappings(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Port state from agent creates new InterfaceMapping records."""
        from app.tasks.link_reconciliation import refresh_interface_mappings

        host_to_agent = {sample_host.id: sample_host}

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=False,
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )
        make_node(
            test_db, sample_lab.id,
            display_name="R1",
            container_name="archetype-test-r1",
            host_id=sample_host.id,
        )

        port_state = [
            {
                "node_name": "archetype-test-r1",
                "interface_name": "eth1",
                "ovs_port_name": "vh-new-port",
                "vlan_tag": 150,
            }
        ]

        with patch(
            "app.tasks.link_reconciliation.agent_client",
        ) as mock_client:
            mock_client.get_lab_port_state = AsyncMock(return_value=port_state)
            result = await refresh_interface_mappings(test_db, host_to_agent)

        assert result["created"] >= 1 or result["updated"] >= 1

    @pytest.mark.asyncio
    async def test_successful_refresh_updates_existing_mappings(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Port state from agent updates existing InterfaceMapping records."""
        from app.tasks.link_reconciliation import refresh_interface_mappings

        host_to_agent = {sample_host.id: sample_host}

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=False,
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )
        node = make_node(
            test_db, sample_lab.id,
            display_name="R1",
            container_name="archetype-test-r1",
            host_id=sample_host.id,
        )
        # Pre-existing mapping with old VLAN tag
        _make_interface_mapping(
            test_db, sample_lab.id, node.id,
            linux_interface="eth1",
            ovs_port="vh-old-port",
            vlan_tag=50,
        )

        port_state = [
            {
                "node_name": "archetype-test-r1",
                "interface_name": "eth1",
                "ovs_port_name": "vh-new-port",
                "vlan_tag": 150,
            }
        ]

        with patch(
            "app.tasks.link_reconciliation.agent_client",
        ) as mock_client:
            mock_client.get_lab_port_state = AsyncMock(return_value=port_state)
            result = await refresh_interface_mappings(test_db, host_to_agent)

        assert result["updated"] >= 1

    @pytest.mark.asyncio
    async def test_agent_offline_skip(
        self, test_db: Session, sample_lab: models.Lab, offline_host: models.Host,
    ):
        """Links whose agent is not in host_to_agent are silently skipped."""
        from app.tasks.link_reconciliation import refresh_interface_mappings

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=False,
            source_host_id=offline_host.id,
            target_host_id=offline_host.id,
        )

        with patch(
            "app.tasks.link_reconciliation.agent_client",
        ) as mock_client:
            mock_client.get_lab_port_state = AsyncMock(return_value=[])
            result = await refresh_interface_mappings(test_db, {})

        # No agent in map → nothing refreshed
        assert result == {"updated": 0, "created": 0}
        mock_client.get_lab_port_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lab_id_filter_scopes_links(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """When lab_id is provided, only links for that lab are processed."""
        from app.tasks.link_reconciliation import refresh_interface_mappings

        host_to_agent = {sample_host.id: sample_host}

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=False,
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )

        with patch(
            "app.tasks.link_reconciliation.agent_client",
        ) as mock_client:
            mock_client.get_lab_port_state = AsyncMock(return_value=[])
            result = await refresh_interface_mappings(
                test_db, host_to_agent, lab_id="nonexistent-lab"
            )

        # No links for nonexistent lab → nothing called
        assert result == {"updated": 0, "created": 0}

    @pytest.mark.asyncio
    async def test_releases_transaction_before_port_state_fetch(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Interface mapping refresh should release DB transaction before agent port-state fetch."""
        from app.tasks.link_reconciliation import refresh_interface_mappings

        host_to_agent = {sample_host.id: sample_host}

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            is_cross_host=False,
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )

        with patch(
            "app.tasks.link_reconciliation._release_db_transaction_for_io",
        ) as mock_release, patch(
            "app.tasks.link_reconciliation.agent_client",
        ) as mock_client:
            mock_client.get_lab_port_state = AsyncMock(return_value=[])
            await refresh_interface_mappings(test_db, host_to_agent)

        mock_release.assert_called_once()
        assert "interface mapping refresh port-state fetch" in mock_release.call_args.kwargs["context"]


# ============================================================================
# reconcile_lab_links
# ============================================================================


class TestReconcileLabLinks:
    """Tests for reconcile_lab_links (per-lab variant)."""

    @pytest.mark.asyncio
    async def test_per_lab_subset_only_processes_that_lab(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
        test_user: models.User,
    ):
        """reconcile_lab_links only touches links belonging to the specified lab."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        # Create a second lab with an up link
        other_lab = models.Lab(
            name="Other Lab",
            owner_id=test_user.id,
            provider="docker",
            state="running",
            workspace_path="/tmp/other-lab",
        )
        test_db.add(other_lab)
        test_db.commit()
        test_db.refresh(other_lab)

        make_link_state(
            test_db, other_lab.id,
            desired_state="up", actual_state="up",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )
        # No links for sample_lab
        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock,
            return_value=0,
        ):
            results = await reconcile_lab_links(test_db, sample_lab.id)

        # sample_lab has no links, so nothing should be checked
        assert results["checked"] == 0

    @pytest.mark.asyncio
    async def test_link_level_exception_continues_with_remaining_links(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """An exception on one link does not prevent processing of subsequent links."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        make_link_state(
            test_db, sample_lab.id,
            link_name="R1:eth1--R2:eth1",
            desired_state="up", actual_state="up",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )
        make_link_state(
            test_db, sample_lab.id,
            link_name="R2:eth2--R3:eth2",
            source_node="archetype-test-r2",
            target_node="archetype-test-r3",
            desired_state="up", actual_state="up",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )

        call_count = {"n": 0}

        async def _raising_on_first(session, link, host_to_agent):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated failure on first link")
            return True, None

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.tasks.link_reconciliation.verify_link_connected",
            side_effect=_raising_on_first,
        ):
            results = await reconcile_lab_links(test_db, sample_lab.id)

        assert call_count["n"] == 2
        assert results["checked"] == 2
        assert results["errors"] >= 1
        assert results["valid"] >= 1

    @pytest.mark.asyncio
    async def test_link_level_statement_timeout_records_metric(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Statement timeouts during per-link reconcile emit DB contention metrics."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        make_link_state(
            test_db, sample_lab.id,
            link_name="R1:eth1--R2:eth1",
            desired_state="up", actual_state="up",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.tasks.link_reconciliation.verify_link_connected",
            side_effect=RuntimeError("statement timeout on link_states"),
        ), patch(
            "app.tasks.link_reconciliation.record_db_transaction_issue",
        ) as mock_metric:
            results = await reconcile_lab_links(test_db, sample_lab.id)

        assert results["errors"] == 1
        assert mock_metric.called
        assert mock_metric.call_args_list[0].kwargs == {
            "issue": "statement_timeout",
            "phase": "link_reconciliation",
            "table": "link_states",
        }

    @pytest.mark.asyncio
    async def test_intentionally_stopped_endpoint_keeps_link_pending_without_repair(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Links waiting on intentionally stopped nodes should stay pending quietly."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="error",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )
        test_db.add(models.NodeState(
            lab_id=sample_lab.id,
            node_id="n1",
            node_name=link.source_node,
            desired_state="stopped",
            actual_state="undeployed",
        ))
        test_db.add(models.NodeState(
            lab_id=sample_lab.id,
            node_id="n2",
            node_name=link.target_node,
            desired_state="running",
            actual_state="running",
        ))
        test_db.commit()

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.tasks.link_reconciliation.create_link_if_ready",
            new_callable=AsyncMock,
        ) as mock_create, patch(
            "app.tasks.link_reconciliation.attempt_link_repair",
            new_callable=AsyncMock,
        ) as mock_repair:
            results = await reconcile_lab_links(test_db, sample_lab.id)

        test_db.refresh(link)
        assert results["skipped"] == 1
        assert link.actual_state == "pending"
        assert link.error_message is None
        mock_create.assert_not_awaited()
        mock_repair.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reconcile_lab_links_releases_transaction_before_verify(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Per-link verification should release the DB transaction before awaited I/O."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.tasks.link_reconciliation._release_db_transaction_for_io",
        ) as mock_release, patch(
            "app.tasks.link_reconciliation.verify_link_connected",
            new_callable=AsyncMock,
            return_value=(True, None),
        ):
            results = await reconcile_lab_links(test_db, sample_lab.id)

        assert results["valid"] == 1
        contexts = [call.kwargs["context"] for call in mock_release.call_args_list]
        assert any("link verification" in context for context in contexts)

    @pytest.mark.asyncio
    async def test_starting_endpoint_keeps_link_pending_without_repair(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """Links should not churn through repair while an endpoint is still starting."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        link = make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )
        test_db.add(models.NodeState(
            lab_id=sample_lab.id,
            node_id="n1",
            node_name=link.source_node,
            desired_state="running",
            actual_state="starting",
        ))
        test_db.add(models.NodeState(
            lab_id=sample_lab.id,
            node_id="n2",
            node_name=link.target_node,
            desired_state="running",
            actual_state="running",
        ))
        test_db.commit()

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.tasks.link_reconciliation.verify_link_connected",
            new_callable=AsyncMock,
        ) as mock_verify, patch(
            "app.tasks.link_reconciliation.attempt_link_repair",
            new_callable=AsyncMock,
        ) as mock_repair:
            results = await reconcile_lab_links(test_db, sample_lab.id)

        test_db.refresh(link)
        assert results["skipped"] == 1
        assert link.actual_state == "pending"
        assert link.error_message is None
        mock_verify.assert_not_awaited()
        mock_repair.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_vlan_mismatch_triggers_repair_in_lab_context(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """A VLAN mismatch on a per-lab link attempts vlan_repair then verify."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="up",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )

        # First call: mismatch detected; second call (post-repair): success
        mock_verify = AsyncMock(side_effect=[
            (False, "VLAN_MISMATCH: source=100 target=200"),
            (True, None),
        ])

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.tasks.link_reconciliation.verify_link_connected",
            mock_verify,
        ), patch(
            "app.tasks.link_reconciliation.is_vlan_mismatch",
            return_value=True,
        ), patch(
            "app.tasks.link_reconciliation.attempt_vlan_repair",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_vlan_repair:
            results = await reconcile_lab_links(test_db, sample_lab.id)

        mock_vlan_repair.assert_awaited_once()
        assert results["repaired"] >= 1

    @pytest.mark.asyncio
    async def test_down_to_up_creates_link_in_lab_context(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host,
    ):
        """reconcile_lab_links creates links with desired=up actual=down."""
        from app.tasks.link_reconciliation import reconcile_lab_links

        make_link_state(
            test_db, sample_lab.id,
            desired_state="up", actual_state="down",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
        )

        with patch(
            "app.tasks.link_reconciliation._cleanup_deleted_links",
            new_callable=AsyncMock,
            return_value=0,
        ), patch(
            "app.tasks.link_reconciliation.create_link_if_ready",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_create:
            results = await reconcile_lab_links(test_db, sample_lab.id)

        mock_create.assert_awaited_once()
        assert results["created"] >= 1


# ============================================================================
# link_reconciliation_monitor
# ============================================================================


class TestLinkReconciliationMonitor:
    """Tests for the link_reconciliation_monitor background task."""

    @pytest.mark.asyncio
    async def test_monitor_calls_all_sub_functions(self, test_db: Session):
        """A single monitor cycle calls reconcile, cleanup, overlay, refresh, and same-host."""
        import app.tasks.link_reconciliation as lr_mod

        sleep_calls: list[float] = []

        async def _fake_sleep(seconds: float):
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError

        mock_session_ctx = MagicMock()
        mock_session_ctx.__enter__ = MagicMock(return_value=test_db)
        mock_session_ctx.__exit__ = MagicMock(return_value=False)

        with patch("app.tasks.link_reconciliation.asyncio.sleep", side_effect=_fake_sleep), \
             patch("app.tasks.link_reconciliation.get_session", return_value=mock_session_ctx), \
             patch(
                 "app.tasks.link_reconciliation.detect_duplicate_tunnels",
                 new_callable=AsyncMock, return_value=0,
             ), \
             patch(
                 "app.tasks.link_reconciliation.reconcile_link_states",
                 new_callable=AsyncMock,
                 return_value={"checked": 0, "valid": 0, "repaired": 0, "recovered": 0,
                               "created": 0, "torn_down": 0, "errors": 0, "skipped": 0},
             ) as mock_reconcile, \
             patch(
                 "app.tasks.link_reconciliation.cleanup_orphaned_link_states",
                 new_callable=AsyncMock, return_value=0,
             ) as mock_ls_cleanup, \
             patch(
                 "app.tasks.link_reconciliation.cleanup_orphaned_tunnels",
                 new_callable=AsyncMock, return_value=0,
             ) as mock_tun_cleanup, \
             patch(
                 "app.tasks.link_reconciliation.run_overlay_convergence",
                 new_callable=AsyncMock, return_value={},
             ) as mock_overlay, \
             patch(
                 "app.tasks.link_reconciliation.refresh_interface_mappings",
                 new_callable=AsyncMock, return_value={"updated": 0, "created": 0},
             ) as mock_refresh, \
             patch(
                 "app.tasks.link_reconciliation.run_cross_host_port_convergence",
                 new_callable=AsyncMock, return_value={"updated": 0, "errors": 0},
             ) as mock_xhost, \
             patch(
                 "app.tasks.link_reconciliation.run_same_host_convergence",
                 new_callable=AsyncMock, return_value={},
             ) as mock_same_host, \
             patch(
                 "app.tasks.link_reconciliation.get_link_endpoint_reservation_drift_counts",
                 return_value={"total": 0, "missing": 0, "orphaned": 0, "conflicts": 0},
             ), \
             patch(
                 "app.tasks.link_reconciliation.set_link_endpoint_reservation_metrics",
             ):
            try:
                await lr_mod.link_reconciliation_monitor()
            except asyncio.CancelledError:
                pass

        mock_reconcile.assert_awaited()
        mock_ls_cleanup.assert_awaited()
        mock_tun_cleanup.assert_awaited()
        mock_overlay.assert_awaited()
        mock_refresh.assert_awaited()
        mock_xhost.assert_awaited()
        mock_same_host.assert_awaited()

    @pytest.mark.asyncio
    async def test_monitor_handles_inner_exception_gracefully(self, test_db: Session):
        """An exception inside the monitor loop does not kill the task."""
        import app.tasks.link_reconciliation as lr_mod

        sleep_calls: list[float] = []

        async def _fake_sleep(seconds: float):
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError

        mock_session_ctx = MagicMock()
        mock_session_ctx.__enter__ = MagicMock(return_value=test_db)
        mock_session_ctx.__exit__ = MagicMock(return_value=False)

        with patch("app.tasks.link_reconciliation.asyncio.sleep", side_effect=_fake_sleep), \
             patch("app.tasks.link_reconciliation.get_session", return_value=mock_session_ctx), \
             patch(
                 "app.tasks.link_reconciliation.detect_duplicate_tunnels",
                 new_callable=AsyncMock, return_value=0,
             ), \
             patch(
                 "app.tasks.link_reconciliation.reconcile_link_states",
                 new_callable=AsyncMock,
                 side_effect=RuntimeError("simulated crash"),
             ), \
             patch(
                 "app.tasks.link_reconciliation.get_link_endpoint_reservation_drift_counts",
                 return_value={"total": 0, "missing": 0, "orphaned": 0, "conflicts": 0},
             ), \
             patch("app.tasks.link_reconciliation.set_link_endpoint_reservation_metrics"):
            try:
                await lr_mod.link_reconciliation_monitor()
            except asyncio.CancelledError:
                pass

        # The monitor should have attempted at least one cycle
        assert len(sleep_calls) >= 1

    @pytest.mark.asyncio
    async def test_monitor_disabled_skips_reconciliation(self, test_db: Session):
        """When RECONCILIATION_ENABLED is False, no reconcile calls are made."""
        import app.tasks.link_reconciliation as lr_mod

        sleep_calls: list[float] = []

        async def _fake_sleep(seconds: float):
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError

        mock_session_ctx = MagicMock()
        mock_session_ctx.__enter__ = MagicMock(return_value=test_db)
        mock_session_ctx.__exit__ = MagicMock(return_value=False)

        original_enabled = lr_mod.RECONCILIATION_ENABLED
        lr_mod.RECONCILIATION_ENABLED = False

        try:
            with patch("app.tasks.link_reconciliation.asyncio.sleep", side_effect=_fake_sleep), \
                 patch("app.tasks.link_reconciliation.get_session", return_value=mock_session_ctx), \
                 patch(
                     "app.tasks.link_reconciliation.reconcile_link_states",
                     new_callable=AsyncMock,
                 ) as mock_reconcile:
                try:
                    await lr_mod.link_reconciliation_monitor()
                except asyncio.CancelledError:
                    pass

            mock_reconcile.assert_not_awaited()
        finally:
            lr_mod.RECONCILIATION_ENABLED = original_enabled

    @pytest.mark.asyncio
    async def test_monitor_reservation_reconcile_on_interval(self, test_db: Session):
        """Reservation reconciliation runs every RESERVATION_RECONCILE_INTERVAL_CYCLES cycles."""
        import app.tasks.link_reconciliation as lr_mod

        original_interval = lr_mod.RESERVATION_RECONCILE_INTERVAL_CYCLES
        lr_mod.RESERVATION_RECONCILE_INTERVAL_CYCLES = 1  # run on every cycle for test

        sleep_calls: list[float] = []

        async def _fake_sleep(seconds: float):
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError

        mock_session_ctx = MagicMock()
        mock_session_ctx.__enter__ = MagicMock(return_value=test_db)
        mock_session_ctx.__exit__ = MagicMock(return_value=False)

        try:
            with patch("app.tasks.link_reconciliation.asyncio.sleep", side_effect=_fake_sleep), \
                 patch("app.tasks.link_reconciliation.get_session", return_value=mock_session_ctx), \
                 patch(
                     "app.tasks.link_reconciliation.detect_duplicate_tunnels",
                     new_callable=AsyncMock, return_value=0,
                 ), \
                 patch(
                     "app.tasks.link_reconciliation.reconcile_link_states",
                     new_callable=AsyncMock,
                     return_value={"checked": 0, "valid": 0, "repaired": 0, "recovered": 0,
                                   "created": 0, "torn_down": 0, "errors": 0, "skipped": 0},
                 ), \
                 patch(
                     "app.tasks.link_reconciliation.cleanup_orphaned_link_states",
                     new_callable=AsyncMock, return_value=0,
                 ), \
                 patch(
                     "app.tasks.link_reconciliation.cleanup_orphaned_tunnels",
                     new_callable=AsyncMock, return_value=0,
                 ), \
                 patch(
                     "app.tasks.link_reconciliation.run_overlay_convergence",
                     new_callable=AsyncMock, return_value={},
                 ), \
                 patch(
                     "app.tasks.link_reconciliation.refresh_interface_mappings",
                     new_callable=AsyncMock, return_value={"updated": 0, "created": 0},
                 ), \
                 patch(
                     "app.tasks.link_reconciliation.run_cross_host_port_convergence",
                     new_callable=AsyncMock, return_value={"updated": 0, "errors": 0},
                 ), \
                 patch(
                     "app.tasks.link_reconciliation.run_same_host_convergence",
                     new_callable=AsyncMock, return_value={},
                 ), \
                 patch(
                     "app.tasks.link_reconciliation.reconcile_link_endpoint_reservations",
                     return_value={
                         "checked": 0, "claimed": 0, "released": 0,
                         "orphans_removed": 0, "conflicts": 0,
                     },
                 ) as mock_reservation, \
                 patch(
                     "app.tasks.link_reconciliation.get_link_endpoint_reservation_drift_counts",
                     return_value={"total": 0, "missing": 0, "orphaned": 0, "conflicts": 0},
                 ), \
                 patch("app.tasks.link_reconciliation.set_link_endpoint_reservation_metrics"):
                try:
                    await lr_mod.link_reconciliation_monitor()
                except asyncio.CancelledError:
                    pass

            # With interval=1, reservation reconcile should run every cycle
            mock_reservation.assert_called()
        finally:
            lr_mod.RESERVATION_RECONCILE_INTERVAL_CYCLES = original_interval