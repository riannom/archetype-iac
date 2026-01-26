from __future__ import annotations

from typing import Any
import hashlib
import re

import yaml

from app.schemas import (
    CrossHostLink,
    GraphEndpoint,
    GraphLink,
    GraphNode,
    NodePlacement,
    TopologyAnalysis,
    TopologyGraph,
)
from app.image_store import find_image_reference

LINK_ATTRS = {
    "bandwidth",
    "bridge",
    "disable",
    "gateway",
    "group",
    "mtu",
    "name",
    "pool",
    "prefix",
    "ra",
    "role",
    "shutdown",
    "type",
}

_NODE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,15}$")


def _safe_node_name(name: str, used: set[str]) -> str:
    if _NODE_NAME_RE.match(name) and name not in used:
        return name
    clean = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_")
    if not clean or not re.match(r"^[A-Za-z_]", clean):
        clean = f"n_{clean}" if clean else "n"
    for attempt in range(100):
        suffix = hashlib.md5(f"{name}-{attempt}".encode()).hexdigest()[:4]
        base_max = 16 - len(suffix) - 1
        base = clean[: max(base_max, 1)]
        candidate = f"{base}_{suffix}"
        if candidate not in used:
            return candidate
    return f"n_{hashlib.md5(name.encode()).hexdigest()[:13]}"


def _format_external_endpoint(endpoint: GraphEndpoint) -> str:
    """Format an external endpoint for containerlab YAML.

    External endpoints use format: type:name (e.g., bridge:br-ext, macvlan:eth0)
    """
    if endpoint.type == "node":
        return endpoint.node
    return f"{endpoint.type}:{endpoint.node}"


def graph_to_yaml(graph: TopologyGraph) -> str:
    nodes: dict[str, Any] = {}
    name_map: dict[str, str] = {}
    used_names: set[str] = set()
    for node in graph.nodes:
        safe_name = _safe_node_name(node.name, used_names)
        name_map[node.name] = safe_name
        used_names.add(safe_name)
        node_data: dict[str, Any] = {}
        if node.device:
            node_data["device"] = node.device
        if node.image:
            node_data["image"] = node.image
        if node.version:
            node_data["version"] = node.version
        if node.role:
            node_data["role"] = node.role
        if node.mgmt:
            node_data["mgmt"] = node.mgmt
        if node.network_mode:
            node_data["network-mode"] = node.network_mode
        if node.vars:
            vars_copy = dict(node.vars)
            if "label" in vars_copy and "name" not in vars_copy:
                vars_copy["name"] = vars_copy.pop("label")
            node_data.update(vars_copy)
        nodes[safe_name] = node_data or None

    links = []
    for link in graph.links:
        link_data: dict[str, Any] = {}
        if link.type:
            link_data["type"] = link.type
        if link.name:
            link_data["name"] = link.name
        if link.pool:
            link_data["pool"] = link.pool
        if link.prefix:
            link_data["prefix"] = link.prefix
        if link.bridge:
            link_data["bridge"] = link.bridge
        if link.mtu is not None:
            link_data["mtu"] = link.mtu
        if link.bandwidth is not None:
            link_data["bandwidth"] = link.bandwidth

        for endpoint in link.endpoints:
            # Handle external endpoints (bridge, macvlan, host)
            if endpoint.type != "node":
                endpoint_key = _format_external_endpoint(endpoint)
                if endpoint.ifname:
                    link_data[endpoint_key] = {"ifname": endpoint.ifname}
                else:
                    link_data[endpoint_key] = {}
            else:
                endpoint_name = name_map.get(endpoint.node, endpoint.node)
                if endpoint.ifname:
                    link_data[endpoint_name] = {"ifname": endpoint.ifname}
                else:
                    link_data[endpoint_name] = {}

        links.append(link_data)

    topology: dict[str, Any] = {}
    if graph.defaults:
        topology["defaults"] = graph.defaults
    topology["nodes"] = nodes
    topology["links"] = links

    return yaml.safe_dump(topology, sort_keys=False)


EXTERNAL_ENDPOINT_TYPES = {"bridge", "macvlan", "host"}


def _parse_endpoint_key(key: str) -> tuple[str, str]:
    """Parse an endpoint key that may be an external endpoint.

    External endpoints use format: type:name (e.g., bridge:br-ext, macvlan:eth0)
    Returns (node_or_name, endpoint_type) where endpoint_type is "node" for regular nodes.
    """
    if ":" in key:
        parts = key.split(":", 1)
        if parts[0] in EXTERNAL_ENDPOINT_TYPES:
            return parts[1], parts[0]
    return key, "node"


