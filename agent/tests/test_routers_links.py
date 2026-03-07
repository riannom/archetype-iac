"""Tests for link hot-connect and hot-disconnect endpoints (agent/routers/links.py).

Covers:
- _split_link_id_candidates parsing logic
- _resolve_link_id_endpoints resolution
- POST /labs/{lab_id}/links — create link
- DELETE /labs/{lab_id}/links/{link_id} — delete link
- GET /labs/{lab_id}/links — list links
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent.config import settings
from agent.helpers import OVSPortInfo
from agent.main import app
from agent.routers.links import _split_link_id_candidates, _resolve_link_id_endpoints


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """TestClient with auth disabled."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    monkeypatch.setattr(settings, "controller_secret", "")


@pytest.fixture(autouse=True)
def _enable_ovs(monkeypatch):
    """Most tests need OVS enabled; individual tests disable as needed."""
    monkeypatch.setattr(settings, "enable_ovs", True)
    monkeypatch.setattr(settings, "ovs_bridge_name", "arch-ovs")
    monkeypatch.setattr(settings, "enable_ovs_plugin", False)


# ---------------------------------------------------------------------------
# TestSplitLinkIdCandidates
# ---------------------------------------------------------------------------


class TestSplitLinkIdCandidates:
    """Tests for _split_link_id_candidates parsing."""

    def test_standard_link_id(self):
        """Simple 'nodeA:eth1-nodeB:eth2' should produce one candidate."""
        result = _split_link_id_candidates("r1:eth1-r2:eth2")
        assert len(result) == 1
        assert result[0] == ("r1", "eth1", "r2", "eth2")

    def test_hyphenated_node_name(self):
        """Nodes containing hyphens should produce multiple candidates."""
        result = _split_link_id_candidates("my-router:eth1-sw1:eth1")
        # Both "my" / "router:eth1-sw1:eth1" and "my-router" / "sw1" splits
        assert len(result) >= 1
        # The valid candidate should be present
        assert ("my-router", "eth1", "sw1", "eth1") in result

    def test_no_colon_gives_empty(self):
        """Input without colon should return no candidates."""
        result = _split_link_id_candidates("abc-def")
        assert result == []

    def test_deduplicates(self):
        """Identical tuples from different split points should be deduped."""
        # A link_id that can only parse one way produces exactly one entry
        result = _split_link_id_candidates("a:e1-b:e2")
        assert len(result) == 1

    def test_empty_parts_filtered(self):
        """Splits producing empty node or iface should be excluded."""
        # Leading hyphen: left would be empty before colon
        result = _split_link_id_candidates("-:eth1-r2:eth2")
        # All candidates with empty node/iface should be filtered
        for node_a, iface_a, node_b, iface_b in result:
            assert node_a and iface_a and node_b and iface_b


# ---------------------------------------------------------------------------
# TestResolveLinkIdEndpoints
# ---------------------------------------------------------------------------


