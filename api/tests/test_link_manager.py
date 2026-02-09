"""Tests for app/services/link_manager.py - Link connectivity management service."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app import models
from app.services.link_manager import LinkManager, allocate_vni


class TestAllocateVni:
    """Tests for the allocate_vni function."""

    def test_returns_deterministic_vni(self):
        """Same inputs should always return same VNI."""
        vni1 = allocate_vni("lab-123", "R1:eth1-R2:eth1")
        vni2 = allocate_vni("lab-123", "R1:eth1-R2:eth1")

        assert vni1 == vni2

    def test_different_links_get_different_vnis(self):
        """Different links should get different VNIs."""
        vni1 = allocate_vni("lab-123", "R1:eth1-R2:eth1")
        vni2 = allocate_vni("lab-123", "R1:eth2-R3:eth1")

        assert vni1 != vni2

    def test_different_labs_get_different_vnis(self):
        """Same link name in different labs should get different VNIs."""
        vni1 = allocate_vni("lab-123", "R1:eth1-R2:eth1")
        vni2 = allocate_vni("lab-456", "R1:eth1-R2:eth1")

        assert vni1 != vni2

    def test_vni_in_valid_range(self):
        """VNI should be in range 1000-16001000."""
        for i in range(100):
            vni = allocate_vni(f"lab-{i}", f"link-{i}")
            assert 1000 <= vni <= 16001000


class TestLinkManagerConnectLink:
    """Tests for LinkManager.connect_link method."""

    @pytest.fixture
    def link_manager(self, test_db: Session):
        """Create a LinkManager instance."""
        return LinkManager(test_db)

    @pytest.fixture
    def same_host_link_state(self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host):
        """Create a link state for same-host link."""
        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r2",
            target_interface="eth1",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
            desired_state="up",
            actual_state="pending",
        )
        test_db.add(link)
        test_db.commit()
        test_db.refresh(link)
        return link

    @pytest.fixture
    def cross_host_link_state(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """Create a link state for cross-host link."""
        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R3:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r3",
            target_interface="eth1",
            source_host_id=multiple_hosts[0].id,
            target_host_id=multiple_hosts[1].id,
            desired_state="up",
            actual_state="pending",
        )
        test_db.add(link)
        test_db.commit()
        test_db.refresh(link)
        return link

    @pytest.mark.asyncio
    async def test_connect_same_host_link_success(
        self, link_manager: LinkManager, same_host_link_state: models.LinkState, sample_host: models.Host
    ):
        """Should successfully connect a same-host link."""
        agents = {sample_host.id: sample_host}

        with patch("app.services.link_manager.agent_client") as mock_client:
            mock_client.create_link_on_agent = AsyncMock(return_value={
                "success": True,
                "vlan_tag": 100,
            })

            result = await link_manager.connect_link(same_host_link_state, agents)

            assert result is True
            assert same_host_link_state.actual_state == "up"
            assert same_host_link_state.vlan_tag == 100
            assert same_host_link_state.is_cross_host is False
            assert same_host_link_state.source_carrier_state == "on"
            assert same_host_link_state.target_carrier_state == "on"

    @pytest.mark.asyncio
    async def test_connect_same_host_link_failure(
        self, link_manager: LinkManager, same_host_link_state: models.LinkState, sample_host: models.Host
    ):
        """Should handle failed same-host link connection."""
        agents = {sample_host.id: sample_host}

        with patch("app.services.link_manager.agent_client") as mock_client:
            mock_client.create_link_on_agent = AsyncMock(return_value={
                "success": False,
                "error": "Port not found",
            })

            result = await link_manager.connect_link(same_host_link_state, agents)

            assert result is False
            assert same_host_link_state.actual_state == "error"
            assert same_host_link_state.error_message == "Port not found"

    @pytest.mark.asyncio
    async def test_connect_link_missing_agent(
        self, link_manager: LinkManager, same_host_link_state: models.LinkState
    ):
        """Should handle missing agent gracefully."""
        agents = {}  # No agents available

        result = await link_manager.connect_link(same_host_link_state, agents)

        assert result is False
        assert same_host_link_state.actual_state == "error"

    @pytest.mark.asyncio
    async def test_connect_cross_host_link_success(
        self, link_manager: LinkManager, cross_host_link_state: models.LinkState, multiple_hosts: list[models.Host]
    ):
        """Should successfully connect a cross-host link."""
        agents = {h.id: h for h in multiple_hosts}

        with patch("app.services.link_manager.agent_client") as mock_client:
            mock_client.setup_cross_host_link_v2 = AsyncMock(return_value={
                "success": True,
                "vlan_tag": 200,
            })
            mock_client.resolve_agent_ip = AsyncMock(side_effect=lambda addr: addr.split(":")[0])

            result = await link_manager.connect_link(cross_host_link_state, agents)

            assert result is True
            assert cross_host_link_state.actual_state == "up"
            assert cross_host_link_state.is_cross_host is True


class TestLinkManagerDisconnectLink:
    """Tests for LinkManager.disconnect_link method."""

    @pytest.fixture
    def link_manager(self, test_db: Session):
        """Create a LinkManager instance."""
        return LinkManager(test_db)

    @pytest.fixture
    def active_link_state(self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host):
        """Create an active link state."""
        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r2",
            target_interface="eth1",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
            desired_state="up",
            actual_state="up",
            vlan_tag=100,
            source_carrier_state="on",
            target_carrier_state="on",
        )
        test_db.add(link)
        test_db.commit()
        test_db.refresh(link)
        return link

    @pytest.mark.asyncio
    async def test_disconnect_link_success(
        self, link_manager: LinkManager, active_link_state: models.LinkState, sample_host: models.Host
    ):
        """Should successfully disconnect a link."""
        agents = {sample_host.id: sample_host}

        with patch("app.services.link_manager.agent_client") as mock_client:
            mock_client.get_http_client = MagicMock(return_value=AsyncMock(
                post=AsyncMock(return_value=MagicMock(
                    status_code=200,
                    json=MagicMock(return_value={"success": True}),
                ))
            ))

            result = await link_manager.disconnect_link(active_link_state, agents)

            assert result is True
            assert active_link_state.actual_state == "down"
            assert active_link_state.source_carrier_state == "off"
            assert active_link_state.target_carrier_state == "off"

    @pytest.mark.asyncio
    async def test_disconnect_link_partial_failure(
        self, link_manager: LinkManager, active_link_state: models.LinkState, sample_host: models.Host
    ):
        """Should handle partial failure during disconnect."""
        agents = {sample_host.id: sample_host}

        with patch("app.services.link_manager.agent_client") as mock_client:
            call_count = 0
            async def mock_post(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return MagicMock(status_code=200, json=MagicMock(return_value={"success": True}))
                else:
                    return MagicMock(status_code=500, json=MagicMock(return_value={"success": False}))

            mock_client.get_http_client = MagicMock(return_value=AsyncMock(
                post=mock_post
            ))

            result = await link_manager.disconnect_link(active_link_state, agents)

            assert result is False
            assert active_link_state.actual_state == "error"


class TestLinkManagerSetEndpointCarrier:
    """Tests for LinkManager.set_endpoint_carrier method."""

    @pytest.fixture
    def link_manager(self, test_db: Session):
        """Create a LinkManager instance."""
        return LinkManager(test_db)

    @pytest.fixture
    def link_state(self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host):
        """Create a link state."""
        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r2",
            target_interface="eth1",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
            desired_state="up",
            actual_state="up",
            source_carrier_state="on",
            target_carrier_state="on",
        )
        test_db.add(link)
        test_db.commit()
        test_db.refresh(link)
        return link

    @pytest.mark.asyncio
    async def test_set_source_carrier_off(
        self, link_manager: LinkManager, link_state: models.LinkState, sample_host: models.Host
    ):
        """Should set source endpoint carrier to off."""
        agents = {sample_host.id: sample_host}

        with patch("app.services.link_manager.agent_client") as mock_client:
            mock_client.get_http_client = MagicMock(return_value=AsyncMock(
                post=AsyncMock(return_value=MagicMock(
                    status_code=200,
                    json=MagicMock(return_value={"success": True}),
                ))
            ))

            result = await link_manager.set_endpoint_carrier(
                link_state, "source", "off", agents
            )

            assert result is True
            assert link_state.source_carrier_state == "off"
            assert link_state.target_carrier_state == "on"  # unchanged

    @pytest.mark.asyncio
    async def test_set_target_carrier_off(
        self, link_manager: LinkManager, link_state: models.LinkState, sample_host: models.Host
    ):
        """Should set target endpoint carrier to off."""
        agents = {sample_host.id: sample_host}

        with patch("app.services.link_manager.agent_client") as mock_client:
            mock_client.get_http_client = MagicMock(return_value=AsyncMock(
                post=AsyncMock(return_value=MagicMock(
                    status_code=200,
                    json=MagicMock(return_value={"success": True}),
                ))
            ))

            result = await link_manager.set_endpoint_carrier(
                link_state, "target", "off", agents
            )

            assert result is True
            assert link_state.source_carrier_state == "on"  # unchanged
            assert link_state.target_carrier_state == "off"

    @pytest.mark.asyncio
    async def test_set_carrier_invalid_endpoint(
        self, link_manager: LinkManager, link_state: models.LinkState, sample_host: models.Host
    ):
        """Should reject invalid endpoint."""
        agents = {sample_host.id: sample_host}

        result = await link_manager.set_endpoint_carrier(
            link_state, "invalid", "off", agents
        )

        assert result is False


class TestLinkManagerEnforceLinkState:
    """Tests for LinkManager.enforce_link_state method."""

    @pytest.fixture
    def link_manager(self, test_db: Session):
        """Create a LinkManager instance."""
        return LinkManager(test_db)

    @pytest.fixture
    def link_state(self, test_db: Session, sample_lab: models.Lab, sample_host: models.Host):
        """Create a link state."""
        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r2",
            target_interface="eth1",
            source_host_id=sample_host.id,
            target_host_id=sample_host.id,
            desired_state="up",
            actual_state="pending",
        )
        test_db.add(link)
        test_db.commit()
        test_db.refresh(link)
        return link

    @pytest.mark.asyncio
    async def test_enforce_up_state(
        self, link_manager: LinkManager, link_state: models.LinkState, sample_host: models.Host
    ):
        """Should connect link when desired state is 'up'."""
        agents = {sample_host.id: sample_host}
        link_state.desired_state = "up"

        with patch("app.services.link_manager.agent_client") as mock_client:
            mock_client.create_link_on_agent = AsyncMock(return_value={
                "success": True,
                "vlan_tag": 100,
            })

            result = await link_manager.enforce_link_state(link_state, agents)

            assert result is True
            assert link_state.actual_state == "up"

    @pytest.mark.asyncio
    async def test_enforce_down_state(
        self, link_manager: LinkManager, link_state: models.LinkState, sample_host: models.Host
    ):
        """Should disconnect link when desired state is 'down'."""
        agents = {sample_host.id: sample_host}
        link_state.desired_state = "down"
        link_state.actual_state = "up"

        with patch("app.services.link_manager.agent_client") as mock_client:
            mock_client.get_http_client = MagicMock(return_value=AsyncMock(
                post=AsyncMock(return_value=MagicMock(
                    status_code=200,
                    json=MagicMock(return_value={"success": True}),
                ))
            ))

            result = await link_manager.enforce_link_state(link_state, agents)

            assert result is True
            assert link_state.actual_state == "down"


class TestLinkManagerCreateCrossHostLink:
    """Tests for LinkManager.create_cross_host_link method."""

    @pytest.fixture
    def link_manager(self, test_db: Session):
        """Create a LinkManager instance."""
        return LinkManager(test_db)

    @pytest.fixture
    def cross_host_link_state(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """Create a cross-host link state."""
        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R3:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r3",
            target_interface="eth1",
            source_host_id=multiple_hosts[0].id,
            target_host_id=multiple_hosts[1].id,
            desired_state="up",
            actual_state="pending",
            is_cross_host=True,
        )
        test_db.add(link)
        test_db.commit()
        test_db.refresh(link)
        return link

    @pytest.mark.asyncio
    async def test_create_cross_host_link_success(
        self, test_db: Session, link_manager: LinkManager,
        cross_host_link_state: models.LinkState, multiple_hosts: list[models.Host]
    ):
        """Should create VXLAN tunnel for cross-host link."""
        agent_a = multiple_hosts[0]
        agent_b = multiple_hosts[1]

        with patch("app.services.link_manager.agent_client") as mock_client:
            mock_client.setup_cross_host_link_v2 = AsyncMock(return_value={
                "success": True,
                "vni": 200,
            })
            mock_client.resolve_agent_ip = AsyncMock(side_effect=lambda addr: addr.split(":")[0])

            result = await link_manager.create_cross_host_link(
                cross_host_link_state, agent_a, agent_b
            )

            assert result is True
            assert cross_host_link_state.vni == 200

            # Flush session to make pending objects queryable
            test_db.flush()

            # Check VxlanTunnel record was created
            tunnel = test_db.query(models.VxlanTunnel).filter(
                models.VxlanTunnel.link_state_id == cross_host_link_state.id
            ).first()
            assert tunnel is not None
            assert tunnel.status == "active"

    @pytest.mark.asyncio
    async def test_create_cross_host_link_failure(
        self, link_manager: LinkManager, cross_host_link_state: models.LinkState,
        multiple_hosts: list[models.Host]
    ):
        """Should handle VXLAN tunnel creation failure."""
        agent_a = multiple_hosts[0]
        agent_b = multiple_hosts[1]

        with patch("app.services.link_manager.agent_client") as mock_client:
            mock_client.setup_cross_host_link_v2 = AsyncMock(return_value={
                "success": False,
                "error": "VXLAN port creation failed",
            })
            mock_client.resolve_agent_ip = AsyncMock(side_effect=lambda addr: addr.split(":")[0])

            result = await link_manager.create_cross_host_link(
                cross_host_link_state, agent_a, agent_b
            )

            assert result is False
            assert cross_host_link_state.actual_state == "error"
            assert "VXLAN" in cross_host_link_state.error_message or "VTEP" in cross_host_link_state.error_message


class TestLinkManagerTeardownCrossHostLink:
    """Tests for LinkManager.teardown_cross_host_link method."""

    @pytest.fixture
    def link_manager(self, test_db: Session):
        """Create a LinkManager instance."""
        return LinkManager(test_db)

    @pytest.fixture
    def active_vxlan_link(
        self, test_db: Session, sample_lab: models.Lab, multiple_hosts: list[models.Host]
    ):
        """Create an active cross-host link with VXLAN tunnel."""
        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R3:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r3",
            target_interface="eth1",
            source_host_id=multiple_hosts[0].id,
            target_host_id=multiple_hosts[1].id,
            desired_state="up",
            actual_state="up",
            is_cross_host=True,
            vni=12345,
            vlan_tag=200,
        )
        test_db.add(link)
        test_db.flush()

        tunnel = models.VxlanTunnel(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_state_id=link.id,
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
        test_db.refresh(link)
        return link

    @pytest.mark.asyncio
    async def test_teardown_cross_host_link_success(
        self, test_db: Session, link_manager: LinkManager,
        active_vxlan_link: models.LinkState, multiple_hosts: list[models.Host]
    ):
        """Should tear down VXLAN tunnel."""
        agent_a = multiple_hosts[0]
        agent_b = multiple_hosts[1]

        with patch("app.services.link_manager.agent_client") as mock_client:
            mock_client.detach_overlay_interface_on_agent = AsyncMock(return_value={
                "success": True,
            })
            mock_client.resolve_agent_ip = AsyncMock(side_effect=lambda addr: addr.split(":")[0])

            result = await link_manager.teardown_cross_host_link(
                active_vxlan_link, agent_a, agent_b
            )

            assert result is True
            assert active_vxlan_link.vni is None
            assert active_vxlan_link.vlan_tag is None

            # Flush pending deletes so they're visible in queries
            test_db.flush()

            # Check tunnel record was deleted (both sides succeeded)
            tunnel = test_db.query(models.VxlanTunnel).filter(
                models.VxlanTunnel.link_state_id == active_vxlan_link.id
            ).first()
            assert tunnel is None

    @pytest.mark.asyncio
    async def test_teardown_no_vni(
        self, link_manager: LinkManager, sample_lab: models.Lab,
        multiple_hosts: list[models.Host], test_db: Session
    ):
        """Should handle teardown when no VNI is set."""
        link = models.LinkState(
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
            vni=None,  # No VNI set
        )
        test_db.add(link)
        test_db.commit()

        result = await link_manager.teardown_cross_host_link(
            link, multiple_hosts[0], multiple_hosts[1]
        )

        assert result is True  # Nothing to tear down is considered success


class TestLookupEndpointHosts:
    """Tests for lookup_endpoint_hosts utility function."""

    def test_lookup_from_node_table(
        self, test_db: Session,
        sample_lab: models.Lab, sample_host: models.Host
    ):
        """Should find hosts from Node records."""
        from app.utils.link import lookup_endpoint_hosts

        # Create nodes with host assignments
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

        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r2",
            target_interface="eth1",
        )
        test_db.add(link)
        test_db.commit()

        source_host, target_host = lookup_endpoint_hosts(test_db, link)

        assert source_host == sample_host.id
        assert target_host == sample_host.id

    def test_lookup_from_node_placement(
        self, test_db: Session,
        sample_lab: models.Lab, sample_host: models.Host
    ):
        """Should fall back to NodePlacement records."""
        from app.utils.link import lookup_endpoint_hosts

        # Create placements without Node records
        placement1 = models.NodePlacement(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_name="archetype-test-r1",
            host_id=sample_host.id,
        )
        placement2 = models.NodePlacement(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            node_name="archetype-test-r2",
            host_id=sample_host.id,
        )
        test_db.add_all([placement1, placement2])

        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r2",
            target_interface="eth1",
        )
        test_db.add(link)
        test_db.commit()

        source_host, target_host = lookup_endpoint_hosts(test_db, link)

        assert source_host == sample_host.id
        assert target_host == sample_host.id

    def test_lookup_missing_hosts(
        self, test_db: Session, sample_lab: models.Lab
    ):
        """Should return None for missing host placements."""
        from app.utils.link import lookup_endpoint_hosts

        link = models.LinkState(
            id=str(uuid4()),
            lab_id=sample_lab.id,
            link_name="R1:eth1-R2:eth1",
            source_node="archetype-test-r1",
            source_interface="eth1",
            target_node="archetype-test-r2",
            target_interface="eth1",
        )
        test_db.add(link)
        test_db.commit()

        source_host, target_host = lookup_endpoint_hosts(test_db, link)

        assert source_host is None
        assert target_host is None


class TestResolveAgentIp:
    """Tests for agent_client.resolve_agent_ip function."""

    @pytest.mark.asyncio
    async def test_extract_ip_from_address(self):
        """Should extract IP from host:port format."""
        from app.agent_client import resolve_agent_ip

        ip = await resolve_agent_ip("192.168.1.100:8080")
        assert ip == "192.168.1.100"

    @pytest.mark.asyncio
    async def test_extract_ip_with_http_prefix(self):
        """Should handle http:// prefix."""
        from app.agent_client import resolve_agent_ip

        ip = await resolve_agent_ip("http://192.168.1.100:8080")
        assert ip == "192.168.1.100"

    @pytest.mark.asyncio
    async def test_extract_ip_with_https_prefix(self):
        """Should handle https:// prefix."""
        from app.agent_client import resolve_agent_ip

        ip = await resolve_agent_ip("https://192.168.1.100:8080")
        assert ip == "192.168.1.100"