def _parse_link_item(item: Any) -> GraphLink | None:
    if isinstance(item, str) and "-" in item:
        parts = item.split("-")
        if len(parts) == 2:
            return GraphLink(endpoints=[GraphEndpoint(node=parts[0]), GraphEndpoint(node=parts[1])])
        return None
    if isinstance(item, list):
        endpoints = [GraphEndpoint(node=str(node)) for node in item]
        return GraphLink(endpoints=endpoints)
    if isinstance(item, dict):
        endpoints: list[GraphEndpoint] = []
        attrs: dict[str, Any] = {}
        for key, value in item.items():
            if key in LINK_ATTRS:
                attrs[key] = value
                continue
            # Parse endpoint key for external connections
            node_name, ep_type = _parse_endpoint_key(key)
            if isinstance(value, dict):
                endpoints.append(GraphEndpoint(
                    node=node_name,
                    ifname=value.get("ifname"),
                    type=ep_type,
                    ipv4=value.get("ipv4"),
                    ipv6=value.get("ipv6"),
                ))
            else:
                endpoints.append(GraphEndpoint(node=node_name, type=ep_type))
        return GraphLink(endpoints=endpoints, **attrs)
    return None


def yaml_to_graph(content: str) -> TopologyGraph:
    data = yaml.safe_load(content) or {}

    defaults = data.get("defaults", {})
    nodes_data = data.get("nodes", {})
    links_data = data.get("links", [])

    # Fields that are parsed as explicit GraphNode attributes (not stored in vars)
    node_explicit_fields = {"device", "image", "version", "role", "mgmt", "network-mode", "host"}

    nodes: list[GraphNode] = []
    if isinstance(nodes_data, list):
        for name in nodes_data:
            nodes.append(GraphNode(id=str(name), name=str(name)))
    elif isinstance(nodes_data, dict):
        for name, attrs in nodes_data.items():
            attrs = attrs or {}
            nodes.append(
                GraphNode(
                    id=str(name),
                    name=str(name),
                    device=attrs.get("device"),
                    image=attrs.get("image"),
                    version=attrs.get("version"),
                    role=attrs.get("role"),
                    mgmt=attrs.get("mgmt"),
                    network_mode=attrs.get("network-mode"),
                    host=attrs.get("host"),
                    vars={k: v for k, v in attrs.items() if k not in node_explicit_fields},
                )
            )

    links: list[GraphLink] = []
    if isinstance(links_data, dict):
        for group_links in links_data.values():
            if isinstance(group_links, list):
                for item in group_links:
                    parsed = _parse_link_item(item)
                    if parsed:
                        links.append(parsed)
    elif isinstance(links_data, list):
        for item in links_data:
            parsed = _parse_link_item(item)
            if parsed:
                links.append(parsed)

    return TopologyGraph(nodes=nodes, links=links, defaults=defaults)


def analyze_topology(graph: TopologyGraph, default_host: str | None = None) -> TopologyAnalysis:
    """Analyze a topology for multi-host deployment.

    Detects which nodes should run on which hosts and identifies
    links that span multiple hosts (requiring overlay networking).

    Args:
        graph: The topology graph to analyze
        default_host: Default host for nodes without explicit placement

    Returns:
        TopologyAnalysis with placements and cross-host links
    """
    # Build node -> host mapping
    node_hosts: dict[str, str] = {}
    for node in graph.nodes:
        host = node.host or default_host
        if host:
            node_hosts[node.name] = host

    # If no placements specified, all on default host
    if not node_hosts and default_host:
        for node in graph.nodes:
            node_hosts[node.name] = default_host

    # Group nodes by host
    placements: dict[str, list[NodePlacement]] = {}
    for node in graph.nodes:
        host = node_hosts.get(node.name)
        if host:
            if host not in placements:
                placements[host] = []
            placements[host].append(NodePlacement(node_name=node.name, host_id=host))

    # Detect cross-host links
    cross_host_links: list[CrossHostLink] = []
    link_counter = 0

    for link in graph.links:
        if len(link.endpoints) != 2:
            continue  # Skip non-point-to-point links

        ep_a, ep_b = link.endpoints
        host_a = node_hosts.get(ep_a.node)
        host_b = node_hosts.get(ep_b.node)

        # If both endpoints have hosts and they differ, it's a cross-host link
        if host_a and host_b and host_a != host_b:
            link_id = f"{ep_a.node}:{ep_a.ifname or 'eth0'}-{ep_b.node}:{ep_b.ifname or 'eth0'}"
            cross_host_links.append(
                CrossHostLink(
                    link_id=link_id,
                    node_a=ep_a.node,
                    interface_a=ep_a.ifname or f"eth{link_counter}",
                    host_a=host_a,
                    ip_a=ep_a.ipv4,
                    node_b=ep_b.node,
                    interface_b=ep_b.ifname or f"eth{link_counter}",
                    host_b=host_b,
                    ip_b=ep_b.ipv4,
                )
            )
            link_counter += 1

    # Determine if single-host or multi-host
    unique_hosts = set(node_hosts.values())
    single_host = len(unique_hosts) <= 1

    return TopologyAnalysis(
        placements=placements,
        cross_host_links=cross_host_links,
        single_host=single_host,
    )


