"""Tests for infrastructure router endpoints (routers/infrastructure.py).

Covers infrastructure settings, agent mesh, MTU testing, network configs,
managed interfaces, and NIC groups.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import models


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_online_host(test_db, host_id="agent-1", name="Agent 1", data_plane_address=None):
    """Create and persist an online Host record."""
    host = models.Host(
        id=host_id,
        name=name,
        address=f"{host_id}.local:8080",
        status="online",
        capabilities=json.dumps({"providers": ["docker"]}),
        version="1.0.0",
        resource_usage=json.dumps({}),
        last_heartbeat=datetime.now(timezone.utc),
        data_plane_address=data_plane_address,
    )
    test_db.add(host)
    test_db.commit()
    test_db.refresh(host)
    return host


def _make_managed_interface(
    test_db, host_id, name="eth1.100", interface_type="transport",
    ip_address="10.0.0.1/24", desired_mtu=9000, sync_status="synced",
):
    """Create and persist an AgentManagedInterface record."""
    iface = models.AgentManagedInterface(
        id=str(uuid.uuid4()),
        host_id=host_id,
        name=name,
        interface_type=interface_type,
        ip_address=ip_address,
        desired_mtu=desired_mtu,
        sync_status=sync_status,
        is_up=True,
        last_sync_at=datetime.now(timezone.utc),
    )
    test_db.add(iface)
    test_db.commit()
    test_db.refresh(iface)
    return iface


def _make_network_config(
    test_db, host_id, desired_mtu=9000, data_plane_interface=None,
    transport_mode="management",
):
    """Create and persist an AgentNetworkConfig record."""
    config = models.AgentNetworkConfig(
        id=str(uuid.uuid4()),
        host_id=host_id,
        desired_mtu=desired_mtu,
        data_plane_interface=data_plane_interface,
        transport_mode=transport_mode,
    )
    test_db.add(config)
    test_db.commit()
    test_db.refresh(config)
    return config


def _make_nic_group(test_db, host_id, name="group-1", description=None):
    """Create and persist a HostNicGroup record."""
    group = models.HostNicGroup(
        id=str(uuid.uuid4()),
        host_id=host_id,
        name=name,
        description=description,
    )
    test_db.add(group)
    test_db.commit()
    test_db.refresh(group)
    return group


# ---------------------------------------------------------------------------
# TestInfraSettings
# ---------------------------------------------------------------------------


class TestInfraSettings:
    """Tests for GET/PATCH /infrastructure/settings."""

    def test_get_creates_defaults(
        self, test_client: TestClient, auth_headers: dict,
    ):
        """GET creates default settings row when none exists."""
        resp = test_client.get("/infrastructure/settings", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["overlay_mtu"] == 1450
        assert data["mtu_verification_enabled"] is True

    def test_get_returns_existing(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """GET returns existing settings."""
        settings = models.InfraSettings(id="global", overlay_mtu=1300)
        test_db.add(settings)
        test_db.commit()

        resp = test_client.get("/infrastructure/settings", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["overlay_mtu"] == 1300

    def test_update_requires_admin(
        self, test_client: TestClient, auth_headers: dict,
    ):
        """PATCH requires admin role."""
        resp = test_client.patch(
            "/infrastructure/settings",
            json={"overlay_mtu": 1400},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_update_overlay_mtu(
        self, test_client: TestClient, admin_auth_headers: dict,
    ):
        """PATCH updates overlay_mtu."""
        resp = test_client.patch(
            "/infrastructure/settings",
            json={"overlay_mtu": 1400},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["overlay_mtu"] == 1400

    def test_update_partial_fields(
        self, test_client: TestClient, admin_auth_headers: dict,
    ):
        """PATCH with partial fields only updates those fields."""
        # Set initial state
        test_client.patch(
            "/infrastructure/settings",
            json={"overlay_mtu": 1400, "mtu_verification_enabled": False},
            headers=admin_auth_headers,
        )

        # Update only one field
        resp = test_client.patch(
            "/infrastructure/settings",
            json={"overlay_mtu": 1500},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["overlay_mtu"] == 1500
        # Other field should be preserved
        assert data["mtu_verification_enabled"] is False


# ---------------------------------------------------------------------------
# TestAgentMesh
# ---------------------------------------------------------------------------


class TestAgentMesh:
    """Tests for GET /infrastructure/mesh."""

    def test_empty_mesh(
        self, test_client: TestClient, auth_headers: dict,
    ):
        """Empty mesh when no agents registered."""
        resp = test_client.get("/infrastructure/mesh", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["agents"] == []
        assert data["links"] == []

    def test_mesh_with_agents_and_links(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Mesh includes agents and creates links between pairs."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_online_host(test_db, "h2", "Host 2")

        resp = test_client.get("/infrastructure/mesh", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["agents"]) == 2
        # Should have management links for the pair (A->B and B->A)
        assert len(data["links"]) >= 2
        assert data["settings"]["overlay_mtu"] is not None

    def test_mesh_backfills_data_plane_ip(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Mesh backfills data_plane_address from transport managed interfaces."""
        h1 = _make_online_host(test_db, "h1", "Host 1")
        # Create a transport interface with IP but no data_plane_address on host
        _make_managed_interface(
            test_db, h1.id, name="eth1.100", interface_type="transport",
            ip_address="10.0.0.1/24",
        )

        resp = test_client.get("/infrastructure/mesh", headers=auth_headers)
        assert resp.status_code == 200

        # Check that the host got backfilled
        test_db.refresh(h1)
        assert h1.data_plane_address == "10.0.0.1"


