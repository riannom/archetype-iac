"""Tests for agent network interface endpoints.

Source: agent/routers/interfaces.py
Covers: carrier state, isolate/restore, VLAN get, interface MTU,
        host interface listing, and interface provisioning.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent.config import settings
from agent.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(settings, "controller_secret", "")


# ---------------------------------------------------------------------------
# TestSetCarrier
# ---------------------------------------------------------------------------


class TestSetCarrier:
    """Tests for POST /labs/{lab_id}/interfaces/{node}/{interface}/carrier."""

    def test_docker_carrier_on_success(self, client, monkeypatch):
        """Sets carrier on for a Docker container interface."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        plugin = MagicMock()
        plugin.set_carrier_state = AsyncMock(return_value=True)

        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"

        with patch("agent.routers.interfaces.get_provider", return_value=None):
            with patch("agent.routers.interfaces._get_docker_ovs_plugin", return_value=plugin):
                with patch("agent.routers.interfaces.get_provider_for_request", return_value=provider):
                    resp = client.post(
                        "/labs/lab1/interfaces/r1/eth1/carrier",
                        json={"state": "on"},
                    )

        body = resp.json()
        assert body["success"] is True
        assert body["state"] == "on"

    def test_docker_carrier_off(self, client, monkeypatch):
        """Sets carrier off for a Docker container interface."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        plugin = MagicMock()
        plugin.set_carrier_state = AsyncMock(return_value=True)

        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"

        with patch("agent.routers.interfaces.get_provider", return_value=None):
            with patch("agent.routers.interfaces._get_docker_ovs_plugin", return_value=plugin):
                with patch("agent.routers.interfaces.get_provider_for_request", return_value=provider):
                    resp = client.post(
                        "/labs/lab1/interfaces/r1/eth1/carrier",
                        json={"state": "off"},
                    )

        body = resp.json()
        assert body["success"] is True
        assert body["state"] == "off"

    def test_ovs_plugin_disabled(self, client, monkeypatch):
        """Returns error when OVS plugin is disabled."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", False)

        with patch("agent.routers.interfaces.get_provider", return_value=None):
            resp = client.post(
                "/labs/lab1/interfaces/r1/eth1/carrier",
                json={"state": "on"},
            )

        body = resp.json()
        assert body["success"] is False
        assert "not enabled" in body["error"].lower()

    def test_libvirt_vm_carrier(self, client, monkeypatch):
        """Sets carrier for a libvirt VM using virsh domif-setlink."""
        libvirt_provider = MagicMock()
        libvirt_provider.get_node_kind_async = AsyncMock(return_value="cisco_n9kv")
        libvirt_provider.set_vm_link_state = AsyncMock(return_value=(True, None))

        with patch("agent.routers.interfaces.get_provider", return_value=libvirt_provider):
            resp = client.post(
                "/labs/lab1/interfaces/r1/eth1/carrier",
                json={"state": "on"},
            )

        body = resp.json()
        assert body["success"] is True

    def test_carrier_exception_handled(self, client, monkeypatch):
        """Exception during carrier set surfaces in response."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        with patch("agent.routers.interfaces.get_provider", return_value=None):
            with patch("agent.routers.interfaces._get_docker_ovs_plugin",
                       side_effect=RuntimeError("plugin crashed")):
                resp = client.post(
                    "/labs/lab1/interfaces/r1/eth1/carrier",
                    json={"state": "on"},
                )

        body = resp.json()
        assert body["success"] is False
        assert "plugin crashed" in body["error"]


# ---------------------------------------------------------------------------
# TestIsolateInterface
# ---------------------------------------------------------------------------


class TestIsolateInterface:
    """Tests for POST /labs/{lab_id}/interfaces/{node}/{interface}/isolate."""

    def test_ovs_disabled(self, client, monkeypatch):
        """Returns error when OVS plugin is disabled."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", False)
        resp = client.post("/labs/lab1/interfaces/r1/eth1/isolate")
        body = resp.json()
        assert body["success"] is False

    def test_success(self, client, monkeypatch):
        """Successful isolation returns new VLAN tag."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        plugin = MagicMock()
        plugin.isolate_port = AsyncMock(return_value=4050)

        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"

        with patch("agent.routers.interfaces._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.routers.interfaces.get_provider_for_request", return_value=provider):
                resp = client.post("/labs/lab1/interfaces/r1/eth1/isolate")

        body = resp.json()
        assert body["success"] is True
        assert body["vlan_tag"] == 4050

    def test_isolate_fails(self, client, monkeypatch):
        """Isolation failure returns success=False."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        plugin = MagicMock()
        plugin.isolate_port = AsyncMock(return_value=None)

        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"

        with patch("agent.routers.interfaces._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.routers.interfaces.get_provider_for_request", return_value=provider):
                resp = client.post("/labs/lab1/interfaces/r1/eth1/isolate")

        body = resp.json()
        assert body["success"] is False


