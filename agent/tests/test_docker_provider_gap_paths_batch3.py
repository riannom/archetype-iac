from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from docker.errors import NotFound

from agent.config import settings
from agent.providers import docker as docker_mod
from agent.providers.base import StatusResult
from agent.providers.docker import DockerProvider, ParsedTopology, TopologyNode
from agent.schemas import DeployNode, DeployTopology


def _make_sync_to_thread(monkeypatch):
    async def _sync(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _sync)


@pytest.mark.asyncio
async def test_create_containers_legacy_mode_handles_running_and_stopped(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "enable_ovs", False)
    monkeypatch.setattr(settings, "enable_ovs_plugin", False)
    _make_sync_to_thread(monkeypatch)

    provider = DockerProvider()
    topology = ParsedTopology(
        name="lab",
        nodes={
            "r1": TopologyNode(name="r1", kind="linux", interface_count=1),
            "r2": TopologyNode(name="r2", kind="linux", interface_count=1),
        },
        links=[],
    )

    existing_running = SimpleNamespace(status="running")
    existing_stopped = SimpleNamespace(status="exited", remove=MagicMock())
    created_r2 = SimpleNamespace(id="cid-r2", name="archetype-lab1-r2")

    docker_client = MagicMock()
    docker_client.containers.get.side_effect = [existing_running, existing_stopped]
    docker_client.containers.create.return_value = created_r2
    provider._docker = docker_client

    provider._create_container_config = lambda *_a, **_k: {"image": "alpine", "name": "n", "labels": {}}  # type: ignore[method-assign]
    monkeypatch.setattr(provider, "_calculate_required_interfaces", lambda _top: 1)
    monkeypatch.setattr(provider, "_count_node_interfaces", lambda *_a, **_k: 1)

    containers = await provider._create_containers(topology, "lab1", tmp_path)

    assert containers["r1"] is existing_running
    assert containers["r2"] is created_r2
    existing_stopped.remove.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_create_containers_cleanup_logs_network_cleanup_count(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "enable_ovs", True)
    monkeypatch.setattr(settings, "enable_ovs_plugin", True)
    _make_sync_to_thread(monkeypatch)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))

    provider = DockerProvider()
    topology = ParsedTopology(
        name="lab",
        nodes={
            "r1": TopologyNode(name="r1", kind="linux", interface_count=1),
            "r2": TopologyNode(name="r2", kind="linux", interface_count=1),
        },
        links=[],
    )

    first_container = SimpleNamespace(id="cid-r1", name="archetype-lab1-r1", remove=MagicMock())
    docker_client = MagicMock()
    docker_client.containers.get.side_effect = [NotFound("x"), NotFound("x")]
    docker_client.containers.create.side_effect = [first_container, RuntimeError("create fail")]
    provider._docker = docker_client

    provider._create_lab_networks = AsyncMock(return_value={})
    provider._delete_lab_networks = AsyncMock(return_value=4)
    provider._attach_container_to_networks = AsyncMock(return_value=[])
    provider._create_container_config = lambda *_a, **_k: {"image": "alpine", "name": "n", "labels": {}}  # type: ignore[method-assign]
    monkeypatch.setattr(provider, "_calculate_required_interfaces", lambda _top: 1)
    monkeypatch.setattr(provider, "_count_node_interfaces", lambda *_a, **_k: 1)
    monkeypatch.setattr(docker_mod, "get_config_by_device", lambda _kind: None)

    with pytest.raises(RuntimeError, match="create fail"):
        await provider._create_containers(topology, "lab1", tmp_path)

    first_container.remove.assert_called_once_with(force=True, v=True)
    provider._delete_lab_networks.assert_awaited_once_with("lab1")


