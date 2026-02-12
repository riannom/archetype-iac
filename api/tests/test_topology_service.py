"""Tests for TopologyService database operations.

These tests verify:
1. update_from_graph correctly handles link endpoint ordering
2. Canonical link naming swaps source/target IDs when needed
3. Cross-host link analysis correctly identifies links
"""

import json

from app import models
from app.schemas import (
    GraphEndpoint,
    GraphLink,
    GraphNode,
    TopologyGraph,
)
from app.services.topology import TopologyService, graph_to_deploy_topology


# --- Link Endpoint Ordering Tests ---

class TestLinkEndpointOrdering:
    """Tests for correct endpoint ordering during import.

    When generate_link_name() sorts endpoints alphabetically, the source
    and target node IDs/interfaces must also be swapped to match.
    """

    def test_link_endpoints_not_swapped_when_alphabetical(
        self, test_db, sample_lab, multiple_hosts
    ):
        """Endpoints stay in order when source < target alphabetically."""
        # Create hosts and update lab to have nodes assigned
        host1, host2, _ = multiple_hosts

        # Create graph where "aaa" < "zzz" alphabetically (no swap needed)
        graph = TopologyGraph(
            nodes=[
                GraphNode(id="node-aaa", name="aaa", device="linux", host=host1.id),
                GraphNode(id="node-zzz", name="zzz", device="linux", host=host2.id),
            ],
            links=[
                GraphLink(
                    endpoints=[
                        GraphEndpoint(node="node-aaa", ifname="eth1", ipv4="10.0.0.1/24"),
                        GraphEndpoint(node="node-zzz", ifname="eth2", ipv4="10.0.0.2/24"),
                    ]
                )
            ],
        )

        service = TopologyService(test_db)
        nodes_created, links_created = service.update_from_graph(sample_lab.id, graph)
        test_db.commit()

        assert nodes_created == 2
        assert links_created == 1

        # Get the created link
        links = service.get_links(sample_lab.id)
        assert len(links) == 1
        link = links[0]

        # Link name should be aaa:eth1-zzz:eth2 (no swap)
        assert link.link_name == "aaa:eth1-zzz:eth2"

        # Source should be aaa (original source, no swap)
        source_node = test_db.get(models.Node, link.source_node_id)
        target_node = test_db.get(models.Node, link.target_node_id)

        assert source_node.container_name == "aaa"
        assert target_node.container_name == "zzz"
        assert link.source_interface == "eth1"
        assert link.target_interface == "eth2"

        # Check IP config is in correct order
        config = json.loads(link.config_json) if link.config_json else {}
        assert config.get("ip_a") == "10.0.0.1/24"
        assert config.get("ip_b") == "10.0.0.2/24"


