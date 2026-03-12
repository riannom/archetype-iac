from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from docker.errors import APIError, ImageNotFound

from agent.providers import docker as docker_mod
from agent.providers.base import NodeStatus
from agent.providers.docker import (
    LABEL_LAB_ID,
    LABEL_NODE_DISPLAY_NAME,
    LABEL_NODE_NAME,
    LABEL_PROVIDER,
    DockerProvider,
    ParsedTopology,
    TopologyLink,
    TopologyNode,
)
from agent.schemas import DeployLink, DeployNode, DeployTopology


def _run(coro):
    return asyncio.run(coro)


def _api_error(status_code: int, message: str = "api error") -> APIError:
    response = MagicMock()
    response.status_code = status_code
    return APIError(message, response=response)


def test_topology_node_and_parsed_topology_log_name():
    node = TopologyNode(name="leaf-1", kind="ceos", display_name="Leaf-1")
    topology = ParsedTopology(
        name="lab",
        nodes={"leaf-1": node},
        links=[],
    )

    assert node.log_name() == "Leaf-1(leaf-1)"
    assert topology.log_name("leaf-1") == "Leaf-1(leaf-1)"
    assert topology.log_name("missing") == "missing"


def test_provider_basic_properties_and_lazy_initialization(monkeypatch):
    provider = DockerProvider()

    fake_docker = object()
    fake_local = object()
    fake_ovs = object()
    fake_plugin = object()

    monkeypatch.setattr(docker_mod.settings, "docker_client_timeout", 17)
    monkeypatch.setattr(docker_mod.docker, "from_env", lambda timeout: fake_docker if timeout == 17 else None)
    monkeypatch.setattr(docker_mod, "get_local_manager", lambda: fake_local)
    monkeypatch.setattr(docker_mod, "get_ovs_manager", lambda: fake_ovs)
    monkeypatch.setattr(docker_mod, "get_docker_ovs_plugin", lambda: fake_plugin)

    monkeypatch.setattr(docker_mod.settings, "enable_ovs", True)
    monkeypatch.setattr(docker_mod.settings, "enable_ovs_plugin", True)

    assert provider.name == "docker"
    assert provider.display_name == "Docker (Native)"
    assert provider.docker is fake_docker
    assert provider.docker is fake_docker
    assert provider.local_network is fake_local
    assert provider.local_network is fake_local
    assert provider.ovs_manager is fake_ovs
    assert provider.ovs_manager is fake_ovs
    assert provider.ovs_plugin is fake_plugin
    assert provider.use_ovs is True
    assert provider.use_ovs_plugin is True

    monkeypatch.setattr(docker_mod.settings, "enable_ovs", False)
    assert provider.use_ovs is False
    assert provider.use_ovs_plugin is False


def test_is_transient_docker_error_status_and_message_markers():
    provider = DockerProvider()

    assert provider._is_transient_docker_error(_api_error(503, "service unavailable")) is True
    assert provider._is_transient_docker_error(_api_error(400, "bad request")) is False
    assert provider._is_transient_docker_error(RuntimeError("Connection reset by peer")) is True
    assert provider._is_transient_docker_error(RuntimeError("permission denied")) is False


def test_lab_network_kwargs_and_network_match():
    provider = DockerProvider()
    kwargs = provider._lab_network_create_kwargs("lab1-eth1", "lab1", "eth1")

    assert kwargs["name"] == "lab1-eth1"
    assert kwargs["driver"] == "archetype-ovs"
    assert kwargs["options"] == {"lab_id": "lab1", "interface_name": "eth1"}
    assert kwargs["labels"][LABEL_LAB_ID] == "lab1"
    assert kwargs["labels"][LABEL_PROVIDER] == "docker"
    assert kwargs["labels"]["archetype.type"] == "lab-interface"

    network = SimpleNamespace(
        attrs={
            "Driver": "archetype-ovs",
            "Labels": {
                LABEL_LAB_ID: "lab1",
                LABEL_PROVIDER: "docker",
                "archetype.type": "lab-interface",
            },
            "Options": {"lab_id": "lab1", "interface_name": "eth1"},
        }
    )
    assert provider._network_matches_lab_spec(network, "lab1", "eth1") is True

    network.attrs["Options"]["interface_name"] = "eth9"
    assert provider._network_matches_lab_spec(network, "lab1", "eth1") is False


def test_calculate_required_interfaces_uses_largest_link_index_plus_buffer():
    provider = DockerProvider()
    topology = ParsedTopology(
        name="lab",
        nodes={
            "a": TopologyNode(name="a", kind="linux", interface_count=3),
            "b": TopologyNode(name="b", kind="linux"),
        },
        links=[
            TopologyLink(endpoints=["a:eth7", "b:eth2"]),
            TopologyLink(endpoints=["a:Ethernet12", "b:mgmt"]),
        ],
    )

    assert provider._calculate_required_interfaces(topology) == 16


