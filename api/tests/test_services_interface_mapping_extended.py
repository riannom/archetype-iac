"""Extended tests for app.services.interface_mapping.

Covers edge cases: duplicate ports, container name matching, get_mapping_by_ovs_port,
delete_lab_mappings, populate_all_agents, and error handling not covered by existing tests.
"""
from __future__ import annotations

import pytest

from app import models
import app.services.interface_mapping as interface_mapping


# ---------------------------------------------------------------------------
# Tests: linux_to_vendor_interface edge cases
# ---------------------------------------------------------------------------

class TestLinuxToVendorEdgeCases:
    def test_eth0_for_eth_naming_device(self) -> None:
        """eth0 on a device that uses eth naming (like linux) returns eth0."""
        result = interface_mapping.linux_to_vendor_interface("eth0", "linux")
        assert result == "eth0"

    def test_non_eth_pattern_returns_none(self) -> None:
        """Non-ethN patterns that don't convert should return None."""
        result = interface_mapping.linux_to_vendor_interface("lo", "ceos")
        assert result is None

    def test_mgmt_interface_returns_none(self) -> None:
        """Management-like interfaces should return None for standard devices."""
        result = interface_mapping.linux_to_vendor_interface("mgmt0", "ceos")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: vendor_to_linux_interface edge cases
# ---------------------------------------------------------------------------

class TestVendorToLinuxEdgeCases:
    def test_eth_input_passthrough(self) -> None:
        """ethN input on any device should pass through."""
        result = interface_mapping.vendor_to_linux_interface("eth5", "linux")
        assert result == "eth5"

    def test_unrecognized_vendor_name_returns_none(self) -> None:
        """Random vendor name that doesn't match any pattern returns None."""
        result = interface_mapping.vendor_to_linux_interface("weird0", "ceos")
        assert result is None

    def test_case_insensitive_eth_passthrough(self) -> None:
        """Eth1 should be recognized as eth-style."""
        result = interface_mapping.vendor_to_linux_interface("Eth1", "linux")
        # normalize_interface should handle this
        assert result is not None


# ---------------------------------------------------------------------------
# Tests: get_mapping
# ---------------------------------------------------------------------------

class TestGetMapping:
    def test_returns_none_for_nonexistent(self, test_db, sample_lab) -> None:
        result = interface_mapping.get_mapping(
            test_db, sample_lab.id, "fake-node", "eth1"
        )
        assert result is None

    def test_returns_mapping_when_exists(self, test_db, sample_lab) -> None:
        node = models.Node(
            lab_id=sample_lab.id,
            gui_id="r1", display_name="R1",
            container_name="r1", node_type="device", device="linux",
        )
        test_db.add(node)
        test_db.commit()

        mapping = models.InterfaceMapping(
            lab_id=sample_lab.id,
            node_id=node.id,
            linux_interface="eth1",
            ovs_port="vh123",
            vlan_tag=100,
        )
        test_db.add(mapping)
        test_db.commit()

        result = interface_mapping.get_mapping(
            test_db, sample_lab.id, node.id, "eth1"
        )
        assert result is not None
        assert result.ovs_port == "vh123"
        assert result.vlan_tag == 100


# ---------------------------------------------------------------------------
# Tests: get_mapping_by_ovs_port
# ---------------------------------------------------------------------------

class TestGetMappingByOvsPort:
    def test_returns_none_for_nonexistent(self, test_db) -> None:
        result = interface_mapping.get_mapping_by_ovs_port(test_db, "nonexistent")
        assert result is None

    def test_returns_mapping_when_exists(self, test_db, sample_lab) -> None:
        node = models.Node(
            lab_id=sample_lab.id,
            gui_id="r1", display_name="R1",
            container_name="r1", node_type="device", device="linux",
        )
        test_db.add(node)
        test_db.commit()

        mapping = models.InterfaceMapping(
            lab_id=sample_lab.id,
            node_id=node.id,
            linux_interface="eth1",
            ovs_port="vh999abc",
        )
        test_db.add(mapping)
        test_db.commit()

        result = interface_mapping.get_mapping_by_ovs_port(test_db, "vh999abc")
        assert result is not None
        assert result.linux_interface == "eth1"


