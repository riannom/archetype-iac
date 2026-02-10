"""Tests for app/tasks/link_orchestration.py - Link creation during deployment."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models


class TestCreateDeploymentLinks:
    """Tests for the create_deployment_links function."""

    @pytest.fixture
    def lab_with_links(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ) -> tuple[models.Lab, list[models.Node], list[models.Link]]:
        """Create a lab with nodes and links for testing."""
        # Create nodes
        node1 = models.Node(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            gui_id="n1",
            display_name="R1",
            container_name="archetype-test-r1",
            device="linux",
            host_id=sample_host.id,
        )
        node2 = models.Node(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            gui_id="n2",
            display_name="R2",
            container_name="archetype-test-r2",
            device="linux",
            host_id=sample_host.id,
        )
        test_db.add_all([node1, node2])
        test_db.flush()

        # Create link definition
        link = models.Link(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node_id=node1.id,
            source_interface="eth1",
            target_node_id=node2.id,
            target_interface="eth1",
        )
        test_db.add(link)
        test_db.commit()

        return sample_lab, [node1, node2], [link]

    @pytest.mark.asyncio
    async def test_creates_links_successfully(
        self, test_db: Session, lab_with_links, sample_host: models.Host
    ):
        """Should create all links after deployment."""
        from app.tasks.link_orchestration import create_deployment_links

        lab, nodes, links = lab_with_links
        host_to_agent = {sample_host.id: sample_host}

        with patch("app.tasks.link_orchestration.agent_client") as mock_client, \
             patch("app.tasks.link_orchestration.verify_link_connected", new_callable=AsyncMock, return_value=(True, None)), \
             patch("app.tasks.link_orchestration.update_interface_mappings", new_callable=AsyncMock):
            mock_client.create_link_on_agent = AsyncMock(return_value={
                "success": True,
                "vlan_tag": 100,
            })

            success_count, fail_count = await create_deployment_links(
                test_db, lab.id, host_to_agent
            )

            assert success_count == 1
            assert fail_count == 0

            # Verify LinkState was created
            link_state = test_db.query(models.LinkState).filter(
                models.LinkState.lab_id == lab.id
            ).first()
            assert link_state is not None
            assert link_state.actual_state == "up"
            assert link_state.vlan_tag == 100

    @pytest.mark.asyncio
    async def test_handles_no_links(self, test_db: Session, sample_lab: models.Lab):
        """Should handle labs with no links."""
        from app.tasks.link_orchestration import create_deployment_links

        success_count, fail_count = await create_deployment_links(
            test_db, sample_lab.id, {}
        )

        assert success_count == 0
        assert fail_count == 0

    @pytest.mark.asyncio
    async def test_handles_link_creation_failure(
        self, test_db: Session, lab_with_links, sample_host: models.Host
    ):
        """Should count failed links."""
        from app.tasks.link_orchestration import create_deployment_links

        lab, nodes, links = lab_with_links
        host_to_agent = {sample_host.id: sample_host}

        with patch("app.tasks.link_orchestration.agent_client") as mock_client:
            mock_client.create_link_on_agent = AsyncMock(return_value={
                "success": False,
                "error": "Port not found",
            })

            success_count, fail_count = await create_deployment_links(
                test_db, lab.id, host_to_agent
            )

            assert success_count == 0
            assert fail_count == 1

    @pytest.mark.asyncio
    async def test_handles_missing_node_reference(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """Should handle links with missing node references."""
        from app.tasks.link_orchestration import create_deployment_links

        # Create a link without valid node references
        link = models.Link(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node_id=str(uuid4()),  # Non-existent node
            source_interface="eth1",
            target_node_id=str(uuid4()),  # Non-existent node
            target_interface="eth1",
        )
        test_db.add(link)
        test_db.commit()

        host_to_agent = {sample_host.id: sample_host}

        success_count, fail_count = await create_deployment_links(
            test_db, sample_lab.id, host_to_agent
        )

        assert success_count == 0
        assert fail_count == 1

    @pytest.mark.asyncio
    async def test_reuses_existing_link_state(
        self, test_db: Session, lab_with_links, sample_host: models.Host
    ):
        """Should reuse existing LinkState records."""
        from app.tasks.link_orchestration import create_deployment_links

        lab, nodes, links = lab_with_links
        host_to_agent = {sample_host.id: sample_host}

        # Create existing LinkState
        existing_state = models.LinkState(
            id=str(uuid4()),
            lab_id=lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r2",
            target_interface="eth1",
            actual_state="down",
        )
        test_db.add(existing_state)
        test_db.commit()

        with patch("app.tasks.link_orchestration.agent_client") as mock_client, \
             patch("app.tasks.link_orchestration.verify_link_connected", new_callable=AsyncMock, return_value=(True, None)), \
             patch("app.tasks.link_orchestration.update_interface_mappings", new_callable=AsyncMock):
            mock_client.create_link_on_agent = AsyncMock(return_value={
                "success": True,
                "vlan_tag": 100,
            })

            await create_deployment_links(test_db, lab.id, host_to_agent)

            # Should have only one LinkState (reused existing)
            link_states = test_db.query(models.LinkState).filter(
                models.LinkState.lab_id == lab.id
            ).all()
            assert len(link_states) == 1
            assert link_states[0].id == existing_state.id


class TestCreateSameHostLink:
    """Tests for the create_same_host_link function."""

    @pytest.mark.asyncio
    async def test_creates_same_host_link(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """Should create link using OVS hot_connect."""
        from app.tasks.link_orchestration import create_same_host_link

        link_state = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r2",
            target_interface="eth1",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
            actual_state="pending",
        )
        test_db.add(link_state)
        test_db.commit()

        host_to_agent = {sample_host.id: sample_host}
        log_parts = []

        with patch("app.tasks.link_orchestration.agent_client") as mock_client, \
             patch("app.tasks.link_orchestration.verify_link_connected", new_callable=AsyncMock, return_value=(True, None)), \
             patch("app.tasks.link_orchestration.update_interface_mappings", new_callable=AsyncMock):
            mock_client.create_link_on_agent = AsyncMock(return_value={
                "success": True,
                "vlan_tag": 100,
            })

            result = await create_same_host_link(
                test_db, sample_lab.id, link_state, host_to_agent, log_parts
            )

            assert result is True
        assert link_state.actual_state == "up"
        assert link_state.vlan_tag == 100

    @pytest.mark.asyncio
    async def test_normalizes_interfaces_for_same_host_link(
        self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host
    ):
        """Should normalize vendor interface names before hot-connect."""
        from app.tasks.link_orchestration import create_same_host_link

        link_state = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:Ethernet1-R2:Ethernet2",
            source_node="archetype-test-r1",
            source_interface="Ethernet1",
            target_node="archetype-test-r2",
            target_interface="Ethernet2",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
            actual_state="pending",
        )
        test_db.add(link_state)
        test_db.commit()

        host_to_agent = {sample_host.id: sample_host}

        with patch("app.tasks.link_orchestration.agent_client") as mock_client, \
             patch("app.tasks.link_orchestration.verify_link_connected", new_callable=AsyncMock, return_value=(True, None)), \
             patch("app.tasks.link_orchestration.update_interface_mappings", new_callable=AsyncMock):
            mock_client.create_link_on_agent = AsyncMock(return_value={
                "success": True,
                "vlan_tag": 100,
            })

            await create_same_host_link(test_db, sample_lab.id, link_state, host_to_agent, [])

            mock_client.create_link_on_agent.assert_awaited_once()
            _, kwargs = mock_client.create_link_on_agent.await_args
            assert kwargs["source_interface"] == "eth1"
            assert kwargs["target_interface"] == "eth2"


class TestCreateCrossHostLinkNormalization:
    """Tests for cross-host link interface normalization."""

    @pytest.mark.asyncio
    async def test_normalizes_interfaces_for_cross_host_link(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """Should normalize vendor interface names before VXLAN attach."""
        from app.tasks.link_orchestration import create_cross_host_link

        host_a, host_b = multiple_hosts[:2]
        link_state = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:Ethernet1-R2:Ethernet1",
            source_node="archetype-test-r1",
            source_interface="Ethernet1",
            target_node="archetype-test-r2",
            target_interface="Ethernet1",
            source_host_id=host_a.id,
            target_host_id=host_b.id,
            actual_state="pending",
        )
        test_db.add(link_state)
        test_db.commit()

        host_to_agent = {host_a.id: host_a, host_b.id: host_b}

        with patch("app.tasks.link_orchestration.agent_client") as mock_client, \
             patch("app.tasks.link_orchestration.verify_link_connected", new_callable=AsyncMock, return_value=(True, None)), \
             patch("app.tasks.link_orchestration.update_interface_mappings", new_callable=AsyncMock):
            mock_client.setup_cross_host_link_v2 = AsyncMock(return_value={
                "success": True,
                "vni": 3884,
            })
            mock_client.resolve_agent_ip = AsyncMock(side_effect=lambda addr: addr.split(":")[0])

            await create_cross_host_link(test_db, sample_lab.id, link_state, host_to_agent, [])

            mock_client.setup_cross_host_link_v2.assert_awaited_once()
            _, kwargs = mock_client.setup_cross_host_link_v2.await_args
            assert kwargs["interface_a"] == "eth1"
            assert kwargs["interface_b"] == "eth1"

            # Check in-memory state (no commit yet from create_cross_host_link)
            assert link_state.vni == 3884
            assert link_state.source_carrier_state == "on"
            assert link_state.target_carrier_state == "on"

    @pytest.mark.asyncio
    async def test_handles_missing_agent(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """Should handle missing agent."""
        from app.tasks.link_orchestration import create_same_host_link

        link_state = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r2",
            target_interface="eth1",
            source_host_id="nonexistent-host",
            target_host_id="nonexistent-host",
            actual_state="pending",
        )
        test_db.add(link_state)
        test_db.commit()

        host_to_agent = {}  # No agents
        log_parts = []

        result = await create_same_host_link(
            test_db, sample_lab.id, link_state, host_to_agent, log_parts
        )

        assert result is False
        assert link_state.actual_state == "error"
        assert "agent not found" in link_state.error_message.lower()


class TestCreateCrossHostLink:
    """Tests for the create_cross_host_link function."""

    @pytest.mark.asyncio
    async def test_creates_cross_host_link(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """Should create VXLAN tunnel for cross-host link."""
        from app.tasks.link_orchestration import create_cross_host_link

        link_state = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R3:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r3",
            target_interface="eth1",
            source_host_id=multiple_hosts[0].id,
            target_host_id=multiple_hosts[1].id,
            actual_state="pending",
            is_cross_host=True,
        )
        test_db.add(link_state)
        test_db.commit()

        host_to_agent = {h.id: h for h in multiple_hosts}
        log_parts = []

        with patch("app.tasks.link_orchestration.agent_client") as mock_client, \
             patch("app.tasks.link_orchestration.verify_link_connected", new_callable=AsyncMock, return_value=(True, None)), \
             patch("app.tasks.link_orchestration.update_interface_mappings", new_callable=AsyncMock):
            mock_client.setup_cross_host_link_v2 = AsyncMock(return_value={
                "success": True,
                "vni": 200,
            })
            mock_client.resolve_agent_ip = AsyncMock(side_effect=lambda addr: addr.split(":")[0])

            result = await create_cross_host_link(
                test_db, sample_lab.id, link_state, host_to_agent, log_parts
            )

            assert result is True
            assert link_state.actual_state == "up"
            assert link_state.vni == 200

            # Flush session to make pending objects queryable
            test_db.flush()

            # Check VxlanTunnel record was created
            tunnel = test_db.query(models.VxlanTunnel).filter(
                models.VxlanTunnel.link_state_id == link_state.id
            ).first()
            assert tunnel is not None
            assert tunnel.status == "active"

    @pytest.mark.asyncio
    async def test_handles_vxlan_failure(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """Should handle VXLAN tunnel creation failure."""
        from app.tasks.link_orchestration import create_cross_host_link

        link_state = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R3:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r3",
            target_interface="eth1",
            source_host_id=multiple_hosts[0].id,
            target_host_id=multiple_hosts[1].id,
            actual_state="pending",
            is_cross_host=True,
        )
        test_db.add(link_state)
        test_db.commit()

        host_to_agent = {h.id: h for h in multiple_hosts}
        log_parts = []

        with patch("app.tasks.link_orchestration.agent_client") as mock_client:
            mock_client.setup_cross_host_link_v2 = AsyncMock(return_value={
                "success": False,
                "error": "VXLAN port creation failed",
            })

            result = await create_cross_host_link(
                test_db, sample_lab.id, link_state, host_to_agent, log_parts
            )

            assert result is False
            assert link_state.actual_state == "error"
            assert "VXLAN" in link_state.error_message

    @pytest.mark.asyncio
    async def test_handles_missing_agents(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """Should handle missing agents for cross-host link."""
        from app.tasks.link_orchestration import create_cross_host_link

        link_state = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R3:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r3",
            target_interface="eth1",
            source_host_id=multiple_hosts[0].id,
            target_host_id=multiple_hosts[1].id,
            actual_state="pending",
            is_cross_host=True,
        )
        test_db.add(link_state)
        test_db.commit()

        # Only include one agent
        host_to_agent = {multiple_hosts[0].id: multiple_hosts[0]}
        log_parts = []

        result = await create_cross_host_link(
            test_db, sample_lab.id, link_state, host_to_agent, log_parts
        )

        assert result is False
        assert link_state.actual_state == "error"
        assert "agents not available" in link_state.error_message.lower()


class TestTeardownDeploymentLinks:
    """Tests for the teardown_deployment_links function."""

    @pytest.fixture
    def lab_with_vxlan_tunnels(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ) -> tuple[models.Lab, list[models.VxlanTunnel]]:
        """Create a lab with active VXLAN tunnels."""
        link_state = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R3:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r3",
            target_interface="eth1",
            source_host_id=multiple_hosts[0].id,
            target_host_id=multiple_hosts[1].id,
            is_cross_host=True,
            vni=12345,
            vlan_tag=200,
            actual_state="up",
        )
        test_db.add(link_state)
        test_db.flush()

        tunnel = models.VxlanTunnel(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_state_id=link_state.id,
            vni=12345,
            vlan_tag=200,
            agent_a_id=multiple_hosts[0].id,
            agent_a_ip="192.168.1.1",
            agent_b_id=multiple_hosts[1].id,
            agent_b_ip="192.168.1.2",
            status="active",
        )
        test_db.add(tunnel)
        test_db.commit()

        return sample_lab, [tunnel]

    @pytest.mark.asyncio
    async def test_tears_down_vxlan_tunnels(
        self, test_db: Session, lab_with_vxlan_tunnels, multiple_hosts: list[models.Host]
    ):
        """Should tear down VXLAN tunnels during destroy."""
        from app.tasks.link_orchestration import teardown_deployment_links

        lab, tunnels = lab_with_vxlan_tunnels
        host_to_agent = {h.id: h for h in multiple_hosts}
        log_parts = []

        with patch("app.tasks.link_orchestration.agent_client") as mock_client:
            mock_client.cleanup_overlay_on_agent = AsyncMock(return_value={
                "tunnels_deleted": 1,
                "bridges_deleted": 0,
                "errors": [],
            })

            success_count, fail_count = await teardown_deployment_links(
                test_db, lab.id, host_to_agent, log_parts
            )

            assert success_count == 2  # Two agents cleaned up
            assert fail_count == 0

            # Verify tunnel was deleted
            remaining_tunnels = test_db.query(models.VxlanTunnel).filter(
                models.VxlanTunnel.lab_id == lab.id
            ).all()
            assert len(remaining_tunnels) == 0

    @pytest.mark.asyncio
    async def test_handles_no_tunnels(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """Should handle labs with no VXLAN tunnels."""
        from app.tasks.link_orchestration import teardown_deployment_links

        success_count, fail_count = await teardown_deployment_links(
            test_db, sample_lab.id, {}
        )

        assert success_count == 0
        assert fail_count == 0

    @pytest.mark.asyncio
    async def test_handles_cleanup_failure(
        self, test_db: Session, lab_with_vxlan_tunnels, multiple_hosts: list[models.Host]
    ):
        """Should handle cleanup failure on one agent."""
        from app.tasks.link_orchestration import teardown_deployment_links

        lab, tunnels = lab_with_vxlan_tunnels
        host_to_agent = {h.id: h for h in multiple_hosts}
        log_parts = []

        with patch("app.tasks.link_orchestration.agent_client") as mock_client:
            call_count = 0

            async def cleanup_mock(agent, lab_id):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return {"tunnels_deleted": 1, "errors": []}
                else:
                    raise Exception("Agent unreachable")

            mock_client.cleanup_overlay_on_agent = cleanup_mock

            success_count, fail_count = await teardown_deployment_links(
                test_db, lab.id, host_to_agent, log_parts
            )

            assert success_count == 1
            assert fail_count == 1

    @pytest.mark.asyncio
    async def test_updates_link_states_after_teardown(
        self, test_db: Session, lab_with_vxlan_tunnels, multiple_hosts: list[models.Host]
    ):
        """Should update LinkState records after teardown."""
        from app.tasks.link_orchestration import teardown_deployment_links

        lab, tunnels = lab_with_vxlan_tunnels
        host_to_agent = {h.id: h for h in multiple_hosts}

        with patch("app.tasks.link_orchestration.agent_client") as mock_client:
            mock_client.cleanup_overlay_on_agent = AsyncMock(return_value={
                "tunnels_deleted": 1,
                "errors": [],
            })

            await teardown_deployment_links(test_db, lab.id, host_to_agent)

            # LinkState records are deleted during teardown (fresh ones created on next deploy)
            link_state = test_db.query(models.LinkState).filter(
                models.LinkState.lab_id == lab.id,
            ).first()
            assert link_state is None

            # VxlanTunnel records should also be deleted
            tunnel = test_db.query(models.VxlanTunnel).filter(
                models.VxlanTunnel.lab_id == lab.id,
            ).first()
            assert tunnel is None


class TestResolveAgentIp:
    """Tests for the resolve_agent_ip helper function in agent_client."""

    @pytest.mark.asyncio
    async def test_extracts_ip_from_address(self):
        """Should extract IP from host:port format."""
        from app.agent_client import resolve_agent_ip

        ip = await resolve_agent_ip("192.168.1.100:8080")
        assert ip == "192.168.1.100"

    @pytest.mark.asyncio
    async def test_handles_http_prefix(self):
        """Should strip http:// prefix."""
        from app.agent_client import resolve_agent_ip

        ip = await resolve_agent_ip("http://192.168.1.100:8080")
        assert ip == "192.168.1.100"

    @pytest.mark.asyncio
    async def test_handles_https_prefix(self):
        """Should strip https:// prefix."""
        from app.agent_client import resolve_agent_ip

        ip = await resolve_agent_ip("https://192.168.1.100:8080")
        assert ip == "192.168.1.100"

    @pytest.mark.asyncio
    async def test_handles_hostname(self):
        """Should resolve or return hostname."""
        from app.agent_client import resolve_agent_ip

        # resolve_agent_ip will attempt DNS resolution; if it fails, it returns the hostname as-is
        ip = await resolve_agent_ip("agent1.local:8080")
        # Either resolved to an IP or returned hostname as-is
        assert ip is not None
        assert len(ip) > 0