# Mapping from internal device names to containerlab kinds
DEVICE_TO_CLAB_KIND = {
    "eos": "ceos",
    "arista_eos": "ceos",
    "ceos": "ceos",
    "arista_ceos": "ceos",
    "srlinux": "nokia_srlinux",
    "srl": "nokia_srlinux",
    "nokia_srlinux": "nokia_srlinux",
    "linux": "linux",
    "alpine": "linux",
    "frr": "linux",
    "cumulus": "cvx",
    "cvx": "cvx",
    "sonic": "sonic-vs",
    "crpd": "juniper_crpd",
    "vjunos": "juniper_vjunosswitch",
    "vqfx": "juniper_vqfx",
    "vsrx": "juniper_vsrx3",
    "iosxr": "cisco_iosxr",
    "xrd": "cisco_xrd",
    "nxos": "cisco_n9kv",
    "iosv": "linux",  # fallback for qemu-based
    "csr": "linux",  # fallback for qemu-based
}

# Default images for containerlab kinds (used when node doesn't specify an image)
CLAB_DEFAULT_IMAGES = {
    "ceos": "ceos:latest",  # User must import their own cEOS image
    "nokia_srlinux": "ghcr.io/nokia/srlinux:latest",
    "linux": "alpine:latest",
    "cvx": "networkop/cx:5.4.0",
    "sonic-vs": "docker-sonic-vs:latest",
    "juniper_crpd": "crpd:latest",
    "juniper_vjunosswitch": "vrnetlab/vr-vjunosswitch:latest",
    "juniper_vqfx": "vrnetlab/vr-vqfx:latest",
    "juniper_vsrx3": "vrnetlab/vr-vsrx3:latest",
    "cisco_iosxr": "ios-xr:latest",
    "cisco_xrd": "ios-xrd:latest",
    "cisco_n9kv": "vrnetlab/vr-n9kv:latest",
}


