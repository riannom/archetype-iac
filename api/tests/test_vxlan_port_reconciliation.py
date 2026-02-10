"""Tests for VXLAN port reconciliation.

Covers:
1. compute_vxlan_port_name() hash consistency
2. reconcile_vxlan_ports_on_agent() HTTP client
3. VXLAN port reconciliation in _maybe_cleanup_labless_containers()
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import agent_client, models
from app.tasks.reconciliation import _maybe_cleanup_labless_containers


# ---------------------------------------------------------------------------
# 1. compute_vxlan_port_name() tests
# ---------------------------------------------------------------------------


class TestComputeVxlanPortName:
    """Tests for the deterministic VXLAN port name hash."""

    def test_basic_hash(self):
        """Produces a vxlan-{8-hex-chars} name."""
        result = agent_client.compute_vxlan_port_name("lab-1", "R1:eth1-R2:eth1")
        assert result.startswith("vxlan-")
        assert len(result) == 14  # "vxlan-" (6) + 8 hex chars

    def test_deterministic(self):
        """Same inputs always produce the same port name."""
        a = agent_client.compute_vxlan_port_name("lab-1", "R1:eth1-R2:eth1")
        b = agent_client.compute_vxlan_port_name("lab-1", "R1:eth1-R2:eth1")
        assert a == b

    def test_different_lab_different_hash(self):
        """Different lab IDs produce different port names."""
        a = agent_client.compute_vxlan_port_name("lab-1", "R1:eth1-R2:eth1")
        b = agent_client.compute_vxlan_port_name("lab-2", "R1:eth1-R2:eth1")
        assert a != b

    def test_different_link_different_hash(self):
        """Different link names produce different port names."""
        a = agent_client.compute_vxlan_port_name("lab-1", "R1:eth1-R2:eth1")
        b = agent_client.compute_vxlan_port_name("lab-1", "R1:eth2-R3:eth1")
        assert a != b

    def test_matches_agent_overlay_logic(self):
        """Must match agent/network/overlay.py:_link_tunnel_interface_name()."""
        lab_id = "abc-123"
        link_id = "ceos_1:eth2-ceos_4:eth1"
        combined = f"{lab_id}:{link_id}"
        expected = f"vxlan-{hashlib.md5(combined.encode()).hexdigest()[:8]}"
        assert agent_client.compute_vxlan_port_name(lab_id, link_id) == expected

    def test_within_ovs_name_limit(self):
        """Port name must be <= 15 chars (OVS interface name limit)."""
        # Even with long inputs, the hash truncation keeps it at 14 chars.
        result = agent_client.compute_vxlan_port_name(
            "very-long-lab-id-that-could-be-a-uuid",
            "some-really-long-node-name:eth999-another-long-node:eth999",
        )
        assert len(result) <= 15


# ---------------------------------------------------------------------------
# 2. reconcile_vxlan_ports_on_agent() tests
# ---------------------------------------------------------------------------


class TestReconcileVxlanPortsOnAgent:
    """Tests for the agent client HTTP call."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Successful call returns agent response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"removed_ports": ["vxlan-dead1234"], "valid_count": 2}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        agent = MagicMock(spec=models.Host)
        agent.id = "agent-1"
        agent.address = "http://10.0.0.1:8001"

        with patch.object(agent_client, "get_http_client", return_value=mock_client):
            with patch.object(agent_client, "get_agent_url", return_value="http://10.0.0.1:8001"):
                result = await agent_client.reconcile_vxlan_ports_on_agent(
                    agent, ["vxlan-aabb1122", "vxlan-ccdd3344"]
                )

        assert result == {"removed_ports": ["vxlan-dead1234"], "valid_count": 2}
        mock_client.post.assert_called_once_with(
            "http://10.0.0.1:8001/overlay/reconcile-ports",
            json={
                "valid_port_names": ["vxlan-aabb1122", "vxlan-ccdd3344"],
                "force": False,
                "confirm": False,
                "allow_empty": False,
            },
            timeout=60.0,
        )

    @pytest.mark.asyncio
    async def test_connection_error_returns_empty(self):
        """Connection errors return empty removed_ports list, not exception."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))

        agent = MagicMock(spec=models.Host)
        agent.id = "agent-1"
        agent.address = "http://10.0.0.1:8001"

        with patch.object(agent_client, "get_http_client", return_value=mock_client):
            with patch.object(agent_client, "get_agent_url", return_value="http://10.0.0.1:8001"):
                result = await agent_client.reconcile_vxlan_ports_on_agent(agent, [])

        assert result["removed_ports"] == []
        assert "errors" in result


# ---------------------------------------------------------------------------
# 3. VXLAN reconciliation in _maybe_cleanup_labless_containers()
# ---------------------------------------------------------------------------


class TestVxlanReconciliationInCleanup:
    """Tests for the VXLAN reconciliation wired into the periodic cleanup."""

    @pytest.mark.asyncio
    async def test_reconcile_sends_correct_ports_per_agent(
        self, test_db, sample_lab, sample_vxlan_tunnel, multiple_hosts
    ):
        """Active tunnels generate correct port names sent to each agent."""
        import app.tasks.reconciliation as recon_mod

        # Force the orphan counter past the interval
        recon_mod._lab_orphan_check_counter = recon_mod._LAB_ORPHAN_CHECK_INTERVAL - 1

        # Pre-compute expected port name for the sample tunnel
        link_state = test_db.get(models.LinkState, sample_vxlan_tunnel.link_state_id)
        expected_port = agent_client.compute_vxlan_port_name(
            str(sample_vxlan_tunnel.lab_id), link_state.link_name
        )

        reconcile_mock = AsyncMock(return_value={"removed_ports": []})

        with patch.object(agent_client, "is_agent_online", return_value=True):
            with patch.object(agent_client, "cleanup_orphans_on_agent", new_callable=AsyncMock) as mock_cleanup:
                mock_cleanup.return_value = {"removed_containers": []}
                with patch.object(agent_client, "reconcile_vxlan_ports_on_agent", reconcile_mock):
                    await _maybe_cleanup_labless_containers(test_db)

        # Should have been called for all online agents (agent-1, agent-2, agent-3)
        assert reconcile_mock.call_count == 3

        # Collect the valid_port_names sent to each agent
        calls_by_agent = {}
        for call in reconcile_mock.call_args_list:
            agent_arg = call[0][0]
            ports_arg = call[0][1]
            calls_by_agent[agent_arg.id] = ports_arg

        # Tunnel endpoint agents should get the expected port name
        assert expected_port in calls_by_agent.get("agent-1", [])
        assert expected_port in calls_by_agent.get("agent-2", [])
        # Agent-3 is not a tunnel endpoint, so it gets an empty list
        assert calls_by_agent.get("agent-3", []) == []

    @pytest.mark.asyncio
    async def test_reconcile_skips_offline_agents(
        self, test_db, sample_lab, sample_vxlan_tunnel, multiple_hosts
    ):
        """Offline agents are skipped during VXLAN reconciliation."""
        import app.tasks.reconciliation as recon_mod

        recon_mod._lab_orphan_check_counter = recon_mod._LAB_ORPHAN_CHECK_INTERVAL - 1

        reconcile_mock = AsyncMock(return_value={"removed_ports": []})

        with patch.object(agent_client, "is_agent_online", return_value=False):
            with patch.object(agent_client, "cleanup_orphans_on_agent", new_callable=AsyncMock):
                with patch.object(agent_client, "reconcile_vxlan_ports_on_agent", reconcile_mock):
                    await _maybe_cleanup_labless_containers(test_db)

        reconcile_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconcile_empty_when_no_active_tunnels(
        self, test_db, sample_lab, multiple_hosts
    ):
        """With no active tunnels, agents get empty valid port lists."""
        import app.tasks.reconciliation as recon_mod

        recon_mod._lab_orphan_check_counter = recon_mod._LAB_ORPHAN_CHECK_INTERVAL - 1

        reconcile_mock = AsyncMock(return_value={"removed_ports": []})

        with patch.object(agent_client, "is_agent_online", return_value=True):
            with patch.object(agent_client, "cleanup_orphans_on_agent", new_callable=AsyncMock) as mock_cleanup:
                mock_cleanup.return_value = {"removed_containers": []}
                with patch.object(agent_client, "reconcile_vxlan_ports_on_agent", reconcile_mock):
                    await _maybe_cleanup_labless_containers(test_db)

        # Every agent should get an empty valid list
        for call in reconcile_mock.call_args_list:
            ports_arg = call[0][1]
            assert ports_arg == []

    @pytest.mark.asyncio
    async def test_reconcile_agent_error_doesnt_crash(
        self, test_db, sample_lab, sample_vxlan_tunnel, multiple_hosts
    ):
        """If one agent fails, the rest still get reconciled."""
        import app.tasks.reconciliation as recon_mod

        recon_mod._lab_orphan_check_counter = recon_mod._LAB_ORPHAN_CHECK_INTERVAL - 1

        call_count = 0

        async def fail_then_succeed(agent, ports):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Connection refused")
            return {"removed_ports": []}

        with patch.object(agent_client, "is_agent_online", return_value=True):
            with patch.object(agent_client, "cleanup_orphans_on_agent", new_callable=AsyncMock) as mock_cleanup:
                mock_cleanup.return_value = {"removed_containers": []}
                with patch.object(agent_client, "reconcile_vxlan_ports_on_agent", side_effect=fail_then_succeed):
                    await _maybe_cleanup_labless_containers(test_db)

        # All 3 agents attempted despite first one failing
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_reconcile_ignores_non_active_tunnels(
        self, test_db, sample_lab, multiple_hosts
    ):
        """Only 'active' tunnels contribute to valid port names."""
        # Create a cross-host link state first
        link_state = models.LinkState(
            id="xh-link-pending",
            lab_id=sample_lab.id,
            link_name="R1:eth1-R3:eth1",
            source_node="R1",
            source_interface="eth1",
            target_node="R3",
            target_interface="eth1",
            desired_state="up",
            actual_state="pending",
            is_cross_host=True,
            source_host_id=multiple_hosts[0].id,
            target_host_id=multiple_hosts[1].id,
        )
        test_db.add(link_state)
        test_db.flush()

        # Create a tunnel in "pending" status (not "active")
        tunnel = models.VxlanTunnel(
            lab_id=sample_lab.id,
            link_state_id=link_state.id,
            vni=5000,
            vlan_tag=300,
            agent_a_id=multiple_hosts[0].id,
            agent_a_ip="192.168.1.1",
            agent_b_id=multiple_hosts[1].id,
            agent_b_ip="192.168.1.2",
            status="pending",
        )
        test_db.add(tunnel)
        test_db.commit()

        import app.tasks.reconciliation as recon_mod

        recon_mod._lab_orphan_check_counter = recon_mod._LAB_ORPHAN_CHECK_INTERVAL - 1

        reconcile_mock = AsyncMock(return_value={"removed_ports": []})

        with patch.object(agent_client, "is_agent_online", return_value=True):
            with patch.object(agent_client, "cleanup_orphans_on_agent", new_callable=AsyncMock) as mock_cleanup:
                mock_cleanup.return_value = {"removed_containers": []}
                with patch.object(agent_client, "reconcile_vxlan_ports_on_agent", reconcile_mock):
                    await _maybe_cleanup_labless_containers(test_db)

        # Pending tunnel should NOT produce valid port names
        for call in reconcile_mock.call_args_list:
            ports_arg = call[0][1]
            assert ports_arg == []
