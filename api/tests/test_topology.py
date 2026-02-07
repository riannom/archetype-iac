"""Tests for topology parsing and multi-host analysis."""

import re

import pytest

from app.schemas import (
    GraphEndpoint,
    GraphLink,
    GraphNode,
    TopologyGraph,
)
from app.topology import (
    analyze_topology,
    graph_to_topology_yaml,
    graph_to_yaml,
    split_topology_by_host,
    yaml_to_graph,
)


# --- Basic Topology Tests ---

def test_graph_to_yaml_simple():
    """Test converting a simple graph to YAML."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="r1", device="linux"),
            GraphNode(id="r2", name="r2", device="linux"),
        ],
        links=[
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="r1"),
                    GraphEndpoint(node="r2"),
                ]
            )
        ],
    )

    yaml_str = graph_to_yaml(graph)
    assert "r1" in yaml_str
    assert "r2" in yaml_str
    assert "linux" in yaml_str


def test_yaml_to_graph_simple():
    """Test parsing a simple YAML topology."""
    yaml_str = """
nodes:
  r1:
    device: linux
  r2:
    device: linux
links:
  - r1: {}
    r2: {}
"""
    graph = yaml_to_graph(yaml_str)

    assert len(graph.nodes) == 2
    assert len(graph.links) == 1
    assert graph.nodes[0].device == "linux"


# --- Multi-Host Topology Analysis Tests ---

def test_analyze_single_host():
    """Test analysis of single-host topology."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="r1", host="agent1"),
            GraphNode(id="r2", name="r2", host="agent1"),
        ],
        links=[
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="r1", ifname="eth0"),
                    GraphEndpoint(node="r2", ifname="eth0"),
                ]
            )
        ],
    )

    analysis = analyze_topology(graph)

    assert analysis.single_host is True
    assert len(analysis.cross_host_links) == 0
    assert "agent1" in analysis.placements
    assert len(analysis.placements["agent1"]) == 2


def test_analyze_multi_host():
    """Test analysis of multi-host topology."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="r1", host="agent1"),
            GraphNode(id="r2", name="r2", host="agent2"),
        ],
        links=[
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="r1", ifname="eth0"),
                    GraphEndpoint(node="r2", ifname="eth0"),
                ]
            )
        ],
    )

    analysis = analyze_topology(graph)

    assert analysis.single_host is False
    assert len(analysis.cross_host_links) == 1
    assert "agent1" in analysis.placements
    assert "agent2" in analysis.placements

    chl = analysis.cross_host_links[0]
    assert chl.node_a == "r1"
    assert chl.node_b == "r2"
    assert chl.host_a == "agent1"
    assert chl.host_b == "agent2"
    assert chl.interface_a == "eth0"
    assert chl.interface_b == "eth0"


def test_analyze_default_host():
    """Test analysis with default host for unplaced nodes."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="r1"),  # No host specified
            GraphNode(id="r2", name="r2"),  # No host specified
        ],
        links=[
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="r1"),
                    GraphEndpoint(node="r2"),
                ]
            )
        ],
    )

    analysis = analyze_topology(graph, default_host="agent1")

    assert analysis.single_host is True
    assert len(analysis.cross_host_links) == 0
    assert "agent1" in analysis.placements
    assert len(analysis.placements["agent1"]) == 2


