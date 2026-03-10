from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from docker.errors import NotFound

from agent.config import settings
from agent.providers.docker import DockerProvider, ParsedTopology, TopologyNode


def test_create_containers_attaches_per_node_interface_count(monkeypatch, tmp_path):
    original_enable_ovs_plugin = settings.enable_ovs_plugin
    original_enable_ovs = settings.enable_ovs
    settings.enable_ovs_plugin = True
    settings.enable_ovs = True

    provider = DockerProvider()

    # Stub Docker client and container creation
    container = SimpleNamespace(id="cid123", name="archetype-lab1-n1", status="created")
    docker_client = MagicMock()
    docker_client.containers.get.side_effect = NotFound("not found")
    docker_client.containers.create.return_value = container
    provider._docker = docker_client

    # Avoid real network creation and track attachment call
    provider._create_lab_networks = AsyncMock(return_value={})
    provider._attach_container_to_networks = AsyncMock(return_value=[])

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))

    # Keep container config minimal to avoid vendor dependencies
    def _fake_container_config(node, lab_id, workspace, interface_count=0):
        return {
            "image": "alpine:latest",
            "name": f"archetype-{lab_id}-{node.name}",
            "hostname": node.name,
            "environment": {},
            "labels": {},
            "detach": True,
            "tty": True,
            "stdin_open": True,
            "restart_policy": {"Name": "no"},
        }

    provider._create_container_config = _fake_container_config  # type: ignore[method-assign]

    topology = ParsedTopology(
        name="lab",
        nodes={
            "n1": TopologyNode(name="n1", kind="linux", interface_count=3),
        },
        links=[],
    )

    try:
        asyncio.run(provider._create_containers(topology, "lab1", tmp_path))
    finally:
        settings.enable_ovs_plugin = original_enable_ovs_plugin
        settings.enable_ovs = original_enable_ovs

    provider._attach_container_to_networks.assert_awaited_once()
    _, kwargs = provider._attach_container_to_networks.call_args
    assert kwargs["interface_count"] == 2


def test_create_lab_networks_starts_at_eth0(monkeypatch):
    """_create_lab_networks(lab_id, max_interfaces=2) creates 3 networks: eth0, eth1, eth2."""
    provider = DockerProvider()

    docker_client = MagicMock()
    docker_client.networks.get.side_effect = NotFound("missing")
    docker_client.networks.create = MagicMock()
    provider._docker = docker_client

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)
    # Patch module-level prune function (provider._prune_legacy_lab_networks
    # is an instance method, but create_lab_networks calls the standalone)
    monkeypatch.setattr(
        "agent.providers.docker_networks.prune_legacy_lab_networks",
        AsyncMock(return_value=0),
    )

    result = asyncio.run(provider._create_lab_networks("lab1", max_interfaces=2))

    assert docker_client.networks.create.call_count == 3
    created_names = [
        call.kwargs["name"] for call in docker_client.networks.create.call_args_list
    ]
    assert created_names[0].endswith("-eth0")
    assert created_names[1].endswith("-eth1")
    assert created_names[2].endswith("-eth2")
    assert set(result.keys()) == {"eth0", "eth1", "eth2"}


def _make_create_containers_provider(monkeypatch, tmp_path, *, management_interface, reserved_nics):
    """Shared setup for _create_containers NIC layout tests.

    Returns (provider, topology) with a single node (kind='testdev', interface_count=3).
    """
    original_enable_ovs_plugin = settings.enable_ovs_plugin
    original_enable_ovs = settings.enable_ovs
    settings.enable_ovs_plugin = True
    settings.enable_ovs = True

    provider = DockerProvider()

    container = SimpleNamespace(id="cid123", name="archetype-lab1-n1", status="created")
    docker_client = MagicMock()
    docker_client.containers.get.side_effect = NotFound("not found")
    docker_client.containers.create.return_value = container
    provider._docker = docker_client

    provider._create_lab_networks = AsyncMock(return_value={})
    provider._attach_container_to_networks = AsyncMock(return_value=[])

    async def _sync_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync_to_thread)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))

    def _fake_container_config(node, lab_id, workspace, interface_count=0):
        return {
            "image": "alpine:latest",
            "name": f"archetype-{lab_id}-{node.name}",
            "hostname": node.name,
            "environment": {},
            "labels": {},
            "detach": True,
            "tty": True,
            "stdin_open": True,
            "restart_policy": {"Name": "no"},
        }

    provider._create_container_config = _fake_container_config  # type: ignore[method-assign]

    mock_config = SimpleNamespace(
        management_interface=management_interface,
        reserved_nics=reserved_nics,
    )
    monkeypatch.setattr(
        "agent.providers.docker.get_config_by_device",
        lambda kind: mock_config,
    )

    topology = ParsedTopology(
        name="lab",
        nodes={"n1": TopologyNode(name="n1", kind="testdev", interface_count=3)},
        links=[],
    )

    return provider, topology, (original_enable_ovs_plugin, original_enable_ovs)