class TestInterfaceCountMap:
    """Tests for interface pre-provisioning counts."""

    def test_interface_count_uses_device_defaults(self, test_db, sample_lab):
        """Uses UI/vendor maxPorts even when there are no links."""
        graph = TopologyGraph(
            nodes=[
                GraphNode(
                    id="node-1",
                    name="EOS-1",
                    container_name="eos_1",
                    device="ceos",
                )
            ],
            links=[],
        )

        service = TopologyService(test_db)
        service.update_from_graph(sample_lab.id, graph)
        test_db.commit()

        iface_map = service.get_interface_count_map(sample_lab.id)
        assert iface_map["eos_1"] == 12  # ceos max_ports in vendors.py

    def test_interface_count_raises_to_link_max(self, test_db, sample_lab):
        """Interface count is raised when links reference higher indices."""
        graph = TopologyGraph(
            nodes=[
                GraphNode(
                    id="node-1",
                    name="EOS-1",
                    container_name="eos_1",
                    device="ceos",
                ),
                GraphNode(
                    id="node-2",
                    name="EOS-2",
                    container_name="eos_2",
                    device="ceos",
                ),
            ],
            links=[
                GraphLink(
                    endpoints=[
                        GraphEndpoint(node="node-1", ifname="eth20"),
                        GraphEndpoint(node="node-2", ifname="eth1"),
                    ]
                )
            ],
        )

        service = TopologyService(test_db)
        service.update_from_graph(sample_lab.id, graph)
        test_db.commit()

        iface_map = service.get_interface_count_map(sample_lab.id)
        assert iface_map["eos_1"] == 20

    def test_link_endpoints_swapped_when_reverse_alphabetical(
        self, test_db, sample_lab, multiple_hosts
    ):
        """Endpoints are swapped when source > target alphabetically.

        This is the key bug fix: when generate_link_name() swaps endpoints
        for canonical naming, we must also swap source/target node IDs.
        """
        host1, host2, _ = multiple_hosts

        # Create graph where source "zzz" > target "aaa" (swap needed)
        graph = TopologyGraph(
            nodes=[
                GraphNode(id="node-zzz", name="zzz", device="linux", host=host1.id),
                GraphNode(id="node-aaa", name="aaa", device="linux", host=host2.id),
            ],
            links=[
                GraphLink(
                    endpoints=[
                        # Source is "zzz" which is > "aaa", so endpoints will be swapped
                        GraphEndpoint(node="node-zzz", ifname="eth1", ipv4="10.0.0.1/24"),
                        GraphEndpoint(node="node-aaa", ifname="eth2", ipv4="10.0.0.2/24"),
                    ]
                )
            ],
        )

        service = TopologyService(test_db)
        nodes_created, links_created = service.update_from_graph(sample_lab.id, graph)
        test_db.commit()

        links = service.get_links(sample_lab.id)
        assert len(links) == 1
        link = links[0]

        # Link name should be canonical: aaa:eth2-zzz:eth1
        assert link.link_name == "aaa:eth2-zzz:eth1"

        # CRITICAL: source_node_id should now point to "aaa" (the swapped source)
        source_node = test_db.get(models.Node, link.source_node_id)
        target_node = test_db.get(models.Node, link.target_node_id)

        # After swap: aaa becomes source, zzz becomes target
        assert source_node.container_name == "aaa"
        assert target_node.container_name == "zzz"

        # Interfaces should also be swapped to match
        assert link.source_interface == "eth2"  # Was target's interface
        assert link.target_interface == "eth1"  # Was source's interface

        # IPs should be swapped to match canonical order
        config = json.loads(link.config_json) if link.config_json else {}
        # ip_a should be aaa's IP (was originally ep_b)
        assert config.get("ip_a") == "10.0.0.2/24"
        # ip_b should be zzz's IP (was originally ep_a)
        assert config.get("ip_b") == "10.0.0.1/24"

    def test_link_with_same_prefix_different_suffix(
        self, test_db, sample_lab, multiple_hosts
    ):
        """Test nodes with similar names but different suffixes."""
        host1, host2, _ = multiple_hosts

        # r10 > r2 alphabetically because "10" > "2" in string comparison
        graph = TopologyGraph(
            nodes=[
                GraphNode(id="node-r10", name="r10", device="linux", host=host1.id),
                GraphNode(id="node-r2", name="r2", device="linux", host=host2.id),
            ],
            links=[
                GraphLink(
                    endpoints=[
                        GraphEndpoint(node="node-r10", ifname="eth1"),
                        GraphEndpoint(node="node-r2", ifname="eth1"),
                    ]
                )
            ],
        )

        service = TopologyService(test_db)
        service.update_from_graph(sample_lab.id, graph)
        test_db.commit()

        links = service.get_links(sample_lab.id)
        link = links[0]

        # "r10:eth1" < "r2:eth1" alphabetically ('1' < '2' at position 1)
        assert link.link_name == "r10:eth1-r2:eth1"

        # Since r10 < r2 alphabetically at first char comparison (both 'r'),
        # but r10 has more chars... actually "r10" < "r2" because '1' < '2' at position 1
        # Wait, let me reconsider: comparing "r10:eth1" vs "r2:eth1"
        # Position 0: 'r' == 'r'
        # Position 1: '1' < '2'
        # So "r10:eth1" < "r2:eth1", no swap should happen

        source_node = test_db.get(models.Node, link.source_node_id)
        target_node = test_db.get(models.Node, link.target_node_id)

        # r10 is source (original), r2 is target (original)
        assert source_node.container_name == "r10"
        assert target_node.container_name == "r2"

    def test_cross_host_link_analysis_after_swap(
        self, test_db, sample_lab, multiple_hosts
    ):
        """Cross-host link analysis should work correctly after endpoint swap."""
        host1, host2, _ = multiple_hosts

        # eos_2 > eos_1 alphabetically, so swap will happen
        graph = TopologyGraph(
            nodes=[
                GraphNode(id="node-eos2", name="eos_2", device="ceos", host=host1.id),
                GraphNode(id="node-eos1", name="eos_1", device="ceos", host=host2.id),
            ],
            links=[
                GraphLink(
                    endpoints=[
                        GraphEndpoint(node="node-eos2", ifname="Ethernet1", ipv4="10.1.2.1/24"),
                        GraphEndpoint(node="node-eos1", ifname="Ethernet1", ipv4="10.1.2.2/24"),
                    ]
                )
            ],
        )

        service = TopologyService(test_db)
        service.update_from_graph(sample_lab.id, graph)
        test_db.commit()

        # Analyze placements - should correctly identify cross-host link
        analysis = service.analyze_placements(sample_lab.id)

        assert not analysis.single_host
        assert len(analysis.cross_host_links) == 1

        chl = analysis.cross_host_links[0]

        # The link should have canonical ordering: eos_1 < eos_2
        # So node_a should be eos_1, node_b should be eos_2
        assert chl.node_a == "eos_1"
        assert chl.node_b == "eos_2"
        assert chl.interface_a == "Ethernet1"
        assert chl.interface_b == "Ethernet1"

        # Hosts should match the swapped node assignment
        assert chl.host_a == host2.id  # eos_1 is on host2
        assert chl.host_b == host1.id  # eos_2 is on host1

        # IPs should be in canonical order
        assert chl.ip_a == "10.1.2.2/24"  # eos_1's IP
        assert chl.ip_b == "10.1.2.1/24"  # eos_2's IP