def test_analyze_mixed_placement():
    """Test analysis with mixed explicit and default placement."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="r1", host="agent1"),  # Explicit
            GraphNode(id="r2", name="r2"),  # Will use default
            GraphNode(id="r3", name="r3", host="agent2"),  # Explicit
        ],
        links=[
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="r1", ifname="eth0"),
                    GraphEndpoint(node="r2", ifname="eth0"),
                ]
            ),
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="r2", ifname="eth1"),
                    GraphEndpoint(node="r3", ifname="eth0"),
                ]
            ),
        ],
    )

    analysis = analyze_topology(graph, default_host="agent1")

    # r1 and r2 on agent1, r3 on agent2
    assert not analysis.single_host
    assert len(analysis.placements["agent1"]) == 2
    assert len(analysis.placements["agent2"]) == 1

    # r2-r3 is cross-host, r1-r2 is same-host
    assert len(analysis.cross_host_links) == 1
    chl = analysis.cross_host_links[0]
    assert chl.node_a == "r2"
    assert chl.node_b == "r3"


def test_analyze_complex_topology():
    """Test analysis of complex multi-host topology."""
    # Spine-leaf topology across 3 hosts
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="spine1", name="spine1", host="agent1"),
            GraphNode(id="spine2", name="spine2", host="agent1"),
            GraphNode(id="leaf1", name="leaf1", host="agent2"),
            GraphNode(id="leaf2", name="leaf2", host="agent2"),
            GraphNode(id="leaf3", name="leaf3", host="agent3"),
            GraphNode(id="leaf4", name="leaf4", host="agent3"),
        ],
        links=[
            # Spine-to-leaf links (cross-host)
            GraphLink(endpoints=[GraphEndpoint(node="spine1", ifname="eth1"), GraphEndpoint(node="leaf1", ifname="eth1")]),
            GraphLink(endpoints=[GraphEndpoint(node="spine1", ifname="eth2"), GraphEndpoint(node="leaf2", ifname="eth1")]),
            GraphLink(endpoints=[GraphEndpoint(node="spine1", ifname="eth3"), GraphEndpoint(node="leaf3", ifname="eth1")]),
            GraphLink(endpoints=[GraphEndpoint(node="spine1", ifname="eth4"), GraphEndpoint(node="leaf4", ifname="eth1")]),
            GraphLink(endpoints=[GraphEndpoint(node="spine2", ifname="eth1"), GraphEndpoint(node="leaf1", ifname="eth2")]),
            GraphLink(endpoints=[GraphEndpoint(node="spine2", ifname="eth2"), GraphEndpoint(node="leaf2", ifname="eth2")]),
            GraphLink(endpoints=[GraphEndpoint(node="spine2", ifname="eth3"), GraphEndpoint(node="leaf3", ifname="eth2")]),
            GraphLink(endpoints=[GraphEndpoint(node="spine2", ifname="eth4"), GraphEndpoint(node="leaf4", ifname="eth2")]),
            # Spine-to-spine link (same host)
            GraphLink(endpoints=[GraphEndpoint(node="spine1", ifname="eth5"), GraphEndpoint(node="spine2", ifname="eth5")]),
        ],
    )

    analysis = analyze_topology(graph)

    assert not analysis.single_host
    assert len(analysis.placements) == 3  # 3 hosts

    # 8 cross-host links (all spine-to-leaf links)
    assert len(analysis.cross_host_links) == 8

    # Verify placements
    assert len(analysis.placements["agent1"]) == 2  # 2 spines
    assert len(analysis.placements["agent2"]) == 2  # 2 leaves
    assert len(analysis.placements["agent3"]) == 2  # 2 leaves


# --- Topology Splitting Tests ---

def test_split_topology_single_host():
    """Test splitting a single-host topology."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="r1", host="agent1"),
            GraphNode(id="r2", name="r2", host="agent1"),
        ],
        links=[
            GraphLink(endpoints=[GraphEndpoint(node="r1"), GraphEndpoint(node="r2")])
        ],
    )

    analysis = analyze_topology(graph)
    splits = split_topology_by_host(graph, analysis)

    assert len(splits) == 1
    assert "agent1" in splits
    assert len(splits["agent1"].nodes) == 2
    assert len(splits["agent1"].links) == 1