# ---------------------------------------------------------------------------
# TestRestoreInterface
# ---------------------------------------------------------------------------


class TestRestoreInterface:
    """Tests for POST /labs/{lab_id}/interfaces/{node}/{interface}/restore."""

    def test_ovs_disabled(self, client, monkeypatch):
        """Returns error when OVS plugin is disabled."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", False)
        resp = client.post(
            "/labs/lab1/interfaces/r1/eth1/restore",
            json={"target_vlan": 100},
        )
        body = resp.json()
        assert body["success"] is False

    def test_success(self, client, monkeypatch):
        """Successful restore sets correct VLAN tag."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        plugin = MagicMock()
        plugin.restore_port = AsyncMock(return_value=True)

        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"

        with patch("agent.routers.interfaces._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.routers.interfaces.get_provider_for_request", return_value=provider):
                resp = client.post(
                    "/labs/lab1/interfaces/r1/eth1/restore",
                    json={"target_vlan": 100},
                )

        body = resp.json()
        assert body["success"] is True
        assert body["vlan_tag"] == 100

    def test_restore_failure(self, client, monkeypatch):
        """Restore failure surfaces error."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        plugin = MagicMock()
        plugin.restore_port = AsyncMock(return_value=False)

        provider = MagicMock()
        provider.get_container_name.return_value = "archetype-lab1-r1"

        with patch("agent.routers.interfaces._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.routers.interfaces.get_provider_for_request", return_value=provider):
                resp = client.post(
                    "/labs/lab1/interfaces/r1/eth1/restore",
                    json={"target_vlan": 100},
                )

        body = resp.json()
        assert body["success"] is False


# ---------------------------------------------------------------------------
# TestGetInterfaceVlan
# ---------------------------------------------------------------------------


class TestGetInterfaceVlan:
    """Tests for GET /labs/{lab_id}/interfaces/{node}/{interface}/vlan."""

    def test_ovs_disabled(self, client, monkeypatch):
        """Returns error when OVS plugin is disabled."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", False)
        resp = client.get("/labs/lab1/interfaces/r1/eth1/vlan")
        body = resp.json()
        assert "error" in body

    def test_docker_fast_path(self, client, monkeypatch):
        """Returns VLAN from Docker plugin fast path."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        plugin = MagicMock()
        plugin.get_endpoint_vlan = AsyncMock(return_value=200)

        docker_provider = MagicMock()
        docker_provider.get_container_name.return_value = "archetype-lab1-r1"

        with patch("agent.routers.interfaces._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.routers.interfaces.get_provider",
                       side_effect=lambda x: docker_provider if x == "docker" else None):
                resp = client.get("/labs/lab1/interfaces/r1/eth1/vlan")

        body = resp.json()
        assert body["vlan_tag"] == 200

    def test_fallback_to_resolve_ovs_port(self, client, monkeypatch):
        """Falls back to _resolve_ovs_port when docker plugin returns None."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        plugin = MagicMock()
        plugin.get_endpoint_vlan = AsyncMock(return_value=None)

        docker_provider = MagicMock()
        docker_provider.get_container_name.return_value = "archetype-lab1-r1"

        port_info = SimpleNamespace(port_name="vh-abc", vlan_tag=300)

        with patch("agent.routers.interfaces._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.routers.interfaces.get_provider",
                       side_effect=lambda x: docker_provider if x == "docker" else None):
                with patch("agent.routers.interfaces._resolve_ovs_port",
                           new_callable=AsyncMock, return_value=port_info):
                    resp = client.get("/labs/lab1/interfaces/r1/eth1/vlan")

        body = resp.json()
        assert body["vlan_tag"] == 300

    def test_endpoint_not_found(self, client, monkeypatch):
        """Returns error when endpoint not found anywhere."""
        monkeypatch.setattr(settings, "enable_ovs_plugin", True)

        plugin = MagicMock()
        plugin.get_endpoint_vlan = AsyncMock(return_value=None)

        docker_provider = MagicMock()
        docker_provider.get_container_name.return_value = "archetype-lab1-r1"

        with patch("agent.routers.interfaces._get_docker_ovs_plugin", return_value=plugin):
            with patch("agent.routers.interfaces.get_provider",
                       side_effect=lambda x: docker_provider if x == "docker" else None):
                with patch("agent.routers.interfaces._resolve_ovs_port",
                           new_callable=AsyncMock, return_value=None):
                    resp = client.get("/labs/lab1/interfaces/r1/eth1/vlan")

        body = resp.json()
        assert "error" in body
        assert "not found" in body["error"].lower()


