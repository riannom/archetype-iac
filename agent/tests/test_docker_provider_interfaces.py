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
    monkeypatch.setattr("agent.providers.docker.get_container_config", lambda **_kwargs: runtime)

    node = TopologyNode(name="n1", kind="linux", cpu=2, cpu_limit=50)
    cfg = provider._create_container_config(node, "lab1", tmp_path, interface_count=0)

    assert cfg["nano_cpus"] == 1_000_000_000