@pytest.mark.parametrize(
    ("docker_status", "expected"),
    [
        ("running", NodeStatus.RUNNING),
        ("created", NodeStatus.PENDING),
        ("exited", NodeStatus.STOPPED),
        ("dead", NodeStatus.STOPPED),
        ("paused", NodeStatus.STOPPED),
        ("restarting", NodeStatus.STARTING),
        ("mystery", NodeStatus.UNKNOWN),
    ],
)
def test_get_container_status_maps_known_states(docker_status: str, expected: NodeStatus):
    provider = DockerProvider()
    container = SimpleNamespace(status=docker_status)
    assert provider._get_container_status(container) is expected


def test_get_container_ips_extracts_and_handles_errors():
    provider = DockerProvider()
    container = SimpleNamespace(
        attrs={
            "NetworkSettings": {
                "Networks": {
                    "n1": {"IPAddress": "10.0.0.5"},
                    "n2": {"IPAddress": ""},
                    "n3": {},
                }
            }
        }
    )
    assert provider._get_container_ips(container) == ["10.0.0.5"]

    class _BrokenContainer:
        @property
        def attrs(self):
            raise RuntimeError("boom")

    assert provider._get_container_ips(_BrokenContainer()) == []


def test_node_from_container_requires_name_label_and_handles_image_fallback():
    provider = DockerProvider()

    missing_name = SimpleNamespace(labels={}, status="running")
    assert provider._node_from_container(missing_name) is None

    tagged = SimpleNamespace(
        labels={LABEL_NODE_NAME: "r1"},
        status="running",
        short_id="abc123",
        id="container-id-abc123",
        image=SimpleNamespace(tags=["vendor/image:1"], id="sha256:1234567890abcdef"),
        attrs={"NetworkSettings": {"Networks": {"n1": {"IPAddress": "192.0.2.10"}}}},
    )
    node = provider._node_from_container(tagged)
    assert node is not None
    assert node.name == "r1"
    assert node.image == "vendor/image:1"
    assert node.ip_addresses == ["192.0.2.10"]

    untagged = SimpleNamespace(
        labels={LABEL_NODE_NAME: "r2"},
        status="running",
        short_id="def456",
        id="container-id-def456",
        image=SimpleNamespace(tags=[], id="sha256:1234567890abcdef"),
        attrs={"NetworkSettings": {"Networks": {}}},
    )
    node2 = provider._node_from_container(untagged)
    assert node2 is not None
    assert node2.image == "sha256:12345"


def test_topology_from_json_builds_nodes_and_link_endpoints():
    provider = DockerProvider()
    deploy_topology = DeployTopology(
        nodes=[
            DeployNode(name="r1", display_name="R1", kind="linux", interface_count=2),
            DeployNode(name="r2", kind="linux", interface_count=1),
        ],
        links=[
            DeployLink(
                source_node="r1",
                source_interface="eth1",
                target_node="r2",
                target_interface="eth2",
            )
        ],
    )

    parsed = provider._topology_from_json(deploy_topology)
    assert set(parsed.nodes.keys()) == {"r1", "r2"}
    assert parsed.log_name("r1") == "R1(r1)"
    assert parsed.links[0].endpoints == ["r1:eth1", "r2:eth2"]


@pytest.mark.asyncio
async def test_ensure_directories_uses_to_thread_for_ceos_and_cjunos(monkeypatch, tmp_path):
    provider = DockerProvider()
    topology = ParsedTopology(
        name="lab",
        nodes={
            "ceos1": TopologyNode(name="ceos1", kind="ceos"),
            "cj1": TopologyNode(name="cj1", kind="juniper_cjunos"),
            "host1": TopologyNode(name="host1", kind="linux"),
        },
        links=[],
    )

    ceos_calls: list[tuple] = []
    cj_calls: list[tuple] = []
    to_thread_calls: list[str] = []

    def _setup_ceos(node_name, node, workspace):
        ceos_calls.append((node_name, node.kind, workspace))

    def _setup_cj(node_name, node, workspace):
        cj_calls.append((node_name, node.kind, workspace))

    async def _sync_to_thread(func, *args, **kwargs):
        to_thread_calls.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(provider, "_setup_ceos_directories", _setup_ceos)
    monkeypatch.setattr(provider, "_setup_cjunos_directories", _setup_cj)
    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)

    await provider._ensure_directories(topology, tmp_path, use_thread=True)

    assert to_thread_calls == ["_setup_ceos", "_setup_cj"]
    assert ceos_calls and ceos_calls[0][0] == "ceos1"
    assert cj_calls and cj_calls[0][0] == "cj1"