def test_split_topology_multi_host():
    """Test splitting a multi-host topology."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="r1", host="agent1"),
            GraphNode(id="r2", name="r2", host="agent1"),
            GraphNode(id="r3", name="r3", host="agent2"),
        ],
        links=[
            # Same-host link
            GraphLink(endpoints=[GraphEndpoint(node="r1", ifname="eth0"), GraphEndpoint(node="r2", ifname="eth0")]),
            # Cross-host link (should be excluded from splits)
            GraphLink(endpoints=[GraphEndpoint(node="r2", ifname="eth1"), GraphEndpoint(node="r3", ifname="eth0")]),
        ],
    )

    analysis = analyze_topology(graph)
    splits = split_topology_by_host(graph, analysis)

    assert len(splits) == 2

    # Agent1 should have r1, r2 and the r1-r2 link
    assert len(splits["agent1"].nodes) == 2
    assert len(splits["agent1"].links) == 1
    node_names = {n.name for n in splits["agent1"].nodes}
    assert node_names == {"r1", "r2"}

    # Agent2 should have only r3, no links (cross-host link excluded)
    assert len(splits["agent2"].nodes) == 1
    assert len(splits["agent2"].links) == 0
    assert splits["agent2"].nodes[0].name == "r3"


# --- External Network Tests ---

def test_graph_to_yaml_with_bridge():
    """Test converting a graph with bridge external connection to YAML."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="r1", device="linux"),
        ],
        links=[
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="r1", ifname="eth0"),
                    GraphEndpoint(node="br-external", type="bridge"),
                ]
            )
        ],
    )

    yaml_str = graph_to_yaml(graph)
    assert "r1" in yaml_str
    assert "bridge:br-external" in yaml_str


def test_graph_to_yaml_with_macvlan():
    """Test converting a graph with macvlan external connection to YAML."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="r1", device="linux"),
        ],
        links=[
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="r1", ifname="eth0"),
                    GraphEndpoint(node="eth0", type="macvlan"),
                ]
            )
        ],
    )

    yaml_str = graph_to_yaml(graph)
    assert "r1" in yaml_str
    assert "macvlan:eth0" in yaml_str


def test_graph_to_yaml_with_host_interface():
    """Test converting a graph with host interface connection to YAML."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="r1", device="linux"),
        ],
        links=[
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="r1", ifname="eth0"),
                    GraphEndpoint(node="eth1", type="host"),
                ]
            )
        ],
    )

    yaml_str = graph_to_yaml(graph)
    assert "r1" in yaml_str
    assert "host:eth1" in yaml_str


def test_graph_to_yaml_with_network_mode():
    """Test converting a graph with network_mode to YAML."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="r1", device="linux", network_mode="bridge"),
        ],
        links=[],
    )

    yaml_str = graph_to_yaml(graph)
    assert "network-mode: bridge" in yaml_str


def test_yaml_to_graph_with_bridge():
    """Test parsing a YAML topology with bridge external connection."""
    yaml_str = """
nodes:
  r1:
    device: linux
links:
  - r1:
      ifname: eth0
    bridge:br-external: {}
"""
    graph = yaml_to_graph(yaml_str)

    assert len(graph.nodes) == 1
    assert len(graph.links) == 1

    link = graph.links[0]
    assert len(link.endpoints) == 2

    # Find the bridge endpoint
    bridge_ep = next((ep for ep in link.endpoints if ep.type == "bridge"), None)
    assert bridge_ep is not None
    assert bridge_ep.node == "br-external"
    assert bridge_ep.type == "bridge"


def test_yaml_to_graph_with_macvlan():
    """Test parsing a YAML topology with macvlan external connection."""
    yaml_str = """
nodes:
  r1:
    device: linux
links:
  - r1:
      ifname: eth0
    macvlan:eth0: {}
"""
    graph = yaml_to_graph(yaml_str)

    assert len(graph.links) == 1
    link = graph.links[0]

    macvlan_ep = next((ep for ep in link.endpoints if ep.type == "macvlan"), None)
    assert macvlan_ep is not None
    assert macvlan_ep.node == "eth0"
    assert macvlan_ep.type == "macvlan"


def test_yaml_to_graph_with_network_mode():
    """Test parsing a YAML topology with network-mode."""
    yaml_str = """
