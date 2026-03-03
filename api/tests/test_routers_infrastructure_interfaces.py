"""Tests for infrastructure_interfaces router endpoints.

Covers agent interface listing, MTU setting, network config CRUD,
transport config, managed interface CRUD, and permission checks.
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

MOCK_TARGET = "app.routers.infrastructure_interfaces.agent_client"


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


def _make_network_config(
    test_db, host_id, desired_mtu=9000, data_plane_interface=None,
    transport_mode="management", transport_ip=None, transport_subnet=None,
    parent_interface=None, vlan_id=None,
):
    """Create and persist an AgentNetworkConfig record."""
    config = models.AgentNetworkConfig(
        id=str(uuid.uuid4()),
        host_id=host_id,
        desired_mtu=desired_mtu,
        data_plane_interface=data_plane_interface,
        transport_mode=transport_mode,
        transport_ip=transport_ip,
        transport_subnet=transport_subnet,
        parent_interface=parent_interface,
        vlan_id=vlan_id,
    )
    test_db.add(config)
    test_db.commit()
    test_db.refresh(config)
    return config


def _make_managed_interface(
    test_db, host_id, name="eth1.100", interface_type="transport",
    ip_address="10.0.0.1/24", desired_mtu=9000, sync_status="synced",
    parent_interface=None, vlan_id=None,
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
        parent_interface=parent_interface,
        vlan_id=vlan_id,
    )
    test_db.add(iface)
    test_db.commit()
    test_db.refresh(iface)
    return iface


# ---------------------------------------------------------------------------
# TestGetAgentInterfaces
# ---------------------------------------------------------------------------


class TestGetAgentInterfaces:
    """Tests for GET /infrastructure/agents/{agent_id}/interfaces."""

    def test_success(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Returns interface details from online agent."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.get_agent_interface_details = AsyncMock(return_value={
                "interfaces": [
                    {"name": "eth0", "mtu": 1500, "mac": "00:11:22:33:44:55"},
                    {"name": "ens192", "mtu": 9000, "mac": "aa:bb:cc:dd:ee:ff"},
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
        assert len(data["interfaces"]) == 2
        assert data["default_route_interface"] == "eth0"
        assert data["network_manager"] == "systemd-networkd"
        # Default route interface should be marked
        eth0 = next(i for i in data["interfaces"] if i["name"] == "eth0")
        assert eth0["is_default_route"] is True
        ens = next(i for i in data["interfaces"] if i["name"] == "ens192")
        assert ens["is_default_route"] is False

    def test_agent_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        """Returns 404 when agent does not exist."""
        resp = test_client.get(
            "/infrastructure/agents/nonexistent/interfaces",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_agent_offline(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Returns 503 when agent is offline."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=False)

            resp = test_client.get(
                "/infrastructure/agents/h1/interfaces",
                headers=auth_headers,
            )

        assert resp.status_code == 503
        assert "offline" in resp.json()["detail"].lower()

    def test_agent_error(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Returns 500 when agent call raises an exception."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.get_agent_interface_details = AsyncMock(
                side_effect=Exception("connection refused")
            )

            resp = test_client.get(
                "/infrastructure/agents/h1/interfaces",
                headers=auth_headers,
            )

        assert resp.status_code == 500
        assert "connection refused" in resp.json()["detail"]

    def test_requires_auth(self, test_client: TestClient):
        """Returns 401/403 without auth headers."""
        resp = test_client.get("/infrastructure/agents/h1/interfaces")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# TestSetAgentInterfaceMtu
# ---------------------------------------------------------------------------


class TestSetAgentInterfaceMtu:
    """Tests for POST /infrastructure/agents/{agent_id}/interfaces/{name}/mtu."""

    def test_success(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Sets MTU on agent interface and returns result."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.set_agent_interface_mtu = AsyncMock(return_value={
                "success": True,
                "interface": "eth0",
                "previous_mtu": 1500,
                "new_mtu": 9000,
                "persisted": True,
                "network_manager": "systemd-networkd",
            })

            resp = test_client.post(
                "/infrastructure/agents/h1/interfaces/eth0/mtu",
                json={"mtu": 9000, "persist": True},
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["new_mtu"] == 9000
        assert data["persisted"] is True

    def test_requires_admin(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Regular user cannot set MTU (403)."""
        _make_online_host(test_db, "h1", "Host 1")

        resp = test_client.post(
            "/infrastructure/agents/h1/interfaces/eth0/mtu",
            json={"mtu": 9000},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_agent_not_found(
        self, test_client: TestClient, admin_auth_headers: dict,
    ):
        """Returns 404 for nonexistent agent."""
        resp = test_client.post(
            "/infrastructure/agents/nonexistent/interfaces/eth0/mtu",
            json={"mtu": 9000},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    def test_agent_offline(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Returns 503 when agent is offline."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=False)

            resp = test_client.post(
                "/infrastructure/agents/h1/interfaces/eth0/mtu",
                json={"mtu": 9000},
                headers=admin_auth_headers,
            )

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# TestGetAgentNetworkConfig
# ---------------------------------------------------------------------------


class TestGetAgentNetworkConfig:
    """Tests for GET /infrastructure/agents/{agent_id}/network-config."""

    def test_creates_default_when_missing(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Creates default config when none exists for the agent."""
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
        assert data["sync_status"] == "unconfigured"

    def test_returns_existing_config(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Returns existing network config."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_network_config(test_db, "h1", desired_mtu=1500, data_plane_interface="ens192")

        resp = test_client.get(
            "/infrastructure/agents/h1/network-config",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["desired_mtu"] == 1500
        assert data["data_plane_interface"] == "ens192"

    def test_agent_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        """Returns 404 for nonexistent agent."""
        resp = test_client.get(
            "/infrastructure/agents/nonexistent/network-config",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TestUpdateAgentNetworkConfig
# ---------------------------------------------------------------------------


class TestUpdateAgentNetworkConfig:
    """Tests for PATCH /infrastructure/agents/{agent_id}/network-config."""

    def test_requires_admin(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Regular user cannot update network config (403)."""
        _make_online_host(test_db, "h1", "Host 1")

        resp = test_client.patch(
            "/infrastructure/agents/h1/network-config",
            json={"desired_mtu": 1500},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_agent_not_found(
        self, test_client: TestClient, admin_auth_headers: dict,
    ):
        """Returns 404 for nonexistent agent."""
        resp = test_client.patch(
            "/infrastructure/agents/nonexistent/network-config",
            json={"desired_mtu": 1500},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    def test_update_management_mode_offline(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Update management mode config with agent offline stores fields only."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch(MOCK_TARGET) as mock_ac:
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
        assert data["transport_mode"] == "management"

    def test_update_creates_config_if_missing(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """PATCH creates network config when none exists."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=False)

            resp = test_client.patch(
                "/infrastructure/agents/h1/network-config",
                json={"desired_mtu": 7000},
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["desired_mtu"] == 7000
        assert data["host_id"] == "h1"

    def test_update_subinterface_mode_online(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Subinterface mode provisions on agent when online."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.provision_interface_on_agent = AsyncMock(return_value={
                "success": True,
                "mtu": 9000,
                "ip_address": "10.0.0.1/24",
                "interface_name": "ens192.100",
            })

            resp = test_client.patch(
                "/infrastructure/agents/h1/network-config",
                json={
                    "transport_mode": "subinterface",
                    "parent_interface": "ens192",
                    "vlan_id": 100,
                    "transport_ip": "10.0.0.1/24",
                    "desired_mtu": 9000,
                },
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["sync_status"] == "synced"
        assert data["transport_mode"] == "subinterface"
        # Verify data_plane_address was set on the host
        test_db.expire_all()
        host = test_db.get(models.Host, "h1")
        assert host.data_plane_address == "10.0.0.1"

    def test_update_dedicated_mode_online(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Dedicated mode configures existing interface when online."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.provision_interface_on_agent = AsyncMock(return_value={
                "success": True,
                "mtu": 9000,
                "ip_address": "10.0.0.5/24",
            })

            resp = test_client.patch(
                "/infrastructure/agents/h1/network-config",
                json={
                    "transport_mode": "dedicated",
                    "data_plane_interface": "ens224",
                    "transport_ip": "10.0.0.5/24",
                    "desired_mtu": 9000,
                },
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["sync_status"] == "synced"
        assert data["transport_mode"] == "dedicated"

    def test_update_subinterface_agent_error(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Sync error recorded when agent provisioning fails."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.provision_interface_on_agent = AsyncMock(
                side_effect=Exception("timeout")
            )

            resp = test_client.patch(
                "/infrastructure/agents/h1/network-config",
                json={
                    "transport_mode": "subinterface",
                    "parent_interface": "ens192",
                    "vlan_id": 100,
                    "transport_ip": "10.0.0.1/24",
                },
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["sync_status"] == "error"
        assert "timeout" in data["sync_error"]


# ---------------------------------------------------------------------------
# TestListAgentNetworkConfigs
# ---------------------------------------------------------------------------


class TestListAgentNetworkConfigs:
    """Tests for GET /infrastructure/network-configs."""

    def test_empty_list(
        self, test_client: TestClient, auth_headers: dict,
    ):
        """Returns empty list when no agents exist."""
        resp = test_client.get(
            "/infrastructure/network-configs",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_all_agents(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Returns config entries for all agents."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_online_host(test_db, "h2", "Host 2")
        _make_network_config(test_db, "h1", desired_mtu=9000)

        resp = test_client.get(
            "/infrastructure/network-configs",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        # h1 has real config, h2 gets a placeholder
        h1_entry = next(e for e in data if e["host_id"] == "h1")
        h2_entry = next(e for e in data if e["host_id"] == "h2")
        assert h1_entry["desired_mtu"] == 9000
        assert h2_entry["sync_status"] == "unconfigured"
        assert h2_entry["host_name"] == "Host 2"


# ---------------------------------------------------------------------------
# TestGetTransportConfig
# ---------------------------------------------------------------------------


class TestGetTransportConfig:
    """Tests for GET /infrastructure/agents/{agent_id}/transport-config."""

    def test_management_mode_default(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Returns management mode when no config exists."""
        _make_online_host(test_db, "h1", "Host 1")

        resp = test_client.get(
            "/infrastructure/agents/h1/transport-config",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["transport_mode"] == "management"

    def test_subinterface_mode(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Returns full transport config for subinterface mode."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_network_config(
            test_db, "h1",
            transport_mode="subinterface",
            parent_interface="ens192",
            vlan_id=100,
            transport_ip="10.0.0.1/24",
            desired_mtu=9000,
            data_plane_interface="ens192.100",
        )

        resp = test_client.get(
            "/infrastructure/agents/h1/transport-config",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["transport_mode"] == "subinterface"
        assert data["parent_interface"] == "ens192"
        assert data["vlan_id"] == 100
        assert data["transport_ip"] == "10.0.0.1/24"

    def test_agent_not_found(
        self, test_client: TestClient, admin_auth_headers: dict,
    ):
        """Returns 404 for nonexistent agent."""
        resp = test_client.get(
            "/infrastructure/agents/nonexistent/transport-config",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    def test_requires_admin(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Transport config endpoint requires admin auth."""
        _make_online_host(test_db, "h1", "Host 1")

        resp = test_client.get(
            "/infrastructure/agents/h1/transport-config",
            headers=auth_headers,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# TestApplyTransportConfig
# ---------------------------------------------------------------------------


class TestApplyTransportConfig:
    """Tests for POST /infrastructure/agents/{agent_id}/transport/apply."""

    def test_management_mode_noop(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Management mode returns success without provisioning."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)

            resp = test_client.post(
                "/infrastructure/agents/h1/transport/apply",
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "management" in data["message"].lower()

    def test_subinterface_apply(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Applies subinterface transport config to agent."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_network_config(
            test_db, "h1",
            transport_mode="subinterface",
            parent_interface="ens192",
            vlan_id=100,
            transport_ip="10.0.0.1/24",
            desired_mtu=9000,
        )

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.provision_interface_on_agent = AsyncMock(return_value={
                "success": True,
                "mtu": 9000,
                "ip_address": "10.0.0.1/24",
            })

            resp = test_client.post(
                "/infrastructure/agents/h1/transport/apply",
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_requires_admin(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Regular user cannot apply transport config (403)."""
        _make_online_host(test_db, "h1", "Host 1")

        resp = test_client.post(
            "/infrastructure/agents/h1/transport/apply",
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_agent_not_found(
        self, test_client: TestClient, admin_auth_headers: dict,
    ):
        """Returns 404 for nonexistent agent."""
        resp = test_client.post(
            "/infrastructure/agents/nonexistent/transport/apply",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    def test_agent_offline(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Returns 503 when agent is offline."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=False)

            resp = test_client.post(
                "/infrastructure/agents/h1/transport/apply",
                headers=admin_auth_headers,
            )

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# TestListManagedInterfaces
# ---------------------------------------------------------------------------


class TestListManagedInterfaces:
    """Tests for GET /infrastructure/interfaces."""

    def test_empty_list(
        self, test_client: TestClient, auth_headers: dict,
    ):
        """Returns empty list when no interfaces exist."""
        resp = test_client.get(
            "/infrastructure/interfaces",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["interfaces"] == []
        assert data["total"] == 0

    def test_returns_all_interfaces(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Returns managed interfaces with host names."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_managed_interface(test_db, "h1", name="eth1.100")
        _make_managed_interface(test_db, "h1", name="eth1.200", interface_type="external")

        resp = test_client.get(
            "/infrastructure/interfaces",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        names = {i["name"] for i in data["interfaces"]}
        assert names == {"eth1.100", "eth1.200"}

    def test_filter_by_host(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Filters interfaces by host_id query param."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_online_host(test_db, "h2", "Host 2")
        _make_managed_interface(test_db, "h1", name="eth1.100")
        _make_managed_interface(test_db, "h2", name="eth2.100")

        resp = test_client.get(
            "/infrastructure/interfaces?host_id=h1",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["interfaces"][0]["name"] == "eth1.100"

    def test_filter_by_type(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Filters interfaces by interface_type query param."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_managed_interface(test_db, "h1", name="eth1.100", interface_type="transport")
        _make_managed_interface(test_db, "h1", name="ext0", interface_type="external")

        resp = test_client.get(
            "/infrastructure/interfaces?interface_type=external",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["interfaces"][0]["interface_type"] == "external"


# ---------------------------------------------------------------------------
# TestListAgentManagedInterfaces
# ---------------------------------------------------------------------------


class TestListAgentManagedInterfaces:
    """Tests for GET /infrastructure/agents/{agent_id}/managed-interfaces."""

    def test_empty_list(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Returns empty list for agent with no managed interfaces."""
        _make_online_host(test_db, "h1", "Host 1")

        resp = test_client.get(
            "/infrastructure/agents/h1/managed-interfaces",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["interfaces"] == []
        assert data["total"] == 0

    def test_returns_agent_interfaces(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Returns managed interfaces for a specific agent."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_online_host(test_db, "h2", "Host 2")
        _make_managed_interface(test_db, "h1", name="eth1.100")
        _make_managed_interface(test_db, "h2", name="eth2.100")

        resp = test_client.get(
            "/infrastructure/agents/h1/managed-interfaces",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["interfaces"][0]["name"] == "eth1.100"
        assert data["interfaces"][0]["host_name"] == "Host 1"

    def test_agent_not_found(
        self, test_client: TestClient, auth_headers: dict,
    ):
        """Returns 404 for nonexistent agent."""
        resp = test_client.get(
            "/infrastructure/agents/nonexistent/managed-interfaces",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TestCreateManagedInterface
# ---------------------------------------------------------------------------


class TestCreateManagedInterface:
    """Tests for POST /infrastructure/agents/{agent_id}/managed-interfaces."""

    def test_create_with_agent_online(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Creates and provisions managed interface on online agent."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch(MOCK_TARGET) as mock_ac:
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
        assert data["is_up"] is True

    def test_create_subinterface_auto_name(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Auto-generates name from parent_interface + vlan_id."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.provision_interface_on_agent = AsyncMock(return_value={
                "success": True,
                "mtu": 9000,
                "ip_address": "10.0.0.1/24",
            })

            resp = test_client.post(
                "/infrastructure/agents/h1/managed-interfaces",
                json={
                    "interface_type": "transport",
                    "parent_interface": "ens192",
                    "vlan_id": 100,
                    "desired_mtu": 9000,
                },
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "ens192.100"

    def test_create_requires_name_or_parent_vlan(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Returns 400 when no name and no parent+vlan provided."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)

            resp = test_client.post(
                "/infrastructure/agents/h1/managed-interfaces",
                json={
                    "interface_type": "transport",
                    "desired_mtu": 9000,
                },
                headers=admin_auth_headers,
            )

        assert resp.status_code == 400
        assert "name" in resp.json()["detail"].lower()

    def test_create_duplicate_fails(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Returns 409 when interface already exists on this host."""
        _make_online_host(test_db, "h1", "Host 1")
        _make_managed_interface(test_db, "h1", name="eth1")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)

            resp = test_client.post(
                "/infrastructure/agents/h1/managed-interfaces",
                json={
                    "name": "eth1",
                    "interface_type": "transport",
                    "desired_mtu": 9000,
                },
                headers=admin_auth_headers,
            )

        assert resp.status_code == 409
        assert "already managed" in resp.json()["detail"].lower()

    def test_create_requires_admin(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Regular user cannot create managed interface (403)."""
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

    def test_create_agent_not_found(
        self, test_client: TestClient, admin_auth_headers: dict,
    ):
        """Returns 404 for nonexistent agent."""
        resp = test_client.post(
            "/infrastructure/agents/nonexistent/managed-interfaces",
            json={
                "name": "eth1",
                "interface_type": "transport",
                "desired_mtu": 9000,
            },
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    def test_create_transport_sets_data_plane_address(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Transport interface sets host data_plane_address on success."""
        _make_online_host(test_db, "h1", "Host 1")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.provision_interface_on_agent = AsyncMock(return_value={
                "success": True,
                "mtu": 9000,
                "ip_address": "10.0.0.5/24",
            })

            resp = test_client.post(
                "/infrastructure/agents/h1/managed-interfaces",
                json={
                    "name": "eth1",
                    "interface_type": "transport",
                    "desired_mtu": 9000,
                    "ip_address": "10.0.0.5/24",
                },
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        test_db.expire_all()
        host = test_db.get(models.Host, "h1")
        assert host.data_plane_address == "10.0.0.5"


# ---------------------------------------------------------------------------
# TestUpdateManagedInterface
# ---------------------------------------------------------------------------


class TestUpdateManagedInterface:
    """Tests for PATCH /infrastructure/interfaces/{interface_id}."""

    def test_update_mtu(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Updates MTU on managed interface."""
        _make_online_host(test_db, "h1", "Host 1")
        iface = _make_managed_interface(test_db, "h1", name="eth1")

        with patch(MOCK_TARGET) as mock_ac:
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
        assert data["sync_status"] == "synced"

    def test_update_ip(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Updates IP address on managed interface."""
        _make_online_host(test_db, "h1", "Host 1")
        iface = _make_managed_interface(test_db, "h1", name="eth1", ip_address="10.0.0.1/24")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.provision_interface_on_agent = AsyncMock(return_value={
                "success": True,
                "mtu": 9000,
            })

            resp = test_client.patch(
                f"/infrastructure/interfaces/{iface.id}",
                json={"ip_address": "10.0.0.2/24"},
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ip_address"] == "10.0.0.2/24"

    def test_update_requires_admin(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Regular user cannot update managed interface (403)."""
        _make_online_host(test_db, "h1", "Host 1")
        iface = _make_managed_interface(test_db, "h1", name="eth1")

        resp = test_client.patch(
            f"/infrastructure/interfaces/{iface.id}",
            json={"desired_mtu": 1500},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_update_not_found(
        self, test_client: TestClient, admin_auth_headers: dict,
    ):
        """Returns 404 for nonexistent interface."""
        resp = test_client.patch(
            "/infrastructure/interfaces/nonexistent",
            json={"desired_mtu": 1500},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    def test_update_sync_error(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Records sync error when agent provisioning fails."""
        _make_online_host(test_db, "h1", "Host 1")
        iface = _make_managed_interface(test_db, "h1", name="eth1")

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.provision_interface_on_agent = AsyncMock(return_value={
                "success": False,
                "error": "device busy",
            })

            resp = test_client.patch(
                f"/infrastructure/interfaces/{iface.id}",
                json={"desired_mtu": 1500},
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["sync_status"] == "error"
        assert data["sync_error"] == "device busy"


# ---------------------------------------------------------------------------
# TestDeleteManagedInterface
# ---------------------------------------------------------------------------


class TestDeleteManagedInterface:
    """Tests for DELETE /infrastructure/interfaces/{interface_id}."""

    def test_delete_success(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Deletes managed interface and removes from agent."""
        _make_online_host(test_db, "h1", "Host 1")
        iface = _make_managed_interface(test_db, "h1", name="eth1")
        iface_id = iface.id

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.provision_interface_on_agent = AsyncMock(return_value={"success": True})

            resp = test_client.delete(
                f"/infrastructure/interfaces/{iface_id}",
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # Verify record removed from DB
        assert test_db.get(models.AgentManagedInterface, iface_id) is None

    def test_delete_requires_admin(
        self, test_client: TestClient, test_db: Session, auth_headers: dict,
    ):
        """Regular user cannot delete managed interface (403)."""
        _make_online_host(test_db, "h1", "Host 1")
        iface = _make_managed_interface(test_db, "h1", name="eth1")

        resp = test_client.delete(
            f"/infrastructure/interfaces/{iface.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_delete_not_found(
        self, test_client: TestClient, admin_auth_headers: dict,
    ):
        """Returns 404 for nonexistent interface."""
        resp = test_client.delete(
            "/infrastructure/interfaces/nonexistent",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    def test_delete_blocked_by_referencing_nodes(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
        test_user: models.User,
    ):
        """Cannot delete interface referenced by external network nodes (409)."""
        _make_online_host(test_db, "h1", "Host 1")
        iface = _make_managed_interface(test_db, "h1", name="eth1")

        # Create a lab and node referencing this interface
        lab = models.Lab(
            name="Test Lab",
            owner_id=test_user.id,
            provider="docker",
            state="stopped",
            workspace_path="/tmp/test-lab",
        )
        test_db.add(lab)
        test_db.commit()
        test_db.refresh(lab)

        node = models.Node(
            id=str(uuid.uuid4()),
            lab_id=lab.id,
            gui_id="ext1",
            display_name="External",
            container_name="archetype-test-ext1",
            device="linux",
            host_id="h1",
            managed_interface_id=iface.id,
        )
        test_db.add(node)
        test_db.commit()

        resp = test_client.delete(
            f"/infrastructure/interfaces/{iface.id}",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 409
        assert "external network node" in resp.json()["detail"].lower()

    def test_delete_transport_clears_data_plane_address(
        self, test_client: TestClient, test_db: Session, admin_auth_headers: dict,
    ):
        """Deleting the last transport interface clears host data_plane_address."""
        _make_online_host(test_db, "h1", "Host 1", data_plane_address="10.0.0.1")
        iface = _make_managed_interface(
            test_db, "h1", name="eth1", interface_type="transport",
            ip_address="10.0.0.1/24",
        )

        with patch(MOCK_TARGET) as mock_ac:
            mock_ac.is_agent_online = MagicMock(return_value=True)
            mock_ac.provision_interface_on_agent = AsyncMock(return_value={"success": True})

            resp = test_client.delete(
                f"/infrastructure/interfaces/{iface.id}",
                headers=admin_auth_headers,
            )

        assert resp.status_code == 200
        test_db.expire_all()
        host = test_db.get(models.Host, "h1")
        assert host.data_plane_address is None
