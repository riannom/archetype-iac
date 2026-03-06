"""Tests for api/app/services/topology.py — TopologyService (round 12).

Targets methods and branches not covered by round 11:
- analyze_placements (direct, default_host_id, canonical ordering, IP config)
- _build_interface_index_map edge cases
- get_interface_count_map
- build_deploy_topology / _node_to_deploy_dict / _link_to_deploy_dict
- export_to_graph with link config (IPs, mtu, bandwidth, managed interfaces)
- update_from_graph link endpoint swapping, link config persistence
- normalize_links_for_lab with actual normalization and dedup
- _link_node_states / _link_link_states
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app import models
from app.schemas import (
    CrossHostLink,
    GraphEndpoint,
    GraphLink,
    GraphNode,
    TopologyGraph,
)
from app.services.topology import TopologyService
from app.services.topology_resolution import NodePlacementInfo, TopologyAnalysisResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(db, lab_id, gui_id, name, device="linux", host_id=None,
               config_json=None, image=None, version=None, node_type="device",
               managed_interface_id=None):
    n = models.Node(
        lab_id=lab_id, gui_id=gui_id, display_name=name,
        container_name=name, device=device, host_id=host_id,
        config_json=config_json, image=image, version=version,
        node_type=node_type, managed_interface_id=managed_interface_id,
    )
    db.add(n)
    db.flush()
    return n


def _make_link(db, lab_id, src_node_id, src_iface, tgt_node_id, tgt_iface,
               config_json=None, mtu=None, bandwidth=None):
    from app.utils.link import generate_link_name
    link_name = generate_link_name("src", src_iface, "tgt", tgt_iface)
    lnk = models.Link(
        lab_id=lab_id, link_name=link_name,
        source_node_id=src_node_id, source_interface=src_iface,
        target_node_id=tgt_node_id, target_interface=tgt_iface,
        config_json=config_json, mtu=mtu, bandwidth=bandwidth,
    )
    db.add(lnk)
    db.flush()
    return lnk


def _make_host(db, host_id, name="Host"):
    h = models.Host(
        id=host_id, name=name, address="localhost:8080",
        status="online", capabilities="{}",
    )
    db.add(h)
    db.flush()
    return h


# ---------------------------------------------------------------------------
# analyze_placements — direct tests
# ---------------------------------------------------------------------------


class TestAnalyzePlacements:

    def test_all_nodes_on_default_host(self, test_db: Session, sample_lab: models.Lab):
        """When no node has explicit host, default_host_id assigns all."""
        _make_node(test_db, sample_lab.id, "n1", "R1")
        _make_node(test_db, sample_lab.id, "n2", "R2")
        test_db.commit()

        svc = TopologyService(test_db)
        result = svc.analyze_placements(sample_lab.id, default_host_id="default-h")
        assert result.single_host is True
        assert "default-h" in result.placements
        assert len(result.placements["default-h"]) == 2

    def test_mixed_explicit_and_default_host(self, test_db: Session, sample_lab: models.Lab):
        """Node with explicit host_id uses that; others use default."""
        _make_host(test_db, "h1")
        _make_node(test_db, sample_lab.id, "n1", "R1", host_id="h1")
        _make_node(test_db, sample_lab.id, "n2", "R2")
        test_db.commit()

        svc = TopologyService(test_db)
        result = svc.analyze_placements(sample_lab.id, default_host_id="h-default")
        assert result.single_host is False
        assert "h1" in result.placements
        assert "h-default" in result.placements

    def test_no_hosts_no_default_gives_empty_placements(self, test_db: Session, sample_lab: models.Lab):
        """Without any host info and no default, placements dict is empty."""
        _make_node(test_db, sample_lab.id, "n1", "R1")
        test_db.commit()

        svc = TopologyService(test_db)
        result = svc.analyze_placements(sample_lab.id)
        assert result.placements == {}
        # single_host is True when unique hosts <= 1
        assert result.single_host is True

    def test_cross_host_link_canonical_ordering_swapped(self, test_db: Session, sample_lab: models.Lab):
        """When ep_a > ep_b alphabetically, endpoints are swapped in CrossHostLink."""
        _make_host(test_db, "h1")
        _make_host(test_db, "h2")
        # "Z1" > "A1" alphabetically => swap will happen
        n_z = _make_node(test_db, sample_lab.id, "nz", "Z1", host_id="h1")
        n_a = _make_node(test_db, sample_lab.id, "na", "A1", host_id="h2")
        _make_link(test_db, sample_lab.id, n_z.id, "eth1", n_a.id, "eth2")
        test_db.commit()

        svc = TopologyService(test_db)
        result = svc.analyze_placements(sample_lab.id)
        assert len(result.cross_host_links) == 1
        xlink = result.cross_host_links[0]
        # After canonical sort, A1 should come first
        assert xlink.node_a == "A1"
        assert xlink.interface_a == "eth2"
        assert xlink.node_b == "Z1"
        assert xlink.interface_b == "eth1"

    def test_cross_host_link_with_ip_config(self, test_db: Session, sample_lab: models.Lab):
        """IPs from link config_json are included in CrossHostLink."""
        _make_host(test_db, "h1")
        _make_host(test_db, "h2")
        n1 = _make_node(test_db, sample_lab.id, "n1", "A1", host_id="h1")
        n2 = _make_node(test_db, sample_lab.id, "n2", "B1", host_id="h2")
        config = json.dumps({"ip_a": "10.0.0.1/24", "ip_b": "10.0.0.2/24"})
        _make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1", config_json=config)
        test_db.commit()

        svc = TopologyService(test_db)
        result = svc.analyze_placements(sample_lab.id)
        xlink = result.cross_host_links[0]
        assert xlink.ip_a == "10.0.0.1/24"
        assert xlink.ip_b == "10.0.0.2/24"

    def test_cross_host_link_ip_swap_on_canonical_order(self, test_db: Session, sample_lab: models.Lab):
        """IPs are swapped when endpoints are swapped for canonical ordering."""
        _make_host(test_db, "h1")
        _make_host(test_db, "h2")
        # Z1:eth1 > A1:eth1 so swap will happen
        n_z = _make_node(test_db, sample_lab.id, "nz", "Z1", host_id="h1")
        n_a = _make_node(test_db, sample_lab.id, "na", "A1", host_id="h2")
        config = json.dumps({"ip_a": "1.1.1.1/24", "ip_b": "2.2.2.2/24"})
        _make_link(test_db, sample_lab.id, n_z.id, "eth1", n_a.id, "eth1", config_json=config)
        test_db.commit()

        svc = TopologyService(test_db)
        result = svc.analyze_placements(sample_lab.id)
        xlink = result.cross_host_links[0]
        # A1 is now node_a (swapped), so IPs should also be swapped
        assert xlink.node_a == "A1"
        assert xlink.ip_a == "2.2.2.2/24"
        assert xlink.ip_b == "1.1.1.1/24"

    def test_cross_host_link_invalid_config_json(self, test_db: Session, sample_lab: models.Lab):
        """Invalid config_json is handled gracefully — IPs become None."""
        _make_host(test_db, "h1")
        _make_host(test_db, "h2")
        n1 = _make_node(test_db, sample_lab.id, "n1", "A1", host_id="h1")
        n2 = _make_node(test_db, sample_lab.id, "n2", "B1", host_id="h2")
        _make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1",
                   config_json="not-valid-json{")
        test_db.commit()

        svc = TopologyService(test_db)
        result = svc.analyze_placements(sample_lab.id)
        xlink = result.cross_host_links[0]
        assert xlink.ip_a is None
        assert xlink.ip_b is None


# ---------------------------------------------------------------------------
# _build_interface_index_map edge cases
# ---------------------------------------------------------------------------


class TestBuildInterfaceIndexMap:

    def test_link_with_empty_interface(self, test_db: Session, sample_lab: models.Lab):
        """Links with empty interface are skipped (falsy check)."""
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = _make_node(test_db, sample_lab.id, "n2", "R2")
        test_db.commit()

        # Create link with empty string interfaces
        lnk = models.Link(
            lab_id=sample_lab.id, link_name="test-link",
            source_node_id=n1.id, source_interface="",
            target_node_id=n2.id, target_interface="",
        )
        test_db.add(lnk)
        test_db.flush()

        svc = TopologyService(test_db)
        result = svc._build_interface_index_map([n1, n2], [lnk])
        assert result[n1.id] == 0
        assert result[n2.id] == 0

    def test_link_with_non_numeric_interface(self, test_db: Session, sample_lab: models.Lab):
        """Interfaces without trailing numbers yield index 0."""
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = _make_node(test_db, sample_lab.id, "n2", "R2")
        test_db.commit()

        lnk = models.Link(
            lab_id=sample_lab.id, link_name="test-link",
            source_node_id=n1.id, source_interface="mgmt",
            target_node_id=n2.id, target_interface="mgmt",
        )
        test_db.add(lnk)
        test_db.flush()

        svc = TopologyService(test_db)
        with patch("app.services.topology.normalize_interface", return_value="mgmt"):
            result = svc._build_interface_index_map([n1, n2], [lnk])
        assert result[n1.id] == 0

    def test_max_index_from_multiple_links(self, test_db: Session, sample_lab: models.Lab):
        """Multiple links — highest index wins."""
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = _make_node(test_db, sample_lab.id, "n2", "R2")
        test_db.commit()

        lnk1 = models.Link(
            lab_id=sample_lab.id, link_name="l1",
            source_node_id=n1.id, source_interface="eth3",
            target_node_id=n2.id, target_interface="eth1",
        )
        lnk2 = models.Link(
            lab_id=sample_lab.id, link_name="l2",
            source_node_id=n1.id, source_interface="eth10",
            target_node_id=n2.id, target_interface="eth5",
        )
        test_db.add_all([lnk1, lnk2])
        test_db.flush()

        svc = TopologyService(test_db)
        with patch("app.services.topology.normalize_interface", side_effect=lambda iface, device=None: iface):
            result = svc._build_interface_index_map([n1, n2], [lnk1, lnk2])
        assert result[n1.id] == 10
        assert result[n2.id] == 5

    def test_link_with_unknown_node_id(self, test_db: Session, sample_lab: models.Lab):
        """Links referencing node IDs not in the nodes list are skipped."""
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1")
        test_db.commit()

        lnk = models.Link(
            lab_id=sample_lab.id, link_name="l1",
            source_node_id="nonexistent-id", source_interface="eth5",
            target_node_id=n1.id, target_interface="eth1",
        )
        test_db.add(lnk)
        test_db.flush()

        svc = TopologyService(test_db)
        with patch("app.services.topology.normalize_interface", side_effect=lambda iface, device=None: iface):
            result = svc._build_interface_index_map([n1], [lnk])
        # nonexistent node should not appear; n1 should get eth1 => 1
        assert "nonexistent-id" not in result
        assert result[n1.id] == 1


# ---------------------------------------------------------------------------
# _link_to_deploy_dict
# ---------------------------------------------------------------------------


class TestLinkToDeployDict:

    def test_basic_conversion(self, test_db: Session, sample_lab: models.Lab):
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = _make_node(test_db, sample_lab.id, "n2", "R2")
        lnk = _make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth2")
        test_db.commit()

        svc = TopologyService(test_db)
        id_to_name = {n1.id: "R1", n2.id: "R2"}
        with patch("app.services.topology.normalize_interface", side_effect=lambda iface, **kw: iface):
            result = svc._link_to_deploy_dict(lnk, id_to_name)
        assert result["source_node"] == "R1"
        assert result["target_node"] == "R2"
        assert result["source_interface"] == "eth1"
        assert result["target_interface"] == "eth2"

    def test_missing_node_id_gives_empty_string(self, test_db: Session, sample_lab: models.Lab):
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = _make_node(test_db, sample_lab.id, "n2", "R2")
        lnk = _make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth2")
        test_db.commit()

        svc = TopologyService(test_db)
        # Only n1 in the mapping
        id_to_name = {n1.id: "R1"}
        with patch("app.services.topology.normalize_interface", side_effect=lambda iface, **kw: iface):
            result = svc._link_to_deploy_dict(lnk, id_to_name)
        assert result["target_node"] == ""

    def test_empty_interfaces_become_empty_string(self, test_db: Session, sample_lab: models.Lab):
        """Empty-string interfaces pass through as empty strings."""
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = _make_node(test_db, sample_lab.id, "n2", "R2")
        lnk = models.Link(
            lab_id=sample_lab.id, link_name="test-link",
            source_node_id=n1.id, source_interface="",
            target_node_id=n2.id, target_interface="",
        )
        test_db.add(lnk)
        test_db.flush()
        test_db.commit()

        svc = TopologyService(test_db)
        id_to_name = {n1.id: "R1", n2.id: "R2"}
        result = svc._link_to_deploy_dict(lnk, id_to_name)
        assert result["source_interface"] == ""
        assert result["target_interface"] == ""


# ---------------------------------------------------------------------------
# export_to_graph — link config round-trip
# ---------------------------------------------------------------------------


class TestExportToGraphLinkConfig:

    def test_link_ips_exported(self, test_db: Session, sample_lab: models.Lab):
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = _make_node(test_db, sample_lab.id, "n2", "R2")
        config = json.dumps({"ip_a": "10.0.0.1/24", "ip_b": "10.0.0.2/24"})
        _make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1", config_json=config)
        test_db.commit()

        svc = TopologyService(test_db)
        with patch("app.services.topology.denormalize_interface", side_effect=lambda iface, device=None: iface):
            graph = svc.export_to_graph(sample_lab.id)
        link = graph.links[0]
        assert link.endpoints[0].ipv4 == "10.0.0.1/24"
        assert link.endpoints[1].ipv4 == "10.0.0.2/24"

    def test_link_mtu_and_bandwidth_exported(self, test_db: Session, sample_lab: models.Lab):
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = _make_node(test_db, sample_lab.id, "n2", "R2")
        _make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1", mtu=9000, bandwidth=1000)
        test_db.commit()

        svc = TopologyService(test_db)
        with patch("app.services.topology.denormalize_interface", side_effect=lambda iface, device=None: iface):
            graph = svc.export_to_graph(sample_lab.id)
        link = graph.links[0]
        assert link.mtu == 9000
        assert link.bandwidth == 1000

    def test_link_type_pool_prefix_bridge_exported(self, test_db: Session, sample_lab: models.Lab):
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = _make_node(test_db, sample_lab.id, "n2", "R2")
        config = json.dumps({"type": "p2p", "name": "mylink", "pool": "mgmt", "prefix": "192.168.0.0/24", "bridge": "br0"})
        _make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1", config_json=config)
        test_db.commit()

        svc = TopologyService(test_db)
        with patch("app.services.topology.denormalize_interface", side_effect=lambda iface, device=None: iface):
            graph = svc.export_to_graph(sample_lab.id)
        link = graph.links[0]
        assert link.type == "p2p"
        assert link.name == "mylink"
        assert link.pool == "mgmt"
        assert link.prefix == "192.168.0.0/24"
        assert link.bridge == "br0"

    def test_invalid_link_config_json_handled(self, test_db: Session, sample_lab: models.Lab):
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = _make_node(test_db, sample_lab.id, "n2", "R2")
        _make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1", config_json="bad{json")
        test_db.commit()

        svc = TopologyService(test_db)
        with patch("app.services.topology.denormalize_interface", side_effect=lambda iface, device=None: iface):
            graph = svc.export_to_graph(sample_lab.id)
        # Should not raise — gracefully returns link with no config attrs
        assert len(graph.links) == 1
        assert graph.links[0].endpoints[0].ipv4 is None

    def test_invalid_node_config_json_handled(self, test_db: Session, sample_lab: models.Lab):
        _make_node(test_db, sample_lab.id, "n1", "R1", config_json="not-json!!")
        test_db.commit()

        svc = TopologyService(test_db)
        graph = svc.export_to_graph(sample_lab.id)
        # Should not raise — node exports with no config fields
        assert len(graph.nodes) == 1
        assert graph.nodes[0].role is None


# ---------------------------------------------------------------------------
# _link_node_states / _link_link_states
# ---------------------------------------------------------------------------


class TestLinkStates:

    def test_link_node_states_by_container_name(self, test_db: Session, sample_lab: models.Lab):
        n = _make_node(test_db, sample_lab.id, "g1", "R1")
        ns = models.NodeState(
            lab_id=sample_lab.id, node_id="some-old-id", node_name="R1",
            desired_state="stopped", actual_state="undeployed",
        )
        test_db.add(ns)
        test_db.flush()
        test_db.commit()

        svc = TopologyService(test_db)
        svc._link_node_states(sample_lab.id)
        test_db.flush()
        assert ns.node_definition_id == n.id

    def test_link_node_states_by_gui_id(self, test_db: Session, sample_lab: models.Lab):
        n = _make_node(test_db, sample_lab.id, "gui-abc", "R1")
        ns = models.NodeState(
            lab_id=sample_lab.id, node_id="gui-abc", node_name="different-name",
            desired_state="stopped", actual_state="undeployed",
        )
        test_db.add(ns)
        test_db.flush()
        test_db.commit()

        svc = TopologyService(test_db)
        svc._link_node_states(sample_lab.id)
        test_db.flush()
        assert ns.node_definition_id == n.id

    def test_link_link_states_by_link_name(self, test_db: Session, sample_lab: models.Lab):
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = _make_node(test_db, sample_lab.id, "n2", "R2")
        lnk = _make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1")
        ls = models.LinkState(
            lab_id=sample_lab.id, link_name=lnk.link_name,
            source_node="R1", source_interface="eth1",
            target_node="R2", target_interface="eth1",
            desired_state="up", actual_state="unknown",
        )
        test_db.add(ls)
        test_db.flush()
        test_db.commit()

        svc = TopologyService(test_db)
        svc._link_link_states(sample_lab.id)
        test_db.flush()
        assert ls.link_definition_id == lnk.id

    def test_link_link_states_no_match(self, test_db: Session, sample_lab: models.Lab):
        """LinkState with non-matching name stays unlinked."""
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = _make_node(test_db, sample_lab.id, "n2", "R2")
        _make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1")
        ls = models.LinkState(
            lab_id=sample_lab.id, link_name="completely-different-name",
            source_node="R1", source_interface="eth1",
            target_node="R2", target_interface="eth1",
            desired_state="up", actual_state="unknown",
        )
        test_db.add(ls)
        test_db.flush()
        test_db.commit()

        svc = TopologyService(test_db)
        svc._link_link_states(sample_lab.id)
        test_db.flush()
        assert ls.link_definition_id is None


# ---------------------------------------------------------------------------
# update_from_graph — link config and endpoint swapping
# ---------------------------------------------------------------------------


class TestUpdateFromGraphLinkConfig:

    def test_link_config_persisted(self, test_db: Session, sample_lab: models.Lab):
        """Link type/pool/prefix/bridge fields are stored in config_json."""
        svc = TopologyService(test_db)
        graph = TopologyGraph(
            nodes=[
                GraphNode(id="n1", name="R1", device="linux"),
                GraphNode(id="n2", name="R2", device="linux"),
            ],
            links=[
                GraphLink(
                    endpoints=[
                        GraphEndpoint(node="n1", ifname="eth1", ipv4="10.0.0.1/24"),
                        GraphEndpoint(node="n2", ifname="eth1", ipv4="10.0.0.2/24"),
                    ],
                    type="p2p",
                    pool="mgmt",
                    prefix="192.168.0.0/24",
                    bridge="br0",
                ),
            ],
        )
        svc.update_from_graph(sample_lab.id, graph)
        test_db.commit()

        links = svc.get_links(sample_lab.id)
        assert len(links) == 1
        config = json.loads(links[0].config_json)
        assert config["type"] == "p2p"
        assert config["pool"] == "mgmt"
        assert config["prefix"] == "192.168.0.0/24"
        assert config["bridge"] == "br0"
        # IPs should be stored matching canonical order
        assert "ip_a" in config or "ip_b" in config

    def test_external_endpoint_links_skipped(self, test_db: Session, sample_lab: models.Lab):
        """Links with non-node endpoint types are skipped."""
        svc = TopologyService(test_db)
        graph = TopologyGraph(
            nodes=[
                GraphNode(id="n1", name="R1", device="linux"),
            ],
            links=[
                GraphLink(
                    endpoints=[
                        GraphEndpoint(node="n1", ifname="eth1", type="node"),
                        GraphEndpoint(node="ext1", ifname="eth0", type="external"),
                    ],
                ),
            ],
        )
        _, links_created = svc.update_from_graph(sample_lab.id, graph)
        test_db.commit()
        assert links_created == 0

    def test_link_with_missing_node_skipped(self, test_db: Session, sample_lab: models.Lab):
        """Links referencing unknown node IDs are skipped with warning."""
        svc = TopologyService(test_db)
        graph = TopologyGraph(
            nodes=[
                GraphNode(id="n1", name="R1", device="linux"),
            ],
            links=[
                GraphLink(
                    endpoints=[
                        GraphEndpoint(node="n1", ifname="eth1"),
                        GraphEndpoint(node="nonexistent", ifname="eth1"),
                    ],
                ),
            ],
        )
        _, links_created = svc.update_from_graph(sample_lab.id, graph)
        test_db.commit()
        assert links_created == 0

    def test_node_config_hardware_overrides_persisted(self, test_db: Session, sample_lab: models.Lab):
        """Per-node hardware overrides are stored in config_json."""
        svc = TopologyService(test_db)
        graph = TopologyGraph(
            nodes=[
                GraphNode(
                    id="n1", name="R1", device="n9kv",
                    memory=8192, cpu=4,
                    disk_driver="virtio", nic_driver="e1000",
                    efi_boot=True, efi_vars="stateless",
                ),
            ],
            links=[],
        )
        svc.update_from_graph(sample_lab.id, graph)
        test_db.commit()

        nodes = svc.get_nodes(sample_lab.id)
        config = json.loads(nodes[0].config_json)
        assert config["memory"] == 8192
        assert config["cpu"] == 4
        assert config["disk_driver"] == "virtio"
        assert config["efi_boot"] is True
        assert config["efi_vars"] == "stateless"


# ---------------------------------------------------------------------------
# get_interface_count_map
# ---------------------------------------------------------------------------


class TestGetInterfaceCountMap:

    @patch("app.services.topology.resolve_effective_max_ports", return_value=8)
    @patch("app.services.topology.resolve_node_image", return_value="linux:latest")
    @patch("app.services.topology.resolve_device_kind", return_value="linux")
    @patch("app.services.topology.normalize_interface", side_effect=lambda iface, device=None: iface)
    def test_max_ports_wins_over_link_index(self, mock_norm, mock_kind, mock_image, mock_ports,
                                            test_db: Session, sample_lab: models.Lab):
        """When max_ports > highest link index, max_ports is used."""
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = _make_node(test_db, sample_lab.id, "n2", "R2")
        _make_link(test_db, sample_lab.id, n1.id, "eth3", n2.id, "eth2")
        test_db.commit()

        svc = TopologyService(test_db)
        result = svc.get_interface_count_map(sample_lab.id)
        # max_ports=8 > eth3=3, so R1 should be 8
        assert result["R1"] == 8

    @patch("app.services.topology.resolve_effective_max_ports", return_value=4)
    @patch("app.services.topology.resolve_node_image", return_value="linux:latest")
    @patch("app.services.topology.resolve_device_kind", return_value="linux")
    @patch("app.services.topology.normalize_interface", side_effect=lambda iface, device=None: iface)
    def test_link_index_wins_over_max_ports(self, mock_norm, mock_kind, mock_image, mock_ports,
                                            test_db: Session, sample_lab: models.Lab):
        """When highest link index > max_ports, link index is used."""
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1")
        n2 = _make_node(test_db, sample_lab.id, "n2", "R2")
        _make_link(test_db, sample_lab.id, n1.id, "eth10", n2.id, "eth1")
        test_db.commit()

        svc = TopologyService(test_db)
        result = svc.get_interface_count_map(sample_lab.id)
        # max_ports=4 < eth10=10, so R1 should be 10
        assert result["R1"] == 10


# ---------------------------------------------------------------------------
# to_topology_yaml_for_host — link filtering
# ---------------------------------------------------------------------------


class TestToTopologyYamlForHostLinkFilter:

    def test_cross_host_links_excluded(self, test_db: Session, sample_lab: models.Lab):
        """Links spanning two hosts are excluded from per-host YAML."""
        _make_host(test_db, "h1")
        _make_host(test_db, "h2")
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1", host_id="h1", device="linux")
        n2 = _make_node(test_db, sample_lab.id, "n2", "R2", host_id="h2", device="linux")
        n3 = _make_node(test_db, sample_lab.id, "n3", "R3", host_id="h1", device="linux")
        _make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth1")  # cross-host
        _make_link(test_db, sample_lab.id, n1.id, "eth2", n3.id, "eth1")  # same-host
        test_db.commit()

        svc = TopologyService(test_db)
        with patch("app.topology.graph_to_topology_yaml") as mock_yaml:
            mock_yaml.return_value = "name: lab\n"
            svc.to_topology_yaml_for_host(sample_lab.id, "h1")

        filtered_graph = mock_yaml.call_args[0][0]
        # h1 has R1 and R3
        assert len(filtered_graph.nodes) == 2
        node_names = {n.name for n in filtered_graph.nodes}
        assert node_names == {"R1", "R3"}
        # Only the same-host link should be included
        assert len(filtered_graph.links) == 1


# ---------------------------------------------------------------------------
# get_reserved_interfaces_for_host — target-side reservation
# ---------------------------------------------------------------------------


class TestReservedInterfacesTargetSide:

    def test_target_endpoint_on_host_reserved(self, test_db: Session, sample_lab: models.Lab):
        """When target node is on the queried host, target interface is reserved."""
        _make_host(test_db, "h1")
        _make_host(test_db, "h2")
        n1 = _make_node(test_db, sample_lab.id, "n1", "R1", host_id="h2")
        n2 = _make_node(test_db, sample_lab.id, "n2", "R2", host_id="h1")
        _make_link(test_db, sample_lab.id, n1.id, "eth1", n2.id, "eth3")
        test_db.commit()

        svc = TopologyService(test_db)
        reserved = svc.get_reserved_interfaces_for_host(sample_lab.id, "h1")
        assert ("R2", "eth3") in reserved
        assert ("R1", "eth1") not in reserved

    def test_no_links_gives_empty_reserved(self, test_db: Session, sample_lab: models.Lab):
        _make_host(test_db, "h1")
        _make_node(test_db, sample_lab.id, "n1", "R1", host_id="h1")
        test_db.commit()

        svc = TopologyService(test_db)
        reserved = svc.get_reserved_interfaces_for_host(sample_lab.id, "h1")
        assert len(reserved) == 0