# ---------------------------------------------------------------------------
# TestMtuTest
# ---------------------------------------------------------------------------


class TestMtuTest:
    """Tests for POST /infrastructure/mesh/test-mtu."""

    def test_single_success(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Successful MTU test returns success result."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_online_host(test_db, "h2", "Host 2")

        with (
            patch("app.routers.infrastructure.agent_client") as mock_ac,
        ):
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.test_mtu_on_agent = AsyncMock(return_value={
                "success": True,
                "tested_mtu": 1500,
                "link_type": "direct",
                "latency_ms": 0.5,
            })
            mock_ac.resolve_agent_ip = AsyncMock(return_value="10.0.0.2")

            resp = test_client.post(
                "/infrastructure/mesh/test-mtu",
                json={"source_agent_id": "h1", "target_agent_id": "h2"},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["tested_mtu"] == 1500

    def test_source_offline(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Source agent offline returns error."""
        # h1 is offline (missing heartbeat)
        h1 = models.Host(
            id="h1", name="Host 1", address="h1.local:8080",
            status="offline", version="1.0.0",
        )
        _make_online_host(test_db, "h2", "Host 2")
        test_db.add(h1)
        test_db.commit()

        with patch("app.routers.infrastructure.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(side_effect=lambda a: a.id == "h2")

            resp = test_client.post(
                "/infrastructure/mesh/test-mtu",
                json={"source_agent_id": "h1", "target_agent_id": "h2"},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "offline" in data["error"].lower()

    def test_target_offline(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Target agent offline returns error."""
        _make_online_host(test_db, "h1", "Host 1")
        h2 = models.Host(
            id="h2", name="Host 2", address="h2.local:8080",
            status="offline", version="1.0.0",
        )
        test_db.add(h2)
        test_db.commit()

        with patch("app.routers.infrastructure.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(side_effect=lambda a: a.id == "h1")

            resp = test_client.post(
                "/infrastructure/mesh/test-mtu",
                json={"source_agent_id": "h1", "target_agent_id": "h2"},
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "offline" in data["error"].lower()

    def test_data_plane_path(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Test on data_plane path uses data plane addresses."""
        _make_online_host(test_db, "h1", "Host 1", data_plane_address="10.0.0.1")
        _make_online_host(test_db, "h2", "Host 2", data_plane_address="10.0.0.2")

        with patch("app.routers.infrastructure.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.test_mtu_on_agent = AsyncMock(return_value={
                "success": True,
                "tested_mtu": 9000,
                "link_type": "direct",
            })

            resp = test_client.post(
                "/infrastructure/mesh/test-mtu",
                json={
                    "source_agent_id": "h1",
                    "target_agent_id": "h2",
                    "test_path": "data_plane",
                },
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["test_path"] == "data_plane"


# ---------------------------------------------------------------------------
# TestMtuTestAll
# ---------------------------------------------------------------------------


class TestMtuTestAll:
    """Tests for POST /infrastructure/mesh/test-all."""

    def test_fewer_than_two_agents(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """With fewer than 2 online agents, returns empty results."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch("app.routers.infrastructure.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)

            resp = test_client.post(
                "/infrastructure/mesh/test-all",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_pairs"] == 0

    def test_all_pairs_tested(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """All pairs are tested when multiple agents are online."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_online_host(test_db, "h2", "Host 2")

        with patch("app.routers.infrastructure.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.test_mtu_on_agent = AsyncMock(return_value={
                "success": True,
                "tested_mtu": 1500,
                "link_type": "direct",
            })
            mock_ac.resolve_agent_ip = AsyncMock(return_value="10.0.0.2")

            resp = test_client.post(
                "/infrastructure/mesh/test-all",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        # 2 agents = 1 pair, bidirectional = 2 results (management path)
        assert data["total_pairs"] >= 2
        assert data["successful"] + data["failed"] == data["total_pairs"]

    def test_skips_offline_agents(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Offline agents are excluded from test-all."""
        _make_online_host(test_db, "h1", "Host 1")
        # h2 has status=online in DB but agent is offline by heartbeat check
        _make_online_host(test_db, "h2", "Host 2")

        with patch("app.routers.infrastructure.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(side_effect=lambda a: a.id == "h1")

            resp = test_client.post(
                "/infrastructure/mesh/test-all",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        # Only 1 online agent -> no pairs
        assert data["total_pairs"] == 0


# ---------------------------------------------------------------------------
# TestNetworkConfig
# ---------------------------------------------------------------------------


class TestNetworkConfig:
    """Tests for agent network config endpoints."""

    def test_get_creates_default(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """GET creates a default network config when none exists."""
        _make_online_host(test_db, "h1", "Host 1")

        resp = test_client.get(
            "/infrastructure/agents/h1/network-config",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["host_id"] == "h1"
        assert data["host_name"] == "Host 1"
        assert data["desired_mtu"] == 9000

    def test_get_returns_existing_config(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """GET returns existing network config."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_network_config(test_db, "h1", desired_mtu=1500, data_plane_interface="eth0")

        resp = test_client.get(
            "/infrastructure/agents/h1/network-config",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["desired_mtu"] == 1500
        assert data["data_plane_interface"] == "eth0"

    def test_update_requires_admin(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """PATCH requires admin access."""
        _make_online_host(test_db, "h1", "Host 1")

        resp = test_client.patch(
            "/infrastructure/agents/h1/network-config",
            json={"desired_mtu": 1500},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_update_config(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """PATCH updates network config fields."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch("app.routers.infrastructure.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=False)

            resp = test_client.patch(
                "/infrastructure/agents/h1/network-config",
                json={
                    "data_plane_interface": "ens192",
                    "desired_mtu": 9100,
                    "transport_mode": "management",
                },
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["data_plane_interface"] == "ens192"
        assert data["desired_mtu"] == 9100

    def test_list_network_configs(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """GET /network-configs lists all agents' configs."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_online_host(test_db, "h2", "Host 2")
        _make_network_config(test_db, "h1", desired_mtu=9000)

        resp = test_client.get(
            "/infrastructure/network-configs",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        # Should list entries for all agents (h1 has config, h2 gets placeholder)
        assert len(data) == 2


# ---------------------------------------------------------------------------
# TestManagedInterfaces
# ---------------------------------------------------------------------------


class TestManagedInterfaces:
    """Tests for managed interface CRUD endpoints."""

    def test_list_empty(
        self, test_client: TestClient, auth_headers: dict,
    ):
        """List returns empty when no interfaces exist."""
        resp = test_client.get(
            "/infrastructure/interfaces",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["interfaces"] == []
        assert data["total"] == 0

    def test_list_with_interfaces(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """List returns managed interfaces."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_managed_interface(test_db, "h1", name="eth1.100")

        resp = test_client.get(
            "/infrastructure/interfaces",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["interfaces"][0]["name"] == "eth1.100"

    def test_create_requires_admin(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Create managed interface requires admin."""
        _make_online_host(test_db, "h1", "Host 1")

        resp = test_client.post(
            "/infrastructure/agents/h1/managed-interfaces",
            json={
                "name": "eth1",
                "interface_type": "transport",
                "desired_mtu": 9000,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_create_interface(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Create managed interface succeeds with admin."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch("app.routers.infrastructure.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.provision_interface_on_agent = AsyncMock(return_value={
                "success": True,
                "mtu": 9000,
                "ip_address": "10.0.0.1/24",
            })

            resp = test_client.post(
                "/infrastructure/agents/h1/managed-interfaces",
                json={
                    "name": "eth1",
                    "interface_type": "transport",
                    "desired_mtu": 9000,
                    "ip_address": "10.0.0.1/24",
                },
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "eth1"
        assert data["sync_status"] == "synced"

    def test_update_interface(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """PATCH updates managed interface."""
        _make_online_host(test_db, "h1", "Host 1")
        iface = _make_managed_interface(test_db, "h1", name="eth1")

        with patch("app.routers.infrastructure.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.provision_interface_on_agent = AsyncMock(return_value={
                "success": True,
                "mtu": 1500,
            })

            resp = test_client.patch(
                f"/infrastructure/interfaces/{iface.id}",
                json={"desired_mtu": 1500},
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["desired_mtu"] == 1500

    def test_delete_interface(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """DELETE removes managed interface."""
        _make_online_host(test_db, "h1", "Host 1")
        iface = _make_managed_interface(test_db, "h1", name="eth1")

        with patch("app.routers.infrastructure.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.provision_interface_on_agent = AsyncMock(return_value={"success": True})

            resp = test_client.delete(
                f"/infrastructure/interfaces/{iface.id}",
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

        # Verify deletion
        assert test_db.get(models.AgentManagedInterface, iface.id) is None

    def test_get_agent_interfaces(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """GET agent interfaces returns details from the agent."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch("app.routers.infrastructure.agent_client") as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.get_agent_interface_details = AsyncMock(return_value={
                "interfaces": [
                    {"name": "eth0", "mtu": 1500, "mac": "00:11:22:33:44:55"},
                ],
                "default_route_interface": "eth0",
                "network_manager": "systemd-networkd",
            })

            resp = test_client.get(
                "/infrastructure/agents/h1/interfaces",
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["interfaces"]) == 1
        assert data["default_route_interface"] == "eth0"


# ---------------------------------------------------------------------------
# TestNicGroups
# ---------------------------------------------------------------------------


class TestNicGroups:
    """Tests for NIC group CRUD endpoints."""

    def test_list_empty(
        self, test_client: TestClient, auth_headers: dict,
    ):
        """List returns empty when no NIC groups exist."""
        resp = test_client.get(
            "/infrastructure/nic-groups",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["groups"] == []
        assert data["total"] == 0

    def test_create_group(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Create NIC group succeeds with admin."""
        _make_online_host(test_db, "h1", "Host 1")

        resp = test_client.post(
            "/infrastructure/hosts/h1/nic-groups",
            json={"name": "transport-group", "description": "Transport NICs"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "transport-group"
        assert data["description"] == "Transport NICs"
        assert data["host_name"] == "Host 1"

    def test_create_duplicate_group_fails(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Creating a NIC group with duplicate name returns 409."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_nic_group(test_db, "h1", name="transport-group")

        resp = test_client.post(
            "/infrastructure/hosts/h1/nic-groups",
            json={"name": "transport-group"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 409

    def test_delete_group(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Delete NIC group removes it."""
        _make_online_host(test_db, "h1", "Host 1")
        group = _make_nic_group(test_db, "h1", name="transport-group")

        resp = test_client.delete(
            f"/infrastructure/nic-groups/{group.id}",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_add_member(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Add a managed interface to a NIC group."""
        _make_online_host(test_db, "h1", "Host 1")
        group = _make_nic_group(test_db, "h1", name="transport-group")
        iface = _make_managed_interface(test_db, "h1", name="eth1.100")

        resp = test_client.post(
            f"/infrastructure/nic-groups/{group.id}/members",
            json={"managed_interface_id": iface.id, "role": "transport"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["managed_interface_id"] == iface.id
        assert data["role"] == "transport"

    def test_remove_member(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Remove a member from a NIC group."""
        _make_online_host(test_db, "h1", "Host 1")
        group = _make_nic_group(test_db, "h1", name="transport-group")
        iface = _make_managed_interface(test_db, "h1", name="eth1.100")

        member = models.HostNicGroupMember(
            id=str(uuid.uuid4()),
            nic_group_id=group.id,
            managed_interface_id=iface.id,
            role="transport",
        )
        test_db.add(member)
        test_db.commit()
        test_db.refresh(member)

        resp = test_client.delete(
            f"/infrastructure/nic-groups/{group.id}/members/{member.id}",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_create_group_requires_admin(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Create NIC group requires admin."""
        _make_online_host(test_db, "h1", "Host 1")

        resp = test_client.post(
            "/infrastructure/hosts/h1/nic-groups",
            json={"name": "transport-group"},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_add_member_wrong_host(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Adding an interface from a different host returns 400."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_online_host(test_db, "h2", "Host 2")
        group = _make_nic_group(test_db, "h1", name="transport-group")
        iface = _make_managed_interface(test_db, "h2", name="eth1.100")  # Different host

        resp = test_client.post(
            f"/infrastructure/nic-groups/{group.id}/members",
            json={"managed_interface_id": iface.id},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 400