def graph_to_containerlab_yaml(graph: TopologyGraph, lab_id: str) -> str:
    """Convert topology graph to containerlab YAML format.

    Containerlab uses a different format than netlab:
    - 'kind' instead of 'device'
    - Links use 'endpoints' array format
    - Topology is nested under 'topology' key
    """
    import re

    # Create a safe lab name (max 20 chars, alphanumeric and dash)
    safe_lab_name = re.sub(r'[^a-zA-Z0-9-]', '', lab_id)[:20]
    if not safe_lab_name:
        safe_lab_name = "lab"

    nodes: dict[str, Any] = {}
    name_map: dict[str, str] = {}
    used_names: set[str] = set()

    for node in graph.nodes:
        safe_name = _safe_node_name(node.name, used_names)
        name_map[node.name] = safe_name
        used_names.add(safe_name)

        node_data: dict[str, Any] = {}

        # Map device to containerlab kind
        kind = "linux"  # default
        if node.device:
            device_lower = node.device.lower()
            kind = DEVICE_TO_CLAB_KIND.get(device_lower, device_lower)
        node_data["kind"] = kind

        # Use image if specified, otherwise look up from image library
        if node.image:
            node_data["image"] = node.image
        else:
            # Try to find uploaded image for this device type and version
            library_image = find_image_reference(node.device or kind, node.version)
            if library_image:
                node_data["image"] = library_image
            elif kind in CLAB_DEFAULT_IMAGES:
                node_data["image"] = CLAB_DEFAULT_IMAGES[kind]

        # Network mode
        if node.network_mode:
            node_data["network-mode"] = node.network_mode

        # Add any other vars that containerlab might use
        if node.vars:
            for k, v in node.vars.items():
                # Skip netlab-specific fields
                if k not in ("label", "name", "device", "version", "role", "mgmt"):
                    node_data[k] = v

        nodes[safe_name] = node_data if node_data else {}

    # Build links in containerlab format
    links = []
    interface_counters: dict[str, int] = {name: 1 for name in used_names}

    for link in graph.links:
        if len(link.endpoints) != 2:
            continue  # Skip non-p2p links for now

        ep_a, ep_b = link.endpoints

        # Handle external endpoints
        if ep_a.type != "node" or ep_b.type != "node":
            continue  # Skip external links for basic containerlab

        node_a = name_map.get(ep_a.node, ep_a.node)
        node_b = name_map.get(ep_b.node, ep_b.node)

        # Get or assign interface names
        if ep_a.ifname:
            iface_a = ep_a.ifname
        else:
            iface_a = f"eth{interface_counters.get(node_a, 1)}"
            interface_counters[node_a] = interface_counters.get(node_a, 1) + 1

        if ep_b.ifname:
            iface_b = ep_b.ifname
        else:
            iface_b = f"eth{interface_counters.get(node_b, 1)}"
            interface_counters[node_b] = interface_counters.get(node_b, 1) + 1

        links.append({
            "endpoints": [f"{node_a}:{iface_a}", f"{node_b}:{iface_b}"]
        })

    # Generate a unique subnet based on lab_id to avoid conflicts
    # Use hash of lab_id to generate 2nd and 3rd octets (avoiding common ranges)
    lab_hash = int(hashlib.md5(lab_id.encode()).hexdigest()[:4], 16)
    # Use 10.x.y.0/24 range (private, less likely to conflict)
    octet2 = (lab_hash >> 8) % 256
    octet3 = lab_hash % 256
    # Avoid 0 and common subnets
    if octet2 == 0:
        octet2 = 1
    if octet3 == 0:
        octet3 = 1

    # Build containerlab topology structure
    topology: dict[str, Any] = {
        "name": safe_lab_name,
        "mgmt": {
            "network": f"clab-{safe_lab_name}",
            "ipv4-subnet": f"10.{octet2}.{octet3}.0/24",
        },
        "topology": {
            "nodes": nodes,
        }
    }

    if links:
        topology["topology"]["links"] = links

    return yaml.safe_dump(topology, sort_keys=False)


def split_topology_by_host(
    graph: TopologyGraph,
    analysis: TopologyAnalysis,
) -> dict[str, TopologyGraph]:
    """Split a topology into per-host sub-topologies.

    For multi-host deployments, each host only needs the nodes
    that run on it. Cross-host links are excluded (handled by overlay).

    Args:
        graph: The original topology graph
        analysis: Topology analysis with placements

    Returns:
        Dict mapping host_id to sub-topology for that host
    """
    # Build node name -> host mapping
    node_to_host: dict[str, str] = {}
    for host_id, placements in analysis.placements.items():
        for p in placements:
            node_to_host[p.node_name] = host_id

    # Build set of cross-host link endpoints for exclusion
    cross_host_endpoints: set[tuple[str, str]] = set()
    for chl in analysis.cross_host_links:
        cross_host_endpoints.add((chl.node_a, chl.interface_a))
        cross_host_endpoints.add((chl.node_b, chl.interface_b))

    # Create sub-topology for each host
    result: dict[str, TopologyGraph] = {}

    for host_id in analysis.placements:
        # Nodes for this host
        host_node_names = {p.node_name for p in analysis.placements[host_id]}
        host_nodes = [n for n in graph.nodes if n.name in host_node_names]

        # Links where both endpoints are on this host
        host_links: list[GraphLink] = []
        for link in graph.links:
            if len(link.endpoints) != 2:
                # Include non-point-to-point links if all nodes on this host
                all_on_host = all(ep.node in host_node_names for ep in link.endpoints)
                if all_on_host:
                    host_links.append(link)
                continue

            ep_a, ep_b = link.endpoints

            # Skip if either node not on this host
            if ep_a.node not in host_node_names or ep_b.node not in host_node_names:
                continue

            # Skip cross-host links (they're handled by overlay)
            if (ep_a.node, ep_a.ifname or "eth0") in cross_host_endpoints:
                continue
            if (ep_b.node, ep_b.ifname or "eth0") in cross_host_endpoints:
                continue

            host_links.append(link)

        result[host_id] = TopologyGraph(
            nodes=host_nodes,
            links=host_links,
            defaults=graph.defaults,
        )

    return result