# ---------------------------------------------------------------------------
# Tests: update_vlan_tag edge cases
# ---------------------------------------------------------------------------

class TestUpdateVlanTagEdge:
    def test_returns_false_for_nonexistent(self, test_db, sample_lab) -> None:
        result = interface_mapping.update_vlan_tag(
            test_db, sample_lab.id, "fake-node", "eth1", 500
        )
        assert result is False


# ---------------------------------------------------------------------------
# Tests: delete_lab_mappings
# ---------------------------------------------------------------------------

class TestDeleteLabMappings:
    def test_returns_zero_for_empty_lab(self, test_db, sample_lab) -> None:
        count = interface_mapping.delete_lab_mappings(test_db, sample_lab.id)
        assert count == 0

    def test_deletes_all_mappings_for_lab(self, test_db, sample_lab) -> None:
        node = models.Node(
            lab_id=sample_lab.id,
            gui_id="r1", display_name="R1",
            container_name="r1", node_type="device", device="linux",
        )
        test_db.add(node)
        test_db.commit()

        for iface in ["eth1", "eth2", "eth3"]:
            test_db.add(models.InterfaceMapping(
                lab_id=sample_lab.id,
                node_id=node.id,
                linux_interface=iface,
            ))
        test_db.commit()

        count = interface_mapping.delete_lab_mappings(test_db, sample_lab.id)
        assert count == 3

        remaining = test_db.query(models.InterfaceMapping).filter(
            models.InterfaceMapping.lab_id == sample_lab.id
        ).count()
        assert remaining == 0


# ---------------------------------------------------------------------------
# Tests: populate_from_agent — deduplication and edge cases
# ---------------------------------------------------------------------------

class TestPopulateFromAgentEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_ports_returns_zeros(self, test_db, sample_lab, sample_host, monkeypatch) -> None:
        async def fake_get_ports(agent, lab_id):
            return []
        async def fake_get_port_state(agent, lab_id):
            return []

        monkeypatch.setattr(
            "app.services.interface_mapping.agent_client.get_lab_ports_from_agent",
            fake_get_ports,
        )
        monkeypatch.setattr(
            "app.services.interface_mapping.agent_client.get_lab_port_state",
            fake_get_port_state,
        )

        result = await interface_mapping.populate_from_agent(test_db, sample_lab.id, sample_host)
        assert result["created"] == 0
        assert result["updated"] == 0

    @pytest.mark.asyncio
    async def test_none_ports_returns_zeros(self, test_db, sample_lab, sample_host, monkeypatch) -> None:
        async def fake_get_ports(agent, lab_id):
            return None
        async def fake_get_port_state(agent, lab_id):
            return None

        monkeypatch.setattr(
            "app.services.interface_mapping.agent_client.get_lab_ports_from_agent",
            fake_get_ports,
        )
        monkeypatch.setattr(
            "app.services.interface_mapping.agent_client.get_lab_port_state",
            fake_get_port_state,
        )

        result = await interface_mapping.populate_from_agent(test_db, sample_lab.id, sample_host)
        assert result["created"] == 0

    @pytest.mark.asyncio
    async def test_deduplicates_same_container_interface(self, test_db, sample_lab, sample_host, monkeypatch) -> None:
        node = models.Node(
            lab_id=sample_lab.id,
            gui_id="r1", display_name="R1",
            container_name="r1", node_type="device", device="linux",
        )
        test_db.add(node)
        test_db.commit()

        # Duplicate entries for same (container, interface)
        ports = [
            {"container": f"archetype-{sample_lab.id}-r1", "interface": "eth1",
             "port_name": "vh-old", "bridge_name": "arch-ovs", "vlan_tag": 100},
            {"container": f"archetype-{sample_lab.id}-r1", "interface": "eth1",
             "port_name": "vh-new", "bridge_name": "arch-ovs", "vlan_tag": 200},
        ]

        async def fake_get_ports(agent, lab_id):
            return ports
        async def fake_get_port_state(agent, lab_id):
            return []

        monkeypatch.setattr(
            "app.services.interface_mapping.agent_client.get_lab_ports_from_agent",
            fake_get_ports,
        )
        monkeypatch.setattr(
            "app.services.interface_mapping.agent_client.get_lab_port_state",
            fake_get_port_state,
        )

        result = await interface_mapping.populate_from_agent(test_db, sample_lab.id, sample_host)
        # Second entry wins due to dict key dedup
        assert result["created"] == 1
        mapping = test_db.query(models.InterfaceMapping).filter(
            models.InterfaceMapping.lab_id == sample_lab.id
        ).first()
        assert mapping.ovs_port == "vh-new"
        assert mapping.vlan_tag == 200

    @pytest.mark.asyncio
    async def test_port_state_overrides_stale_docker_inventory(
        self, test_db, sample_lab, sample_host, monkeypatch,
    ) -> None:
        node = models.Node(
            lab_id=sample_lab.id,
            gui_id="vm1", display_name="vm1",
            container_name="vm1", node_type="device", device="cat9000v-q200",
        )
        test_db.add(node)
        test_db.commit()

        async def fake_get_ports(agent, lab_id):
            return [
                {
                    "container": f"archetype-{sample_lab.id}-vm1",
                    "interface": "eth1",
                    "port_name": "vnet-old",
                    "bridge_name": "arch-ovs",
                    "vlan_tag": 999,
                },
            ]

        async def fake_get_port_state(agent, lab_id):
            return [
                {
                    "node_name": "vm1",
                    "interface_name": "eth1",
                    "ovs_port_name": "vnet-new",
                    "vlan_tag": 256,
                },
            ]

        monkeypatch.setattr(
            "app.services.interface_mapping.agent_client.get_lab_ports_from_agent",
            fake_get_ports,
        )
        monkeypatch.setattr(
            "app.services.interface_mapping.agent_client.get_lab_port_state",
            fake_get_port_state,
        )

        result = await interface_mapping.populate_from_agent(test_db, sample_lab.id, sample_host)

        assert result["created"] == 1
        mapping = test_db.query(models.InterfaceMapping).filter(
            models.InterfaceMapping.lab_id == sample_lab.id,
            models.InterfaceMapping.node_id == node.id,
            models.InterfaceMapping.linux_interface == "eth1",
        ).first()
        assert mapping.ovs_port == "vnet-new"
        assert mapping.vlan_tag == 256

    @pytest.mark.asyncio
    async def test_populate_node_from_agent_filters_to_target_node(
        self, test_db, sample_lab, sample_host, monkeypatch,
    ) -> None:
        node1 = models.Node(
            id="node-1",
            lab_id=sample_lab.id,
            gui_id="vm1",
            display_name="vm1",
            container_name="vm1",
            node_type="device",
            device="cat9000v-q200",
        )
        node2 = models.Node(
            id="node-2",
            lab_id=sample_lab.id,
            gui_id="vm2",
            display_name="vm2",
            container_name="vm2",
            node_type="device",
            device="cat9000v-q200",
        )
        test_db.add_all([node1, node2])
        test_db.commit()

        async def fake_get_ports(agent, lab_id):
            return []

        async def fake_get_port_state(agent, lab_id):
            return [
                {"node_name": "vm1", "interface_name": "eth1", "ovs_port_name": "vnet-1", "vlan_tag": 201},
                {"node_name": "vm2", "interface_name": "eth1", "ovs_port_name": "vnet-2", "vlan_tag": 202},
            ]

        monkeypatch.setattr(
            "app.services.interface_mapping.agent_client.get_lab_ports_from_agent",
            fake_get_ports,
        )
        monkeypatch.setattr(
            "app.services.interface_mapping.agent_client.get_lab_port_state",
            fake_get_port_state,
        )

        result = await interface_mapping.populate_node_from_agent(
            test_db,
            sample_lab.id,
            node1,
            sample_host,
        )

        assert result["created"] == 1
        mappings = test_db.query(models.InterfaceMapping).order_by(models.InterfaceMapping.node_id).all()
        assert len(mappings) == 1
        assert mappings[0].node_id == node1.id
        assert mappings[0].ovs_port == "vnet-1"