# ---------------------------------------------------------------------------
# TestSetHostInterfaceMtu
# ---------------------------------------------------------------------------


class TestSetHostInterfaceMtu:
    """Tests for POST /interfaces/{interface_name}/mtu."""

    def test_interface_not_found(self, client):
        """Returns error for non-existent interface."""
        with patch("agent.network.interface_config.get_interface_mtu", return_value=None):
            with patch("agent.network.interface_config.is_physical_interface", return_value=True):
                with patch("agent.network.interface_config.detect_network_manager", return_value="unknown"):
                    resp = client.post("/interfaces/eth99/mtu", json={"mtu": 9000})

        body = resp.json()
        assert body["success"] is False
        assert "not found" in body["error"].lower()

    def test_success_without_persist(self, client):
        """Successful MTU change without persistence."""
        with patch("agent.network.interface_config.get_interface_mtu", side_effect=[1500, 9000]):
            with patch("agent.network.interface_config.is_physical_interface", return_value=True):
                with patch("agent.network.interface_config.detect_network_manager", return_value="systemd-networkd"):
                    with patch("agent.network.interface_config.set_mtu_runtime",
                               new_callable=AsyncMock, return_value=(True, None)):
                        resp = client.post("/interfaces/eth0/mtu", json={"mtu": 9000})

        body = resp.json()
        assert body["success"] is True
        assert body["previous_mtu"] == 1500
        assert body["new_mtu"] == 9000

    def test_runtime_set_failure(self, client):
        """Returns error when runtime MTU change fails."""
        with patch("agent.network.interface_config.get_interface_mtu", return_value=1500):
            with patch("agent.network.interface_config.is_physical_interface", return_value=True):
                with patch("agent.network.interface_config.detect_network_manager", return_value="systemd-networkd"):
                    with patch("agent.network.interface_config.set_mtu_runtime",
                               new_callable=AsyncMock, return_value=(False, "permission denied")):
                        resp = client.post("/interfaces/eth0/mtu", json={"mtu": 9000})

        body = resp.json()
        assert body["success"] is False
        assert "permission denied" in body["error"]


# ---------------------------------------------------------------------------
# TestProvisionInterface
# ---------------------------------------------------------------------------


class TestProvisionInterface:
    """Tests for POST /interfaces/provision."""

    def test_create_subinterface_missing_parent(self, client):
        """Returns error when parent_interface is missing for create."""
        resp = client.post("/interfaces/provision", json={
            "action": "create_subinterface",
        })
        body = resp.json()
        assert body["success"] is False
        assert "parent_interface" in body["error"]

    def test_create_subinterface_missing_vlan_id(self, client):
        """Returns error when vlan_id is missing for create."""
        resp = client.post("/interfaces/provision", json={
            "action": "create_subinterface",
            "parent_interface": "ens5",
        })
        body = resp.json()
        assert body["success"] is False
        assert "vlan_id" in body["error"]

    def test_configure_missing_name(self, client):
        """Returns error when name is missing for configure."""
        resp = client.post("/interfaces/provision", json={
            "action": "configure",
        })
        body = resp.json()
        assert body["success"] is False
        assert "name" in body["error"]

    def test_delete_missing_name(self, client):
        """Returns error when name is missing for delete."""
        resp = client.post("/interfaces/provision", json={
            "action": "delete",
        })
        body = resp.json()
        assert body["success"] is False
        assert "name" in body["error"]

    def test_unknown_action(self, client):
        """Returns 422 for action not in Literal[create_subinterface, configure, delete]."""
        resp = client.post("/interfaces/provision", json={
            "action": "reboot",
        })
        assert resp.status_code == 422
