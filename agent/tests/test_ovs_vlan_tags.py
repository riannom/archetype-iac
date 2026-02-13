from agent.network.ovs_vlan_tags import used_vlan_tags_on_bridge_from_ovs_outputs


def test_used_vlan_tags_on_bridge_filters_by_bridge_ports() -> None:
    list_ports = "vnet605\nvnet606\nvh123\n"
    csv_text = "name,tag\nvnet605,2002\nvnet606,2003\nnot_on_bridge,777\nvh123,[]\n"

    used = used_vlan_tags_on_bridge_from_ovs_outputs(
        bridge_list_ports_output=list_ports,
        list_port_name_tag_csv=csv_text,
    )
    assert used == {2002, 2003}


def test_used_vlan_tags_on_bridge_parses_bracketed_tags_and_ignores_invalid() -> None:
    list_ports = "p1\np2\np3\np4\n"
    csv_text = "name,tag\np1,\"[2002]\"\np2,\"[]\"\np3,0\np4,\"[2003]\"\n"

    used = used_vlan_tags_on_bridge_from_ovs_outputs(
        bridge_list_ports_output=list_ports,
        list_port_name_tag_csv=csv_text,
    )
    assert used == {2002, 2003}