class TestResolveLinkIdEndpoints:
    """Tests for _resolve_link_id_endpoints resolution."""

    @pytest.mark.asyncio
    async def test_both_ports_success(self):
        """Should resolve both endpoints when OVS ports are found."""
        port_a = OVSPortInfo(port_name="vh1", vlan_tag=100, provider="docker")
        port_b = OVSPortInfo(port_name="vh2", vlan_tag=200, provider="docker")

        async def fake_resolve(lab_id, node, iface):
            if node == "r1":
                return port_a
            if node == "r2":
                return port_b
            return None

        with patch("agent.routers.links._resolve_ovs_port", side_effect=fake_resolve):
            resolved, err = await _resolve_link_id_endpoints("lab-1", "r1:eth1-r2:eth2")
        assert err is None
        assert resolved is not None
        assert resolved[0] == "r1"
        assert resolved[4] == port_a
        assert resolved[5] == port_b

    @pytest.mark.asyncio
    async def test_invalid_format(self):
        """Link ID without colons should return an error."""
        resolved, err = await _resolve_link_id_endpoints("lab-1", "noformat")
        assert resolved is None
        assert "Invalid link_id" in err

    @pytest.mark.asyncio
    async def test_ambiguous(self):
        """Multiple valid resolutions should return ambiguous error."""
        # Build a link_id where two split positions both resolve
        # "a-b:e1-c:e2" splits as ("a-b","e1","c","e2") and maybe another
        # We force both candidates to resolve to trigger ambiguity
        port = OVSPortInfo(port_name="vh1", vlan_tag=100, provider="docker")

        async def always_resolve(lab_id, node, iface):
            return port

        with patch("agent.routers.links._resolve_ovs_port", side_effect=always_resolve):
            # "x:e1-y:e2-z:e3" — split at first '-' and second '-' both have colons
            resolved, err = await _resolve_link_id_endpoints(
                "lab-1", "x:e1-y:e2-z:e3",
            )
        assert resolved is None
        assert "Ambiguous" in err

    @pytest.mark.asyncio
    async def test_unresolvable(self):
        """If no OVS ports resolve, return unresolvable error."""
        async def no_resolve(lab_id, node, iface):
            return None

        with patch("agent.routers.links._resolve_ovs_port", side_effect=no_resolve):
            resolved, err = await _resolve_link_id_endpoints("lab-1", "r1:eth1-r2:eth2")
        assert resolved is None
        assert "Unresolvable" in err


# ---------------------------------------------------------------------------
# TestCreateLink
# ---------------------------------------------------------------------------