def test_validate_images_handles_file_and_docker_images(monkeypatch, tmp_path):
    provider = DockerProvider()
    missing_qcow = tmp_path / "missing.qcow2"
    existing_img = tmp_path / "present.img"
    existing_img.write_text("ok")

    topology = ParsedTopology(
        name="lab",
        nodes={
            "n_file_missing": TopologyNode(name="n_file_missing", kind="linux", image=str(missing_qcow)),
            "n_file_present": TopologyNode(name="n_file_present", kind="linux", image=str(existing_img)),
            "n_docker_ok": TopologyNode(name="n_docker_ok", kind="linux", image="image:ok"),
            "n_docker_missing": TopologyNode(name="n_docker_missing", kind="linux", image="image:missing"),
            "n_docker_apierr": TopologyNode(name="n_docker_apierr", kind="linux", image="image:apierr"),
            "n_vendor_default": TopologyNode(name="n_vendor_default", kind="default-kind", image=None),
        },
        links=[],
    )

    docker_client = MagicMock()

    def _get_image(name: str):
        if name == "image:missing":
            raise ImageNotFound("not found")
        if name == "image:apierr":
            response = MagicMock()
            response.status_code = 500
            raise APIError("docker api error", response=response)
        return object()

    docker_client.images.get.side_effect = _get_image
    provider._docker = docker_client
    monkeypatch.setattr(
        docker_mod,
        "get_config_by_device",
        lambda kind: SimpleNamespace(default_image="image:ok") if kind == "default-kind" else None,
    )

    missing = provider._validate_images(topology)
    assert ("n_file_missing", str(missing_qcow)) in missing
    assert ("n_docker_missing", "image:missing") in missing
    assert ("n_file_present", str(existing_img)) not in missing
    assert ("n_docker_apierr", "image:apierr") not in missing
    assert ("n_vendor_default", "image:ok") not in missing


def test_create_container_config_sets_readiness_labels_and_entrypoint_list(monkeypatch, tmp_path):
    provider = DockerProvider()
    runtime = SimpleNamespace(
        image="test:latest",
        hostname="n1",
        environment={},
        binds=[],
        capabilities=[],
        privileged=False,
        sysctls={},
        entrypoint=["/sbin/custom-init"],
        cmd=None,
    )
    monkeypatch.setattr("agent.providers.docker_setup.get_container_config", lambda **_kwargs: runtime)

    node = TopologyNode(
        name="n1",
        kind="linux",
        readiness_probe="log_pattern",
        readiness_pattern="READY",
        readiness_timeout=60,
    )
    cfg = provider._create_container_config(node, "lab1", tmp_path, interface_count=2)

    labels = cfg["labels"]
    assert labels["archetype.readiness_probe"] == "log_pattern"
    assert labels["archetype.readiness_pattern"] == "READY"
    assert labels["archetype.readiness_timeout"] == "60"
    assert cfg["entrypoint"] == ["/sbin/custom-init"]


def test_create_container_config_defaults_command_when_no_entrypoint_or_cmd(monkeypatch, tmp_path):
    provider = DockerProvider()
    runtime = SimpleNamespace(
        image="test:latest",
        hostname="n1",
        environment={},
        binds=[],
        capabilities=[],
        privileged=False,
        sysctls={},
        entrypoint=None,
        cmd=None,
    )
    monkeypatch.setattr("agent.providers.docker_setup.get_container_config", lambda **_kwargs: runtime)

    node = TopologyNode(name="n1", kind="linux")
    cfg = provider._create_container_config(node, "lab1", tmp_path, interface_count=0)
    assert cfg["command"] == ["sleep", "infinity"]


def test_setup_ceos_directories_writes_topology_and_extracted_startup_config(tmp_path):
    provider = DockerProvider()

    node_topology = TopologyNode(name="n_topology", kind="ceos", startup_config="hostname FROM_TOPOLOGY\nend")
    provider._setup_ceos_directories("n_topology", node_topology, tmp_path)
    topology_cfg = tmp_path / "configs" / "n_topology" / "flash" / "startup-config"
    assert topology_cfg.read_text() == "hostname FROM_TOPOLOGY\nend"

    extracted_dir = tmp_path / "configs" / "n_extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)
    (extracted_dir / "startup-config").write_text("hostname FROM_EXTRACTED\nend")
    node_extracted = TopologyNode(name="n_extracted", kind="ceos")
    provider._setup_ceos_directories("n_extracted", node_extracted, tmp_path)
    extracted_cfg = tmp_path / "configs" / "n_extracted" / "flash" / "startup-config"
    assert extracted_cfg.read_text() == "hostname FROM_EXTRACTED\nend"