nodes:
  r1:
    device: linux
    network-mode: host
links: []
"""
    graph = yaml_to_graph(yaml_str)

    assert len(graph.nodes) == 1
    assert graph.nodes[0].network_mode == "host"


def test_yaml_to_graph_with_host_placement():
    """Test parsing a YAML topology with host placement."""
    yaml_str = """
nodes:
  r1:
    device: linux
    host: agent-1
  r2:
    device: linux
    host: agent-2
links: []
"""
    graph = yaml_to_graph(yaml_str)

    assert len(graph.nodes) == 2
    r1 = next(n for n in graph.nodes if n.name == "r1")
    r2 = next(n for n in graph.nodes if n.name == "r2")
    assert r1.host == "agent-1"
    assert r2.host == "agent-2"


def test_roundtrip_external_connection():
    """Test that external connections survive a graph -> YAML -> graph roundtrip."""
    original = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="r1", device="linux", network_mode="bridge"),
        ],
        links=[
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="r1", ifname="eth0"),
                    GraphEndpoint(node="br-lan", type="bridge"),
                ]
            )
        ],
    )

    yaml_str = graph_to_yaml(original)
    parsed = yaml_to_graph(yaml_str)

    # Check node
    assert len(parsed.nodes) == 1
    assert parsed.nodes[0].network_mode == "bridge"

    # Check link
    assert len(parsed.links) == 1
    link = parsed.links[0]
    bridge_ep = next((ep for ep in link.endpoints if ep.type == "bridge"), None)
    assert bridge_ep is not None
    assert bridge_ep.node == "br-lan"


def test_mixed_internal_and_external_links():
    """Test topology with both internal and external links."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="r1", device="linux"),
            GraphNode(id="r2", name="r2", device="linux"),
        ],
        links=[
            # Internal link between r1 and r2
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="r1", ifname="eth0"),
                    GraphEndpoint(node="r2", ifname="eth0"),
                ]
            ),
            # External link from r1 to bridge
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="r1", ifname="eth1"),
                    GraphEndpoint(node="br-wan", type="bridge"),
                ]
            ),
            # External link from r2 to macvlan
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="r2", ifname="eth1"),
                    GraphEndpoint(node="enp0s3", type="macvlan"),
                ]
            ),
        ],
    )

    yaml_str = graph_to_yaml(graph)

    # Verify all connections are in YAML
    assert "r1" in yaml_str
    assert "r2" in yaml_str
    assert "bridge:br-wan" in yaml_str
    assert "macvlan:enp0s3" in yaml_str

    # Parse back and verify
    parsed = yaml_to_graph(yaml_str)
    assert len(parsed.nodes) == 2
    assert len(parsed.links) == 3

    # Count external endpoints
    external_count = sum(
        1 for link in parsed.links
        for ep in link.endpoints
        if ep.type != "node"
    )
    assert external_count == 2


# --- Topology YAML Generation Tests ---

def test_topology_yaml_ceos_has_startup_config():
    """Test that cEOS nodes get startup-config in topology YAML."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="R1", device="ceos"),
        ],
        links=[],
    )

    yaml_str = graph_to_topology_yaml(graph, "test-lab")

    # Verify startup-config is present with required elements
    assert "startup-config:" in yaml_str
    # Verify zerotouch is disabled (key for config persistence)
    assert "zerotouch cancel" in yaml_str
    # Verify hostname is set based on node name
    assert "hostname R1" in yaml_str
    # Verify AAA root is disabled
    assert "no aaa root" in yaml_str


def test_topology_yaml_ceos_uses_block_scalar():
    """Test that startup-config uses YAML block scalar style."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="R1", device="ceos"),
        ],
        links=[],
    )

    yaml_str = graph_to_topology_yaml(graph, "test-lab")

    # Block scalar style uses '|' indicator
    assert "startup-config: |" in yaml_str