class TestLinkNameGeneration:
    """Tests for link name generation helper."""

    def test_generate_link_name_sorts_alphabetically(self, test_db, sample_lab):
        """Verify link names are always sorted alphabetically."""
        service = TopologyService(test_db)

        # Test both orders produce same result
        name1 = service._generate_link_name("aaa", "eth1", "bbb", "eth2")
        name2 = service._generate_link_name("bbb", "eth2", "aaa", "eth1")

        assert name1 == name2
        assert name1 == "aaa:eth1-bbb:eth2"

    def test_generate_link_name_considers_interface(self, test_db, sample_lab):
        """Interface is part of endpoint comparison."""
        service = TopologyService(test_db)

        # Same nodes, different interfaces
        name1 = service._generate_link_name("node", "eth1", "node", "eth2")

        # "node:eth1" < "node:eth2"
        assert name1 == "node:eth1-node:eth2"


class TestImportFromGraphIdempotent:
    """Tests for idempotent import behavior."""

    def test_reimport_same_graph_no_duplicates(
        self, test_db, sample_lab, multiple_hosts
    ):
        """Re-importing the same graph should not create duplicates."""
        host1, host2, _ = multiple_hosts

        graph = TopologyGraph(
            nodes=[
                GraphNode(id="n1", name="r1", device="linux", host=host1.id),
                GraphNode(id="n2", name="r2", device="linux", host=host2.id),
            ],
            links=[
                GraphLink(
                    endpoints=[
                        GraphEndpoint(node="n1", ifname="eth1"),
                        GraphEndpoint(node="n2", ifname="eth1"),
                    ]
                )
            ],
        )

        service = TopologyService(test_db)

        # First import
        nodes1, links1 = service.update_from_graph(sample_lab.id, graph)
        test_db.commit()
        assert nodes1 == 2
        assert links1 == 1

        # Second import (same graph)
        nodes2, links2 = service.update_from_graph(sample_lab.id, graph)
        test_db.commit()
        # Should update, not create new
        assert nodes2 == 0
        assert links2 == 0

        # Verify only one of each exists
        assert len(service.get_nodes(sample_lab.id)) == 2
        assert len(service.get_links(sample_lab.id)) == 1


