from __future__ import annotations

import pytest

from app import models
import app.services.interface_mapping as interface_mapping


@pytest.mark.asyncio
async def test_populate_from_agent_creates_and_updates(test_db, sample_lab, sample_host, monkeypatch) -> None:
    node = models.Node(
        lab_id=sample_lab.id,
        gui_id="r1",
        display_name="R1",
        container_name="r1",
        node_type="device",
        device="ceos",
    )
    test_db.add(node)
    test_db.commit()

    ports = [
        {
            "container": f"archetype-{sample_lab.id}-r1",
            "interface": "eth1",
            "port_name": "vh123",
            "bridge_name": "arch-ovs",
            "vlan_tag": 123,
        },
        {"container": None, "interface": "eth2"},
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

    assert result["created"] == 1
    assert result["skipped"] == 1

    mapping = (
        test_db.query(models.InterfaceMapping)
        .filter(models.InterfaceMapping.lab_id == sample_lab.id)
        .first()
    )
    assert mapping is not None
    assert mapping.vendor_interface == "Ethernet1"

    ports[0]["vlan_tag"] = 456
    result = await interface_mapping.populate_from_agent(test_db, sample_lab.id, sample_host)
    assert result["updated"] == 1

    mapping = (
        test_db.query(models.InterfaceMapping)
        .filter(models.InterfaceMapping.lab_id == sample_lab.id)
        .first()
    )
    assert mapping.vlan_tag == 456


def test_interface_name_translations() -> None:
    # Use canonical vendor catalog keys (not old hardcoded aliases)
    assert interface_mapping.linux_to_vendor_interface("eth1", "ceos") == "Ethernet1"
    assert interface_mapping.linux_to_vendor_interface("eth9", "nokia_srlinux") == "e1-9"
    assert interface_mapping.linux_to_vendor_interface("lo", "ceos") is None
    assert interface_mapping.linux_to_vendor_interface("eth1", None) is None

    assert interface_mapping.vendor_to_linux_interface("Ethernet1", "ceos") == "eth1"
    # Juniper ge-0/0/{N}: port_start_index=0, dps=1 → eth{2 - 0 + 1} = eth3
    assert interface_mapping.vendor_to_linux_interface("ge-0/0/2", "juniper_vjunosswitch") == "eth3"
    assert interface_mapping.vendor_to_linux_interface("ge-0/0/2", "juniper_vjunosrouter") == "eth3"
    assert interface_mapping.vendor_to_linux_interface("ge-0/0/2", "juniper_vjunosevolved") == "eth3"
    # Device-aware translation: GigabitEthernet0/0/0/3 with port_start_index=0
    # Formula: eth{3 - 0 + 3} = eth6 (dps=3: mgmt(1) + reserved_nics(2))
    assert interface_mapping.vendor_to_linux_interface("GigabitEthernet0/0/0/3", "cisco_iosxr") == "eth6"
    assert interface_mapping.vendor_to_linux_interface("weird0", "ceos") is None


def test_update_vlan_tag(test_db, sample_lab) -> None:
    node = models.Node(
        lab_id=sample_lab.id,
        gui_id="r1",
        display_name="R1",
        container_name="r1",
        node_type="device",
        device="linux",
    )
    test_db.add(node)
    test_db.commit()

    mapping = models.InterfaceMapping(
        lab_id=sample_lab.id,
        node_id=node.id,
        linux_interface="eth1",
        vlan_tag=100,
    )
    test_db.add(mapping)
    test_db.commit()

    assert interface_mapping.update_vlan_tag(test_db, sample_lab.id, node.id, "eth1", 200)

    updated = (
        test_db.query(models.InterfaceMapping)
        .filter(models.InterfaceMapping.lab_id == sample_lab.id)
        .first()
    )
    assert updated.vlan_tag == 200


@pytest.mark.asyncio
async def test_populate_from_agent_uses_port_state_for_libvirt_nodes(
    test_db, sample_lab, sample_host, monkeypatch,
) -> None:
    node = models.Node(
        lab_id=sample_lab.id,
        gui_id="vm1",
        display_name="vm1",
        container_name="vm1",
        node_type="device",
        device="cat9000v-q200",
    )
    test_db.add(node)
    test_db.commit()

    async def fake_get_ports(agent, lab_id):
        return []

    async def fake_get_port_state(agent, lab_id):
        return [
            {
                "node_name": "vm1",
                "interface_name": "eth1",
                "ovs_port_name": "vnet306",
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
    mapping = (
        test_db.query(models.InterfaceMapping)
        .filter(
            models.InterfaceMapping.lab_id == sample_lab.id,
            models.InterfaceMapping.node_id == node.id,
            models.InterfaceMapping.linux_interface == "eth1",
        )
        .first()
    )
    assert mapping is not None
    assert mapping.ovs_port == "vnet306"
    assert mapping.vlan_tag == 256
    assert mapping.last_verified_at is not None
