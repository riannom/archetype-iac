from __future__ import annotations

import csv


def parse_list_ports_output(list_ports_output: str) -> set[str]:
    """Parse `ovs-vsctl list-ports <bridge>` output into a set of port names."""
    return {p.strip() for p in list_ports_output.splitlines() if p.strip()}


def _parse_tag_field(tag_raw: str) -> int | None:
    tag_raw = (tag_raw or "").strip().strip('"')
    if not tag_raw or tag_raw == "[]":
        return None

    # Some OVS versions may render as "[2002]" in list output.
    if tag_raw.startswith("[") and tag_raw.endswith("]"):
        inner = tag_raw[1:-1].strip()
        if not inner:
            return None
        tag_raw = inner

    # Defensive: tag is a scalar, skip unexpected composite values.
    if "," in tag_raw or " " in tag_raw:
        return None

    try:
        tag = int(tag_raw)
    except ValueError:
        return None

    return tag if tag > 0 else None


def used_vlan_tags_on_bridge_from_ovs_outputs(
    *,
    bridge_list_ports_output: str,
    list_port_name_tag_csv: str,
) -> set[int]:
    """Compute VLAN tags in-use on a bridge from OVS CLI outputs.

    Inputs are intended to be:
    - `ovs-vsctl list-ports <bridge>`
    - `ovs-vsctl --format=csv --columns=name,tag list port`
    """
    ports_on_bridge = parse_list_ports_output(bridge_list_ports_output)
    if not ports_on_bridge:
        return set()

    csv_text = (list_port_name_tag_csv or "").strip()
    if not csv_text:
        return set()

    used: set[int] = set()
    try:
        reader = csv.DictReader(csv_text.splitlines())
        for row in reader:
            name = (row.get("name") or "").strip().strip('"')
            if not name or name not in ports_on_bridge:
                continue
            tag = _parse_tag_field(row.get("tag") or "")
            if tag is not None:
                used.add(tag)
    except Exception:
        return set()

    return used

