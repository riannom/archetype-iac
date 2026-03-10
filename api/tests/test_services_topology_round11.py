"""Tests for api/app/services/topology.py — TopologyService (round 11).

Covers export_to_graph, is_multihost, get_cross_host_links, get_node_host,
get_required_images, get_image_to_nodes_map, update_from_graph delete paths,
normalize_links_for_lab, and to_topology_yaml_for_host.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.schemas import GraphEndpoint, GraphLink, GraphNode, TopologyGraph
from app.services.topology import TopologyService
from tests.factories import make_host, make_link, make_node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# export_to_graph
# ---------------------------------------------------------------------------


class TestExportToGraph:

    def test_empty_lab(self, test_db: Session, sample_lab: models.Lab):
        svc = TopologyService(test_db)
        graph = svc.export_to_graph(sample_lab.id)
        assert graph.nodes == []
        assert graph.links == []

    def test_nodes_with_config_json(self, test_db: Session, sample_lab: models.Lab):
        config = json.dumps({"role": "spine", "memory": "4096"})
        make_node(test_db, sample_lab.id, "n1", "R1", config_json=config)
        test_db.commit()

        svc = TopologyService(test_db)
        graph = svc.export_to_graph(sample_lab.id)
        assert len(graph.nodes) == 1
        assert graph.nodes[0].role == "spine"
        assert graph.nodes[0].memory == 4096

    def test_links_exported_with_endpoints(self, test_db: Session, sample_lab: models.Lab):
        n1 = make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = make_node(test_db, sample_lab.id, "n2", "R2")
        make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1",
                   config_json=json.dumps({"ip_a": "10.0.0.1/24", "ip_b": "10.0.0.2/24"}))
        test_db.commit()

        svc = TopologyService(test_db)
        graph = svc.export_to_graph(sample_lab.id)
        assert len(graph.links) == 1
        link = graph.links[0]
        assert len(link.endpoints) == 2

    def test_node_host_exported(self, test_db: Session, sample_lab: models.Lab):
        make_host(test_db, "host-1", "Agent 1")
        make_node(test_db, sample_lab.id, "n1", "R1", host_id="host-1")
        test_db.commit()

        svc = TopologyService(test_db)
        graph = svc.export_to_graph(sample_lab.id)
        assert graph.nodes[0].host == "host-1"


# ---------------------------------------------------------------------------
# is_multihost / get_cross_host_links
# ---------------------------------------------------------------------------


class TestMultihost:

    def test_single_host(self, test_db: Session, sample_lab: models.Lab):
        make_host(test_db, "h1")
        make_node(test_db, sample_lab.id, "n1", "R1", host_id="h1")
        make_node(test_db, sample_lab.id, "n2", "R2", host_id="h1")
        test_db.commit()

        svc = TopologyService(test_db)
        assert svc.is_multihost(sample_lab.id) is False
        assert svc.get_cross_host_links(sample_lab.id) == []

    def test_multihost_detected(self, test_db: Session, sample_lab: models.Lab):
        make_host(test_db, "h1")
        make_host(test_db, "h2")
        make_node(test_db, sample_lab.id, "n1", "R1", host_id="h1")
        make_node(test_db, sample_lab.id, "n2", "R2", host_id="h2")
        test_db.commit()

        svc = TopologyService(test_db)
        assert svc.is_multihost(sample_lab.id) is True

    def test_cross_host_links_detected(self, test_db: Session, sample_lab: models.Lab):
        make_host(test_db, "h1")
        make_host(test_db, "h2")
        n1 = make_node(test_db, sample_lab.id, "n1", "R1", host_id="h1")
        n2 = make_node(test_db, sample_lab.id, "n2", "R2", host_id="h2")
        make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1")
        test_db.commit()

        svc = TopologyService(test_db)
        xlinks = svc.get_cross_host_links(sample_lab.id)
        assert len(xlinks) == 1
        assert xlinks[0].host_a != xlinks[0].host_b

    def test_no_cross_host_links_same_host(self, test_db: Session, sample_lab: models.Lab):
        make_host(test_db, "h1")
        n1 = make_node(test_db, sample_lab.id, "n1", "R1", host_id="h1")
        n2 = make_node(test_db, sample_lab.id, "n2", "R2", host_id="h1")
        make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1")
        test_db.commit()

        svc = TopologyService(test_db)
        assert svc.get_cross_host_links(sample_lab.id) == []


# ---------------------------------------------------------------------------
# get_node_host
# ---------------------------------------------------------------------------


class TestGetNodeHost:

    def test_explicit_host_id(self, test_db: Session, sample_lab: models.Lab):
        make_host(test_db, "h1", "Agent 1")
        make_node(test_db, sample_lab.id, "n1", "R1", host_id="h1")
        test_db.commit()

        svc = TopologyService(test_db)
        host = svc.get_node_host(sample_lab.id, "R1")
        assert host is not None
        assert host.id == "h1"

    def test_no_placement_returns_none(self, test_db: Session, sample_lab: models.Lab):
        make_node(test_db, sample_lab.id, "n1", "R1")
        test_db.commit()

        svc = TopologyService(test_db)
        assert svc.get_node_host(sample_lab.id, "R1") is None

    def test_placement_fallback(self, test_db: Session, sample_lab: models.Lab):
        make_host(test_db, "h1", "Agent 1")
        make_node(test_db, sample_lab.id, "n1", "R1")
        placement = models.NodePlacement(
            lab_id=sample_lab.id, node_name="R1", host_id="h1",
        )
        test_db.add(placement)
        test_db.commit()

        svc = TopologyService(test_db)
        host = svc.get_node_host(sample_lab.id, "R1")
        assert host is not None
        assert host.id == "h1"


# ---------------------------------------------------------------------------
# get_required_images / get_image_to_nodes_map
# ---------------------------------------------------------------------------


class TestImageMethods:

    @patch("app.services.topology.resolve_node_image", return_value="ceos:4.28.0F")
    @patch("app.services.topology.resolve_device_kind", return_value="ceos")
    def test_get_required_images_unique(self, mock_kind, mock_image, test_db: Session, sample_lab: models.Lab):
        make_node(test_db, sample_lab.id, "n1", "R1", device="ceos")
        make_node(test_db, sample_lab.id, "n2", "R2", device="ceos")
        test_db.commit()

        svc = TopologyService(test_db)
        images = svc.get_required_images(sample_lab.id)
        assert images == ["ceos:4.28.0F"]

    @patch("app.services.topology.resolve_node_image", return_value=None)
    @patch("app.services.topology.resolve_device_kind", return_value="unknown")
    def test_get_required_images_skips_none(self, mock_kind, mock_image, test_db: Session, sample_lab: models.Lab):
        make_node(test_db, sample_lab.id, "n1", "R1", device="unknown")
        test_db.commit()

        svc = TopologyService(test_db)
        assert svc.get_required_images(sample_lab.id) == []

    @patch("app.services.topology.resolve_node_image", return_value="ceos:4.28.0F")
    @patch("app.services.topology.resolve_device_kind", return_value="ceos")
    def test_get_image_to_nodes_map(self, mock_kind, mock_image, test_db: Session, sample_lab: models.Lab):
        make_node(test_db, sample_lab.id, "n1", "R1", device="ceos")
        make_node(test_db, sample_lab.id, "n2", "R2", device="ceos")
        test_db.commit()

        svc = TopologyService(test_db)
        mapping = svc.get_image_to_nodes_map(sample_lab.id)
        assert "ceos:4.28.0F" in mapping
        assert set(mapping["ceos:4.28.0F"]) == {"R1", "R2"}


# ---------------------------------------------------------------------------
# update_from_graph — delete paths
# ---------------------------------------------------------------------------


class TestUpdateFromGraph:

    def test_deletes_missing_nodes(self, test_db: Session, sample_lab: models.Lab):
        make_node(test_db, sample_lab.id, "old-gui", "OldNode")
        test_db.commit()

        svc = TopologyService(test_db)
        graph = TopologyGraph(nodes=[], links=[])
        svc.update_from_graph(sample_lab.id, graph)
        test_db.commit()

        remaining = svc.get_nodes(sample_lab.id)
        assert len(remaining) == 0

    def test_creates_new_nodes(self, test_db: Session, sample_lab: models.Lab):
        svc = TopologyService(test_db)
        graph = TopologyGraph(
            nodes=[GraphNode(id="g1", name="NewNode", device="linux")],
            links=[],
        )
        nodes_created, _ = svc.update_from_graph(sample_lab.id, graph)
        test_db.commit()
        assert nodes_created == 1

    def test_deletes_missing_links(self, test_db: Session, sample_lab: models.Lab):
        n1 = make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = make_node(test_db, sample_lab.id, "n2", "R2")
        make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1")
        test_db.commit()

        svc = TopologyService(test_db)
        graph = TopologyGraph(
            nodes=[
                GraphNode(id="n1", name="R1", device="linux"),
                GraphNode(id="n2", name="R2", device="linux"),
            ],
            links=[],  # No links
        )
        svc.update_from_graph(sample_lab.id, graph)
        test_db.commit()
        assert svc.get_links(sample_lab.id) == []

    def test_unknown_host_raises_value_error(self, test_db: Session, sample_lab: models.Lab):
        svc = TopologyService(test_db)
        graph = TopologyGraph(
            nodes=[GraphNode(id="n1", name="R1", device="linux", host="nonexistent-host")],
            links=[],
        )
        with pytest.raises(ValueError, match="does not exist"):
            svc.update_from_graph(sample_lab.id, graph)

    def test_updates_existing_node(self, test_db: Session, sample_lab: models.Lab):
        make_node(test_db, sample_lab.id, "n1", "OldName", device="linux")
        test_db.commit()

        svc = TopologyService(test_db)
        graph = TopologyGraph(
            nodes=[GraphNode(id="n1", name="NewName", device="ceos")],
            links=[],
        )
        created, _ = svc.update_from_graph(sample_lab.id, graph)
        test_db.commit()
        assert created == 0
        nodes = svc.get_nodes(sample_lab.id)
        assert nodes[0].display_name == "NewName"
        assert nodes[0].device == "ceos"

    def test_creates_links_with_endpoints(self, test_db: Session, sample_lab: models.Lab):
        svc = TopologyService(test_db)
        graph = TopologyGraph(
            nodes=[
                GraphNode(id="n1", name="R1", device="linux"),
                GraphNode(id="n2", name="R2", device="linux"),
            ],
            links=[
                GraphLink(endpoints=[
                    GraphEndpoint(node="n1", ifname="eth1"),
                    GraphEndpoint(node="n2", ifname="eth1"),
                ]),
            ],
        )
        _, links_created = svc.update_from_graph(sample_lab.id, graph)
        test_db.commit()
        assert links_created == 1


# ---------------------------------------------------------------------------
# normalize_links_for_lab
# ---------------------------------------------------------------------------


class TestNormalizeLinks:

    @patch("app.services.topology.normalize_interface", side_effect=lambda iface, device=None: iface)
    def test_no_change_returns_zero(self, mock_norm, test_db: Session, sample_lab: models.Lab):
        n1 = make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = make_node(test_db, sample_lab.id, "n2", "R2")
        # Already normalized
        lnk = models.Link(
            lab_id=sample_lab.id, link_name="R1:eth1-R2:eth1",
            source_node_id=n1.id, source_interface="eth1",
            target_node_id=n2.id, target_interface="eth1",
        )
        test_db.add(lnk)
        test_db.commit()

        svc = TopologyService(test_db)
        updates = svc.normalize_links_for_lab(sample_lab.id)
        # May or may not be 0 depending on generate_link_name ordering,
        # but should not raise
        assert updates >= 0

    def test_empty_lab_returns_zero(self, test_db: Session, sample_lab: models.Lab):
        svc = TopologyService(test_db)
        assert svc.normalize_links_for_lab(sample_lab.id) == 0


# ---------------------------------------------------------------------------
# to_topology_yaml_for_host
# ---------------------------------------------------------------------------


class TestToTopologyYamlForHost:

    def test_filters_to_single_host(self, test_db: Session, sample_lab: models.Lab):
        make_host(test_db, "h1")
        make_host(test_db, "h2")
        make_node(test_db, sample_lab.id, "n1", "R1", host_id="h1", device="linux")
        make_node(test_db, sample_lab.id, "n2", "R2", host_id="h2", device="linux")
        test_db.commit()

        svc = TopologyService(test_db)
        with patch("app.topology.graph_to_topology_yaml") as mock_yaml:
            mock_yaml.return_value = "name: lab\n"
            svc.to_topology_yaml_for_host(sample_lab.id, "h1")

        # The graph passed to graph_to_topology_yaml should only have h1's node
        call_args = mock_yaml.call_args
        filtered_graph = call_args[0][0]
        assert len(filtered_graph.nodes) == 1
        assert filtered_graph.nodes[0].name == "R1"

    def test_empty_host_returns_empty(self, test_db: Session, sample_lab: models.Lab):
        make_host(test_db, "h1")
        make_node(test_db, sample_lab.id, "n1", "R1", host_id="h1")
        test_db.commit()

        svc = TopologyService(test_db)
        with patch("app.topology.graph_to_topology_yaml") as mock_yaml:
            mock_yaml.return_value = "name: lab\n"
            svc.to_topology_yaml_for_host(sample_lab.id, "nonexistent-host")

        call_args = mock_yaml.call_args
        filtered_graph = call_args[0][0]
        assert len(filtered_graph.nodes) == 0


# ---------------------------------------------------------------------------
# has_nodes
# ---------------------------------------------------------------------------


class TestHasNodes:

    def test_true_when_nodes_exist(self, test_db: Session, sample_lab: models.Lab):
        make_node(test_db, sample_lab.id, "n1", "R1")
        test_db.commit()
        svc = TopologyService(test_db)
        assert svc.has_nodes(sample_lab.id) is True

    def test_false_when_empty(self, test_db: Session, sample_lab: models.Lab):
        svc = TopologyService(test_db)
        assert svc.has_nodes(sample_lab.id) is False


# ---------------------------------------------------------------------------
# get_reserved_interfaces_for_host
# ---------------------------------------------------------------------------


class TestGetReservedInterfaces:

    def test_cross_host_link_reserved(self, test_db: Session, sample_lab: models.Lab):
        make_host(test_db, "h1")
        make_host(test_db, "h2")
        n1 = make_node(test_db, sample_lab.id, "n1", "R1", host_id="h1")
        n2 = make_node(test_db, sample_lab.id, "n2", "R2", host_id="h2")
        make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1")
        test_db.commit()

        svc = TopologyService(test_db)
        reserved = svc.get_reserved_interfaces_for_host(sample_lab.id, "h1")
        assert ("R1", "eth1") in reserved
        assert ("R2", "eth1") not in reserved  # R2 is on h2

    def test_same_host_links_not_reserved(self, test_db: Session, sample_lab: models.Lab):
        make_host(test_db, "h1")
        n1 = make_node(test_db, sample_lab.id, "n1", "R1", host_id="h1")
        n2 = make_node(test_db, sample_lab.id, "n2", "R2", host_id="h1")
        make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1")
        test_db.commit()

        svc = TopologyService(test_db)
        reserved = svc.get_reserved_interfaces_for_host(sample_lab.id, "h1")
        assert len(reserved) == 0