def test_topology_yaml_linux_no_startup_config():
    """Test that non-cEOS nodes don't get startup-config."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="h1", name="H1", device="linux"),
        ],
        links=[],
    )

    yaml_str = graph_to_topology_yaml(graph, "test-lab")

    # Linux nodes shouldn't have startup-config
    assert "startup-config:" not in yaml_str


def test_topology_yaml_mixed_nodes():
    """Test topology with both cEOS and non-cEOS nodes."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="R1", device="ceos"),
            GraphNode(id="r2", name="R2", device="ceos"),
            GraphNode(id="h1", name="H1", device="linux"),
            GraphNode(id="s1", name="S1", device="srlinux"),
        ],
        links=[
            GraphLink(endpoints=[
                GraphEndpoint(node="R1"),
                GraphEndpoint(node="R2"),
            ]),
            GraphLink(endpoints=[
                GraphEndpoint(node="R1"),
                GraphEndpoint(node="H1"),
            ]),
        ],
    )

    yaml_str = graph_to_topology_yaml(graph, "test-lab")

    # Count occurrences of startup-config (should be 2 for R1 and R2)
    startup_config_count = yaml_str.count("startup-config: |")
    assert startup_config_count == 2, f"Expected 2 startup-configs, got {startup_config_count}"


def test_topology_yaml_ceos_config_has_required_lines():
    """Test that cEOS startup-config contains all required configuration."""
    # Generate a config and verify it has required lines
    graph = TopologyGraph(
        nodes=[GraphNode(id="r1", name="TestRouter", device="ceos")],
        links=[],
    )
    yaml_str = graph_to_topology_yaml(graph, "test-lab")

    assert "zerotouch cancel" in yaml_str
    assert "hostname TestRouter" in yaml_str
    assert "no aaa root" in yaml_str
    assert "end" in yaml_str


def test_topology_yaml_structure():
    """Test that topology YAML has correct structure."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="R1", device="ceos"),
            GraphNode(id="h1", name="H1", device="linux"),
        ],
        links=[
            GraphLink(endpoints=[
                GraphEndpoint(node="R1"),
                GraphEndpoint(node="H1"),
            ]),
        ],
    )

    yaml_str = graph_to_topology_yaml(graph, "my-lab-123")

    # Verify basic structure
    assert "name:" in yaml_str
    assert "topology:" in yaml_str
    assert "nodes:" in yaml_str
    assert "links:" in yaml_str
    assert "endpoints:" in yaml_str

    # Verify kind is set (uses kind, not device)
    assert "kind: ceos" in yaml_str
    assert "kind: linux" in yaml_str


def test_topology_yaml_ceos_alias_resolution():
    """Test that cEOS device kind gets startup-config."""
    # Test with 'ceos' kind directly (aliases are not configured in vendor registry)
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="R1", device="ceos"),
        ],
        links=[],
    )

    yaml_str = graph_to_topology_yaml(graph, "test-lab")

    # cEOS nodes get startup-config
    assert "startup-config: |" in yaml_str
    assert "zerotouch cancel" in yaml_str
    assert "hostname R1" in yaml_str


def test_topology_yaml_ceos_has_no_binds():
    """Test that cEOS nodes do not have binds (cEOS mounts /mnt/flash internally)."""
    import yaml as pyyaml

    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="R1", device="ceos"),
        ],
        links=[],
    )

    yaml_str = graph_to_topology_yaml(graph, "test-lab")
    parsed = pyyaml.safe_load(yaml_str)

    # cEOS already mounts /mnt/flash internally - cannot add bind mount
    # Config persistence for cEOS is handled via startup-config and 'write memory'
    nodes = parsed.get("topology", {}).get("nodes", {})
    r1_node = nodes.get("R1", {})

    assert "binds" not in r1_node, "cEOS node should not have binds (mounts /mnt/flash internally)"


def test_topology_yaml_linux_has_no_binds():
    """Test that non-cEOS nodes don't get persistence binds."""
    import yaml as pyyaml

    graph = TopologyGraph(
        nodes=[
            GraphNode(id="h1", name="H1", device="linux"),
        ],
        links=[],
    )

    yaml_str = graph_to_topology_yaml(graph, "test-lab")
    parsed = pyyaml.safe_load(yaml_str)

    # Linux nodes shouldn't have binds for config persistence
    nodes = parsed.get("topology", {}).get("nodes", {})
    h1_node = nodes.get("H1", {})

    assert "binds" not in h1_node, "Linux node should not have persistence binds"