@pytest.mark.asyncio
async def test_start_containers_ceos_stagger_and_failure(monkeypatch):
    monkeypatch.setattr(settings, "enable_ovs", True)
    monkeypatch.setattr(settings, "enable_ovs_plugin", False)
    _make_sync_to_thread(monkeypatch)
    sleep_calls: list[float] = []

    async def _sleep(delay: float):
        sleep_calls.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _sleep)

    provider = DockerProvider()
    provider._ovs_manager = MagicMock()
    provider._ovs_manager.initialize = AsyncMock(return_value=None)
    provider._ovs_manager._initialized = True
    provider._provision_ovs_interfaces = AsyncMock(return_value=1)

    topology = ParsedTopology(
        name="lab",
        nodes={
            "n1": TopologyNode(name="n1", kind="ceos", interface_count=1),
            "n2": TopologyNode(name="n2", kind="ceos", interface_count=1),
        },
        links=[],
    )
    c1 = SimpleNamespace(name="archetype-lab1-n1", status="created", start=MagicMock())
    c2 = SimpleNamespace(name="archetype-lab1-n2", status="created", start=MagicMock(side_effect=RuntimeError("boom")))

    cfg = SimpleNamespace(port_naming="eth", port_start_index=1, max_ports=2, provision_interfaces=True)
    monkeypatch.setattr(docker_mod, "get_config_by_device", lambda _kind: cfg)

    failed = await provider._start_containers({"n1": c1, "n2": c2}, topology, "lab1")

    assert failed == ["n2"]
    assert 5 in sleep_calls  # cEOS stagger delay
    provider._provision_ovs_interfaces.assert_awaited()


@pytest.mark.asyncio
async def test_start_containers_dummy_interface_fallback(monkeypatch):
    monkeypatch.setattr(settings, "enable_ovs", False)
    monkeypatch.setattr(settings, "enable_ovs_plugin", False)
    _make_sync_to_thread(monkeypatch)

    provider = DockerProvider()
    provider._local_network = MagicMock()
    provider._local_network.provision_dummy_interfaces = AsyncMock(return_value=None)

    topology = ParsedTopology(
        name="lab",
        nodes={"n1": TopologyNode(name="n1", kind="linux", interface_count=1)},
        links=[],
    )
    c1 = SimpleNamespace(name="archetype-lab1-n1", status="running", start=MagicMock())
    cfg = SimpleNamespace(port_naming="eth", port_start_index=1, max_ports=3, provision_interfaces=True)
    monkeypatch.setattr(docker_mod, "get_config_by_device", lambda _kind: cfg)

    failed = await provider._start_containers({"n1": c1}, topology, "lab1")
    assert failed == []
    provider._local_network.provision_dummy_interfaces.assert_awaited_once()


@pytest.mark.asyncio
async def test_provision_ovs_interfaces_counts_successes(monkeypatch):
    provider = DockerProvider()
    provider._ovs_manager = MagicMock()
    provider._ovs_manager.provision_interface = AsyncMock(
        side_effect=[None, RuntimeError("fail"), None]
    )

    count = await provider._provision_ovs_interfaces(
        container_name="c1",
        interface_prefix="eth",
        start_index=1,
        count=3,
        lab_id="lab1",
    )
    assert count == 2


def test_topology_from_json_applies_ceos_interface_fallback(monkeypatch):
    provider = DockerProvider()
    cfg = SimpleNamespace(max_ports=8)
    monkeypatch.setattr(docker_mod, "is_ceos_kind", lambda kind: kind == "ceos")
    monkeypatch.setattr(docker_mod, "get_config_by_device", lambda _kind: cfg)

    topo = DeployTopology(nodes=[DeployNode(name="n1", kind="ceos", interface_count=0)], links=[])
    parsed = provider._topology_from_json(topo)
    assert parsed.nodes["n1"].interface_count == 8


def test_deploy_no_topology_and_empty_nodes(tmp_path):
    provider = DockerProvider()
    out_none = asyncio.run(provider.deploy("lab1", None, tmp_path))
    assert out_none.success is False
    assert "No topology provided" in (out_none.error or "")

    out_empty = asyncio.run(provider.deploy("lab1", DeployTopology(nodes=[], links=[]), tmp_path))
    assert out_empty.success is False
    assert "No nodes found" in (out_empty.error or "")


def test_deploy_logs_recovered_network_state(monkeypatch, tmp_path):
    provider = DockerProvider()
    provider._recover_stale_network = AsyncMock(return_value={"n1": [100]})
    provider._validate_images = MagicMock(return_value=[])
    provider._ensure_directories = AsyncMock(return_value=None)
    provider._create_containers = AsyncMock(return_value={"n1": SimpleNamespace(name="c1")})
    provider._start_containers = AsyncMock(return_value=[])
    provider._create_links = AsyncMock(return_value=0)
    provider._capture_container_vlans = AsyncMock(return_value=None)
    provider._wait_for_readiness = AsyncMock(return_value={"n1": True})
    provider.status = AsyncMock(return_value=StatusResult(lab_exists=True, nodes=[]))

    topo = DeployTopology(nodes=[DeployNode(name="n1", kind="linux", interface_count=1)], links=[])
    out = asyncio.run(provider.deploy("lab1", topo, tmp_path))
    assert out.success is True
