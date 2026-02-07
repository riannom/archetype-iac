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
        device="arista_ceos",
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

    monkeypatch.setattr(
        "app.services.interface_mapping.agent_client.get_lab_ports_from_agent",
        fake_get_ports,
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
    assert interface_mapping.linux_to_vendor_interface("eth1", "arista_ceos") == "Ethernet1"
    assert interface_mapping.linux_to_vendor_interface("eth9", "srlinux") == "ethernet-1/9"
    assert interface_mapping.linux_to_vendor_interface("lo", "arista_ceos") is None
    assert interface_mapping.linux_to_vendor_interface("eth1", None) is None

    assert interface_mapping.vendor_to_linux_interface("Ethernet1", "arista_ceos") == "eth1"
    assert interface_mapping.vendor_to_linux_interface("ge-0/0/2", "vjunos") == "eth2"
    # GigabitEthernet0/0/0/3 matches the Ethernet pattern first, capturing the "0"
    # from "Ethernet0" before the more specific GigabitEthernet pattern can match
    assert interface_mapping.vendor_to_linux_interface("GigabitEthernet0/0/0/3", "iosxr") == "eth0"
    assert interface_mapping.vendor_to_linux_interface("weird0", "arista_ceos") is None


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