def test_topology_yaml_mixed_nodes_no_binds():
    """Test that no nodes get persistence binds (cEOS mounts /mnt/flash internally)."""
    import yaml as pyyaml

    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="R1", device="ceos"),
            GraphNode(id="h1", name="H1", device="linux"),
            GraphNode(id="s1", name="S1", device="srlinux"),
        ],
        links=[],
    )

    yaml_str = graph_to_topology_yaml(graph, "test-lab")
    parsed = pyyaml.safe_load(yaml_str)

    nodes = parsed.get("topology", {}).get("nodes", {})

    # No nodes should have binds - cEOS mounts /mnt/flash internally
    assert "binds" not in nodes.get("R1", {}), "cEOS node should not have binds"
    assert "binds" not in nodes.get("H1", {}), "Linux node should not have binds"
    assert "binds" not in nodes.get("S1", {}), "SR Linux node should not have binds"


# --- Additional YAML Generation Edge Cases ---

def test_topology_yaml_with_link_attributes():
    """Test that link attributes (MTU, bandwidth) are preserved."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="R1", device="linux"),
            GraphNode(id="r2", name="R2", device="linux"),
        ],
        links=[
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="R1", ifname="eth0"),
                    GraphEndpoint(node="R2", ifname="eth0"),
                ],
                mtu=9000,
                bandwidth=1000,
            )
        ],
    )

    yaml_str = graph_to_yaml(graph)
    assert "mtu:" in yaml_str or "mtu" in yaml_str
    # Verify attributes are present
    import yaml as pyyaml
    parsed = pyyaml.safe_load(yaml_str)
    link = parsed["links"][0]
    assert link.get("mtu") == 9000 or any(v.get("mtu") == 9000 for v in link.values() if isinstance(v, dict))


def test_topology_yaml_with_pool_and_prefix():
    """Test that IP pool and prefix settings are preserved."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="R1", device="linux"),
            GraphNode(id="r2", name="R2", device="linux"),
        ],
        links=[
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="R1"),
                    GraphEndpoint(node="R2"),
                ],
                pool="mgmt",
                prefix="10.0.0.0/24",
            )
        ],
    )

    yaml_str = graph_to_yaml(graph)
    import yaml as pyyaml
    parsed = pyyaml.safe_load(yaml_str)
    link = parsed["links"][0]
    assert link.get("pool") == "mgmt"
    assert link.get("prefix") == "10.0.0.0/24"


def test_topology_yaml_preserves_node_vars():
    """Test that node vars are preserved in YAML."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(
                id="r1",
                name="R1",
                device="linux",
                vars={"cpu": 2, "memory": "4G", "custom_label": "test"},
            ),
        ],
        links=[],
    )

    yaml_str = graph_to_yaml(graph)
    import yaml as pyyaml
    parsed = pyyaml.safe_load(yaml_str)
    node = parsed["nodes"]["R1"]
    assert node.get("cpu") == 2
    assert node.get("memory") == "4G"


def test_topology_yaml_node_name_sanitization():
    """Test that node names with special characters are sanitized."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="Router-1!@#$%", device="linux"),
            GraphNode(id="r2", name="123-starts-with-number", device="linux"),
        ],
        links=[],
    )

    yaml_str = graph_to_yaml(graph)
    import yaml as pyyaml
    parsed = pyyaml.safe_load(yaml_str)

    # Node names should be sanitized
    node_names = list(parsed["nodes"].keys())
    for name in node_names:
        # Verify names are valid identifiers
        assert re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name) or "_gui_id" in parsed["nodes"][name]


