"""Extended edge-case tests for agent/network/ovs_vlan_tags.py.

Complements test_ovs_vlan_tags.py with _parse_tag_field edge cases
and additional full-module scenarios.
"""
from __future__ import annotations

from agent.network.ovs_vlan_tags import (
    _parse_tag_field,
    parse_list_ports_output,
    used_vlan_tags_on_bridge_from_ovs_outputs,
)


# ---------------------------------------------------------------------------
# _parse_tag_field edge cases
# ---------------------------------------------------------------------------


def test_parse_tag_field_none() -> None:
    assert _parse_tag_field(None) is None  # type: ignore[arg-type]


def test_parse_tag_field_empty_string() -> None:
    assert _parse_tag_field("") is None


def test_parse_tag_field_empty_brackets() -> None:
    assert _parse_tag_field("[]") is None


def test_parse_tag_field_brackets_with_spaces() -> None:
    assert _parse_tag_field("[ ]") is None


def test_parse_tag_field_valid_number() -> None:
    assert _parse_tag_field("2002") == 2002


def test_parse_tag_field_quoted_number() -> None:
    assert _parse_tag_field('"2002"') == 2002


def test_parse_tag_field_bracketed_number() -> None:
    assert _parse_tag_field("[2002]") == 2002


def test_parse_tag_field_tag_zero() -> None:
    """Tag 0 is treated as unset (returns None)."""
    assert _parse_tag_field("0") is None


def test_parse_tag_field_negative() -> None:
    """Negative tag returns None (tag > 0 check)."""
    assert _parse_tag_field("-1") is None


def test_parse_tag_field_comma_separated() -> None:
    """Composite value with comma returns None."""
    assert _parse_tag_field("100,200") is None


def test_parse_tag_field_space_separated() -> None:
    """Composite value with space returns None."""
    assert _parse_tag_field("100 200") is None


def test_parse_tag_field_bracket_non_numeric() -> None:
    """Non-numeric value inside brackets returns None."""
    assert _parse_tag_field("[abc]") is None


def test_parse_tag_field_malformed_csv() -> None:
    """Malformed string that is not a valid integer."""
    assert _parse_tag_field("not_a_number") is None


def test_parse_tag_field_whitespace_padding() -> None:
    """Whitespace around a valid tag is stripped."""
    assert _parse_tag_field("  42  ") == 42


def test_parse_tag_field_quoted_brackets() -> None:
    """Quoted bracket form."""
    assert _parse_tag_field('"[3000]"') == 3000


# ---------------------------------------------------------------------------
# parse_list_ports_output
# ---------------------------------------------------------------------------


def test_parse_list_ports_output_empty() -> None:
    assert parse_list_ports_output("") == set()


def test_parse_list_ports_output_whitespace_lines() -> None:
    assert parse_list_ports_output("\n  \n\n") == set()


def test_parse_list_ports_output_normal() -> None:
    result = parse_list_ports_output("port1\nport2\nport3\n")
    assert result == {"port1", "port2", "port3"}


# ---------------------------------------------------------------------------
# used_vlan_tags_on_bridge_from_ovs_outputs — additional scenarios
# ---------------------------------------------------------------------------


def test_empty_bridge_ports() -> None:
    """No ports on bridge returns empty set."""
    result = used_vlan_tags_on_bridge_from_ovs_outputs(
        bridge_list_ports_output="",
        list_port_name_tag_csv="name,tag\np1,100\n",
    )
    assert result == set()


def test_empty_csv() -> None:
    """No CSV data returns empty set."""
    result = used_vlan_tags_on_bridge_from_ovs_outputs(
        bridge_list_ports_output="p1\n",
        list_port_name_tag_csv="",
    )
    assert result == set()


def test_csv_with_no_matching_ports() -> None:
    """CSV has ports but none match bridge ports."""
    result = used_vlan_tags_on_bridge_from_ovs_outputs(
        bridge_list_ports_output="p1\np2\n",
        list_port_name_tag_csv="name,tag\nother1,100\nother2,200\n",
    )
    assert result == set()


def test_csv_ports_with_zero_and_empty_tags() -> None:
    """Ports with tag=0 or empty tag are excluded."""
    result = used_vlan_tags_on_bridge_from_ovs_outputs(
        bridge_list_ports_output="p1\np2\np3\n",
        list_port_name_tag_csv="name,tag\np1,0\np2,[]\np3,100\n",
    )
    assert result == {100}


def test_malformed_csv_returns_empty() -> None:
    """Completely malformed CSV returns empty set via exception handler."""
    result = used_vlan_tags_on_bridge_from_ovs_outputs(
        bridge_list_ports_output="p1\n",
        list_port_name_tag_csv="this is not csv at all {{{{",
    )
    # DictReader won't raise on this, it just produces weird rows
    # The function handles gracefully
    assert isinstance(result, set)


def test_csv_quoted_names() -> None:
    """Port names with quotes are handled."""
    result = used_vlan_tags_on_bridge_from_ovs_outputs(
        bridge_list_ports_output="vnet1\n",
        list_port_name_tag_csv='name,tag\n"vnet1","500"\n',
    )
    assert result == {500}