class TestCreateLink:
    """Tests for POST /labs/{lab_id}/links."""

    def _link_body(self, **overrides) -> dict:
        d = {
            "source_node": "r1",
            "source_interface": "eth1",
            "target_node": "r2",
            "target_interface": "eth1",
        }
        d.update(overrides)
        return d

    def test_success(self, client: TestClient, monkeypatch):
        """Should return success=True with link details."""
        port_a = OVSPortInfo(port_name="vh1", vlan_tag=100, provider="docker")
        port_b = OVSPortInfo(port_name="vh2", vlan_tag=200, provider="docker")

        async def fake_resolve(lab_id, node, iface):
            return port_a if node == "r1" else port_b

        async def fake_alloc(bridge):
            return 2050

        async def fake_set_vlan(port_name, vlan_tag):
            return True

        monkeypatch.setattr("agent.routers.links._resolve_ovs_port", fake_resolve)
        monkeypatch.setattr("agent.routers.links._ovs_allocate_link_vlan", fake_alloc)
        monkeypatch.setattr("agent.routers.links._ovs_set_port_vlan", fake_set_vlan)

        resp = client.post("/labs/lab-1/links", json=self._link_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["link"]["vlan_tag"] == 2050
        assert data["link"]["state"] == "connected"

    def test_ovs_disabled(self, client: TestClient, monkeypatch):
        """Should return success=False when OVS is disabled."""
        monkeypatch.setattr(settings, "enable_ovs", False)
        resp = client.post("/labs/lab-1/links", json=self._link_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "not enabled" in data["error"]

    def test_port_a_not_found(self, client: TestClient, monkeypatch):
        """Missing source port should return error."""
        async def fake_resolve(lab_id, node, iface):
            return None

        monkeypatch.setattr("agent.routers.links._resolve_ovs_port", fake_resolve)
        resp = client.post("/labs/lab-1/links", json=self._link_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "r1" in data["error"]

    def test_port_b_not_found(self, client: TestClient, monkeypatch):
        """Missing target port should return error."""
        port_a = OVSPortInfo(port_name="vh1", vlan_tag=100, provider="docker")

        async def fake_resolve(lab_id, node, iface):
            return port_a if node == "r1" else None

        monkeypatch.setattr("agent.routers.links._resolve_ovs_port", fake_resolve)
        resp = client.post("/labs/lab-1/links", json=self._link_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "r2" in data["error"]

    def test_live_interface_fallback_resolves_missing_source(self, client: TestClient, monkeypatch):
        """If cached resolution fails, live interface probe can still create the link."""
        port_b = OVSPortInfo(port_name="vh2", vlan_tag=200, provider="docker")

        async def fake_resolve(lab_id, node, iface):
            return None if node == "r1" else port_b

        async def fake_live(lab_id, node_name, interface_name):
            if node_name == "r1":
                return OVSPortInfo(port_name="vh-live", vlan_tag=111, provider="live")
            return None

        async def fake_alloc(bridge):
            return 2050

        async def fake_set_vlan(port_name, vlan_tag):
            return True

        monkeypatch.setattr("agent.routers.links._resolve_ovs_port", fake_resolve)
        monkeypatch.setattr("agent.routers.links._resolve_ovs_port_live", fake_live)
        monkeypatch.setattr("agent.routers.links._ovs_allocate_link_vlan", fake_alloc)
        monkeypatch.setattr("agent.routers.links._ovs_set_port_vlan", fake_set_vlan)

        resp = client.post("/labs/lab-1/links", json=self._link_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["link"]["source_ovs_port"] == "vh-live"

    def test_no_free_vlan(self, client: TestClient, monkeypatch):
        """No available VLAN should return error."""
        port_a = OVSPortInfo(port_name="vh1", vlan_tag=100, provider="docker")
        port_b = OVSPortInfo(port_name="vh2", vlan_tag=200, provider="docker")

        async def fake_resolve(lab_id, node, iface):
            return port_a if node == "r1" else port_b

        async def fake_alloc(bridge):
            return None

        monkeypatch.setattr("agent.routers.links._resolve_ovs_port", fake_resolve)
        monkeypatch.setattr("agent.routers.links._ovs_allocate_link_vlan", fake_alloc)

        resp = client.post("/labs/lab-1/links", json=self._link_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "No free VLAN" in data["error"]

    def test_vlan_set_failure_rolls_back(self, client: TestClient, monkeypatch):
        """Failure to set VLAN on port B should roll back port A."""
        port_a = OVSPortInfo(port_name="vh1", vlan_tag=100, provider="docker")
        port_b = OVSPortInfo(port_name="vh2", vlan_tag=200, provider="docker")
        rollback_calls = []

        async def fake_resolve(lab_id, node, iface):
            return port_a if node == "r1" else port_b

        async def fake_alloc(bridge):
            return 2050

        async def fake_set_vlan(port_name, vlan_tag):
            if port_name == "vh2" and vlan_tag == 2050:
                return False  # Fail on B
            rollback_calls.append((port_name, vlan_tag))
            return True

        monkeypatch.setattr("agent.routers.links._resolve_ovs_port", fake_resolve)
        monkeypatch.setattr("agent.routers.links._ovs_allocate_link_vlan", fake_alloc)
        monkeypatch.setattr("agent.routers.links._ovs_set_port_vlan", fake_set_vlan)

        resp = client.post("/labs/lab-1/links", json=self._link_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        # Port A should have been rolled back to old VLAN
        assert any(vlan == 100 for _, vlan in rollback_calls)

    def test_updates_docker_plugin(self, client: TestClient, monkeypatch):
        """When enable_ovs_plugin is True, plugin state should be updated."""
        port_a = OVSPortInfo(port_name="vh1", vlan_tag=100, provider="docker")
        port_b = OVSPortInfo(port_name="vh2", vlan_tag=200, provider="docker")

        async def fake_resolve(lab_id, node, iface):
            return port_a if node == "r1" else port_b

        async def fake_alloc(bridge):
            return 2050

        async def fake_set_vlan(port_name, vlan_tag):
            return True

        monkeypatch.setattr(settings, "enable_ovs_plugin", True)
        monkeypatch.setattr("agent.routers.links._resolve_ovs_port", fake_resolve)
        monkeypatch.setattr("agent.routers.links._ovs_allocate_link_vlan", fake_alloc)
        monkeypatch.setattr("agent.routers.links._ovs_set_port_vlan", fake_set_vlan)

        mock_ep = MagicMock()
        mock_ep.host_veth = "vh1"
        mock_ep.vlan_tag = 100

        mock_plugin = MagicMock()
        mock_plugin.endpoints = {"ep1": mock_ep}
        mock_plugin._release_vlan = MagicMock()
        mock_plugin._release_linked_vlan = MagicMock()
        mock_plugin._mark_dirty_and_save = AsyncMock()

        monkeypatch.setattr("agent.routers.links._get_docker_ovs_plugin", lambda: mock_plugin)

        resp = client.post("/labs/lab-1/links", json=self._link_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # Plugin should have been told to save
        mock_plugin._mark_dirty_and_save.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestDeleteLink
# ---------------------------------------------------------------------------


class TestDeleteLink:
    """Tests for DELETE /labs/{lab_id}/links/{link_id}."""

    def test_success(self, client: TestClient, monkeypatch):
        """Successful disconnect should return success=True."""
        port_a = OVSPortInfo(port_name="vh1", vlan_tag=2050, provider="docker")
        port_b = OVSPortInfo(port_name="vh2", vlan_tag=2050, provider="docker")

        async def fake_resolve(lab_id, node, iface):
            return port_a if node == "r1" else port_b

        async def fake_used(bridge):
            return {2050}

        async def fake_set_vlan(port_name, vlan_tag):
            return True

        monkeypatch.setattr("agent.routers.links._resolve_ovs_port", fake_resolve)
        monkeypatch.setattr("agent.routers.links._ovs_list_used_vlans", fake_used)
        monkeypatch.setattr("agent.routers.links._ovs_set_port_vlan", fake_set_vlan)
        monkeypatch.setattr("agent.routers.links._pick_isolation_vlan", lambda used, bridge, port: 150)

        resp = client.delete("/labs/lab-1/links/r1:eth1-r2:eth2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_ovs_disabled(self, client: TestClient, monkeypatch):
        """Should return success=False when OVS is disabled."""
        monkeypatch.setattr(settings, "enable_ovs", False)
        resp = client.delete("/labs/lab-1/links/r1:eth1-r2:eth2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "not enabled" in data["error"]

    def test_parse_error(self, client: TestClient, monkeypatch):
        """Invalid link_id should return parse error."""
        async def no_resolve(lab_id, node, iface):
            return None

        monkeypatch.setattr("agent.routers.links._resolve_ovs_port", no_resolve)

        resp = client.delete("/labs/lab-1/links/badformat")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "Invalid link_id" in data["error"] or "Unresolvable" in data["error"]

    def test_allocation_failure(self, client: TestClient, monkeypatch):
        """Failure to allocate isolation VLAN should return error."""
        port_a = OVSPortInfo(port_name="vh1", vlan_tag=2050, provider="docker")
        port_b = OVSPortInfo(port_name="vh2", vlan_tag=2050, provider="docker")

        async def fake_resolve(lab_id, node, iface):
            return port_a if node == "r1" else port_b

        async def fake_used(bridge):
            return set()

        monkeypatch.setattr("agent.routers.links._resolve_ovs_port", fake_resolve)
        monkeypatch.setattr("agent.routers.links._ovs_list_used_vlans", fake_used)
        monkeypatch.setattr("agent.routers.links._pick_isolation_vlan", lambda used, bridge, port: None)

        resp = client.delete("/labs/lab-1/links/r1:eth1-r2:eth2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "allocate" in data["error"].lower() or "VLAN" in data["error"]

    def test_rollback_on_second_port_failure(self, client: TestClient, monkeypatch):
        """Failure on second port should roll back the first."""
        port_a = OVSPortInfo(port_name="vh1", vlan_tag=2050, provider="docker")
        port_b = OVSPortInfo(port_name="vh2", vlan_tag=2050, provider="docker")

        async def fake_resolve(lab_id, node, iface):
            return port_a if node == "r1" else port_b

        async def fake_used(bridge):
            return set()

        call_count = 0

        async def fake_set_vlan(port_name, vlan_tag):
            nonlocal call_count
            call_count += 1
            # Succeed first, fail second
            if call_count == 2:
                return False
            return True

        monkeypatch.setattr("agent.routers.links._resolve_ovs_port", fake_resolve)
        monkeypatch.setattr("agent.routers.links._ovs_list_used_vlans", fake_used)
        monkeypatch.setattr("agent.routers.links._ovs_set_port_vlan", fake_set_vlan)
        monkeypatch.setattr("agent.routers.links._pick_isolation_vlan", lambda used, bridge, port: 150)

        resp = client.delete("/labs/lab-1/links/r1:eth1-r2:eth2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        # At least 3 calls: set A, fail B, rollback A
        assert call_count >= 3

    def test_updates_docker_plugin(self, client: TestClient, monkeypatch):
        """Plugin state should be updated on successful disconnect."""
        port_a = OVSPortInfo(port_name="vh1", vlan_tag=2050, provider="docker")
        port_b = OVSPortInfo(port_name="vh2", vlan_tag=2050, provider="docker")

        async def fake_resolve(lab_id, node, iface):
            return port_a if node == "r1" else port_b

        async def fake_used(bridge):
            return {2050}

        async def fake_set_vlan(port_name, vlan_tag):
            return True

        monkeypatch.setattr(settings, "enable_ovs_plugin", True)
        monkeypatch.setattr("agent.routers.links._resolve_ovs_port", fake_resolve)
        monkeypatch.setattr("agent.routers.links._ovs_list_used_vlans", fake_used)
        monkeypatch.setattr("agent.routers.links._ovs_set_port_vlan", fake_set_vlan)
        monkeypatch.setattr("agent.routers.links._pick_isolation_vlan", lambda used, bridge, port: 150)

        mock_ep_a = MagicMock()
        mock_ep_a.host_veth = "vh1"
        mock_ep_a.vlan_tag = 2050

        mock_plugin = MagicMock()
        mock_plugin.endpoints = {"ep_a": mock_ep_a}
        mock_plugin._release_vlan = MagicMock()
        mock_plugin._release_linked_vlan = MagicMock()
        mock_plugin._allocated_vlans = set()
        mock_plugin._allocated_linked_vlans = set()
        mock_plugin._mark_dirty_and_save = AsyncMock()

        monkeypatch.setattr("agent.routers.links._get_docker_ovs_plugin", lambda: mock_plugin)

        resp = client.delete("/labs/lab-1/links/r1:eth1-r2:eth2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        mock_plugin._mark_dirty_and_save.assert_awaited_once()


# ---------------------------------------------------------------------------
# TestListLinks
# ---------------------------------------------------------------------------


class TestListLinks:
    """Tests for GET /labs/{lab_id}/links."""

    def test_success(self, client: TestClient, monkeypatch):
        """Should return list of links for a lab."""
        mock_link = MagicMock()
        mock_link.link_id = "link-1"
        mock_link.lab_id = "lab-1"
        mock_link.port_a = "archetype-lab-1-r1:eth1"
        mock_link.port_b = "archetype-lab-1-r2:eth1"
        mock_link.vlan_tag = 2050

        mock_backend = MagicMock()
        mock_backend.ovs_initialized.return_value = True
        mock_backend.get_links_for_lab.return_value = [mock_link]

        monkeypatch.setattr("agent.routers.links.get_network_backend", lambda: mock_backend)
        monkeypatch.setattr("agent.routers.links.get_provider_for_request", lambda: MagicMock())

        resp = client.get("/labs/lab-1/links")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["links"]) == 1
        assert data["links"][0]["vlan_tag"] == 2050

    def test_empty(self, client: TestClient, monkeypatch):
        """Empty lab should return empty list."""
        mock_backend = MagicMock()
        mock_backend.ovs_initialized.return_value = True
        mock_backend.get_links_for_lab.return_value = []

        monkeypatch.setattr("agent.routers.links.get_network_backend", lambda: mock_backend)
        monkeypatch.setattr("agent.routers.links.get_provider_for_request", lambda: MagicMock())

        resp = client.get("/labs/lab-1/links")
        assert resp.status_code == 200
        data = resp.json()
        assert data["links"] == []

    def test_ovs_disabled(self, client: TestClient, monkeypatch):
        """Should return empty list when OVS is disabled."""
        monkeypatch.setattr(settings, "enable_ovs", False)
        resp = client.get("/labs/lab-1/links")
        assert resp.status_code == 200
        data = resp.json()
        assert data["links"] == []
