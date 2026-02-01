"""Link-related utility functions."""
from __future__ import annotations


def generate_link_name(
    source_node: str,
    source_interface: str,
    target_node: str,
    target_interface: str,
) -> str:
    """Generate a canonical link name from endpoints.

    Link names are sorted alphabetically to ensure the same link always gets
    the same name regardless of endpoint order.

    Args:
        source_node: Source node name
        source_interface: Source interface name
        target_node: Target node name
        target_interface: Target interface name

    Returns:
        Canonical link name in format "nodeA:ifaceA-nodeB:ifaceB"
    """
    ep_a = f"{source_node}:{source_interface}"
    ep_b = f"{target_node}:{target_interface}"
    if ep_a <= ep_b:
        return f"{ep_a}-{ep_b}"
    return f"{ep_b}-{ep_a}"