class TestDeployHardwareProfile:
    """Tests for runtime hardware profile propagation into deploy payloads."""

    def test_graph_to_deploy_topology_includes_resolved_hardware_specs(self, monkeypatch):
        class _StubDeviceService:
            def resolve_hardware_specs(self, _device_id, _node_cfg, _image_reference=None):
                return {
                    "memory": 18432,
                    "cpu": 4,
                    "disk_driver": "ide",
                    "nic_driver": "e1000",
                    "machine_type": "pc-i440fx-6.2",
                }
            def get_device_config(self, _device_id):
                return {
                    "effective": {
                        "readinessProbe": "log_pattern",
                        "readinessPattern": "Press RETURN",
                        "readinessTimeout": 2400,
                    }
                }

        monkeypatch.setattr(
            "app.services.device_service.get_device_service",
            lambda: _StubDeviceService(),
        )

        graph = TopologyGraph(
            nodes=[
                GraphNode(
                    id="n1",
                    name="cat9k-1",
                    container_name="cat9k_1",
                    device="cat9000v-uadp",
                    image="/var/lib/archetype/images/cat9kv_prd.17.15.03.qcow2",
                )
            ],
            links=[],
        )

        topology = graph_to_deploy_topology(graph)
        assert len(topology["nodes"]) == 1
        node = topology["nodes"][0]
        assert node["memory"] == 18432
        assert node["cpu"] == 4
        assert node["disk_driver"] == "ide"
        assert node["nic_driver"] == "e1000"
        assert node["machine_type"] == "pc-i440fx-6.2"
        assert node["readiness_probe"] == "log_pattern"
        assert node["readiness_pattern"] == "Press RETURN"
        assert node["readiness_timeout"] == 2400

    def test_build_deploy_topology_includes_resolved_hardware_specs(
        self, test_db, sample_lab, multiple_hosts, monkeypatch
    ):
        class _StubDeviceService:
            def resolve_hardware_specs(self, _device_id, _node_cfg, _image_reference=None):
                return {
                    "memory": 12288,
                    "cpu": 4,
                    "disk_driver": "ide",
                    "nic_driver": "e1000",
                    "machine_type": "pc-i440fx-6.2",
                }
            def get_device_config(self, _device_id):
                return {
                    "effective": {
                        "readinessProbe": "log_pattern",
                        "readinessPattern": "Router>",
                        "readinessTimeout": 1800,
                    }
                }

        monkeypatch.setattr(
            "app.services.device_service.get_device_service",
            lambda: _StubDeviceService(),
        )

        host1, _, _ = multiple_hosts
        graph = TopologyGraph(
            nodes=[
                GraphNode(
                    id="n1",
                    name="cat9k-q200",
                    container_name="cat9k_q200",
                    device="cat9000v-q200",
                    image="/var/lib/archetype/images/cat9kv_prd.17.15.03.qcow2",
                    host=host1.id,
                )
            ],
            links=[],
        )

        service = TopologyService(test_db)
        service.update_from_graph(sample_lab.id, graph)
        test_db.commit()

        deploy_topology = service.build_deploy_topology(sample_lab.id, host1.id)
        assert len(deploy_topology["nodes"]) == 1
        node = deploy_topology["nodes"][0]
        assert node["memory"] == 12288
        assert node["cpu"] == 4
        assert node["disk_driver"] == "ide"
        assert node["nic_driver"] == "e1000"
        assert node["machine_type"] == "pc-i440fx-6.2"
        assert node["readiness_probe"] == "log_pattern"
        assert node["readiness_pattern"] == "Router>"
        assert node["readiness_timeout"] == 1800
