"""Tests for app.services.topology_deploy — graph to deploy topology conversion."""

from unittest.mock import MagicMock

import pytest

from app.schemas import TopologyGraph
from app.schemas.lab import GraphEndpoint, GraphLink, GraphNode
from app.services.topology_deploy import graph_to_deploy_topology


def _node(name, device="ceos", image=None, version=None, node_type="device",
          container_name=None, vars_=None, **hw):
    return GraphNode(
        id=f"id-{name}",
        name=name,
        device=device,
        image=image,
        version=version,
        node_type=node_type,
        container_name=container_name,
        vars=vars_,
        **hw,
    )


def _link(src_node, src_if, dst_node, dst_if):
    return GraphLink(endpoints=[
        GraphEndpoint(node=src_node, ifname=src_if),
        GraphEndpoint(node=dst_node, ifname=dst_if),
    ])


def _default_hw_specs():
    return {
        "memory": 2048,
        "cpu": 1,
        "cpu_limit": None,
        "disk_driver": None,
        "nic_driver": None,
        "machine_type": None,
        "libvirt_driver": None,
        "efi_boot": None,
        "efi_vars": None,
        "readiness_probe": None,
        "readiness_pattern": None,
        "readiness_timeout": None,
        "max_ports": None,
    }


@pytest.fixture(autouse=True)
def _mock_deps(monkeypatch):
    """Mock heavy dependencies so tests stay pure."""
    monkeypatch.setattr(
        "app.services.topology_deploy.normalize_interface",
        lambda iface, **kw: iface.lower() if iface else "",
    )
    monkeypatch.setattr(
        "app.services.topology_deploy.resolve_device_kind",
        lambda device: device or "linux",
    )
    monkeypatch.setattr(
        "app.services.topology_deploy.resolve_node_image",
        lambda device, kind, explicit=None, version=None: explicit or f"{kind}:latest",
    )
    monkeypatch.setattr(
        "app.services.topology_deploy.resolve_effective_max_ports",
        lambda device_id, kind, image_ref=None, version=None: 8,
    )
    mock_service = MagicMock()
    mock_service.resolve_hardware_specs.return_value = _default_hw_specs()
    monkeypatch.setattr(
        "app.services.device_service.get_device_service",
        lambda: mock_service,
    )


