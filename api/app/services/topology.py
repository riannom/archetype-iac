"""TopologyService - centralized topology management.

This service encapsulates all topology operations, making the database
the authoritative source for topology definitions. YAML is generated
on-demand for exports and agent communication.

Key responsibilities:
- Import: Parse YAML/graph and store in database
- Export: Generate YAML/graph from database
- Queries: Get nodes, links, placements from database
- Analysis: Detect multi-host topologies, cross-host links
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_

from agent.vendors import get_kind_for_device, get_default_image
from app.image_store import find_image_reference
from sqlalchemy.orm import Session

from app import models
from app.schemas import (
    CrossHostLink,
    GraphEndpoint,
    GraphLink,
    GraphNode,
    TopologyGraph,
)
from app.topology import _denormalize_interface_name, _normalize_interface_name
from app.utils.link import generate_link_name

logger = logging.getLogger(__name__)


def resolve_node_image(
    device: str | None,
    kind: str,
    explicit_image: str | None = None,
    version: str | None = None,
) -> str | None:
    """Resolve the Docker image for a node using 3-step fallback.

    This is the canonical image resolution logic used throughout the codebase.
    Priority:
    1. Explicit image if specified (node.image)
    2. Image from manifest via find_image_reference() (uploaded images)
    3. Vendor default via get_default_image()

    Args:
        device: Device type (e.g., "ceos", "nokia_srlinux") for manifest lookup
        kind: Resolved kind (e.g., "ceos") for vendor default lookup
        explicit_image: Explicitly specified image (highest priority)
        version: Optional version for manifest lookup

    Returns:
        Resolved image reference or None if no image found
    """
    if explicit_image:
        return explicit_image

    # Try to find uploaded image for this device type and version
    image = find_image_reference(device or kind, version)
    if image:
        return image

    # Fall back to vendor default image
    return get_default_image(kind)


@dataclass
class NodePlacementInfo:
    """Placement of a node on a specific host."""
    node_name: str
    host_id: str
    node_id: str | None = None  # DB Node.id


@dataclass
class TopologyAnalysisResult:
    """Analysis of a topology for multi-host deployment."""
    placements: dict[str, list[NodePlacementInfo]]  # host_id -> nodes
    cross_host_links: list[CrossHostLink]
    single_host: bool


def graph_to_deploy_topology(graph: TopologyGraph) -> dict:
    """Convert a TopologyGraph to deploy topology JSON format.

    This function converts the internal graph representation to the JSON
    format expected by the agent's DeployTopology schema. Used for partial
    deploys in run_node_reconcile where a filtered graph needs to be deployed.

    NOTE: This function resolves images using the same 3-step logic as
    build_deploy_topology(): node.image → manifest → vendor default.

    Args:
        graph: TopologyGraph to convert

    Returns:
        Dict with 'nodes' and 'links' lists suitable for DeployTopology schema
    """
    nodes = []
    for n in graph.nodes:
        # Skip external network nodes - they're not containers
        if n.node_type == "external":
            continue

        # Extract env, binds, etc. from vars if present
        env = {}
        binds = []
        ports = []
        exec_cmds = []
        startup_config = None

        if n.vars:
            env = n.vars.get("env", {})
            binds = n.vars.get("binds", [])
            ports = n.vars.get("ports", [])
            exec_cmds = n.vars.get("exec", [])
            startup_config = n.vars.get("startup-config")

        # Resolve image using canonical 3-step fallback
        kind = get_kind_for_device(n.device) if n.device else "linux"
        image = resolve_node_image(n.device, kind, n.image, n.version)

        if not image:
            raise ValueError(
                f"No image found for node '{n.name}' (device={n.device}, kind={kind}). "
                f"Please upload an image or specify one explicitly."
            )

        nodes.append({
            "name": n.container_name or n.name,
            "display_name": n.name,
            "kind": kind,
            "image": image,
            "binds": binds,
            "env": env,
            "ports": ports,
            "startup_config": startup_config,
            "exec_cmds": exec_cmds,
        })

    # Build node ID to container_name mapping for link resolution
    node_id_to_name = {n.id: (n.container_name or n.name) for n in graph.nodes}

    links = []
    for link in graph.links:
        if len(link.endpoints) != 2:
            continue
        ep1, ep2 = link.endpoints
        # Resolve node references - could be GUI ID or container name
        source = node_id_to_name.get(ep1.node, ep1.node)
        target = node_id_to_name.get(ep2.node, ep2.node)
        # Normalize interface names (e.g., Ethernet1 -> eth1)
        source_iface = _normalize_interface_name(ep1.ifname) if ep1.ifname else ""
        target_iface = _normalize_interface_name(ep2.ifname) if ep2.ifname else ""
        links.append({
            "source_node": source,
            "source_interface": source_iface,
            "target_node": target,
            "target_interface": target_iface,
        })

    return {"nodes": nodes, "links": links}


class TopologyService:
    """Service for topology operations.

    All topology queries go through this service. The database is the
    source of truth for topology structure.
    """

    def __init__(self, db: Session):
        self.db = db

    # =========================================================================
    # Query Methods
    # =========================================================================

    def get_nodes(self, lab_id: str) -> list[models.Node]:
        """Get all nodes for a lab."""
        return (
            self.db.query(models.Node)
            .filter(models.Node.lab_id == lab_id)
            .order_by(models.Node.container_name)
            .all()
        )

    def get_links(self, lab_id: str) -> list[models.Link]:
        """Get all links for a lab."""
        return (
            self.db.query(models.Link)
            .filter(models.Link.lab_id == lab_id)
            .order_by(models.Link.link_name)
            .all()
        )

    def get_node_by_container_name(self, lab_id: str, name: str) -> models.Node | None:
        """Get a node by its container name (YAML key)."""
        return (
            self.db.query(models.Node)
            .filter(
                models.Node.lab_id == lab_id,
                models.Node.container_name == name,
            )
            .first()
        )

    def get_node_by_gui_id(self, lab_id: str, gui_id: str) -> models.Node | None:
        """Get a node by its GUI ID (frontend ID)."""
        return (
            self.db.query(models.Node)
            .filter(
                models.Node.lab_id == lab_id,
                models.Node.gui_id == gui_id,
            )
            .first()
        )

    def get_node_by_any_id(self, lab_id: str, identifier: str) -> models.Node | None:
        """Get a node by container_name or gui_id."""
        return (
            self.db.query(models.Node)
            .filter(
                models.Node.lab_id == lab_id,
                or_(
                    models.Node.container_name == identifier,
                    models.Node.gui_id == identifier,
                )
            )
            .first()
        )

    def get_node_host(self, lab_id: str, node_identifier: str) -> models.Host | None:
        """Get the host for a node.

        First checks Node.host_id (explicit placement in topology),
        then falls back to NodePlacement (runtime placement).
        """
        node = self.get_node_by_any_id(lab_id, node_identifier)
        if node and node.host_id:
            return self.db.get(models.Host, node.host_id)

        # Fall back to NodePlacement for backwards compatibility
        # and for nodes without explicit topology placement
        placement = (
            self.db.query(models.NodePlacement)
            .filter(
                models.NodePlacement.lab_id == lab_id,
                models.NodePlacement.node_name == (node.container_name if node else node_identifier),
            )
            .first()
        )
        if placement:
            return self.db.get(models.Host, placement.host_id)

        return None

    def has_nodes(self, lab_id: str) -> bool:
        """Check if a lab has any nodes in the database."""
        return (
            self.db.query(models.Node.id)
            .filter(models.Node.lab_id == lab_id)
            .first()
        ) is not None

    def get_required_images(self, lab_id: str) -> list[str]:
        """Get unique Docker images required for a lab's topology.

        Uses resolve_node_image() for canonical 3-step resolution:
        1. Explicit node.image if set
        2. Image from manifest via find_image_reference()
        3. Vendor default via get_default_image()

        Args:
            lab_id: Lab ID to get images for

        Returns:
            List of unique image references
        """
        nodes = self.get_nodes(lab_id)
        images: set[str] = set()

        for node in nodes:
            kind = get_kind_for_device(node.device) if node.device else "linux"
            image = resolve_node_image(node.device, kind, node.image, node.version)
            if image:
                images.add(image)

        return list(images)

    def get_image_to_nodes_map(self, lab_id: str) -> dict[str, list[str]]:
        """Get mapping from image references to node names.

        Uses resolve_node_image() for canonical 3-step resolution.

        Args:
            lab_id: Lab ID to get mapping for

        Returns:
            Dict mapping image references to list of node names using that image
        """
        nodes = self.get_nodes(lab_id)
        image_to_nodes: dict[str, list[str]] = {}

        for node in nodes:
            kind = get_kind_for_device(node.device) if node.device else "linux"
            image = resolve_node_image(node.device, kind, node.image, node.version)
            if image:
                if image not in image_to_nodes:
                    image_to_nodes[image] = []
                image_to_nodes[image].append(node.container_name)

        return image_to_nodes

    # =========================================================================
    # Analysis Methods
    # =========================================================================

    def analyze_placements(self, lab_id: str, default_host_id: str | None = None) -> TopologyAnalysisResult:
        """Analyze a topology for multi-host deployment.

        Detects which nodes should run on which hosts and identifies
        links that span multiple hosts (requiring overlay networking).

        Args:
            lab_id: The lab ID to analyze
            default_host_id: Default host ID for nodes without explicit placement

        Returns:
            TopologyAnalysisResult with placements and cross-host links
        """
        nodes = self.get_nodes(lab_id)
        links = self.get_links(lab_id)

        # Build node -> host mapping
        node_hosts: dict[str, str] = {}  # node_id -> host_id
        node_names: dict[str, str] = {}  # node_id -> container_name

        for node in nodes:
            node_names[node.id] = node.container_name
            if node.host_id:
                node_hosts[node.id] = node.host_id
            elif default_host_id:
                node_hosts[node.id] = default_host_id

        # If no placements specified, all on default host
        if not node_hosts and default_host_id:
            for node in nodes:
                node_hosts[node.id] = default_host_id

        # Group nodes by host
        placements: dict[str, list[NodePlacementInfo]] = {}
        for node in nodes:
            host_id = node_hosts.get(node.id)
            if host_id:
                if host_id not in placements:
                    placements[host_id] = []
                placements[host_id].append(NodePlacementInfo(
                    node_name=node.container_name,
                    host_id=host_id,
                    node_id=node.id,
                ))

        # Detect cross-host links
        cross_host_links: list[CrossHostLink] = []

        for link in links:
            host_a = node_hosts.get(link.source_node_id)
            host_b = node_hosts.get(link.target_node_id)

            # If both endpoints have hosts and they differ, it's a cross-host link
            if host_a and host_b and host_a != host_b:
                node_a = node_names.get(link.source_node_id, "")
                node_b = node_names.get(link.target_node_id, "")
                interface_a = link.source_interface
                interface_b = link.target_interface

                # Generate canonical link_id (sorted alphabetically) for consistency
                # This ensures the same link always gets the same ID regardless of
                # source/target ordering in the database
                ep_a = f"{node_a}:{interface_a}"
                ep_b = f"{node_b}:{interface_b}"
                if ep_a <= ep_b:
                    link_id = f"{ep_a}-{ep_b}"
                else:
                    link_id = f"{ep_b}-{ep_a}"
                    # Swap all assignments to match canonical order
                    node_a, node_b = node_b, node_a
                    interface_a, interface_b = interface_b, interface_a
                    host_a, host_b = host_b, host_a

                # Get IP addresses from link config if present
                ip_a = None
                ip_b = None
                if link.config_json:
                    try:
                        config = json.loads(link.config_json)
                        ip_a = config.get("ip_a")
                        ip_b = config.get("ip_b")
                        # Swap IPs if we swapped the endpoints
                        if ep_a > ep_b:
                            ip_a, ip_b = ip_b, ip_a
                    except json.JSONDecodeError:
                        pass

                cross_host_links.append(CrossHostLink(
                    link_id=link_id,
                    node_a=node_a,
                    interface_a=interface_a,
                    host_a=host_a,
                    ip_a=ip_a,
                    node_b=node_b,
                    interface_b=interface_b,
                    host_b=host_b,
                    ip_b=ip_b,
                ))

        # Determine if single-host or multi-host
        unique_hosts = set(node_hosts.values())
        single_host = len(unique_hosts) <= 1

        return TopologyAnalysisResult(
            placements=placements,
            cross_host_links=cross_host_links,
            single_host=single_host,
        )

    def get_cross_host_links(self, lab_id: str) -> list[CrossHostLink]:
        """Get links that span multiple hosts."""
        analysis = self.analyze_placements(lab_id)
        return analysis.cross_host_links

    def is_multihost(self, lab_id: str) -> bool:
        """Check if a lab has nodes on multiple hosts."""
        analysis = self.analyze_placements(lab_id)
        return not analysis.single_host

    # =========================================================================
    # Update Methods
    # =========================================================================

    def update_from_graph(self, lab_id: str, graph: TopologyGraph) -> tuple[int, int]:
        """Update topology from a graph structure in the database.

        Creates/updates Node and Link records from the graph.
        Existing nodes/links not in the graph are deleted.

        Args:
            lab_id: Lab ID to update
            graph: The topology graph to apply

        Returns:
            Tuple of (nodes_created, links_created)
        """
        from app.topology import _safe_node_name

        # Track existing records for deletion detection
        existing_nodes = {n.gui_id: n for n in self.get_nodes(lab_id)}
        existing_links = {l.link_name: l for l in self.get_links(lab_id)}

        # Track which records we've seen
        seen_node_gui_ids: set[str] = set()
        seen_link_names: set[str] = set()

        # Map GUI ID to DB Node.id for link creation
        gui_id_to_node_id: dict[str, str] = {}
        # Map GUI ID to container_name for link naming
        gui_id_to_container_name: dict[str, str] = {}

        nodes_created = 0
        used_names: set[str] = set()

        # First pass: create/update all nodes
        for graph_node in graph.nodes:
            seen_node_gui_ids.add(graph_node.id)

            # Determine container_name (YAML key)
            if graph_node.container_name:
                container_name = graph_node.container_name
                if container_name in used_names:
                    container_name = _safe_node_name(container_name, used_names)
            else:
                container_name = _safe_node_name(graph_node.name, used_names)
            used_names.add(container_name)

            # Build config_json for extra fields
            config: dict[str, Any] = {}
            if graph_node.role:
                config["role"] = graph_node.role
            if graph_node.mgmt:
                config["mgmt"] = graph_node.mgmt
            if graph_node.vars:
                config["vars"] = graph_node.vars
            config_json = json.dumps(config) if config else None

            # Resolve host name to host_id
            host_id = None
            if graph_node.host:
                host = (
                    self.db.query(models.Host)
                    .filter(
                        or_(
                            models.Host.name == graph_node.host,
                            models.Host.id == graph_node.host,
                        )
                    )
                    .first()
                )
                if host:
                    host_id = host.id
                    logger.debug(
                        f"Node '{graph_node.name}' assigned to host '{host.name}' "
                        f"(id={host.id})"
                    )
                else:
                    # Explicit host assignment must succeed - fail import if host not found
                    logger.error(
                        f"Node '{graph_node.name}' specifies host '{graph_node.host}' "
                        f"which does not exist or is not registered"
                    )
                    raise ValueError(
                        f"Node '{graph_node.name}' specifies host '{graph_node.host}' "
                        f"which does not exist or is not registered"
                    )

            if graph_node.id in existing_nodes:
                # Update existing node
                node = existing_nodes[graph_node.id]
                node.display_name = graph_node.name
                node.container_name = container_name
                node.node_type = graph_node.node_type or "device"
                node.device = graph_node.device
                node.image = graph_node.image
                node.version = graph_node.version
                node.network_mode = graph_node.network_mode
                node.host_id = host_id
                node.connection_type = graph_node.connection_type
                node.parent_interface = graph_node.parent_interface
                node.vlan_id = graph_node.vlan_id
                node.bridge_name = graph_node.bridge_name
                node.config_json = config_json
            else:
                # Create new node
                node = models.Node(
                    lab_id=lab_id,
                    gui_id=graph_node.id,
                    display_name=graph_node.name,
                    container_name=container_name,
                    node_type=graph_node.node_type or "device",
                    device=graph_node.device,
                    image=graph_node.image,
                    version=graph_node.version,
                    network_mode=graph_node.network_mode,
                    host_id=host_id,
                    connection_type=graph_node.connection_type,
                    parent_interface=graph_node.parent_interface,
                    vlan_id=graph_node.vlan_id,
                    bridge_name=graph_node.bridge_name,
                    config_json=config_json,
                )
                self.db.add(node)
                nodes_created += 1

            # Flush to get node.id
            self.db.flush()
            gui_id_to_node_id[graph_node.id] = node.id
            gui_id_to_container_name[graph_node.id] = container_name

        # Delete nodes not in the graph
        for gui_id, node in existing_nodes.items():
            if gui_id not in seen_node_gui_ids:
                self.db.delete(node)

        # Second pass: create/update links
        links_created = 0

        for graph_link in graph.links:
            if len(graph_link.endpoints) != 2:
                continue  # Skip non-point-to-point links

            ep_a, ep_b = graph_link.endpoints

            # Skip external endpoints for now (they don't create Link records)
            if ep_a.type != "node" or ep_b.type != "node":
                continue

            # Resolve node IDs
            source_node_id = gui_id_to_node_id.get(ep_a.node)
            target_node_id = gui_id_to_node_id.get(ep_b.node)

            if not source_node_id or not target_node_id:
                logger.warning(f"Skipping link with unknown node: {ep_a.node} -> {ep_b.node}")
                continue

            # Generate link name (alphabetically sorted for canonical naming)
            source_name = gui_id_to_container_name.get(ep_a.node, ep_a.node)
            target_name = gui_id_to_container_name.get(ep_b.node, ep_b.node)
            source_iface = ep_a.ifname or "eth0"
            target_iface = ep_b.ifname or "eth0"
            link_name = self._generate_link_name(
                source_name, source_iface, target_name, target_iface
            )
            seen_link_names.add(link_name)

            # Check if endpoints were swapped by generate_link_name (alphabetical sort)
            # If link_name starts with target endpoint, endpoints were swapped
            expected_start = f"{source_name}:{source_iface}"
            endpoints_swapped = not link_name.startswith(expected_start)

            if endpoints_swapped:
                # Swap node IDs and interfaces to match canonical link name
                source_node_id, target_node_id = target_node_id, source_node_id
                source_name, target_name = target_name, source_name
                source_iface, target_iface = target_iface, source_iface

            # Build config_json for extra link attributes
            link_config: dict[str, Any] = {}
            if graph_link.type:
                link_config["type"] = graph_link.type
            if graph_link.name:
                link_config["name"] = graph_link.name
            if graph_link.pool:
                link_config["pool"] = graph_link.pool
            if graph_link.prefix:
                link_config["prefix"] = graph_link.prefix
            if graph_link.bridge:
                link_config["bridge"] = graph_link.bridge
            # Store IPs matching the canonical endpoint order
            if endpoints_swapped:
                if ep_b.ipv4:
                    link_config["ip_a"] = ep_b.ipv4
                if ep_a.ipv4:
                    link_config["ip_b"] = ep_a.ipv4
            else:
                if ep_a.ipv4:
                    link_config["ip_a"] = ep_a.ipv4
                if ep_b.ipv4:
                    link_config["ip_b"] = ep_b.ipv4
            link_config_json = json.dumps(link_config) if link_config else None

            if link_name in existing_links:
                # Update existing link
                link = existing_links[link_name]
                link.source_node_id = source_node_id
                link.source_interface = source_iface
                link.target_node_id = target_node_id
                link.target_interface = target_iface
                link.mtu = graph_link.mtu
                link.bandwidth = graph_link.bandwidth
                link.config_json = link_config_json
            else:
                # Create new link
                link = models.Link(
                    lab_id=lab_id,
                    link_name=link_name,
                    source_node_id=source_node_id,
                    source_interface=source_iface,
                    target_node_id=target_node_id,
                    target_interface=target_iface,
                    mtu=graph_link.mtu,
                    bandwidth=graph_link.bandwidth,
                    config_json=link_config_json,
                )
                self.db.add(link)
                links_created += 1

        # Delete links not in the graph
        for link_name, link in existing_links.items():
            if link_name not in seen_link_names:
                self.db.delete(link)

        # Link NodeState records to Node definitions
        self._link_node_states(lab_id)

        # Link LinkState records to Link definitions
        self._link_link_states(lab_id)

        return nodes_created, links_created

    def update_from_yaml(self, lab_id: str, yaml_content: str) -> tuple[int, int]:
        """Update topology from YAML in the database.

        Args:
            lab_id: Lab ID to update
            yaml_content: YAML topology content

        Returns:
            Tuple of (nodes_created, links_created)
        """
        from app.topology import yaml_to_graph
        graph = yaml_to_graph(yaml_content)
        return self.update_from_graph(lab_id, graph)

    # =========================================================================
    # Export Methods
    # =========================================================================

    def export_to_graph(self, lab_id: str) -> TopologyGraph:
        """Export topology from database to graph structure.

        Args:
            lab_id: Lab ID to export

        Returns:
            TopologyGraph with nodes and links
        """
        nodes = self.get_nodes(lab_id)
        links = self.get_links(lab_id)

        # Build node ID map for link endpoint resolution
        node_id_to_gui_id: dict[str, str] = {n.id: n.gui_id for n in nodes}
        # Build node ID to device type map for interface name denormalization
        node_id_to_device: dict[str, str | None] = {n.id: n.device for n in nodes}

        graph_nodes: list[GraphNode] = []
        for node in nodes:
            # Parse config_json
            config: dict[str, Any] = {}
            if node.config_json:
                try:
                    config = json.loads(node.config_json)
                except json.JSONDecodeError:
                    pass

            graph_nodes.append(GraphNode(
                id=node.gui_id,
                name=node.display_name,
                container_name=node.container_name,
                node_type=node.node_type,
                device=node.device,
                image=node.image,
                version=node.version,
                network_mode=node.network_mode,
                host=node.host_id,  # Export host_id directly (frontend uses agent ID)
                connection_type=node.connection_type,
                parent_interface=node.parent_interface,
                vlan_id=node.vlan_id,
                bridge_name=node.bridge_name,
                role=config.get("role"),
                mgmt=config.get("mgmt"),
                vars=config.get("vars"),
            ))

        graph_links: list[GraphLink] = []
        for link in links:
            source_gui_id = node_id_to_gui_id.get(link.source_node_id, link.source_node_id)
            target_gui_id = node_id_to_gui_id.get(link.target_node_id, link.target_node_id)

            # Get device types for interface name denormalization
            source_device = node_id_to_device.get(link.source_node_id)
            target_device = node_id_to_device.get(link.target_node_id)

            # Denormalize interface names to vendor-specific format for UI display
            source_iface = _denormalize_interface_name(link.source_interface, source_device)
            target_iface = _denormalize_interface_name(link.target_interface, target_device)

            # Parse link config_json
            link_config: dict[str, Any] = {}
            if link.config_json:
                try:
                    link_config = json.loads(link.config_json)
                except json.JSONDecodeError:
                    pass

            graph_links.append(GraphLink(
                endpoints=[
                    GraphEndpoint(
                        node=source_gui_id,
                        ifname=source_iface,
                        ipv4=link_config.get("ip_a"),
                    ),
                    GraphEndpoint(
                        node=target_gui_id,
                        ifname=target_iface,
                        ipv4=link_config.get("ip_b"),
                    ),
                ],
                type=link_config.get("type"),
                name=link_config.get("name"),
                pool=link_config.get("pool"),
                prefix=link_config.get("prefix"),
                bridge=link_config.get("bridge"),
                mtu=link.mtu,
                bandwidth=link.bandwidth,
            ))

        return TopologyGraph(nodes=graph_nodes, links=graph_links)

    def export_to_yaml(self, lab_id: str) -> str:
        """Export topology from database to YAML format.

        Args:
            lab_id: Lab ID to export

        Returns:
            YAML string
        """
        from app.topology import graph_to_yaml
        graph = self.export_to_graph(lab_id)
        return graph_to_yaml(graph)

    def to_topology_yaml(
        self,
        lab_id: str,
        reserved_interfaces: set[tuple[str, str]] | None = None,
    ) -> str:
        """Generate deployment YAML from database topology.

        Args:
            lab_id: Lab ID to generate for
            reserved_interfaces: Optional set of (node_name, interface_name) tuples
                that should be treated as used (for cross-host links)

        Returns:
            Deployment YAML string
        """
        from app.topology import graph_to_topology_yaml
        graph = self.export_to_graph(lab_id)
        return graph_to_topology_yaml(graph, lab_id, reserved_interfaces)

    # Backward compatibility alias
    to_containerlab_yaml = to_topology_yaml

    def to_topology_yaml_for_host(
        self,
        lab_id: str,
        host_id: str,
        reserved_interfaces: set[tuple[str, str]] | None = None,
    ) -> str:
        """Generate deployment YAML for nodes on a specific host.

        Used for multi-host deployments where each host gets a sub-topology.

        Args:
            lab_id: Lab ID to generate for
            host_id: Host ID to filter nodes by
            reserved_interfaces: Optional set of reserved interfaces

        Returns:
            Deployment YAML string for nodes on this host
        """
        from app.topology import graph_to_topology_yaml

        # Get full topology graph
        full_graph = self.export_to_graph(lab_id)

        # Get nodes on this host
        nodes = self.get_nodes(lab_id)
        host_node_gui_ids = {n.gui_id for n in nodes if n.host_id == host_id}

        # Filter nodes
        filtered_nodes = [n for n in full_graph.nodes if n.id in host_node_gui_ids]

        # Filter links to only include those where both endpoints are on this host
        filtered_links = [
            l for l in full_graph.links
            if all(ep.node in host_node_gui_ids for ep in l.endpoints)
        ]

        filtered_graph = TopologyGraph(
            nodes=filtered_nodes,
            links=filtered_links,
            defaults=full_graph.defaults,
        )

        return graph_to_topology_yaml(filtered_graph, lab_id, reserved_interfaces)

    # Backward compatibility alias
    to_containerlab_yaml_for_host = to_topology_yaml_for_host

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _generate_link_name(
        self,
        source_node: str,
        source_interface: str,
        target_node: str,
        target_interface: str,
    ) -> str:
        """Generate a canonical link name from endpoints.

        Link names are sorted alphabetically to ensure the same link always gets
        the same name regardless of endpoint order.
        """
        return generate_link_name(source_node, source_interface, target_node, target_interface)

    def _link_node_states(self, lab_id: str) -> None:
        """Link NodeState records to their Node definitions."""
        nodes = self.get_nodes(lab_id)
        node_by_name = {n.container_name: n for n in nodes}
        node_by_gui_id = {n.gui_id: n for n in nodes}

        node_states = (
            self.db.query(models.NodeState)
            .filter(models.NodeState.lab_id == lab_id)
            .all()
        )

        for ns in node_states:
            # Try to find matching node by node_name (container_name) or node_id (gui_id)
            node = node_by_name.get(ns.node_name) or node_by_gui_id.get(ns.node_id)
            if node:
                ns.node_definition_id = node.id

    def _link_link_states(self, lab_id: str) -> None:
        """Link LinkState records to their Link definitions."""
        links = self.get_links(lab_id)
        link_by_name = {l.link_name: l for l in links}

        link_states = (
            self.db.query(models.LinkState)
            .filter(models.LinkState.lab_id == lab_id)
            .all()
        )

        for ls in link_states:
            link = link_by_name.get(ls.link_name)
            if link:
                ls.link_definition_id = link.id

    # =========================================================================
    # Migration Helper
    # =========================================================================

    def migrate_from_yaml_file(self, lab_id: str, yaml_content: str) -> tuple[int, int]:
        """Migrate an existing lab's topology from YAML to database.

        This is a one-time operation for existing labs. After migration,
        the database becomes the source of truth.

        Args:
            lab_id: Lab ID to migrate
            yaml_content: Current YAML content

        Returns:
            Tuple of (nodes_created, links_created)
        """
        return self.update_from_yaml(lab_id, yaml_content)

    # =========================================================================
    # Deploy Topology Generation
    # =========================================================================

    def build_deploy_topology(self, lab_id: str, host_id: str) -> dict:
        """Build deploy topology JSON for nodes on a specific host.

        This is the key method that uses database `nodes.host_id` as the
        authoritative source for host assignments, replacing the previous
        YAML-based `host:` field approach.

        Args:
            lab_id: Lab ID to build topology for
            host_id: Host ID to filter nodes by

        Returns:
            Dict with 'nodes' and 'links' lists suitable for DeployTopology schema
        """
        nodes = self.get_nodes(lab_id)
        links = self.get_links(lab_id)

        # Filter nodes for this host
        host_nodes = [n for n in nodes if n.host_id == host_id]
        host_node_ids = {n.id for n in host_nodes}

        # Filter links where BOTH endpoints are on this host (local links)
        # Cross-host links are handled separately via VXLAN overlay
        host_links = [
            l for l in links
            if l.source_node_id in host_node_ids and l.target_node_id in host_node_ids
        ]

        # Build node ID to container_name mapping for link endpoint resolution
        node_id_to_name = {n.id: n.container_name for n in host_nodes}

        return {
            "nodes": [self._node_to_deploy_dict(n) for n in host_nodes],
            "links": [self._link_to_deploy_dict(l, node_id_to_name) for l in host_links],
        }

    def _node_to_deploy_dict(self, node: models.Node) -> dict:
        """Convert a Node model to deploy dict format.

        Args:
            node: Node database model

        Returns:
            Dict matching DeployNode schema
        """
        # Parse config_json for additional fields
        config: dict[str, Any] = {}
        if node.config_json:
            try:
                config = json.loads(node.config_json)
            except json.JSONDecodeError:
                pass

        # Extract environment and binds from config or defaults
        env = config.get("env", {})
        binds = config.get("binds", [])
        ports = config.get("ports", [])
        exec_cmds = config.get("exec", [])
        startup_config = config.get("startup-config")

        # Resolve image using canonical 3-step fallback
        kind = get_kind_for_device(node.device) if node.device else "linux"
        image = resolve_node_image(node.device, kind, node.image, node.version)

        if not image:
            raise ValueError(
                f"No image found for node '{node.display_name}' (device={node.device}, kind={kind}). "
                f"Please upload an image or specify one explicitly."
            )

        return {
            "name": node.container_name,
            "display_name": node.display_name,
            "kind": kind,
            "image": image,
            "binds": binds,
            "env": env,
            "ports": ports,
            "startup_config": startup_config,
            "exec_cmds": exec_cmds,
        }

    def _link_to_deploy_dict(
        self,
        link: models.Link,
        node_id_to_name: dict[str, str],
    ) -> dict:
        """Convert a Link model to deploy dict format.

        Args:
            link: Link database model
            node_id_to_name: Mapping of node IDs to container names

        Returns:
            Dict matching DeployLink schema
        """
        # Normalize interface names (e.g., Ethernet1 -> eth1)
        source_iface = _normalize_interface_name(link.source_interface) if link.source_interface else ""
        target_iface = _normalize_interface_name(link.target_interface) if link.target_interface else ""
        return {
            "source_node": node_id_to_name.get(link.source_node_id, ""),
            "source_interface": source_iface,
            "target_node": node_id_to_name.get(link.target_node_id, ""),
            "target_interface": target_iface,
        }

    def get_reserved_interfaces_for_host(
        self,
        lab_id: str,
        host_id: str,
    ) -> set[tuple[str, str]]:
        """Get interfaces reserved for cross-host links on a specific host.

        Cross-host links require VXLAN overlay setup, so their interfaces
        should not be created by the local deploy. This returns the set
        of (node_name, interface_name) tuples that should be excluded
        from local link creation.

        Args:
            lab_id: Lab ID
            host_id: Host ID to get reserved interfaces for

        Returns:
            Set of (node_name, interface_name) tuples for cross-host link endpoints
        """
        nodes = self.get_nodes(lab_id)
        links = self.get_links(lab_id)

        # Build lookup maps
        node_by_id = {n.id: n for n in nodes}
        host_node_ids = {n.id for n in nodes if n.host_id == host_id}

        reserved: set[tuple[str, str]] = set()

        for link in links:
            source_on_host = link.source_node_id in host_node_ids
            target_on_host = link.target_node_id in host_node_ids

            # Cross-host link: one endpoint on this host, one on another
            if source_on_host != target_on_host:
                if source_on_host:
                    source_node = node_by_id.get(link.source_node_id)
                    if source_node:
                        reserved.add((source_node.container_name, link.source_interface))
                if target_on_host:
                    target_node = node_by_id.get(link.target_node_id)
                    if target_node:
                        reserved.add((target_node.container_name, link.target_interface))

        return reserved