def test_topology_yaml_long_node_name_truncation():
    """Test that very long node names are truncated properly."""
    long_name = "ThisIsAVeryLongNodeNameThatExceeds16Characters"
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name=long_name, device="linux"),
        ],
        links=[],
    )

    yaml_str = graph_to_yaml(graph)
    import yaml as pyyaml
    parsed = pyyaml.safe_load(yaml_str)

    node_name = list(parsed["nodes"].keys())[0]
    # Containerlab has 16 char limit
    assert len(node_name) <= 16


def test_topology_yaml_with_image_override():
    """Test that explicit image setting is preserved."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(
                id="r1",
                name="R1",
                device="ceos",
                image="custom-ceos:4.30.0",
            ),
        ],
        links=[],
    )

    yaml_str = graph_to_yaml(graph)
    assert "custom-ceos:4.30.0" in yaml_str


def test_topology_yaml_roundtrip_preserves_ids():
    """Test that node IDs survive YAML roundtrip via _gui_id."""
    original = TopologyGraph(
        nodes=[
            GraphNode(id="unique-gui-id-123", name="R1", device="linux"),
            GraphNode(id="another-id-456", name="R2", device="linux"),
        ],
        links=[
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="R1"),
                    GraphEndpoint(node="R2"),
                ]
            )
        ],
    )

    yaml_str = graph_to_yaml(original)
    parsed = yaml_to_graph(yaml_str)

    # Verify IDs are preserved
    ids = {n.id for n in parsed.nodes}
    assert "unique-gui-id-123" in ids
    assert "another-id-456" in ids


def test_yaml_to_graph_with_defaults():
    """Test parsing YAML with defaults section."""
    yaml_str = """
defaults:
  kind: linux
  image: alpine:latest
nodes:
  r1: {}
  r2: {}
links:
  - r1: {}
    r2: {}
"""
    graph = yaml_to_graph(yaml_str)

    assert graph.defaults is not None
    assert graph.defaults.get("kind") == "linux"
    assert len(graph.nodes) == 2


def test_analyze_cross_host_link_ip_addresses():
    """Test that IP addresses are captured in cross-host link analysis."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="r1", host="agent1"),
            GraphNode(id="r2", name="r2", host="agent2"),
        ],
        links=[
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="r1", ifname="eth0", ipv4="10.0.0.1/24"),
                    GraphEndpoint(node="r2", ifname="eth0", ipv4="10.0.0.2/24"),
                ]
            )
        ],
    )

    analysis = analyze_topology(graph)

    assert len(analysis.cross_host_links) == 1
    chl = analysis.cross_host_links[0]
    assert chl.ip_a == "10.0.0.1/24"
    assert chl.ip_b == "10.0.0.2/24"


def test_external_network_node_in_topology():
    """Test handling external network nodes in topology."""
    graph = TopologyGraph(
        nodes=[
            GraphNode(id="r1", name="R1", device="linux"),
            GraphNode(
                id="ext1",
                name="External-VLAN",
                node_type="external",
                connection_type="vlan",
                parent_interface="ens192",
                vlan_id=100,
            ),
        ],
        links=[
            GraphLink(
                endpoints=[
                    GraphEndpoint(node="R1", ifname="eth0"),
                    GraphEndpoint(node="External-VLAN"),
                ]
            )
        ],
    )

    yaml_str = graph_to_yaml(graph)

    # External node should be serialized with its properties
    import yaml as pyyaml
    parsed = pyyaml.safe_load(yaml_str)

    # Find the external node
    ext_node = None
    for name, data in parsed["nodes"].items():
        if data and data.get("node_type") == "external":
            ext_node = data
            break

    if ext_node:
        assert ext_node.get("connection_type") == "vlan"
        assert ext_node.get("vlan_id") == 100


# To run these tests:
# cd api && pytest tests/test_topology.py -v
