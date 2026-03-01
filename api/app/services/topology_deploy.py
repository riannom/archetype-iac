"""Deploy topology generation - pure data transforms, no DB dependency.

Converts TopologyGraph objects into the JSON format expected by the
agent's DeployTopology schema. Used for partial deploys where a
filtered graph needs to be deployed without a full database round-trip.
"""
from __future__ import annotations

import re

from app.schemas import TopologyGraph
from app.services.interface_naming import normalize_interface
from app.services.topology_resolution import (
    resolve_device_kind,
    resolve_effective_max_ports,
    resolve_node_image,
)


def graph_to_deploy_topology(graph: TopologyGraph) -> dict:
    """Convert a TopologyGraph to deploy topology JSON format.

    This function converts the internal graph representation to the JSON
    format expected by the agent's DeployTopology schema. Used for partial
    deploys in run_node_reconcile where a filtered graph needs to be deployed.

    NOTE: This function resolves images using the same 3-step logic as
    build_deploy_topology(): node.image -> manifest -> vendor default.

    Args:
        graph: TopologyGraph to convert

    Returns:
        Dict with 'nodes' and 'links' lists suitable for DeployTopology schema
    """
    # Calculate max interface index per node from graph links
    max_if_index: dict[str, int] = {}
    for link in graph.links:
        if len(link.endpoints) != 2:
            continue
        for ep in link.endpoints:
            node_key = ep.node
            if ep.ifname:
                iface = normalize_interface(ep.ifname)
                match = re.search(r"(\d+)$", iface)
                if match:
                    max_if_index[node_key] = max(
                        max_if_index.get(node_key, 0),
                        int(match.group(1)),
                    )

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
            # Optional override for max interface index
            if_override = n.vars.get("interface_count")
        else:
            if_override = None

        # Resolve hardware specs using the same priority chain as per-node lifecycle:
        # per-node override > device override/custom device > vendor defaults.
        # This keeps topology deploy path behavior consistent with create-node path.
        from app.services.device_service import get_device_service
        per_node_hw = {
            "memory": n.memory,
            "cpu": n.cpu,
            "cpu_limit": getattr(n, "cpu_limit", None),
            "disk_driver": n.disk_driver,
            "nic_driver": n.nic_driver,
            "machine_type": n.machine_type,
            "libvirt_driver": n.libvirt_driver,
            "efi_boot": n.efi_boot,
            "efi_vars": n.efi_vars,
        }
        # Drop unset keys so resolver can fall back correctly.
        per_node_hw = {k: v for k, v in per_node_hw.items() if v is not None}

        # Resolve image using canonical 3-step fallback
        kind = resolve_device_kind(n.device)
        image = resolve_node_image(n.device, kind, n.image, n.version)

        if not image:
            raise ValueError(
                f"No image found for node '{n.name}' (device={n.device}, kind={kind}). "
                f"Please upload an image or specify one explicitly."
            )

        node_name = n.container_name or n.name
        interface_count = None
        if isinstance(if_override, int) and if_override > 0:
            interface_count = if_override
        else:
            # Use UI-configured maxPorts (vendor defaults/overrides), but ensure
            # we pre-provision enough interfaces for any referenced links.
            device_ports = resolve_effective_max_ports(n.device, kind, image, n.version)
            max_index = max_if_index.get(n.id) or max_if_index.get(node_name) or 0
            interface_count = max(device_ports, max_index)
            if interface_count == 0:
                interface_count = None

        node_dict = {
            "name": n.container_name or n.name,
            "display_name": n.name,
            "kind": kind,
            "image": image,
            "binds": binds,
            "env": env,
            "ports": ports,
            "startup_config": startup_config,
            "exec_cmds": exec_cmds,
        }
        hw_specs = get_device_service().resolve_hardware_specs(
            n.device or kind,
            per_node_hw or None,
            image,
            version=n.version,
        )
        for key in ("memory", "cpu", "cpu_limit", "disk_driver", "nic_driver", "machine_type", "libvirt_driver", "efi_boot", "efi_vars"):
            if hw_specs.get(key) is not None:
                node_dict[key] = hw_specs[key]
        if hw_specs.get("readiness_probe"):
            node_dict["readiness_probe"] = hw_specs.get("readiness_probe")
        if hw_specs.get("readiness_pattern"):
            node_dict["readiness_pattern"] = hw_specs.get("readiness_pattern")
        if hw_specs.get("readiness_timeout"):
            node_dict["readiness_timeout"] = hw_specs.get("readiness_timeout")
        if interface_count:
            node_dict["interface_count"] = interface_count
        nodes.append(node_dict)

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
        source_iface = normalize_interface(ep1.ifname) if ep1.ifname else ""
        target_iface = normalize_interface(ep2.ifname) if ep2.ifname else ""
        links.append({
            "source_node": source,
            "source_interface": source_iface,
            "target_node": target,
            "target_interface": target_iface,
        })

    return {"nodes": nodes, "links": links}