def test_create_containers_nic_layout_with_management(monkeypatch, tmp_path):
    """Devices with management_interface use eth0 as first network, data starts at eth1."""
    provider, topology, originals = _make_create_containers_provider(
        monkeypatch, tmp_path,
        management_interface="Management0",
        reserved_nics=0,
    )
    try:
        asyncio.run(provider._create_containers(topology, "lab1", tmp_path))
    finally:
        settings.enable_ovs_plugin, settings.enable_ovs = originals

    # First network should be eth0 (management)
    create_kwargs = provider._docker.containers.create.call_args
    config = create_kwargs.kwargs if create_kwargs.kwargs else create_kwargs[1]
    # containers.create is called via lambda, check the "network" key in config
    # The lambda wraps it: lambda cfg=config: self.docker.containers.create(**cfg)
    # So we need to check args[0] which is the lambda, or check the call
    # Actually with our mock of to_thread, the lambda is called directly
    # The container.create gets **cfg, so kwargs will have 'network'
    assert config["network"].endswith("-eth0")

    # Attachment: reserved=0 + data=3 → interface_count=3, start_index=1
    provider._attach_container_to_networks.assert_awaited_once()
    _, attach_kwargs = provider._attach_container_to_networks.call_args
    assert attach_kwargs["interface_count"] == 3  # reserved(0) + data(3)
    assert attach_kwargs["start_index"] == 1


def test_create_containers_nic_layout_with_reserved_nics(monkeypatch, tmp_path):
    """Devices with reserved_nics=2 (e.g., XRv9k) get correct attachment count."""
    provider, topology, originals = _make_create_containers_provider(
        monkeypatch, tmp_path,
        management_interface="MgmtEth0/RP0/CPU0/0",
        reserved_nics=2,
    )
    try:
        asyncio.run(provider._create_containers(topology, "lab1", tmp_path))
    finally:
        settings.enable_ovs_plugin, settings.enable_ovs = originals

    # First network is eth0 (management)
    config = provider._docker.containers.create.call_args.kwargs
    assert config["network"].endswith("-eth0")

    # Attachment: reserved=2 + data=3 → interface_count=5, start_index=1
    _, attach_kwargs = provider._attach_container_to_networks.call_args
    assert attach_kwargs["interface_count"] == 5
    assert attach_kwargs["start_index"] == 1


def test_create_containers_nic_layout_without_management(monkeypatch, tmp_path):
    """Devices without management_interface use eth1 as first network."""
    provider, topology, originals = _make_create_containers_provider(
        monkeypatch, tmp_path,
        management_interface=None,
        reserved_nics=0,
    )
    try:
        asyncio.run(provider._create_containers(topology, "lab1", tmp_path))
    finally:
        settings.enable_ovs_plugin, settings.enable_ovs = originals

    # First network is eth1 (no management)
    config = provider._docker.containers.create.call_args.kwargs
    assert config["network"].endswith("-eth1")

    # Attachment: max(3-1, 0) = 2, start_index=2
    _, attach_kwargs = provider._attach_container_to_networks.call_args
    assert attach_kwargs["interface_count"] == 2
    assert attach_kwargs["start_index"] == 2


def test_create_container_config_applies_cpu_limit(monkeypatch, tmp_path):
    provider = DockerProvider()

    runtime = SimpleNamespace(
        image="alpine:latest",
        hostname="n1",
        environment={},
        binds=[],
        capabilities=[],
        privileged=False,
        sysctls={},
        entrypoint=None,
        cmd=["sleep", "infinity"],
    )
    monkeypatch.setattr("agent.providers.docker_setup.get_container_config", lambda **_kwargs: runtime)

    node = TopologyNode(name="n1", kind="linux", cpu=2, cpu_limit=50)
    cfg = provider._create_container_config(node, "lab1", tmp_path, interface_count=0)

    assert cfg["nano_cpus"] == 1_000_000_000