class TestGraphToDeployTopology:
    def test_single_node_no_links(self):
        graph = TopologyGraph(nodes=[_node("R1")], links=[])
        result = graph_to_deploy_topology(graph)
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["name"] == "R1"
        assert result["nodes"][0]["display_name"] == "R1"
        assert result["nodes"][0]["kind"] == "ceos"
        assert result["nodes"][0]["image"] == "ceos:latest"
        assert result["links"] == []

    def test_external_nodes_skipped(self):
        graph = TopologyGraph(
            nodes=[_node("R1"), _node("ext1", node_type="external")],
            links=[],
        )
        result = graph_to_deploy_topology(graph)
        assert len(result["nodes"]) == 1
        assert result["nodes"][0]["name"] == "R1"

    def test_explicit_image_used(self):
        graph = TopologyGraph(
            nodes=[_node("R1", image="my-ceos:4.30")],
            links=[],
        )
        result = graph_to_deploy_topology(graph)
        assert result["nodes"][0]["image"] == "my-ceos:4.30"

    def test_container_name_override(self):
        graph = TopologyGraph(
            nodes=[_node("R1", container_name="custom-r1")],
            links=[],
        )
        result = graph_to_deploy_topology(graph)
        assert result["nodes"][0]["name"] == "custom-r1"
        assert result["nodes"][0]["display_name"] == "R1"

    def test_node_vars_extraction(self):
        vars_ = {
            "env": {"VAR1": "val1"},
            "binds": ["/host:/container"],
            "ports": ["8080:80"],
            "exec": ["ip route add 10.0.0.0/8 via 10.1.1.1"],
            "startup-config": "hostname R1\n",
        }
        graph = TopologyGraph(nodes=[_node("R1", vars_=vars_)], links=[])
        result = graph_to_deploy_topology(graph)
        node = result["nodes"][0]
        assert node["env"] == {"VAR1": "val1"}
        assert node["binds"] == ["/host:/container"]
        assert node["ports"] == ["8080:80"]
        assert node["exec_cmds"] == ["ip route add 10.0.0.0/8 via 10.1.1.1"]
        assert node["startup_config"] == "hostname R1\n"

    def test_links_resolved(self):
        nodes = [_node("R1"), _node("R2")]
        links = [_link("id-R1", "eth1", "id-R2", "eth1")]
        graph = TopologyGraph(nodes=nodes, links=links)
        result = graph_to_deploy_topology(graph)
        assert len(result["links"]) == 1
        link = result["links"][0]
        assert link["source_node"] == "R1"
        assert link["target_node"] == "R2"
        assert link["source_interface"] == "eth1"
        assert link["target_interface"] == "eth1"

    def test_link_node_id_resolution(self):
        """Link endpoints using graph node IDs resolve to container names."""
        nodes = [_node("R1", container_name="clab-r1"), _node("R2", container_name="clab-r2")]
        links = [_link("id-R1", "eth1", "id-R2", "eth2")]
        graph = TopologyGraph(nodes=nodes, links=links)
        result = graph_to_deploy_topology(graph)
        link = result["links"][0]
        assert link["source_node"] == "clab-r1"
        assert link["target_node"] == "clab-r2"

    def test_no_image_raises(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.topology_deploy.resolve_node_image",
            lambda device, kind, explicit=None, version=None: None,
        )
        graph = TopologyGraph(nodes=[_node("R1")], links=[])
        with pytest.raises(ValueError, match="No image found"):
            graph_to_deploy_topology(graph)

    def test_interface_count_from_links(self, monkeypatch):
        """Interface count should be at least max link index."""
        monkeypatch.setattr(
            "app.services.topology_deploy.normalize_interface",
            lambda iface, **kw: iface,
        )
        monkeypatch.setattr(
            "app.services.topology_deploy.resolve_effective_max_ports",
            lambda device_id, kind, image_ref=None, version=None: 4,
        )
        nodes = [_node("R1"), _node("R2")]
        links = [_link("id-R1", "eth10", "id-R2", "eth1")]
        graph = TopologyGraph(nodes=nodes, links=links)
        result = graph_to_deploy_topology(graph)
        r1 = [n for n in result["nodes"] if n["name"] == "R1"][0]
        assert r1["interface_count"] >= 10

    def test_interface_count_override_from_vars(self, monkeypatch):
        vars_ = {"interface_count": 24}
        graph = TopologyGraph(nodes=[_node("R1", vars_=vars_)], links=[])
        result = graph_to_deploy_topology(graph)
        assert result["nodes"][0]["interface_count"] == 24

    def test_hardware_specs_populated(self, monkeypatch):
        mock_service = MagicMock()
        mock_service.resolve_hardware_specs.return_value = {
            "memory": 8192,
            "cpu": 4,
            "cpu_limit": 80,
            "disk_driver": "virtio",
            "nic_driver": "e1000",
            "machine_type": None,
            "libvirt_driver": None,
            "efi_boot": True,
            "efi_vars": "stateless",
            "readiness_probe": "log_pattern",
            "readiness_pattern": r"login:",
            "readiness_timeout": 600,
            "max_ports": None,
        }
        monkeypatch.setattr(
            "app.services.device_service.get_device_service",
            lambda: mock_service,
        )
        graph = TopologyGraph(nodes=[_node("R1", device="n9kv")], links=[])
        result = graph_to_deploy_topology(graph)
        node = result["nodes"][0]
        assert node["memory"] == 8192
        assert node["cpu"] == 4
        assert node["efi_boot"] is True
        assert node["readiness_probe"] == "log_pattern"

    def test_per_node_hw_overrides(self, monkeypatch):
        mock_service = MagicMock()
        mock_service.resolve_hardware_specs.return_value = {
            "memory": 16384,
            "cpu": 8,
            "cpu_limit": None,
            "disk_driver": None,
            "nic_driver": None,
            "machine_type": None,
            "libvirt_driver": None,
            "efi_boot": None,
            "efi_vars": None,
            "readiness_probe": None,
            "readiness_pattern": None,
            "readiness_timeout": None,
            "max_ports": None,
        }
        monkeypatch.setattr(
            "app.services.device_service.get_device_service",
            lambda: mock_service,
        )
        graph = TopologyGraph(
            nodes=[_node("R1", memory=16384, cpu=8)],
            links=[],
        )
        graph_to_deploy_topology(graph)
        # The per-node hw values should have been passed to resolve_hardware_specs
        call_args = mock_service.resolve_hardware_specs.call_args
        per_node = call_args[0][1]  # second positional arg
        assert per_node["memory"] == 16384
        assert per_node["cpu"] == 8

    def test_multiple_nodes_and_links(self):
        nodes = [_node("R1"), _node("R2"), _node("R3")]
        links = [
            _link("id-R1", "eth1", "id-R2", "eth1"),
            _link("id-R2", "eth2", "id-R3", "eth1"),
        ]
        graph = TopologyGraph(nodes=nodes, links=links)
        result = graph_to_deploy_topology(graph)
        assert len(result["nodes"]) == 3
        assert len(result["links"]) == 2

    def test_link_with_wrong_endpoint_count_skipped(self):
        nodes = [_node("R1")]
        bad_link = GraphLink(endpoints=[GraphEndpoint(node="id-R1", ifname="eth1")])
        graph = TopologyGraph(nodes=nodes, links=[bad_link])
        result = graph_to_deploy_topology(graph)
        assert result["links"] == []

    def test_null_device_defaults_to_linux(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.topology_deploy.resolve_device_kind",
            lambda device: "linux" if not device else device,
        )
        graph = TopologyGraph(nodes=[_node("host1", device=None)], links=[])
        result = graph_to_deploy_topology(graph)
        assert result["nodes"][0]["kind"] == "linux"

    def test_empty_graph(self):
        graph = TopologyGraph(nodes=[], links=[])
        result = graph_to_deploy_topology(graph)
        assert result == {"nodes": [], "links": []}
